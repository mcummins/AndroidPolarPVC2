#!/usr/bin/env python3
"""Generate the cross-language parity fixture for the on-device classifier.

Writes a synthetic ECG (app CSV format) plus the per-beat labels produced by
*this* (Python) pipeline into the Android test resources. The Kotlin
`EcgParityTest` loads the same CSV, runs the Kotlin port of detect/features/
classify, and asserts its labels match these — so the two implementations
cannot silently drift (CLAUDE.md "Planned next" item 0).

Re-run this after any intentional change to the offline detector/classifier,
then re-run the Kotlin and Python parity tests.

    analysis/.venv/bin/python analysis/scripts/make_parity_fixture.py
"""

import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from polarpvc import mockdata, load_ecg_csv
from polarpvc.pipeline import label_record

# deterministic, modest size: ~120 s so the JVM test is fast but has enough
# beats (incl. PVCs) to be meaningful
SEED = 7
DURATION_S = 120.0
MEAN_HR = 78.0
PVC_BURDEN = 0.12
NOISE_MV = 0.04
WANDER_MV = 0.08

OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "app", "src", "test", "resources", "parity",
)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rec = mockdata.generate(
        duration_s=DURATION_S, mean_hr=MEAN_HR, pvc_burden=PVC_BURDEN,
        seed=SEED, noise_mv=NOISE_MV, baseline_wander_mv=WANDER_MV,
    )
    # inject bad-signal stretches so the parity fixture also exercises the
    # artifact vetoes (amplitude / width-saturation / noise) cross-language
    fs = int(rec.fs)
    i = 60 * fs
    rec.mv[i:i + 20] += np.hanning(20) * 13.0          # gross motion spike
    j0, j1 = 75 * fs, 78 * fs
    burst = np.random.default_rng(SEED + 1).normal(0, 0.35, j1 - j0)
    rec.mv[j0:j1] += burst                              # noisy burst
    ecg_path = os.path.join(OUT_DIR, "ecg_mock.csv")
    mockdata.write_ecg_csv(rec, ecg_path)

    # Label the *written* CSV (integer µV -> mV), not the float signal, so the
    # labels match exactly what both Python's loader and the Kotlin CSV parser
    # see — otherwise integer-rounding makes the noise ratio drift at ~1e-3.
    record = load_ecg_csv(ecg_path)
    result = label_record(record)

    labels_path = os.path.join(OUT_DIR, "labels.csv")
    with open(labels_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([
            "sample_index", "time_s", "is_pvc", "is_artifact",
            "qrs_width_ms", "template_corr", "prematurity", "noise_ratio",
        ])
        for f, lab in zip(result.features, result.labels):
            is_artifact = 1 if "artifact" in lab.reasons.split("|") else 0
            w.writerow([
                f.index, f"{f.t:.6f}", int(lab.is_pvc), is_artifact,
                f"{f.qrs_width_ms:.4f}", f"{f.template_corr:.6f}",
                ("" if not (f.prematurity == f.prematurity) else f"{f.prematurity:.6f}"),
                f"{f.noise_ratio:.6f}",
            ])

    n_pvc = sum(1 for x in result.labels if x.is_pvc)
    n_art = sum(1 for x in result.labels if "artifact" in x.reasons.split("|"))
    print(f"wrote {ecg_path}")
    print(f"wrote {labels_path}: {result.n_beats} beats, {n_pvc} PVCs, "
          f"{n_art} artifacts, burden {result.burden_pct:.2f}%")


if __name__ == "__main__":
    main()
