"""End-to-end labeling: ECG CSV -> per-beat labels.

Produces a beats table (one row per detected beat) with timing/morphology
features and the PVC label, which is both the ground-truth material for
refining the classifier and the input to burden computation.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass

import numpy as np

from .classify import BeatLabel, ClassifierConfig, classify_beats
from .detect import detect_beats
from .features import BeatFeatures, extract_features
from .io import EcgRecord, load_ecg_csv


@dataclass
class LabeledBeats:
    record: EcgRecord
    beats: np.ndarray
    features: list[BeatFeatures]
    labels: list[BeatLabel]

    @property
    def n_beats(self) -> int:
        """Classifiable beats (bad-signal artifacts excluded)."""
        return sum(1 for x in self.labels if not x.is_artifact)

    @property
    def n_pvc(self) -> int:
        return sum(1 for x in self.labels if x.is_pvc)

    @property
    def n_artifact(self) -> int:
        return sum(1 for x in self.labels if x.is_artifact)

    @property
    def burden_pct(self) -> float:
        """PVC burden over classifiable beats (artifacts excluded)."""
        return 100.0 * self.n_pvc / self.n_beats if self.n_beats else 0.0


_COLUMNS = [
    "beat_index",
    "sample_index",
    "time_s",
    "rr_before_s",
    "rr_after_s",
    "baseline_rr_s",
    "prematurity",
    "compensatory",
    "qrs_width_ms",
    "amplitude_mv",
    "polarity",
    "template_corr",
    "noise_ratio",
    "is_pvc",
    "reasons",
]


def label_record(
    record: EcgRecord, cfg: ClassifierConfig | None = None
) -> LabeledBeats:
    beats = detect_beats(record)
    feats = extract_features(record, beats)
    labels = classify_beats(feats, cfg)
    return LabeledBeats(record=record, beats=beats, features=feats, labels=labels)


def label_csv(
    in_path: str, out_path: str | None = None, cfg: ClassifierConfig | None = None
) -> LabeledBeats:
    record = load_ecg_csv(in_path)
    result = label_record(record, cfg)
    if out_path:
        write_beats_csv(result, out_path)
    return result


def write_beats_csv(result: LabeledBeats, out_path: str) -> None:
    with open(out_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_COLUMNS)
        for i, (f, lab) in enumerate(zip(result.features, result.labels)):
            writer.writerow(
                [
                    i,
                    f.index,
                    f"{f.t:.6f}",
                    _fmt(f.rr_before),
                    _fmt(f.rr_after),
                    _fmt(f.baseline_rr),
                    _fmt(f.prematurity),
                    _fmt(f.compensatory),
                    f"{f.qrs_width_ms:.1f}",
                    f"{f.amplitude_mv:.4f}",
                    f.polarity,
                    f"{f.template_corr:.4f}",
                    f"{f.noise_ratio:.3f}",
                    int(lab.is_pvc),
                    lab.reasons,
                ]
            )


def _fmt(x: float) -> str:
    return "" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.4f}"
