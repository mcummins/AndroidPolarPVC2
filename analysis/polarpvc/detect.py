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
from scipy.signal import butter, filtfilt, find_peaks

from .io import EcgRecord, Segment

# refractory period: no two R peaks closer than this (300 bpm ceiling)
_REFRACTORY_S = 0.20
# QRS integration window
_INTEGRATION_S = 0.15
# The threshold is signal-relative: a fraction of the LOCAL QRS-energy level,
# estimated as the running MEDIAN of candidate-peak energies. Candidate peaks are
# local maxima above a low global floor; the median of their energies tracks the
# typical QRS regardless of heart rate and is robust to the large motion/EMG
# spikes seen in gym/weights work (a few outliers 3-6x the QRS that a high
# percentile of the raw energy would chase, raising the threshold into the QRS
# band and dropping beats). A noise + K·MAD rule instead fails the opposite way
# during steady exercise (noise rises to the QRS level). Median-of-peaks handles
# both, from rest to ~170 bpm.
_LOCAL_WINDOW_S = 5.0           # window for the running median of peak energies
# keep a peak if its energy >= this fraction of the local median. Kept low so
# PVCs, whose (wide) integrated energy is below the normal-QRS median, are not
# dropped; the global floor still rejects noise.
_SIG_FRAC = 0.35
# candidate peaks (and the threshold floor in quiet stretches) must clear this
# fraction of a robust global QRS-energy level, so tiny noise maxima are excluded
_FLOOR_FRAC = 0.05


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
    peaks = _select_peaks(energy, fs)

    # refine each detection to the local extremum of the band-limited signal,
    # taking whichever polarity has the larger absolute deflection (PVCs are
    # frequently inverted in a single lead)
    return _refine(mv, fs, peaks, refine_window_s)


def _select_peaks(energy: np.ndarray, fs: float) -> np.ndarray:
    """Pick R-peaks from the integrated energy with a signal-relative threshold.

    Candidate peaks are local maxima a refractory period apart and above a low
    global floor (excludes tiny noise maxima). A peak is kept if its energy is at
    least ``_SIG_FRAC`` of the running MEDIAN of nearby candidate energies — the
    median tracks the typical QRS level at any heart rate and ignores the sparse,
    much larger motion/EMG spikes that a percentile would chase."""
    refractory = max(1, int(round(_REFRACTORY_S * fs)))
    floor = _FLOOR_FRAC * float(np.percentile(energy, 98))
    cand, _ = find_peaks(energy, height=floor, distance=refractory)
    if cand.size == 0:
        return cand
    ce = energy[cand]
    half = int(round(_LOCAL_WINDOW_S * fs))
    keep = []
    lo = 0
    hi = 0
    n = cand.size
    for i in range(n):
        while hi < n and cand[hi] <= cand[i] + half:
            hi += 1
        while cand[lo] < cand[i] - half:
            lo += 1
        med = float(np.median(ce[lo:hi]))
        if ce[i] >= max(floor, _SIG_FRAC * med):
            keep.append(cand[i])
    return np.array(keep, dtype=int)


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
