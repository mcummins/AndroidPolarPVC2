#!/usr/bin/env python3
"""Generate a mock ECG file (app CSV format) plus a ground-truth file.

    python scripts/make_mock.py --out-dir /tmp/mock --minutes 10 --burden 0.08
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from polarpvc import mockdata


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=".", help="output directory")
    ap.add_argument("--name", default="ecg_mock", help="base filename")
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--hr", type=float, default=65.0, help="mean heart rate (bpm)")
    ap.add_argument("--burden", type=float, default=0.08, help="PVC fraction")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    rec = mockdata.generate(
        duration_s=args.minutes * 60.0,
        mean_hr=args.hr,
        pvc_burden=args.burden,
        seed=args.seed,
    )
    ecg_path = os.path.join(args.out_dir, f"{args.name}.csv")
    truth_path = os.path.join(args.out_dir, f"{args.name}_truth.csv")
    mockdata.write_ecg_csv(rec, ecg_path)
    mockdata.write_truth_csv(rec, truth_path)

    n_pvc = sum(1 for b in rec.beats if b.is_pvc)
    print(f"Wrote {ecg_path} ({rec.t.size} samples, {len(rec.beats)} beats)")
    print(f"Wrote {truth_path} ({n_pvc} PVCs, "
          f"{100.0 * n_pvc / len(rec.beats):.1f}% burden)")


if __name__ == "__main__":
    main()
