#!/usr/bin/env python3
"""Generate a multi-day synthetic recording and build the exploration report.

Lets you see the report tooling working before any real data exists. Each
day has rest/activity/exercise blocks with HR-dependent PVC burden and
intermittent bigeminy/trigeminy, so all four charts show real structure.

    python scripts/make_demo.py --days 3 --out-dir /tmp/pvcdemo
    open /tmp/pvcdemo/pvc_report.html
"""

import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from polarpvc import mockdata
from polarpvc.io import EcgRecord, split_segments
from polarpvc.pipeline import label_record
from polarpvc.windows import compute_windows
from polarpvc.report import build_report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--start-hour", type=int, default=7, help="local hour each day starts")
    ap.add_argument("--out-dir", default="/tmp/pvcdemo")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    rng = np.random.default_rng(args.seed)

    # start at local midnight a few days ago, plus the start hour
    base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    base = base - timedelta(days=args.days)

    all_windows = []
    for d in range(args.days):
        day_start = base + timedelta(days=d, hours=args.start_hour)
        start_epoch = day_start.timestamp()
        blocks = mockdata.diurnal_blocks(rng)
        rec = mockdata.generate_session(blocks, start_epoch_s=start_epoch,
                                        seed=args.seed * 100 + d)
        # save the ECG in app format too (so make_report.py can be exercised)
        path = os.path.join(args.out_dir, f"ecg_day{d+1}.csv")
        mockdata.write_ecg_csv(rec, path)

        ecg = EcgRecord(t=rec.t, mv=rec.mv, fs=rec.fs)
        ecg.segments = split_segments(rec.t, rec.mv, rec.fs)
        result = label_record(ecg)
        wins = compute_windows(result.features, result.labels)
        all_windows.extend(wins)
        print(f"day {d+1}: {result.n_beats} beats, {result.n_pvc} PVCs "
              f"({result.burden_pct:.1f}%), {len(wins)} windows")

    all_windows.sort(key=lambda w: w.t_start)
    out = os.path.join(args.out_dir, "pvc_report.html")
    summary = build_report(all_windows, out, title="PVC exploration (synthetic demo)")
    print(f"\nOverall burden {summary['overall_burden_pct']}% over "
          f"{summary['coverage_hours']} h")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
