#!/usr/bin/env python3
"""Validate the labeler against synthetic ground truth.

Generates several mock recordings spanning heart rates and PVC burdens,
runs the full pipeline, and reports beat-detection and PVC-classification
metrics (sensitivity / precision / F1) plus burden error.

    python scripts/evaluate_mock.py
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from polarpvc import label_record
from polarpvc.evaluate import score_detection, score_pvc
from polarpvc import mockdata


SCENARIOS = [
    dict(minutes=10, hr=55, burden=0.02, seed=1),
    dict(minutes=10, hr=65, burden=0.08, seed=2),
    dict(minutes=10, hr=80, burden=0.15, seed=3),
    dict(minutes=10, hr=100, burden=0.05, seed=4),
    dict(minutes=10, hr=130, burden=0.10, seed=5),
    # harder: 4x noise + larger baseline wander, to show real-world margin
    dict(minutes=10, hr=70, burden=0.08, seed=6, noise_mv=0.10,
         baseline_wander_mv=0.20),
    dict(minutes=10, hr=90, burden=0.12, seed=7, noise_mv=0.10,
         baseline_wander_mv=0.20),
]


def run_scenario(sc) -> dict:
    rec = mockdata.generate(
        duration_s=sc["minutes"] * 60.0,
        mean_hr=sc["hr"],
        pvc_burden=sc["burden"],
        seed=sc["seed"],
        noise_mv=sc.get("noise_mv", 0.025),
        baseline_wander_mv=sc.get("baseline_wander_mv", 0.05),
    )
    record = mockdata.MockRecord  # type hint only
    from polarpvc.io import EcgRecord, split_segments

    ecg = EcgRecord(t=rec.t, mv=rec.mv, fs=rec.fs)
    ecg.segments = split_segments(rec.t, rec.mv, rec.fs)

    result = label_record(ecg)
    pred_beat_times = ecg.t[result.beats]
    pred_pvc_times = np.array(
        [lab.t for lab in result.labels if lab.is_pvc]
    )

    det = score_detection(rec.beat_times, pred_beat_times)
    pvc = score_pvc(rec.pvc_times, pred_pvc_times)

    true_burden = 100.0 * len(rec.pvc_times) / len(rec.beat_times)
    pred_burden = result.burden_pct

    return {
        "scenario": sc,
        "detection": det.as_dict(),
        "pvc": pvc.as_dict(),
        "true_burden_pct": round(true_burden, 2),
        "pred_burden_pct": round(pred_burden, 2),
        "burden_abs_err_pp": round(abs(pred_burden - true_burden), 2),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    rows = [run_scenario(sc) for sc in SCENARIOS]

    print("\n=== PVC labeler validation (synthetic ground truth) ===\n")
    header = (
        f"{'HR':>4} {'burden':>7} | "
        f"{'beat se':>7} {'beat pr':>7} | "
        f"{'pvc se':>6} {'pvc pr':>6} {'pvc f1':>6} | "
        f"{'true%':>6} {'pred%':>6} {'errpp':>6}"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        sc = r["scenario"]
        d = r["detection"]
        p = r["pvc"]
        print(
            f"{sc['hr']:>4} {sc['burden']:>7.2f} | "
            f"{d['sensitivity']:>7.3f} {d['precision']:>7.3f} | "
            f"{p['sensitivity']:>6.3f} {p['precision']:>6.3f} {p['f1']:>6.3f} | "
            f"{r['true_burden_pct']:>6.2f} {r['pred_burden_pct']:>6.2f} "
            f"{r['burden_abs_err_pp']:>6.2f}"
        )

    # aggregate
    pvc_se = np.nanmean([r["pvc"]["sensitivity"] for r in rows])
    pvc_pr = np.nanmean([r["pvc"]["precision"] for r in rows])
    pvc_f1 = np.nanmean([r["pvc"]["f1"] for r in rows])
    max_err = max(r["burden_abs_err_pp"] for r in rows)
    print("-" * len(header))
    print(
        f"mean PVC sensitivity={pvc_se:.3f} precision={pvc_pr:.3f} "
        f"f1={pvc_f1:.3f} | max burden error={max_err:.2f} pp"
    )


if __name__ == "__main__":
    main()
