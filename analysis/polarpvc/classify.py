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
    # the morphology-only "strong" path must still have plausible coupling: a
    # grossly-aberrant complex arriving well after the expected beat (large
    # prematurity) is a detection in a noisy gap, not a PVC. Real PVCs here
    # ran 0.42-0.86; this ceiling stops the strong path firing on late beats.
    strong_max_prematurity: float = 1.3
    # wide-QRS threshold; informational only (130 Hz makes width unreliable
    # as a gate -- see module docstring), reported in `reasons` for auditing
    qrs_width_ms: float = 110.0

    # --- signal-quality vetoes: when the local signal is bad we mark the beat
    # an artifact and decline to classify it, rather than risk a false PVC.
    # Excluded beats are dropped from the burden denominator too. ---
    # a real QRS (normal or PVC) has measurable, bounded width. Below the floor
    # is a sharp motion spike. The ceiling targets only *saturation* of the
    # width search window (~200 ms span = energy never falling back to
    # baseline = a slow motion wave / sustained noise, not a discrete complex):
    # the measurement is coarse, so even wide PVCs and noisy normals legitimately
    # read up to ~180 ms -- a lower cap would wrongly exclude genuinely wide
    # PVCs, the very hallmark we want to keep.
    min_qrs_width_ms: float = 60.0
    max_qrs_width_ms: float = 190.0
    # local high-frequency noise above this multiple of the recording's median
    # marks a stretch of bad signal. Real PVCs stayed under ~2.7x; normal beats
    # are noisier (sharp QRS), so this is set with headroom above both.
    max_noise_ratio: float = 3.5
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
    is_artifact: bool = False  # bad-signal beat: excluded, not classified


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
    # non-premature PVC that abnormal-timing tests would miss), but only when
    # the coupling interval is plausible -- otherwise it is noise in a gap
    strong = f.template_corr < cfg.strong_corr and (
        not math.isfinite(f.prematurity)
        or f.prematurity < cfg.strong_max_prematurity
    )

    # signal-quality veto: a grossly non-physiological deflection, an
    # implausible QRS width, or a locally noisy stretch is not a beat we can
    # classify. Mark it an artifact, decline to call it, and exclude it from
    # the burden denominator downstream.
    artifact = (
        abs(f.amplitude_mv) > cfg.max_amplitude_mv
        or f.qrs_width_ms < cfg.min_qrs_width_ms
        or f.qrs_width_ms > cfg.max_qrs_width_ms
        or f.noise_ratio > cfg.max_noise_ratio
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

    return BeatLabel(
        index=f.index,
        t=f.t,
        is_pvc=is_pvc,
        reasons="|".join(reasons),
        is_artifact=artifact,
    )


def classify_beats(
    feats: list[BeatFeatures], cfg: ClassifierConfig | None = None
) -> list[BeatLabel]:
    cfg = cfg or ClassifierConfig()
    return [classify_beat(f, cfg) for f in feats]
