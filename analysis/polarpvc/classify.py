"""PVC classification from per-beat features.

Clinical hallmarks of a PVC in a single lead:
  1. aberrant morphology (poor correlation to the patient's normal beat)
  2. wide QRS (>~120 ms)
  3. premature (short coupling interval), and/or
  4. followed by a compensatory pause

The decision requires an aberrant complex together with abnormal timing
(premature OR compensatory pause), or morphology so grossly aberrant that
it stands on its own (e.g. an interpolated PVC with near-normal coupling).
Pairing aberrant morphology with a timing abnormality rejects the two big
false-positive sources: motion/noise that distorts morphology on a
normally-timed beat, and benign sinus-arrhythmia timing on a
normal-looking beat.

Aberrancy is judged primarily from the template correlation, not QRS
width. The Polar H10 samples at 130 Hz, so QRS width has ~7.7 ms
resolution -- too coarse to separate a 110 ms PVC from a 95 ms sinus beat
reliably. Against a hand-labelled real recording, requiring a wide QRS as
a hard gate missed ~85% of true PVCs (their measured widths clustered at
100-115 ms, below any sensible width threshold), while the template
correlation separated PVCs from sinus beats almost perfectly. Width is
therefore kept only as an informational flag in `reasons`, not as a gate.

Thresholds are collected in ClassifierConfig so they can be tuned and
validated against labelled data (see scripts/evaluate.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .features import BeatFeatures


@dataclass
class ClassifierConfig:
    aberrant_corr: float = 0.85  # template corr below this = aberrant morphology
    premature_ratio: float = 0.85  # rr_before < ratio * baseline -> premature
    compensatory_min: float = 1.8  # (rr_before+rr_after)/baseline window
    compensatory_max: float = 2.2
    # morphology so aberrant it is accepted on its own, even when the
    # coupling interval is near-normal (interpolated / non-premature PVC)
    strong_corr: float = 0.50
    # wide-QRS threshold; informational only (130 Hz makes width unreliable
    # as a gate -- see module docstring), reported in `reasons` for auditing
    qrs_width_ms: float = 110.0
    # a real QRS (normal or PVC) has measurable width; a complex this narrow
    # is a detection artifact (e.g. a sharp motion spike), not a beat we can
    # classify. Real beats observed at >= ~69 ms; this floor vetoes the
    # near-zero-width spurious detections that morphology alone would
    # otherwise flag as aberrant PVCs.
    min_qrs_width_ms: float = 60.0
    # peak deflections above this are non-physiological for a single chest-lead
    # QRS (real beats in field recordings sit under ~3 mV); a complex this large
    # is a motion artifact, not a PVC, and is vetoed. Observed: a ~13 mV motion
    # spike otherwise produced a false PVC.
    max_amplitude_mv: float = 5.0


@dataclass
class BeatLabel:
    index: int
    t: float
    is_pvc: bool
    reasons: str  # which criteria fired, for auditing


def _is_wide(f: BeatFeatures, cfg: ClassifierConfig) -> bool:
    return f.qrs_width_ms >= cfg.qrs_width_ms


def _is_aberrant(f: BeatFeatures, cfg: ClassifierConfig) -> bool:
    return f.template_corr < cfg.aberrant_corr


def _is_premature(f: BeatFeatures, cfg: ClassifierConfig) -> bool:
    return math.isfinite(f.prematurity) and f.prematurity < cfg.premature_ratio


def _has_compensatory_pause(f: BeatFeatures, cfg: ClassifierConfig) -> bool:
    if not math.isfinite(f.compensatory) or not math.isfinite(f.prematurity):
        return False
    return (
        f.prematurity < cfg.premature_ratio
        and cfg.compensatory_min <= f.compensatory <= cfg.compensatory_max
    )


def classify_beat(f: BeatFeatures, cfg: ClassifierConfig) -> BeatLabel:
    wide = _is_wide(f, cfg)
    aberrant = _is_aberrant(f, cfg)
    premature = _is_premature(f, cfg)
    compensatory = _has_compensatory_pause(f, cfg)

    abnormal_timing = premature or compensatory

    # grossly aberrant morphology stands on its own (interpolated /
    # non-premature PVC that abnormal-timing tests would miss)
    strong = f.template_corr < cfg.strong_corr

    # a grossly non-physiological deflection, or an implausibly narrow
    # complex, is a motion/detection artifact -- not a beat we can classify;
    # veto any PVC call and flag it for auditing
    artifact = (
        abs(f.amplitude_mv) > cfg.max_amplitude_mv
        or f.qrs_width_ms < cfg.min_qrs_width_ms
    )

    is_pvc = ((aberrant and abnormal_timing) or strong) and not artifact

    reasons = []
    if artifact:
        reasons.append("artifact")
    if aberrant:
        reasons.append("aberrant")
    if wide:
        reasons.append("wide")
    if premature:
        reasons.append("premature")
    if compensatory:
        reasons.append("compensatory")
    if strong:
        reasons.append("strong")

    return BeatLabel(index=f.index, t=f.t, is_pvc=is_pvc, reasons="|".join(reasons))


def classify_beats(
    feats: list[BeatFeatures], cfg: ClassifierConfig | None = None
) -> list[BeatLabel]:
    cfg = cfg or ClassifierConfig()
    return [classify_beat(f, cfg) for f in feats]
