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
from scipy.signal import butter, filtfilt

from .io import EcgRecord, Segment

# refractory period: no two R peaks closer than this (300 bpm ceiling)
_REFRACTORY_S = 0.20
# QRS integration window
_INTEGRATION_S = 0.15
# search-back: if no beat for this multiple of the recent RR, lower threshold
_SEARCHBACK_RR_FACTOR = 1.66


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

    # adaptive thresholds (Pan–Tompkins): running signal and noise levels
    spki = 0.0  # running estimate of signal peak
    npki = 0.0  # running estimate of noise peak
    # seed from the first couple of seconds
    seed = energy[: int(2 * fs)] if n >= int(2 * fs) else energy
    spki = float(np.max(seed)) * 0.25 if seed.size else 0.0
    npki = float(np.mean(seed)) if seed.size else 0.0

    # candidate local maxima in the energy signal
    peaks = _local_maxima(energy, refractory)

    rr_recent: list[int] = []
    qrs_peaks: list[int] = []
    last_qrs = -refractory
    last_energy_peak = None

    def threshold() -> float:
        return npki + 0.25 * (spki - npki)

    for p in peaks:
        val = energy[p]
        if val >= threshold() and (p - last_qrs) >= refractory:
            qrs_peaks.append(p)
            spki = 0.125 * val + 0.875 * spki
            if last_qrs >= 0:
                rr = p - last_qrs
                rr_recent.append(rr)
                if len(rr_recent) > 8:
                    rr_recent.pop(0)
            last_qrs = p
        else:
            npki = 0.125 * val + 0.875 * npki
        last_energy_peak = p

    # search-back: recover beats missed during threshold transients
    qrs_peaks = _search_back(energy, qrs_peaks, refractory, threshold(), fs)

    # refine each detection to the local extremum of the band-limited signal,
    # taking whichever polarity has the larger absolute deflection (PVCs are
    # frequently inverted in a single lead)
    return _refine(mv, fs, np.array(sorted(set(qrs_peaks)), dtype=int), refine_window_s)


def _local_maxima(x: np.ndarray, min_dist: int) -> np.ndarray:
    """Indices of local maxima at least min_dist apart (greedy by height)."""
    # candidate maxima
    greater = (x[1:-1] >= x[:-2]) & (x[1:-1] > x[2:])
    cand = np.flatnonzero(greater) + 1
    if cand.size == 0:
        return cand
    # enforce spacing, keeping the taller peak
    order = cand[np.argsort(x[cand])[::-1]]
    chosen = []
    taken = np.zeros(x.size, dtype=bool)
    for c in order:
        lo = max(0, c - min_dist)
        hi = min(x.size, c + min_dist + 1)
        if not taken[lo:hi].any():
            chosen.append(c)
            taken[c] = True
    return np.array(sorted(chosen), dtype=int)


def _search_back(energy, qrs_peaks, refractory, thr, fs):
    if len(qrs_peaks) < 2:
        return qrs_peaks
    recovered = list(qrs_peaks)
    rrs = np.diff(qrs_peaks)
    rr_mean = float(np.median(rrs))
    half_thr = thr * 0.5
    for a, b in zip(qrs_peaks[:-1], qrs_peaks[1:]):
        if (b - a) > _SEARCHBACK_RR_FACTOR * rr_mean:
            window = energy[a + refractory : b - refractory]
            if window.size:
                local = np.argmax(window) + a + refractory
                if energy[local] >= half_thr:
                    recovered.append(int(local))
    return sorted(set(recovered))


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
