#!/usr/bin/env python3
"""Build the HTML PVC exploration report from one or more ECG recordings.

    python scripts/make_report.py ~/Dropbox/Apps/PolarPVC2 -o report.html
    python scripts/make_report.py 'data/ecg_*.csv' -o report.html

Accepts files, glob patterns, and/or directories (all ecg_*.csv[.gz] inside
are used, plain .csv and gzipped). Every recording is labeled independently
and the windows are pooled into one report. Activity tags from the events
logs (events_*.csv[.gz]) in the same directories add a burden-by-activity
breakdown.
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from polarpvc import load_ecg_csv, label_record
from polarpvc.events import load_activity_tags
from polarpvc.windows import burden_by_activity, compute_windows
from polarpvc.report import build_report


def collect_events(inputs):
    """Find events_*.csv[.gz] in the same directories/globs as the ECG."""
    files = []
    for item in inputs:
        d = item if os.path.isdir(item) else os.path.dirname(item) or "."
        files.extend(glob.glob(os.path.join(d, "events_*.csv")))
        files.extend(glob.glob(os.path.join(d, "events_*.csv.gz")))
    # de-dup, prefer plain .csv over .csv.gz of the same session
    chosen = {}
    for f in sorted(files):
        key = f[:-3] if f.endswith(".gz") else f
        if key not in chosen or (chosen[key].endswith(".gz") and not f.endswith(".gz")):
            chosen[key] = f
    return list(chosen.values())


def collect_files(inputs):
    files = []
    for item in inputs:
        if os.path.isdir(item):
            files.extend(sorted(glob.glob(os.path.join(item, "ecg_*.csv"))))
            files.extend(sorted(glob.glob(os.path.join(item, "ecg_*.csv.gz"))))
        else:
            files.extend(sorted(glob.glob(item)))
    # de-dup by recording (a file may be present as both .csv and .csv.gz, e.g.
    # in the Dropbox folder); prefer the uncompressed copy. keep order.
    chosen: dict = {}
    order = []
    # the ecg_*.csv glob also matches derived files (ecg_*.csv_verdicts.csv etc.)
    derived = ("_truth.csv", "_verdicts.csv", "_viewer.html")
    for f in files:
        if any(f.endswith(suf) for suf in derived):
            continue
        key = f[:-3] if f.endswith(".gz") else f  # recording id (no .gz)
        if key not in chosen:
            order.append(key)
            chosen[key] = f
        elif chosen[key].endswith(".gz") and not f.endswith(".gz"):
            chosen[key] = f  # replace .gz with the plain .csv
    return [chosen[k] for k in order]


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

    tags = load_activity_tags(collect_events(args.inputs))
    activity = burden_by_activity(all_windows, tags)
    if tags:
        print(f"  activity tags: {len(tags)} "
              f"({len([a for a in activity if a.label != '(untagged)'])} distinct labels)")

    summary = build_report(all_windows, args.output, title=args.title, activity=activity)
    print(f"\n{len(files)} file(s), {summary['n_windows']} windows, "
          f"{summary['coverage_hours']} h, overall burden "
          f"{summary['overall_burden_pct']}%")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
