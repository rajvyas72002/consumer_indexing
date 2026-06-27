"""
run_tn.py -- Ingest new day(s) of data for one TN and produce current matches.

USAGE:
    python3 run_tn.py tn_32 consumer1.csv dt1.csv [consumer2.csv dt2.csv ...]

Each (consumer_csv, dt_csv) pair should represent the SAME single day. You can
pass multiple day-pairs in one call, or run this script once per day as new
exports arrive -- evidence accumulates either way, nothing is lost between runs.

OUTPUT (written into <tn_folder>/state/):
    matches.csv      -- current best DT match per consumer + confidence band
    change_log.csv   -- consumers whose recent daily pattern disagrees with
                         the historical consensus (possible re-wiring)
"""

import sys
import os
import shutil

import engine


def main():
    if len(sys.argv) < 4 or (len(sys.argv) - 2) % 2 != 0:
        print(__doc__)
        sys.exit(1)

    tn_name = sys.argv[1]
    file_args = sys.argv[2:]
    day_pairs = list(zip(file_args[0::2], file_args[1::2]))  # (consumer_csv, dt_csv) pairs

    tn_folder = os.path.join(os.path.dirname(__file__), tn_name)
    os.makedirs(os.path.join(tn_folder, "raw"), exist_ok=True)
    os.makedirs(os.path.join(tn_folder, "state"), exist_ok=True)

    state = engine.load_state(tn_folder)
    print(f"[{tn_name}] Loaded state: {len(state['days_ingested'])} day(s) already ingested, "
          f"{len(state['pair_evidence'])} (dt,consumer) pairs with evidence so far.")

    for consumer_csv, dt_csv in day_pairs:
        print(f"[{tn_name}] Ingesting {os.path.basename(consumer_csv)} + {os.path.basename(dt_csv)}...")
        state = engine.ingest_new_day(state, consumer_csv, dt_csv)
        # archive a copy of the raw files into this TN's raw/ folder for audit trail
        for src in (consumer_csv, dt_csv):
            dst = os.path.join(tn_folder, "raw", os.path.basename(src))
            if not os.path.exists(dst):
                shutil.copy(src, dst)

    engine.save_state(state, tn_folder)

    matches_df, changes_df = engine.compute_matches(state)
    matches_path = os.path.join(tn_folder, "state", "matches.csv")
    changes_path = os.path.join(tn_folder, "state", "change_log.csv")
    matches_df.to_csv(matches_path, index=False)
    changes_df.to_csv(changes_path, index=False)

    print()
    print(f"[{tn_name}] Total days ingested so far: {len(state['days_ingested'])}")
    print(f"[{tn_name}] Consumers with a match: {len(matches_df)}")
    print(matches_df["confidence"].value_counts())
    print()
    if len(changes_df):
        print(f"[{tn_name}] !! {len(changes_df)} consumer(s) flagged for possible topology change:")
        print(changes_df.to_string(index=False))
    else:
        print(f"[{tn_name}] No topology-change alerts.")
    print()
    print(f"[{tn_name}] Results written to: {matches_path}")


if __name__ == "__main__":
    main()