#!/usr/bin/env python3
"""Label beats in an ECG CSV: detect beats, extract features, classify PVCs.

    python scripts/label_pvcs.py input_ecg.csv -o beats.csv
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from polarpvc import label_csv


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="ecg_*.csv written by the app")
    ap.add_argument("-o", "--output", help="beats CSV (default: <input>_beats.csv)")
    args = ap.parse_args()

    out = args.output or os.path.splitext(args.input)[0] + "_beats.csv"
    result = label_csv(args.input, out)

    print(f"Loaded {result.record.n_samples} samples, "
          f"{result.record.duration_s:.1f} s, "
          f"{len(result.record.segments)} segment(s)")
    print(f"Detected {result.n_beats} beats, {result.n_pvc} PVCs "
          f"({result.burden_pct:.2f}% burden)")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
