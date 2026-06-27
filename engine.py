"""
engine.py -- Incremental DT <-> Consumer matching engine.

Designed to be run repeatedly as NEW DAYS of data arrive, for a single TN
(substation network) at a time. Evidence accumulates in a JSON state file
across runs -- you never need to "retrain" or reprocess old raw files.

USAGE (see run_tn.py for the actual CLI):
    state = load_state(tn_folder)
    state = ingest_new_day(state, consumer_csv_path, dt_csv_path)
    matches_df, changes_df = compute_matches(state)
    save_state(state, tn_folder)

CORE IDEA
---------
For every (dt, consumer) pair that ever shows ANY outage-time overlap, we keep
a running tally of evidence:
    - jaccard_overlap_seconds : raw overlap time (plausibility signal)
    - isolation_minutes       : overlap time during DT-isolated events only
                                 (disambiguation signal -- see prior analysis)
    - end_match_score_sum/count : how often restoration TIMES line up precisely
                                 (second disambiguation signal)
    - days_with_evidence      : SET of dates that contributed >0 overlap
                                 (this is what "more confident over time" means:
                                 more independent days -> more trustworthy match)

Each new day's ingestion ADDS to these tallies -- nothing is ever recomputed
from scratch, and nothing from a previous day is thrown away. This is what
makes the system "learn" as more data arrives, without literally training any
ML weights -- it's evidence accumulation, which is the right tool for this
problem (see all the analysis earlier: there are no labels to train against).

CONFIDENCE LEVELS
------------------
For each consumer, the top-evidence DT is assigned, along with a confidence
band:
    HIGH    - unique top candidate AND backed by >=3 days of evidence
    MEDIUM  - resolved via isolation/end-time tiebreak, but <3 days OR a
              moderately close second candidate
    LOW     - still ambiguous (multiple DTs with statistically similar evidence)

CHANGE DETECTION
----------------
Rather than comparing single noisy days against the full historical total
(which falsely flags "changes" between two DTs that were always statistically
tied), we split all ingested days into a RECENT window and an OLDER window,
compute the best match independently for each window using the same
pick_best_match logic as everywhere else, and only flag a change when BOTH
windows are individually confident (non-ambiguous) AND they disagree. See
detect_changes() for details.
"""

import json
import os
from collections import defaultdict
from datetime import datetime, date

import pandas as pd

MIN_DAYS_FOR_HIGH_CONFIDENCE = 3
TIE_TOLERANCE = 0.02  # jaccard-score tolerance to consider two DTs "tied" candidates


# ---------------------------------------------------------------------------
# Low-level interval math (same as before, unchanged -- this part was already
# validated against real data)
# ---------------------------------------------------------------------------

def overlap_seconds(a_start, a_end, b_start, b_end):
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    return max(0.0, (earliest_end - latest_start).total_seconds())


def end_time_match(a_end, b_end, tolerance_sec=120, cutoff_sec=3600):
    diff = abs((a_end - b_end).total_seconds())
    if diff <= tolerance_sec:
        return 1.0
    if diff <= cutoff_sec:
        return 1.0 - (diff - tolerance_sec) / (cutoff_sec - tolerance_sec)
    return 0.0


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def empty_state():
    return {
        "pair_evidence": {},          # "dt|consumer" -> accumulated totals across ALL days
                                       # (ONLY for consumers still "active" -- see frozen_consumers)
        "pair_evidence_by_day": {},   # "dt|consumer" -> {date_str: that day's raw numbers}
                                       # (ONLY for active consumers)
        "dt_total_seconds": {},       # dt -> running total downtime seconds (all days)
        "dt_feeder": {},              # dt -> feeder_name (latest known)
        "consumer_feeder": {},        # consumer -> feeder_name (latest known)
        "consumer_meta": {},          # consumer -> {name, claimed_dt_code, ...}
        "days_ingested": [],          # list of date strings already processed (avoid double-counting)
        "frozen_consumers": {},       # consumer -> {predicted_dt, locked_date, recheck_after_date,
                                       #              light_check_jaccard}
                                       # HIGH-confidence consumers "graduate" here and their other
                                       # ~20+ candidate DTs are DELETED from pair_evidence -- this is
                                       # the fix that took storage from 119MB -> ~3.5MB.
    }


def load_state(tn_folder):
    path = os.path.join(tn_folder, "state", "evidence.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return empty_state()


def save_state(state, tn_folder):
    path = os.path.join(tn_folder, "state", "evidence.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f)


# ---------------------------------------------------------------------------
# Ingesting one new day of data
# ---------------------------------------------------------------------------

def _load_csv(path, id_col):
    df = pd.read_csv(path, skiprows=4, parse_dates=["event_occurred", "event_restored"], dayfirst=True)
    df = df.dropna(subset=["event_occurred", "event_restored", id_col])
    df = df[df["event_restored"] >= df["event_occurred"]]
    return df


def _find_header_row(csv_path, must_contain_any, max_scan=20):
    """
    Scan the first `max_scan` lines of a CSV export and return the
    0-indexed row number of the first line that contains ANY of the
    candidate column names in `must_contain_any`.

    Why this exists: these JdVVNL exports come with a variable-length
    "report banner" (title/date/substation lines) before the real header
    row -- the daily outage exports happen to use a fixed 4-line banner
    (see _load_csv's skiprows=4), but the master installation roster
    exports don't reliably follow that same convention. Hardcoding a
    second magic number (skiprows=4, skiprows=6, ...) for every export
    type is fragile and silently breaks the moment someone re-exports
    with one extra metadata line. Detecting the header by CONTENT instead
    of by POSITION survives that kind of drift.
    """
    with open(csv_path, encoding="utf-8-sig") as f:
        for i in range(max_scan):
            line = f.readline()
            if line == "":
                break  # end of file before max_scan reached
            if any(col in line for col in must_contain_any):
                return i
    raise ValueError(
        f"Could not find a header row containing any of {must_contain_any} "
        f"in the first {max_scan} lines of {csv_path}. The file format may "
        f"have changed -- open it manually and check."
    )


def _read_csv_with_detected_header(csv_path, must_contain_any, **read_csv_kwargs):
    """Locate the real header row by content, then read the CSV from there."""
    header_row = _find_header_row(csv_path, must_contain_any)
    return pd.read_csv(csv_path, skiprows=header_row, **read_csv_kwargs)


def ingest_new_day(state, consumer_csv, dt_csv):
    """
    Parse one day's consumer + DT CSVs and ADD their evidence into `state`.
    Safe to call multiple times with the same files (de-duplicated by date),
    and safe to call with files arriving out of order.
    """
    consumer_df = _load_csv(consumer_csv, "consumer_number")
    dt_df = _load_csv(dt_csv, "dt_code")

    day_dates = set(consumer_df["event_occurred"].dt.date.astype(str)) | \
                set(dt_df["event_occurred"].dt.date.astype(str))
    for d in day_dates:
        if d in state["days_ingested"]:
            print(f"  [skip] {d} already ingested -- not double-counting.")
            return state  # whole file skipped if its date was already seen
    state["days_ingested"].extend(sorted(day_dates))

    # update feeder lookups (latest wins -- topology metadata can change)
    for dt, feeder in dt_df.drop_duplicates("dt_code", keep="last")[["dt_code", "feeder_name"]].itertuples(index=False):
        state["dt_feeder"][dt] = feeder
    for cons, feeder in consumer_df.drop_duplicates("consumer_number", keep="last")[["consumer_number", "feeder_name"]].itertuples(index=False):
        state["consumer_feeder"][str(cons)] = feeder

    # store rich DT metadata (dt_name, substation, feeder_code) from DT CSV
    if "dt_meta" not in state:
        state["dt_meta"] = {}
    dt_meta_cols = [c for c in ["dt_code", "dt_name", "feeder_name", "feeder_code", "substation_name"] if c in dt_df.columns]
    for _, row in dt_df.drop_duplicates("dt_code", keep="last")[dt_meta_cols].iterrows():
        state["dt_meta"][str(row["dt_code"])] = {c: row.get(c, "") for c in dt_meta_cols if c != "dt_code"}

    # store rich consumer metadata (name, claimed dt, category, meter_id)
    for _, row in consumer_df.drop_duplicates("consumer_number", keep="last").iterrows():
        state["consumer_meta"][str(row["consumer_number"])] = {
            "name": row.get("consumer_name", ""),
            "claimed_dt_code": str(row.get("dt_code", "")),
            "consumer_category": row.get("consumer_category", ""),
            "meter_id": str(row.get("meter_id", "")),
        }

    # build per-DT and per-consumer interval lists for JUST this day's data
    dt_intervals = defaultdict(list)
    for dt, s, e in dt_df[["dt_code", "event_occurred", "event_restored"]].itertuples(index=False):
        dt_intervals[dt].append((s, e))
        state["dt_total_seconds"][dt] = state["dt_total_seconds"].get(dt, 0.0) + (e - s).total_seconds()

    consumer_intervals = defaultdict(list)
    for cons, s, e in consumer_df[["consumer_number", "event_occurred", "event_restored"]].itertuples(index=False):
        consumer_intervals[str(cons)].append((s, e))

    # isolation weight per DT-event: 1 / (# feeder-mates also down during this window)
    feeder_to_dts = defaultdict(set)
    for dt, feeder in state["dt_feeder"].items():
        feeder_to_dts[feeder].add(dt)

    dt_event_weight = {}  # (dt, start, end) -> weight, for THIS day only
    for feeder, dts in feeder_to_dts.items():
        day_events = [(dt, s, e) for dt in dts for s, e in dt_intervals.get(dt, [])]
        for dt, s, e in day_events:
            co_tripping = {other for other, os_, oe in day_events if overlap_seconds(s, e, os_, oe) > 0}
            dt_event_weight[(dt, s, e)] = 1.0 / max(1, len(co_tripping))

    # only compare (dt, consumer) pairs sharing a feeder -- the proven, free disambiguator
    today_str = max(day_dates)

    for cons, c_ivals in consumer_intervals.items():
        c_feeder = state["consumer_feeder"].get(cons)
        candidate_dts = feeder_to_dts.get(c_feeder, set())

        # --- FROZEN consumers: skip the expensive full scan entirely ---------
        frozen = state["frozen_consumers"].get(cons)
        if frozen is not None:
            if today_str < frozen["recheck_after_date"]:
                # Light check only: does the locked DT still show overlap today?
                # O(events for ONE dt) instead of O(events for ~24 dts) -- this
                # is the actual compute/storage saving, not just a bookkeeping one.
                locked_dt = frozen["predicted_dt"]
                if locked_dt in dt_intervals:
                    day_ov = sum(
                        overlap_seconds(ds, de, cs, ce)
                        for ds, de in dt_intervals[locked_dt]
                        for cs, ce in c_ivals
                        if not (ce < ds or cs > de)
                    )
                    if day_ov > 0:
                        frozen["light_check_jaccard"] = day_ov  # just a freshness signal
                continue  # do NOT touch pair_evidence for frozen consumers
            # else: recheck window has arrived -- fall through to a FULL re-scan
            # below (don't `continue`), so candidate_dts gets fully rebuilt.

        for dt in candidate_dts:
            if dt not in dt_intervals:
                continue
            ov, iso_min, end_sum, end_n = 0.0, 0.0, 0.0, 0
            dt_today_total = sum((de - ds).total_seconds() for ds, de in dt_intervals[dt])
            for ds, de in dt_intervals[dt]:
                w = dt_event_weight.get((dt, ds, de), 1.0)
                for cs, ce in c_ivals:
                    if ce < ds or cs > de:
                        continue
                    o = overlap_seconds(ds, de, cs, ce)
                    if o <= 0:
                        continue
                    ov += o
                    iso_min += w * o / 60.0
                    end_sum += end_time_match(de, ce)
                    end_n += 1
            if ov <= 0:
                continue

            # accumulate into persistent state (the "more data = more confident" evidence)
            key = f"{dt}|{cons}"
            ev = state["pair_evidence"].setdefault(key, {
                "jaccard_overlap_seconds": 0.0, "isolation_minutes": 0.0,
                "end_match_score_sum": 0.0, "end_match_count": 0,
                "days_with_evidence": [],
            })
            ev["jaccard_overlap_seconds"] += ov
            ev["isolation_minutes"] += iso_min
            ev["end_match_score_sum"] += end_sum
            ev["end_match_count"] += end_n
            if today_str not in ev["days_with_evidence"]:
                ev["days_with_evidence"].append(today_str)

            # also record this day's raw numbers on their own (not accumulated),
            # so we can later regroup days into "recent window" vs "older window"
            # for change detection -- a single noisy day should never be compared
            # directly against a big multi-day total, only against another
            # window of comparable size/confidence.
            per_day = state["pair_evidence_by_day"].setdefault(key, {})
            per_day[today_str] = {
                "jaccard_overlap_seconds": ov,
                "isolation_minutes": iso_min,
                "end_match_score_sum": end_sum,
                "end_match_count": end_n,
                "dt_total_seconds_that_day": dt_today_total,
            }

        # --- after scoring this consumer for today, decide: cap or freeze? ---
        # (Skipped naturally if this consumer was frozen AND not yet due for
        # recheck, because of the `continue` above -- we only reach here for
        # active consumers, or frozen consumers whose recheck date arrived.)
        candidates_now = _consumer_candidates_from_evidence(state, cons, candidate_dts)
        if not candidates_now:
            continue

        best, tie_group_size, is_ambiguous = pick_best_match(candidates_now)
        was_frozen = cons in state["frozen_consumers"]

        if (not is_ambiguous and tie_group_size == 1
                and best["days_with_evidence"] >= MIN_DAYS_FOR_HIGH_CONFIDENCE):
            # HIGH confidence reached (or reconfirmed at recheck time) -> freeze
            _freeze_consumer(state, cons, best, today_str)
            state["frozen_consumers"][cons]["recheck_after_date"] = _add_days(today_str, FREEZE_DAYS)
            if was_frozen:
                # this was a recheck that came back agreeing -- nothing else to do
                pass
        else:
            # still active -- cap to top 3 candidates so storage doesn't grow
            # without bound while this consumer remains unresolved
            if was_frozen:
                # recheck came back DISAGREEING with the old frozen answer --
                # unfreeze fully and surface this immediately rather than
                # waiting for the next windowed detect_changes() run.
                old = state["frozen_consumers"].pop(cons)
                state.setdefault("immediate_change_alerts", []).append({
                    "consumer_id": cons,
                    "old_dt": old["predicted_dt"],
                    "note": f"Recheck on {today_str} found evidence no longer "
                            f"clearly supports the frozen match -- now ambiguous "
                            f"or pointing elsewhere. Needs a fresh look.",
                })
            _cap_candidates(state, cons, candidate_dts)

    return state


# ---------------------------------------------------------------------------
# Computing current best matches + confidence, from accumulated evidence
# ---------------------------------------------------------------------------

RELATIVE_GAP_THRESHOLD = 0.05  # require >=5% relative difference to call a tie "resolved"


def pick_best_match(candidates):
    """
    THE single source of truth for "which DT is the best match for this
    consumer", given a list of candidate dicts, each with:
        {"dt": ..., "jaccard": ..., "isolation_minutes": ..., "end_match_avg": ...}

    Used identically whether `candidates` represents ONE day of evidence
    (for change detection) or ALL accumulated evidence (for the main match
    table) -- this is what guarantees the two never silently disagree.

    Returns: (best_candidate_dict, tie_group_size, is_ambiguous)
    """
    candidates = sorted(candidates, key=lambda c: c["jaccard"], reverse=True)
    top_jaccard = candidates[0]["jaccard"]
    tie_group = [c for c in candidates if (top_jaccard - c["jaccard"]) <= TIE_TOLERANCE]

    tie_group.sort(key=lambda c: (c["isolation_minutes"], c["end_match_avg"]), reverse=True)
    best = tie_group[0]
    second = tie_group[1] if len(tie_group) > 1 else None

    # Relative gap, not absolute: "is the difference big compared to the SIZE
    # of the evidence we have" rather than a fixed number of minutes. This
    # matters because evidence totals grow as more days are ingested -- a
    # small absolute gap might be huge evidence on day 1 but noise on day 30.
    if second is not None:
        iso_scale = max(best["isolation_minutes"], second["isolation_minutes"], 1e-9)
        iso_relative_gap = abs(best["isolation_minutes"] - second["isolation_minutes"]) / iso_scale
        end_scale = max(best["end_match_avg"], second["end_match_avg"], 1e-9)
        end_relative_gap = abs(best["end_match_avg"] - second["end_match_avg"]) / end_scale
    else:
        iso_relative_gap = 1.0
        end_relative_gap = 1.0

    is_resolved_by_tiebreak = len(tie_group) > 1 and (
        second is None or
        iso_relative_gap >= RELATIVE_GAP_THRESHOLD or
        end_relative_gap >= RELATIVE_GAP_THRESHOLD
    )
    is_ambiguous = len(tie_group) > 1 and not is_resolved_by_tiebreak

    return best, len(tie_group), is_ambiguous


# ---------------------------------------------------------------------------
# FREEZE + CAP -- the storage fix
# ---------------------------------------------------------------------------
# Problem: storing full evidence for every (DT, consumer) candidate pair grows
# without bound -- on real TN-32 data, 5 days produced ~119MB of state, almost
# all of it candidates that were never going to win (a consumer typically has
# ~24 plausible DTs on its feeder, but only 1 is ever reported as the answer).
#
# Fix, two parts:
#   1. CAP: for consumers still "active" (not yet confidently resolved), only
#      keep the top 3 candidates by current jaccard score. We will never
#      report candidate #4, so there's no reason to keep accumulating its
#      evidence forever.
#   2. FREEZE: once a consumer reaches HIGH confidence, stop tracking its
#      other candidates AT ALL -- delete them -- and store just a tiny
#      "frozen" record: {predicted_dt, locked_date, recheck_after_date}.
#      Every day while frozen, we do a cheap "is the locked DT still showing
#      overlap today?" check (no rescanning other DTs). After FREEZE_DAYS,
#      we unfreeze and do one full re-scan to confirm nothing changed --
#      this is what stops a stale answer from being trusted forever if a
#      meter actually gets rewired.
FREEZE_DAYS = 30
TOP_N_CANDIDATES = 3


def _cap_candidates(state, cons, candidate_dts):
    """
    Keep only the top TOP_N_CANDIDATES (by jaccard) of evidence for this
    consumer in pair_evidence / pair_evidence_by_day. Deletes the rest.
    Called after scoring an active (non-frozen) consumer each day.
    """
    scored = []
    for dt in candidate_dts:
        key = f"{dt}|{cons}"
        ev = state["pair_evidence"].get(key)
        if ev is None:
            continue
        dt_total = state["dt_total_seconds"].get(dt, 0.0)
        union = dt_total + ev["jaccard_overlap_seconds"]
        jaccard = ev["jaccard_overlap_seconds"] / union if union > 0 else 0.0
        scored.append((jaccard, dt, key))

    scored.sort(key=lambda t: t[0], reverse=True)
    keep_keys = {key for _, _, key in scored[:TOP_N_CANDIDATES]}
    drop_keys = {key for _, _, key in scored[TOP_N_CANDIDATES:]}

    for key in drop_keys:
        state["pair_evidence"].pop(key, None)
        state["pair_evidence_by_day"].pop(key, None)


def _consumer_candidates_from_evidence(state, cons, candidate_dts):
    """Build the candidate-dict list pick_best_match expects, for ONE consumer,
    from whatever (dt, consumer) pairs currently exist in pair_evidence."""
    candidates = []
    for dt in candidate_dts:
        key = f"{dt}|{cons}"
        ev = state["pair_evidence"].get(key)
        if ev is None:
            continue
        dt_total = state["dt_total_seconds"].get(dt, 0.0)
        union = dt_total + ev["jaccard_overlap_seconds"]
        jaccard = ev["jaccard_overlap_seconds"] / union if union > 0 else 0.0
        end_avg = ev["end_match_score_sum"] / ev["end_match_count"] if ev["end_match_count"] else 0.0
        candidates.append({
            "dt": dt,
            "jaccard": jaccard,
            "isolation_minutes": ev["isolation_minutes"],
            "end_match_avg": end_avg,
            "days_with_evidence": len(ev["days_with_evidence"]),
        })
    return candidates


def _freeze_consumer(state, cons, best, today_str):
    """Move a consumer into frozen_consumers and delete its other candidates."""
    dt_feeder = state["dt_feeder"].get(best["dt"])
    all_dts_on_feeder = {d for d, f in state["dt_feeder"].items() if f == dt_feeder}
    _cap_candidates(state, cons, all_dts_on_feeder)  # cap first (cheap top-3 cleanup)
    # then drop even the runner-ups -- frozen consumers need NOTHING but the winner
    for dt in list(all_dts_on_feeder):
        if dt == best["dt"]:
            continue
        key = f"{dt}|{cons}"
        state["pair_evidence"].pop(key, None)
        state["pair_evidence_by_day"].pop(key, None)

    state["frozen_consumers"][cons] = {
        "predicted_dt": best["dt"],
        "locked_date": today_str,
        "recheck_after_date": today_str,  # placeholder, real date math done by caller
        "light_check_jaccard": best["jaccard"],
    }


def _add_days(date_str, n_days):
    from datetime import timedelta
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return str(d + timedelta(days=n_days))


def compute_matches(state):
    """
    From accumulated evidence, compute the current best DT match per consumer,
    with a confidence band. Call this any time -- it's read-only, never
    mutates state, and reflects ALL evidence ingested so far.
    """
    by_consumer = defaultdict(list)
    for key, ev in state["pair_evidence"].items():
        dt, cons = key.split("|", 1)
        dt_total = state["dt_total_seconds"].get(dt, 0.0)
        union = dt_total + ev["jaccard_overlap_seconds"]  # approx union (consumer side small vs DT total in practice)
        jaccard = ev["jaccard_overlap_seconds"] / union if union > 0 else 0.0
        end_avg = ev["end_match_score_sum"] / ev["end_match_count"] if ev["end_match_count"] else 0.0
        by_consumer[cons].append({
            "dt": dt,
            "jaccard": jaccard,
            "isolation_minutes": ev["isolation_minutes"],
            "end_match_avg": end_avg,
            "days_with_evidence": len(ev["days_with_evidence"]),
        })

    rows = []
    for cons, candidates in by_consumer.items():
        best, tie_group_size, is_ambiguous = pick_best_match(candidates)

        if is_ambiguous:
            confidence = "LOW"
        elif tie_group_size == 1 and best["days_with_evidence"] >= MIN_DAYS_FOR_HIGH_CONFIDENCE:
            confidence = "HIGH"
        elif tie_group_size == 1:
            confidence = "MEDIUM"  # unique but not enough days yet to fully trust
        else:
            confidence = "MEDIUM"  # resolved by tiebreak, but had real competition

        meta = state["consumer_meta"].get(cons, {})
        rows.append({
            "consumer_id": cons,
            "consumer_name": meta.get("name", ""),
            "predicted_dt": best["dt"],
            "confidence": confidence,
            "jaccard_score": round(best["jaccard"], 4),
            "isolation_minutes": round(best["isolation_minutes"], 2),
            "end_time_match_avg": round(best["end_match_avg"], 3),
            "days_with_evidence": best["days_with_evidence"],
            "tie_group_size": tie_group_size,
            "is_ambiguous": is_ambiguous,
            "claimed_dt_code": meta.get("claimed_dt_code", ""),
        })

    matches_df = pd.DataFrame(rows).sort_values(["confidence", "jaccard_score"], ascending=[True, False])

    # frozen consumers are already-decided -- no candidates to score, just
    # report their locked answer directly (this is the whole point of
    # freezing: skip the work, not just skip the storage)
    if matches_df.empty:
        seen_frozen = set()
    else:
        seen_frozen = set(matches_df["consumer_id"])
    frozen_rows = []
    for cons, fr in state["frozen_consumers"].items():
        if cons in seen_frozen:
            continue  # shouldn't happen, but don't double-report
        meta = state["consumer_meta"].get(cons, {})
        frozen_rows.append({
            "consumer_id": cons,
            "consumer_name": meta.get("name", ""),
            "predicted_dt": fr["predicted_dt"],
            "confidence": "HIGH",
            "jaccard_score": None,
            "isolation_minutes": None,
            "end_time_match_avg": None,
            "days_with_evidence": None,
            "tie_group_size": 1,
            "is_ambiguous": False,
            "claimed_dt_code": meta.get("claimed_dt_code", ""),
            "frozen": True,
            "locked_date": fr["locked_date"],
            "recheck_after_date": fr["recheck_after_date"],
        })
    if frozen_rows:
        matches_df["frozen"] = matches_df.get("frozen", False)
        matches_df = pd.concat([matches_df, pd.DataFrame(frozen_rows)], ignore_index=True)

    changes_df = detect_changes(state)
    return matches_df, changes_df


RECENT_WINDOW_DAYS = 5     # how many of the most recent days count as the "recent window"
MIN_OLDER_DAYS = 3         # require at least this many older days to trust the "before" picture


def _aggregate_pair_evidence(per_day_dict, days):
    """Sum a pair's per-day evidence over a specific set of days (a 'window')."""
    totals = {"jaccard_overlap_seconds": 0.0, "isolation_minutes": 0.0,
              "end_match_score_sum": 0.0, "end_match_count": 0, "dt_total_seconds": 0.0}
    n_days = 0
    for d in days:
        day_ev = per_day_dict.get(d)
        if day_ev is None:
            continue
        n_days += 1
        totals["jaccard_overlap_seconds"] += day_ev["jaccard_overlap_seconds"]
        totals["isolation_minutes"] += day_ev["isolation_minutes"]
        totals["end_match_score_sum"] += day_ev["end_match_score_sum"]
        totals["end_match_count"] += day_ev["end_match_count"]
        totals["dt_total_seconds"] += day_ev["dt_total_seconds_that_day"]
    return totals, n_days


def detect_changes(state):
    """
    Compare a RECENT WINDOW of days against an OLDER WINDOW of days for each
    consumer, using the exact same pick_best_match logic as the main matcher
    on EACH window separately. This avoids the core problem with comparing
    single noisy days against big multi-day totals: small windows are noisier
    than large ones, so naive day-vs-total comparisons flag false "changes"
    just from sampling noise between two DTs that were never really
    distinguishable to begin with.

    A change is only flagged when:
      1. The OLDER window has enough days to trust on its own, AND produces a
         confident (non-ambiguous) best match.
      2. The RECENT window also produces a confident (non-ambiguous) best match.
      3. The two confident answers actually disagree.

    This means: two near-identical DTs that always tied will simply show up
    as "ambiguous" in BOTH windows and never trigger an alert -- which is the
    honest, correct behavior.
    """
    all_days = sorted(state["days_ingested"])
    if len(all_days) < RECENT_WINDOW_DAYS + MIN_OLDER_DAYS:
        return pd.DataFrame()  # not enough history yet to split into two trustworthy windows

    recent_days = set(all_days[-RECENT_WINDOW_DAYS:])
    older_days = set(all_days[:-RECENT_WINDOW_DAYS])

    # group per-pair-per-day evidence by consumer
    by_consumer_recent = defaultdict(list)
    by_consumer_older = defaultdict(list)

    for key, per_day in state["pair_evidence_by_day"].items():
        dt, cons = key.split("|", 1)

        recent_totals, recent_n = _aggregate_pair_evidence(per_day, recent_days)
        if recent_n > 0:
            union = recent_totals["dt_total_seconds"] + recent_totals["jaccard_overlap_seconds"]
            by_consumer_recent[cons].append({
                "dt": dt,
                "jaccard": recent_totals["jaccard_overlap_seconds"] / union if union > 0 else 0.0,
                "isolation_minutes": recent_totals["isolation_minutes"],
                "end_match_avg": (recent_totals["end_match_score_sum"] / recent_totals["end_match_count"]
                                   if recent_totals["end_match_count"] else 0.0),
                "days_with_evidence": recent_n,
            })

        older_totals, older_n = _aggregate_pair_evidence(per_day, older_days)
        if older_n > 0:
            union = older_totals["dt_total_seconds"] + older_totals["jaccard_overlap_seconds"]
            by_consumer_older[cons].append({
                "dt": dt,
                "jaccard": older_totals["jaccard_overlap_seconds"] / union if union > 0 else 0.0,
                "isolation_minutes": older_totals["isolation_minutes"],
                "end_match_avg": (older_totals["end_match_score_sum"] / older_totals["end_match_count"]
                                   if older_totals["end_match_count"] else 0.0),
                "days_with_evidence": older_n,
            })

    flags = []
    for cons, recent_candidates in by_consumer_recent.items():
        older_candidates = by_consumer_older.get(cons)
        if not older_candidates:
            continue  # no older evidence to compare against

        older_best, older_tie_size, older_ambiguous = pick_best_match(older_candidates)
        if older_ambiguous or older_best["days_with_evidence"] < MIN_OLDER_DAYS:
            continue  # don't trust an ambiguous or thin "before" picture as a baseline

        recent_best, recent_tie_size, recent_ambiguous = pick_best_match(recent_candidates)
        if recent_ambiguous:
            continue  # recent window itself isn't confident -- nothing to alarm about

        if recent_best["dt"] != older_best["dt"]:
            meta = state["consumer_meta"].get(cons, {})
            flags.append({
                "consumer_id": cons,
                "consumer_name": meta.get("name", ""),
                "older_dt": older_best["dt"],
                "older_days": older_best["days_with_evidence"],
                "recent_dt": recent_best["dt"],
                "recent_days": recent_best["days_with_evidence"],
                "note": "Both the older and recent windows independently produced a "
                        "CONFIDENT (non-ambiguous) match, and they disagree -- this is "
                        "a much stronger signal than daily noise. Worth a field check.",
            })

    return pd.DataFrame(flags)


# ---------------------------------------------------------------------------
# Master roster (installation/survey data) -- used for COVERAGE checking,
# not for matching. This answers a different question than compute_matches:
# "of every DT/consumer that physically exists on the ground, how many have
# we actually seen ANY outage evidence for at all?" A consumer with zero
# outage evidence isn't "LOW confidence" -- it's a different, more concerning
# case (possibly a meter that isn't reporting at all).
# ---------------------------------------------------------------------------
def load_master_dt_roster(csv_path):
    """Read a DT installation/survey CSV, return the set of unique dt_codes."""
    df = _read_csv_with_detected_header(csv_path, ["DT Code", "dt_code"], dtype=str)
    col = "DT Code" if "DT Code" in df.columns else "dt_code"
    codes = df[col].dropna().str.strip()
    return set(codes[codes != ""])


def load_master_consumer_roster(csv_path):
    """Read a consumer installation/survey CSV, return the set of unique
    consumer numbers (as strings, to match consumer_number in outage data)."""
    df = _read_csv_with_detected_header(
        csv_path, ["Consumer Number (KNO)", "consumer_number"], dtype=str
    )
    col = "Consumer Number (KNO)" if "Consumer Number (KNO)" in df.columns else "consumer_number"
    nums = df[col].dropna().str.strip()
    return set(nums[nums != ""])


def load_master_consumer_status(csv_path):
    """Read a consumer installation/survey CSV, return a DataFrame keyed by
    consumer_number with the two installer-recorded quality/status fields:
        l2_approval_status : 'Approved' / 'Rejected' / blank
        mdm_payload_status : 'Success' / 'ValidationFailure' / blank
    Used to flag installation records that weren't cleanly approved or
    weren't confirmed reaching the MDM (meter data management) system --
    this is independent of outage-timing confidence, it's a DATA QUALITY
    signal about the installation record itself.
    """
    df = _read_csv_with_detected_header(
        csv_path, ["Consumer Number (KNO)", "consumer_number"], dtype=str
    )
    id_col = "Consumer Number (KNO)" if "Consumer Number (KNO)" in df.columns else "consumer_number"
    out = df[[id_col]].copy()
    out.columns = ["consumer_id"]
    out["consumer_id"] = out["consumer_id"].str.strip()
    out["l2_approval_status"] = df.get("L2 Approval Status", "").fillna("").str.strip()
    out["mdm_payload_status"] = df.get("MDM Payload Status", "").fillna("").str.strip()
    out = out.dropna(subset=["consumer_id"])
    out = out[out["consumer_id"] != ""]
    # a consumer could in theory appear more than once in survey data (re-visit,
    # correction) -- keep the LAST record, which is the most recent installer entry
    out = out.drop_duplicates(subset="consumer_id", keep="last")
    return out.set_index("consumer_id")


def compute_coverage(state, master_dt_codes, master_consumer_nums):
    """
    Compare the master roster against what's actually been seen in ingested
    outage data. Returns two small summary dicts (DT side, consumer side).
    """
    seen_dts = set(state["dt_total_seconds"].keys())
    dt_covered = master_dt_codes & seen_dts
    dt_uncovered = master_dt_codes - seen_dts

    matches_df, _ = compute_matches(state)
    seen_consumers = set(matches_df["consumer_id"].astype(str))
    cons_covered = master_consumer_nums & seen_consumers
    cons_uncovered = master_consumer_nums - seen_consumers

    by_confidence = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    if len(matches_df):
        covered_rows = matches_df[matches_df["consumer_id"].astype(str).isin(cons_covered)]
        for conf in by_confidence:
            by_confidence[conf] = int((covered_rows["confidence"] == conf).sum())

    return {
        "dt_total": len(master_dt_codes),
        "dt_covered": len(dt_covered),
        "dt_uncovered": len(dt_uncovered),
        "dt_uncovered_list": sorted(dt_uncovered),
        "consumer_total": len(master_consumer_nums),
        "consumer_covered": len(cons_covered),
        "consumer_uncovered": len(cons_uncovered),
        "consumer_uncovered_list": sorted(cons_uncovered),
        "consumer_by_confidence": by_confidence,
    }