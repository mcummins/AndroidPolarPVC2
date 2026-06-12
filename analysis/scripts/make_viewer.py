#!/usr/bin/env python3
"""Build a standalone HTML ECG viewer for manually reviewing beat labels.

    python scripts/make_viewer.py data/ecg_2026-06-12_144623Z.csv -o viewer.html

Opens offline (no CDN). Scroll the raw ECG, see every detected beat marked
and colour-coded by the classifier (normal / PVC / artifact), inspect a
beat's features, and record your own verdict per beat. The viewer shows the
classifier's sensitivity/precision over the beats you've reviewed and lists
disagreements; Export saves your verdicts as CSV for re-tuning.

One file per viewer (the whole signal is embedded). Pass a custom
ClassifierConfig only by editing the pipeline; this uses the defaults.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from polarpvc import load_ecg_csv, label_record
from polarpvc.viewer import build_viewer


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="one ecg_*.csv file")
    ap.add_argument("-o", "--output", default=None,
                    help="output HTML (default: <input>_viewer.html)")
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        print(f"No such file: {args.input}", file=sys.stderr)
        sys.exit(1)

    name = os.path.basename(args.input)
    out = args.output or os.path.splitext(args.input)[0] + "_viewer.html"
    title = args.title or f"ECG review — {name}"

    record = load_ecg_csv(args.input)
    result = label_record(record)
    info = build_viewer(result, out, title=title, rec_id=name)
    print(f"{name}: {info['n_beats']} beats, {info['n_pvc']} PVCs, "
          f"{info['burden_pct']:.2f}% burden")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
