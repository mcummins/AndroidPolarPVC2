#!/usr/bin/env python3
"""Build the HTML PVC exploration report from one or more ECG CSVs.

    python scripts/make_report.py 'data/ecg_*.csv' -o report.html

Accepts glob patterns and/or directories (all ecg_*.csv inside are used).
Each file is labeled independently, then windows are pooled for the report.
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from polarpvc import load_ecg_csv, label_record
from polarpvc.windows import compute_windows
from polarpvc.report import build_report


def collect_files(inputs):
    files = []
    for item in inputs:
        if os.path.isdir(item):
            files.extend(sorted(glob.glob(os.path.join(item, "ecg_*.csv"))))
        else:
            files.extend(sorted(glob.glob(item)))
    # de-dup, keep order
    seen, out = set(), []
    for f in files:
        if f not in seen and not f.endswith("_truth.csv"):
            seen.add(f)
            out.append(f)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs", nargs="+", help="ecg_*.csv files, globs, or directories")
    ap.add_argument("-o", "--output", default="pvc_report.html")
    ap.add_argument("--window", type=float, default=30.0, help="window length (s)")
    ap.add_argument("--title", default="PVC exploration")
    args = ap.parse_args()

    files = collect_files(args.inputs)
    if not files:
        print("No ECG files matched.", file=sys.stderr)
        sys.exit(1)

    all_windows = []
    for f in files:
        record = load_ecg_csv(f)
        result = label_record(record)
        wins = compute_windows(result.features, result.labels, window_s=args.window)
        all_windows.extend(wins)
        print(f"  {os.path.basename(f)}: {result.n_beats} beats, "
              f"{result.n_pvc} PVCs, {len(wins)} windows")

    all_windows.sort(key=lambda w: w.t_start)
    summary = build_report(all_windows, args.output, title=args.title)
    print(f"\n{len(files)} file(s), {summary['n_windows']} windows, "
          f"{summary['coverage_hours']} h, overall burden "
          f"{summary['overall_burden_pct']}%")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
