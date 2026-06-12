"""Aggregate per-beat labels into windows and rhythm states for reporting.

Two views, mirroring how PVC data is usually explored:

  * fixed-length windows (default 30 s, as in kbroman's plots) — each window
    gets a PVC burden, mean heart rate, and wall-clock placement (time of
    day, date). Plotting one dot per window exposes heterogeneity that a
    running average hides.
  * rhythm states — stretches of bigeminy (every 2nd beat a PVC) and
    trigeminy (every 3rd) vs normal rhythm, since PVCs tend to cluster
    rather than fall independently.

Times are epoch seconds; time-of-day and date use the local timezone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from .classify import BeatLabel
from .features import BeatFeatures

# minimum consecutive ectopic cycles to call a stretch bi-/trigeminy
_MIN_PATTERN_PVCS = 6
STATE_NORMAL = "normal"
STATE_BIGEMINY = "bigeminy"
STATE_TRIGEMINY = "trigeminy"
STATE_OTHER = "other"
RHYTHM_STATES = [STATE_NORMAL, STATE_BIGEMINY, STATE_TRIGEMINY, STATE_OTHER]


@dataclass
class Window:
    t_start: float  # epoch seconds
    n_beats: int
    n_pvc: int
    burden_pct: float
    mean_hr: float
    hour_of_day: float  # 0..24, fractional, local time
    date: str  # YYYY-MM-DD, local
    state_fractions: dict = field(default_factory=dict)


def classify_rhythm(is_pvc: list[bool]) -> list[str]:
    """Per-beat rhythm state from the PVC/normal sequence.

    A run of PVCs spaced exactly every 2nd beat (>= _MIN_PATTERN_PVCS of
    them) is bigeminy; every 3rd beat is trigeminy. Beats in neither a
    qualifying pattern nor a clean normal stretch are 'other' (isolated
    PVCs and their neighbours)."""
    n = len(is_pvc)
    states = [STATE_NORMAL if not p else STATE_OTHER for p in is_pvc]
    pvc_idx = [i for i, p in enumerate(is_pvc) if p]
    if len(pvc_idx) < _MIN_PATTERN_PVCS:
        return states

    gaps = np.diff(pvc_idx)  # beats between consecutive PVCs
    # find maximal runs of constant gap == 2 (bigeminy) or == 3 (trigeminy)
    i = 0
    while i < len(gaps):
        g = gaps[i]
        if g in (2, 3):
            j = i
            while j < len(gaps) and gaps[j] == g:
                j += 1
            run_pvcs = j - i + 1  # number of PVCs in this constant-gap run
            if run_pvcs >= _MIN_PATTERN_PVCS:
                state = STATE_BIGEMINY if g == 2 else STATE_TRIGEMINY
                start_beat = pvc_idx[i]
                end_beat = pvc_idx[j]
                for k in range(start_beat, end_beat + 1):
                    states[k] = state
            i = j
        else:
            i += 1
    return states


def compute_windows(
    features: list[BeatFeatures],
    labels: list[BeatLabel],
    window_s: float = 30.0,
    min_beats: int = 5,
) -> list[Window]:
    """Bin beats into fixed windows with burden, HR, and rhythm fractions.

    Beats must be time-sorted. Windows are aligned to the local-midnight
    grid so windows from different files/days line up."""
    if not features:
        return []

    # bad-signal beats are not classifiable -- drop them so they count toward
    # neither the PVC numerator nor the beat denominator of the burden
    keep = [i for i, l in enumerate(labels) if not l.is_artifact]
    features = [features[i] for i in keep]
    labels = [labels[i] for i in keep]
    if not features:
        return []

    times = np.array([f.t for f in features])
    is_pvc = [bool(l.is_pvc) for l in labels]
    rhythm = classify_rhythm(is_pvc)

    order = np.argsort(times)
    times = times[order]
    is_pvc = [is_pvc[i] for i in order]
    rhythm = [rhythm[i] for i in order]
    rr_before = np.array([features[i].rr_before for i in order])

    win_ids = np.floor(times / window_s).astype(np.int64)
    windows: list[Window] = []
    for wid in np.unique(win_ids):
        mask = win_ids == wid
        idx = np.flatnonzero(mask)
        if idx.size < min_beats:
            continue
        n_beats = int(idx.size)
        n_pvc = int(sum(is_pvc[i] for i in idx))
        rr = rr_before[idx]
        rr = rr[np.isfinite(rr) & (rr > 0)]
        mean_hr = float(60.0 / np.median(rr)) if rr.size else float("nan")
        t_start = float(wid * window_s)
        dt = datetime.fromtimestamp(t_start)  # local time
        frac = {s: 0.0 for s in RHYTHM_STATES}
        for i in idx:
            frac[rhythm[i]] += 1.0 / n_beats
        windows.append(
            Window(
                t_start=t_start,
                n_beats=n_beats,
                n_pvc=n_pvc,
                burden_pct=100.0 * n_pvc / n_beats,
                mean_hr=mean_hr,
                hour_of_day=dt.hour + dt.minute / 60.0 + dt.second / 3600.0,
                date=dt.strftime("%Y-%m-%d"),
                state_fractions=frac,
            )
        )
    return windows
