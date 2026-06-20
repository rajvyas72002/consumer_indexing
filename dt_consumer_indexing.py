"""
DT <-> Consumer matching using outage-timing overlap (Jaccard-style interval similarity).

Core idea:
  If consumer C is actually fed by transformer DT, then every time DT goes
  off, C's meter should ALSO show an outage covering roughly the same time
  window. We measure this with interval (time) overlap instead of trusting
  the (noisy) dt_code column already present in the consumer file.

Algorithm (Jaccard similarity over total downtime, not just event count):
  1. Build a list of (start, end) outage intervals for every DT and every consumer.
  2. For each (DT, consumer) pair, compute:
       overlap_time   = sum of intersection durations across all interval pairs
       union_time     = DT_total_downtime + consumer_total_downtime - overlap_time
       jaccard_score  = overlap_time / union_time      (0 = no relation, 1 = identical pattern)
  3. For each consumer, the DT with the highest jaccard_score is the predicted parent DT.
  4. Naive pairwise comparison is O(num_dt * num_consumers) interval-overlap computations,
     which would be slow at scale -> we restrict comparisons to only (DT, consumer) pairs
     that share at least one overlapping day/hour bucket, using a simple time-bucket index.
"""

import pandas as pd
from collections import defaultdict


def load_events(paths, id_col):
    """
    Load one or more power-failure CSVs (skipping the 4 metadata rows), parse
    timestamps, concatenate them, and do basic cleanup.
    `paths` can be a single path (str) or a list of paths (multi-day data).
    """
    if isinstance(paths, str):
        paths = [paths]

    frames = []
    for path in paths:
        df = pd.read_csv(
            path,
            skiprows=4,
            parse_dates=["event_occurred", "event_restored"],
            dayfirst=True,
        )
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)

    # Defensive cleanup: drop rows with missing/invalid timestamps or end < start
    df = df.dropna(subset=["event_occurred", "event_restored", id_col])
    df = df[df["event_restored"] >= df["event_occurred"]]
    # drop exact duplicate events (can happen if the same day was exported twice)
    df = df.drop_duplicates(subset=[id_col, "event_occurred", "event_restored"])
    return df


def build_intervals(df, id_col):
    """
    Convert a dataframe of events into: {id_value: [(start, end), (start, end), ...]}
    """
    intervals = defaultdict(list)
    for id_value, start, end in zip(df[id_col], df["event_occurred"], df["event_restored"]):
        intervals[id_value].append((start, end))
    return intervals


def total_duration(intervals):
    """Sum of (end - start) in seconds for a list of intervals. Assumes intervals don't overlap themselves."""
    return sum((e - s).total_seconds() for s, e in intervals)


def weighted_total_duration(weighted_intervals):
    """Sum of weight * (end - start) in seconds for a list of (start, end, weight) tuples."""
    return sum(w * (e - s).total_seconds() for s, e, w in weighted_intervals)


def overlap_seconds(a_start, a_end, b_start, b_end):
    """Overlap (in seconds) between two intervals; 0 if disjoint."""
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    return max(0.0, (earliest_end - latest_start).total_seconds())


def pair_overlap_seconds(intervals_a, intervals_b):
    """
    Total overlap time between two lists of intervals.
    Naive O(n*m) per pair -- fine here since each meter only has ~1-85 events.
    """
    total = 0.0
    for a_start, a_end in intervals_a:
        for b_start, b_end in intervals_b:
            # quick skip if entirely outside each other (cheap prune)
            if b_end < a_start or b_start > a_end:
                continue
            total += overlap_seconds(a_start, a_end, b_start, b_end)
    return total


def pair_end_time_match_score(intervals_a, intervals_b, tolerance_sec=120):
    """
    Disambiguation signal #2: restoration-time precision.

    When several DTs on a feeder trip together at the same instant, they often
    restore at DIFFERENT times if one of them had its own local fault that
    outlasted the shared event (e.g. 11 DTs restore after 8 min, but 8 DTs on a
    damaged downstream section stay off for 626 min -- a real example found in
    this data). A consumer's own restoration time should line up tightly with
    its TRUE parent DT's restoration time, even when both candidate DTs shared
    the exact same outage START.

    For every pair of overlapping intervals (one from each side), score how
    close their END times are. Score per matched pair = 1 if end times are
    within `tolerance_sec`, decaying linearly to 0 beyond that, up to a 1-hour
    cutoff (treated as "unrelated" beyond that).
    Returns: average match score across all overlapping interval pairs (0-1),
    and the count of such pairs (so a single coincidental match doesn't
    outweigh a meter with many corroborating events).
    """
    scores = []
    for a_start, a_end in intervals_a:
        for b_start, b_end in intervals_b:
            if b_end < a_start or b_start > a_end:
                continue
            if overlap_seconds(a_start, a_end, b_start, b_end) <= 0:
                continue
            end_diff = abs((a_end - b_end).total_seconds())
            if end_diff <= tolerance_sec:
                scores.append(1.0)
            elif end_diff <= 3600:
                scores.append(1.0 - (end_diff - tolerance_sec) / (3600 - tolerance_sec))
            else:
                scores.append(0.0)
    if not scores:
        return 0.0, 0
    return sum(scores) / len(scores), len(scores)


def compute_dt_event_weights(dt_intervals, dt_feeder):
    """
    For every individual DT outage event, compute an 'isolation weight' between 0 and 1:

        weight = 1 / (number of DTs on the SAME feeder that were ALSO down
                       during an overlapping time window, including itself)

    Intuition:
      - If DT-37 trips ALONE on its feeder at 14:00-14:10 -> weight = 1.0
        (this is gold: only consumers truly fed by DT-37 should show this outage)
      - If all 20 DTs on the feeder trip together at 14:00-14:10 (upstream fault)
        -> weight = 1/20 = 0.05 (this event carries almost no information about
        WHICH of the 20 DTs a consumer belongs to, so we downweight it)

    Returns: {(dt_code, event_index): weight} via a parallel structure --
    we return a dict {dt_code: [(start, end, weight), ...]} so it can directly
    replace the plain interval lists used elsewhere.
    """
    # group DTs by feeder so we only compare DTs that could plausibly trip together
    feeder_to_dts = defaultdict(list)
    for dt, feeder in dt_feeder.items():
        feeder_to_dts[feeder].append(dt)

    weighted_intervals = defaultdict(list)
    for feeder, dts in feeder_to_dts.items():
        # build a flat list of (dt, start, end) for all DTs sharing this feeder
        events = []
        for dt in dts:
            if dt not in dt_intervals:
                continue
            for s, e in dt_intervals[dt]:
                events.append((dt, s, e))

        for dt, s, e in events:
            # count distinct DTs (including self) with an overlapping event in this feeder
            co_tripping_dts = set()
            for other_dt, os_, oe in events:
                if oe < s or os_ > e:
                    continue  # no overlap
                if overlap_seconds(s, e, os_, oe) > 0:
                    co_tripping_dts.add(other_dt)
            weight = 1.0 / max(1, len(co_tripping_dts))
            weighted_intervals[dt].append((s, e, weight))

    return weighted_intervals


def pair_weighted_overlap_seconds(weighted_a, intervals_b):
    """
    Like pair_overlap_seconds, but each interval in `weighted_a` (the DT side)
    carries a weight in [0,1]. Overlap seconds are multiplied by that weight,
    so isolated DT-only events count far more than feeder-wide shared events.
    """
    total = 0.0
    for a_start, a_end, w in weighted_a:
        for b_start, b_end in intervals_b:
            if b_end < a_start or b_start > a_end:
                continue
            total += w * overlap_seconds(a_start, a_end, b_start, b_end)
    return total


def hour_buckets(intervals):
    """Return the set of hour-buckets (date+hour) an interval list touches. Used to prune comparisons."""
    buckets = set()
    for s, e in intervals:
        # bucket every hour touched between s and e (inclusive)
        cur = s.replace(minute=0, second=0, microsecond=0)
        end_bucket = e.replace(minute=0, second=0, microsecond=0)
        while cur <= end_bucket:
            buckets.add(cur)
            cur += pd.Timedelta(hours=1)
    return buckets


def match_consumers_to_dts(dt_intervals, consumer_intervals, dt_feeder, consumer_feeder,
                            dt_weighted_intervals, tie_tolerance=0.02):
    """
    Two-stage matching for each consumer:

      STAGE 1 - Plausibility ranking:
        Score every candidate DT (same feeder + overlapping time bucket) using
        plain Jaccard similarity on outage-interval overlap. This answers
        "how good is this match overall" and is never distorted by weighting.

      STAGE 2 - Disambiguation among near-ties:
        Take every candidate within `tie_tolerance` of the top jaccard_score
        (the genuine tie group -- e.g. two DTs on the same feeder that almost
        always trip together). Within ONLY that group, break the tie using
        isolation_evidence_minutes: total minutes of overlap that came from
        DT events where FEW other feeder-mates were simultaneously down.
        This is a raw minutes count, not a ratio, so it can never shrink a
        good match's score -- it only decides between already-plausible options.

      If isolation evidence is itself tied (e.g. two DTs that have NEVER once
      tripped independently of each other across all available days), the match
      is honestly flagged as ambiguous rather than an arbitrary pick being made.
    """
    dt_buckets = {dt: hour_buckets(ivals) for dt, ivals in dt_intervals.items()}
    dt_total = {dt: total_duration(ivals) for dt, ivals in dt_intervals.items()}

    bucket_to_dts = defaultdict(set)
    for dt, buckets in dt_buckets.items():
        for b in buckets:
            bucket_to_dts[b].add(dt)

    feeder_to_dts = defaultdict(set)
    for dt, feeder in dt_feeder.items():
        feeder_to_dts[feeder].add(dt)

    results = []
    for consumer_id, c_ivals in consumer_intervals.items():
        c_buckets = hour_buckets(c_ivals)
        c_total = total_duration(c_ivals)
        c_feeder = consumer_feeder.get(consumer_id)

        time_candidates = set()
        for b in c_buckets:
            time_candidates |= bucket_to_dts.get(b, set())

        feeder_candidates = feeder_to_dts.get(c_feeder, set())
        candidates = time_candidates & feeder_candidates

        used_feeder_filter = True
        if not candidates:
            candidates = time_candidates
            used_feeder_filter = False

        # ---- STAGE 1: plausibility (plain jaccard) ----
        stage1 = []
        for dt in candidates:
            ov = pair_overlap_seconds(dt_intervals[dt], c_ivals)
            if ov <= 0:
                continue
            union = dt_total[dt] + c_total - ov
            jaccard = ov / union if union > 0 else 0.0
            stage1.append((dt, jaccard, ov))

        if not stage1:
            results.append({
                "consumer_id": consumer_id, "predicted_dt": None, "jaccard_score": 0.0,
                "overlap_minutes": 0.0, "isolation_evidence_minutes": 0.0,
                "end_time_match_score": 0.0, "end_time_match_pairs": 0,
                "num_candidates_considered": len(candidates), "used_feeder_filter": used_feeder_filter,
                "num_dts_evaluated": 0, "tie_group_size": 0, "is_ambiguous": False,
            })
            continue

        stage1.sort(key=lambda x: x[1], reverse=True)
        top_jaccard = stage1[0][1]

        # genuine tie group: candidates within tie_tolerance of the best plausibility score
        tie_group = [row for row in stage1 if (top_jaccard - row[1]) <= tie_tolerance]

        # ---- STAGE 2: disambiguate within the tie group using isolation evidence
        #               AND restoration-time precision ----
        stage2 = []
        for dt, jaccard, ov in tie_group:
            iso_minutes = pair_weighted_overlap_seconds(dt_weighted_intervals.get(dt, []), c_ivals) / 60.0
            end_score, end_pairs = pair_end_time_match_score(dt_intervals[dt], c_ivals)
            stage2.append((dt, jaccard, ov, iso_minutes, end_score, end_pairs))

        # rank by isolation evidence first (strongest signal when present), then
        # by restoration-time match score (catches cases where DTs never had a
        # uniquely-isolated event but still diverged in WHEN they restored),
        # then jaccard as a final tiebreak
        stage2.sort(key=lambda x: (x[3], x[4], x[1]), reverse=True)
        best_dt, best_jaccard, best_overlap, best_iso, best_end_score, best_end_pairs = stage2[0]

        # ambiguous only if BOTH disambiguating signals tie across 2+ candidates
        second = stage2[1] if len(stage2) > 1 else None
        is_ambiguous = (
            len(tie_group) > 1
            and second is not None
            and abs(best_iso - second[3]) < 0.5      # isolation evidence ties (within 30 sec)
            and abs(best_end_score - second[4]) < 0.05  # restoration-time match also ties
        )

        results.append({
            "consumer_id": consumer_id,
            "predicted_dt": best_dt,
            "jaccard_score": round(best_jaccard, 4),
            "overlap_minutes": round(best_overlap / 60, 1),
            "isolation_evidence_minutes": round(best_iso, 2),
            "end_time_match_score": round(best_end_score, 3),
            "end_time_match_pairs": best_end_pairs,
            "num_candidates_considered": len(candidates),
            "used_feeder_filter": used_feeder_filter,
            "num_dts_evaluated": len(stage1),
            "tie_group_size": len(tie_group),
            "is_ambiguous": is_ambiguous,
        })

    return pd.DataFrame(results)


if __name__ == "__main__":
    CONSUMER_CSVS = [
        "/mnt/user-data/uploads/Power_Failure_Report_Consumer_Meter__4_.csv",  # 2026-06-13
        "/mnt/user-data/uploads/Power_Failure_Report_Consumer_Meter__1_.csv",  # 2026-06-14
        "/mnt/user-data/uploads/Power_Failure_Report_Consumer_Meter__2_.csv",  # 2026-06-15
        "/mnt/user-data/uploads/Power_Failure_Report_Consumer_Meter__3_.csv",  # 2026-06-16
        "/mnt/user-data/uploads/Power_Failure_Report_Consumer_Meter.csv",       # 2026-06-17
    ]
    DT_CSVS = [
        "/mnt/user-data/uploads/Power_Failure_Report_DT_Meter__1_.csv",  # 2026-06-13
        "/mnt/user-data/uploads/Power_Failure_Report_DT_Meter__2_.csv",  # 2026-06-14
        "/mnt/user-data/uploads/Power_Failure_Report_DT_Meter__3_.csv",  # 2026-06-15
        "/mnt/user-data/uploads/Power_Failure_Report_DT_Meter__4_.csv",  # 2026-06-16
        "/mnt/user-data/uploads/Power_Failure_Report_DT_Meter.csv",       # 2026-06-17
    ]

    consumer_df = load_events(CONSUMER_CSVS, id_col="consumer_number")
    dt_df = load_events(DT_CSVS, id_col="dt_code")

    dt_intervals = build_intervals(dt_df, "dt_code")
    consumer_intervals = build_intervals(consumer_df, "consumer_number")

    # feeder lookup: dt_code -> feeder_name, consumer_number -> feeder_name
    # (use the most recent file's mapping in case feeder assignment ever changes)
    dt_feeder = dt_df.drop_duplicates("dt_code", keep="last").set_index("dt_code")["feeder_name"].to_dict()
    consumer_feeder = consumer_df.drop_duplicates("consumer_number", keep="last").set_index("consumer_number")["feeder_name"].to_dict()

    print(f"Loaded {len(dt_intervals)} DTs ({len(dt_df)} events) and "
          f"{len(consumer_intervals)} consumers ({len(consumer_df)} events) across "
          f"{consumer_df['event_occurred'].dt.date.nunique()} day(s).")

    print("Computing isolation weights for DT events (this distinguishes local DT "
          "faults from feeder-wide shared trips)...")
    dt_weighted_intervals = compute_dt_event_weights(dt_intervals, dt_feeder)

    matches = match_consumers_to_dts(
        dt_intervals, consumer_intervals, dt_feeder, consumer_feeder, dt_weighted_intervals
    )

    # join back consumer metadata (name, original dt_code claim) for inspection
    meta_cols = ["consumer_number", "consumer_name", "dt_code", "dt_name", "feeder_name"]
    meta = consumer_df[meta_cols].drop_duplicates("consumer_number", keep="last")
    matches = matches.merge(meta, left_on="consumer_id", right_on="consumer_number", how="left")
    matches = matches.drop(columns=["consumer_number"])

    # flag agreement with the original (possibly unreliable) dt_code field
    matches["agrees_with_original_dt_code"] = matches["predicted_dt"] == matches["dt_code"]

    matches = matches.sort_values("jaccard_score", ascending=False)
    matches.to_csv("/home/claude/dt_consumer/dt_consumer_matches.csv", index=False)

    print(matches.head(20).to_string())
    print()
    print("Jaccard (plausibility) score distribution:")
    print(matches["jaccard_score"].describe())
    print()
    print("% with a unique top candidate (no tie at stage 1):", (matches["tie_group_size"] <= 1).mean())
    print("% flagged ambiguous after isolation-evidence tiebreak:", matches["is_ambiguous"].mean())
    print("% confidently resolved (tie existed but isolation evidence broke it):",
          ((matches["tie_group_size"] > 1) & (~matches["is_ambiguous"])).mean())
    print("Agreement rate with original dt_code field:", matches["agrees_with_original_dt_code"].mean())