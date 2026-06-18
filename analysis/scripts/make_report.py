#!/usr/bin/env python3
"""Build the HTML PVC exploration report from one or more ECG recordings.

    python scripts/make_report.py ~/Dropbox/Apps/PolarPVC2 -o report.html
    python scripts/make_report.py 'data/ecg_*.csv' -o report.html

Accepts files, glob patterns, and/or directories (all ecg_*.csv[.gz] inside
are used, plain .csv and gzipped). Every recording is labeled independently
and the windows are pooled into one report. Activity tags from the events
logs (events_*.csv[.gz]) in the same directories add a burden-by-activity
breakdown.

Each recording also gets a standalone <rec>_viewer.html written next to it
(skipped if it already exists; pass --regenerate_viewers to force a rebuild
after re-tuning the classifier). Double-clicking a point in the report's
Day-view opens that recording's viewer at the clicked time.
"""

import argparse
import glob
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from polarpvc import load_ecg_csv, label_record
from polarpvc.events import load_activity_tags
from polarpvc.windows import burden_by_activity, compute_windows
from polarpvc.report import build_report
from polarpvc.viewer import build_viewer


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


def find_viewer(ecg_path):
    """Return a file:// URI for the viewer HTML of an ECG file, or None.

    make_viewer.py names its output os.path.splitext(input)[0] + "_viewer.html",
    so the name depends on whether it was run against the .csv or the .csv.gz:
    ecg_X.csv -> ecg_X_viewer.html, ecg_X.csv.gz -> ecg_X.csv_viewer.html.
    Check both; return the first that exists as an absolute file URI."""
    rec_key = ecg_path[:-3] if ecg_path.endswith(".gz") else ecg_path  # strip .gz
    candidates = [
        os.path.splitext(rec_key)[0] + "_viewer.html",  # ecg_X_viewer.html
        rec_key + "_viewer.html",                        # ecg_X.csv_viewer.html
    ]
    for c in candidates:
        if os.path.isfile(c):
            return pathlib.Path(os.path.abspath(c)).as_uri()
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs", nargs="+", help="ecg_*.csv files, globs, or directories")
    ap.add_argument("-o", "--output", default="pvc_report.html")
    ap.add_argument("--window", type=float, default=30.0, help="window length (s)")
    ap.add_argument("--title", default="PVC exploration")
    ap.add_argument("--regenerate_viewers", action="store_true",
                    help="rebuild every recording's viewer.html even if it "
                         "already exists (e.g. after re-tuning the classifier)")
    args = ap.parse_args()

    files = collect_files(args.inputs)
    if not files:
        print("No ECG files matched.", file=sys.stderr)
        sys.exit(1)

    all_windows = []
    viewers = {}  # recording id (basename) -> viewer file:// URI, for drill-down
    n_built = 0
    for f in files:
        record = load_ecg_csv(f)
        result = label_record(record)
        wins = compute_windows(result.features, result.labels, window_s=args.window)
        rec_key = f[:-3] if f.endswith(".gz") else f
        rec_id = os.path.basename(rec_key)
        for w in wins:
            w.source = rec_id

        # Generate a per-recording viewer.html for the Day-view drill-down,
        # skipping ones that already exist (fast on reruns) unless asked to
        # regenerate. We already have the labeled result, so this is cheap.
        viewer_path = os.path.splitext(rec_key)[0] + "_viewer.html"
        built = False
        if args.regenerate_viewers or not os.path.isfile(viewer_path):
            build_viewer(result, viewer_path, title=f"ECG review — {rec_id}",
                         rec_id=rec_id)
            built = True
            n_built += 1
        viewer_uri = find_viewer(f)
        if viewer_uri:
            viewers[rec_id] = viewer_uri

        all_windows.extend(wins)
        print(f"  {os.path.basename(f)}: {result.n_beats} beats, "
              f"{result.n_pvc} PVCs, {len(wins)} windows"
              f"{'  [viewer built]' if built else ('  [viewer]' if viewer_uri else '')}")

    all_windows.sort(key=lambda w: w.t_start)
    if viewers:
        print(f"  {len(viewers)} of {len(files)} recording(s) have a viewer.html "
              f"({n_built} built this run) — double-click a Day-view point to open")

    tags = load_activity_tags(collect_events(args.inputs))
    activity = burden_by_activity(all_windows, tags)
    if tags:
        print(f"  activity tags: {len(tags)} "
              f"({len([a for a in activity if a.label != '(untagged)'])} distinct labels)")

    summary = build_report(all_windows, args.output, title=args.title,
                           activity=activity, viewers=viewers)
    print(f"\n{len(files)} file(s), {summary['n_windows']} windows, "
          f"{summary['coverage_hours']} h, overall burden "
          f"{summary['overall_burden_pct']}%")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
