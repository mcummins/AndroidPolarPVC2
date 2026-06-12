"""PVC classification from per-beat features.

Clinical hallmarks of a PVC in a single lead:
  1. wide QRS (>~120 ms)
  2. aberrant morphology (poor correlation to the patient's normal beat)
  3. premature (short coupling interval), and/or
  4. followed by a compensatory pause

The decision requires genuine QRS aberrancy (wide AND abnormal
morphology) together with abnormal timing (premature OR compensatory
pause). Requiring both an aberrant complex and a timing abnormality
rejects the two big false-positive sources: motion/noise that distorts
morphology on a normally-timed beat, and benign sinus-arrhythmia timing
on a normal-looking beat.

Thresholds are collected in ClassifierConfig so they can be tuned and
validated against labelled data (see scripts/evaluate.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .features import BeatFeatures


@dataclass
class ClassifierConfig:
    qrs_width_ms: float = 110.0  # wide-QRS threshold
    template_corr: float = 0.90  # below this = aberrant morphology
    premature_ratio: float = 0.85  # rr_before < ratio * baseline -> premature
    compensatory_min: float = 1.8  # (rr_before+rr_after)/baseline window
    compensatory_max: float = 2.2
    # a beat that is extremely aberrant (very wide AND very low corr) is
    # accepted even if timing is ambiguous (e.g. interpolated PVC)
    strong_width_ms: float = 140.0
    strong_corr: float = 0.70
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
    return f.template_corr < cfg.template_corr


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

    aberrant_complex = wide and aberrant
    abnormal_timing = premature or compensatory

    strong = (
        f.qrs_width_ms >= cfg.strong_width_ms
        and f.template_corr < cfg.strong_corr
    )

    # a grossly non-physiological deflection is a motion artifact, not a beat
    # we can classify; veto any PVC call and flag it for auditing
    artifact = abs(f.amplitude_mv) > cfg.max_amplitude_mv

    is_pvc = ((aberrant_complex and abnormal_timing) or strong) and not artifact

    reasons = []
    if artifact:
        reasons.append("artifact")
    if wide:
        reasons.append("wide")
    if aberrant:
        reasons.append("aberrant")
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
