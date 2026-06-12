"""R-peak (beat) detection.

A compact Pan–Tompkins-style detector run over the whole signal. Unlike
the on-phone detector (which sees one half-batch at a time and so can
merge beats above ~160 bpm), this works on the full record, so closely
spaced beats — including PVCs and exercise tachycardia — are resolved.

Each contiguous segment is processed independently; detection across a
dropout gap would be meaningless.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import butter, filtfilt, find_peaks

from .io import EcgRecord, Segment

# refractory period: no two R peaks closer than this (300 bpm ceiling)
_REFRACTORY_S = 0.20
# QRS integration window
_INTEGRATION_S = 0.15
# sliding-threshold window: local energy stats are computed over this span so
# the threshold tracks slow amplitude drift (electrode contact, posture) yet a
# single motion artifact cannot raise it for more than a beat or two
_THRESHOLD_WINDOW_S = 2.5
# a candidate must exceed local median energy by this many local MADs
_THRESHOLD_MAD_K = 8.0
# floor the threshold at this fraction of a robust QRS-energy level so quiet
# stretches with no beats do not detect their own noise as beats
_THRESHOLD_FLOOR_FRAC = 0.10


def _bandpass(sig: np.ndarray, fs: float, lo: float, hi: float) -> np.ndarray:
    nyq = 0.5 * fs
    hi = min(hi, nyq * 0.99)
    b, a = butter(2, [lo / nyq, hi / nyq], btype="band")
    return filtfilt(b, a, sig)


def _integrated_energy(sig: np.ndarray, fs: float) -> np.ndarray:
    """Pan–Tompkins front end: bandpass, derivative, square, moving sum."""
    filtered = _bandpass(sig, fs, 5.0, 15.0)
    deriv = np.gradient(filtered)
    squared = deriv ** 2
    win = max(1, int(round(_INTEGRATION_S * fs)))
    kernel = np.ones(win) / win
    return np.convolve(squared, kernel, mode="same")


def _detect_in_array(
    mv: np.ndarray, fs: float, refine_window_s: float = 0.05
) -> np.ndarray:
    """Return R-peak sample indices within a single contiguous array."""
    n = mv.size
    if n < int(0.5 * fs):
        return np.empty(0, dtype=int)

    energy = _integrated_energy(mv, fs)
    refractory = max(1, int(round(_REFRACTORY_S * fs)))

    # Per-sample adaptive threshold from local robust statistics. The earlier
    # Pan–Tompkins running EWMA had a failure mode on real data: a single large
    # motion artifact (seen ~7900x median energy in field recordings) pushes the
    # running signal estimate so high that the threshold never recovers and all
    # subsequent beats are missed. A windowed median/MAD threshold is immune —
    # the median ignores the artifact, so detection resumes within a beat.
    thr = _adaptive_threshold(energy, fs)
    qrs_peaks, _ = find_peaks(energy, height=thr, distance=refractory)

    # refine each detection to the local extremum of the band-limited signal,
    # taking whichever polarity has the larger absolute deflection (PVCs are
    # frequently inverted in a single lead)
    return _refine(mv, fs, np.array(sorted(set(qrs_peaks.tolist())), dtype=int), refine_window_s)


def _adaptive_threshold(energy: np.ndarray, fs: float) -> np.ndarray:
    """Per-sample detection threshold: local median + K·MAD of the energy,
    floored at a fraction of a robust QRS-energy level."""
    win = int(round(_THRESHOLD_WINDOW_S * fs)) | 1  # odd window
    win = min(win, energy.size if energy.size % 2 else energy.size - 1)
    win = max(win, 1)
    med = median_filter(energy, size=win, mode="nearest")
    mad = median_filter(np.abs(energy - med), size=win, mode="nearest")
    thr = med + _THRESHOLD_MAD_K * mad
    # QRS complexes occupy a small fraction of the record, so a high energy
    # percentile is a robust stand-in for "typical beat energy".
    floor = _THRESHOLD_FLOOR_FRAC * float(np.percentile(energy, 98))
    return np.maximum(thr, floor)


def _refine(mv, fs, peaks, window_s):
    if peaks.size == 0:
        return peaks
    w = max(1, int(round(window_s * fs)))
    # remove slow baseline (1 s moving average) so "largest absolute
    # deflection" reflects the QRS, not baseline wander
    base_win = max(1, int(fs))
    baseline = np.convolve(mv, np.ones(base_win) / base_win, mode="same")
    detr = mv - baseline
    refined = []
    for p in peaks:
        lo = max(0, p - w)
        hi = min(mv.size, p + w + 1)
        seg = detr[lo:hi]
        idx = lo + int(np.argmax(np.abs(seg)))
        refined.append(idx)
    return np.array(sorted(set(refined)), dtype=int)


def detect_beats(record: EcgRecord) -> np.ndarray:
    """Detect R-peaks across all segments; returns sample indices into the
    full record arrays (record.t / record.mv)."""
    all_indices: list[int] = []
    segments = record.segments or [
        Segment(t=record.t, mv=record.mv, start_index=0)
    ]
    for seg in segments:
        local = _detect_in_array(seg.mv, record.fs)
        all_indices.extend((local + seg.start_index).tolist())
    return np.array(sorted(set(all_indices)), dtype=int)
