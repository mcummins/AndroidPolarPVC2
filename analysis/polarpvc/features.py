"""Per-beat feature extraction.

For each detected R-peak we compute the features that distinguish PVCs
from normal sinus beats:

  * timing — RR before/after vs a local baseline RR (prematurity,
    compensatory pause)
  * morphology — correlation of the beat's QRS-T window against a template
    built from the normal beats (PVCs are aberrant, so they correlate
    poorly)
  * QRS width — PVCs are wide (>~120 ms); measured from the energy
    envelope around the R-peak
  * amplitude and polarity — PVCs are often taller and/or inverted

Offline we can build a template and a robust local baseline RR, which the
streaming on-phone classifier cannot.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import butter, filtfilt

from .io import EcgRecord

# morphology window around the R-peak (seconds)
_MORPH_PRE_S = 0.12
_MORPH_POST_S = 0.20
# template correlation is matched over +/- this many samples of fiducial shift.
# A biphasic QRS (R and S nearly equal) makes the detector's fiducial jitter
# between the R and S peaks beat-to-beat; without this tolerance the morphology
# window shifts and a normal beat anti-correlates with the template, producing
# mass false PVCs. The search is small enough that genuinely aberrant (wide)
# complexes still don't match.
_MORPH_MAX_LAG = 5
# QRS width search window around R (seconds)
_QRS_HALF_S = 0.10
# window (each side of R) for the local high-frequency noise estimate
_NOISE_HALF_S = 0.20
# fraction of the per-beat energy peak defining QRS onset/offset
_QRS_ENVELOPE_FRAC = 0.15
# beats on each side used for the local baseline RR (rolling median)
_BASELINE_RR_BEATS = 8


@dataclass
class BeatFeatures:
    index: int  # sample index into the record
    t: float  # time (s, epoch)
    rr_before: float  # s (nan if unknown)
    rr_after: float  # s (nan if unknown)
    baseline_rr: float  # s (nan if unknown)
    prematurity: float  # rr_before / baseline_rr
    compensatory: float  # (rr_before + rr_after) / baseline_rr
    qrs_width_ms: float
    amplitude_mv: float  # peak deflection above local baseline
    polarity: int  # +1 upright, -1 inverted
    template_corr: float  # correlation to normal template ([-1, 1])
    noise_ratio: float = 1.0  # local HF noise / recording-median HF noise


def _bandpass(sig, fs, lo, hi):
    nyq = 0.5 * fs
    hi = min(hi, nyq * 0.99)
    b, a = butter(2, [lo / nyq, hi / nyq], btype="band")
    return filtfilt(b, a, sig)


def _energy_envelope(sig, fs):
    filt = _bandpass(sig, fs, 5.0, 25.0)
    sq = filt ** 2
    win = max(1, int(round(0.05 * fs)))
    return np.convolve(sq, np.ones(win) / win, mode="same")


def _qrs_width_ms(envelope, r_idx, fs):
    half = max(1, int(round(_QRS_HALF_S * fs)))
    lo = max(0, r_idx - half)
    hi = min(envelope.size, r_idx + half + 1)
    local = envelope[lo:hi]
    if local.size == 0:
        return 0.0
    peak = local.max()
    if peak <= 0:
        return 0.0
    thr = peak * _QRS_ENVELOPE_FRAC
    r_local = r_idx - lo
    # walk left/right from the R-peak to where energy falls below threshold
    left = r_local
    while left > 0 and local[left] > thr:
        left -= 1
    right = r_local
    while right < local.size - 1 and local[right] > thr:
        right += 1
    return (right - left) / fs * 1000.0


def _noise_ratio(mv, fs, beats):
    """Per-beat high-frequency noise, normalised to the recording baseline.

    A clean QRS lives in the 5-25 Hz band; energy *above* 25 Hz around a beat
    is electrode/motion noise, not cardiac signal. We take the RMS of that
    out-of-band residual in a window around each R-peak and divide by the
    recording's median (a robust per-recording baseline, since artifacts are
    sparse). A real beat -- normal or PVC -- sits near 1; values several times
    the baseline mark a stretch of bad signal we should not try to classify.
    """
    n = beats.size
    if n == 0:
        return np.ones(0)
    hf = mv - _bandpass(mv, fs, 0.5, 25.0)
    half = max(1, int(round(_NOISE_HALF_S * fs)))
    rms = np.empty(n)
    for i, r in enumerate(beats):
        lo = max(0, int(r) - half)
        hi = min(hf.size, int(r) + half + 1)
        seg = hf[lo:hi]
        rms[i] = float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0
    base = np.median(rms[rms > 0]) if np.any(rms > 0) else 1.0
    if base <= 0:
        base = 1.0
    return rms / base


def _baseline_mv(mv, fs, r_idx):
    """Local baseline level just before the QRS (PR-segment region)."""
    a = max(0, r_idx - int(round(0.12 * fs)))
    b = max(0, r_idx - int(round(0.06 * fs)))
    if b <= a:
        return 0.0
    return float(np.median(mv[a:b]))


def _rolling_baseline_rr(rr: np.ndarray) -> np.ndarray:
    """Robust local baseline RR per interval (median of surrounding RRs).

    PVCs perturb individual RRs but are sparse, so a windowed median gives
    the underlying sinus RR even around an ectopic beat."""
    n = rr.size
    out = np.full(n, np.nan)
    k = _BASELINE_RR_BEATS
    for i in range(n):
        lo = max(0, i - k)
        hi = min(n, i + k + 1)
        window = rr[lo:hi]
        window = window[np.isfinite(window)]
        if window.size:
            out[i] = np.median(window)
    return out


def _build_template(windows, valid_mask):
    """Median normal-beat waveform from beats flagged as template-eligible."""
    sel = windows[valid_mask]
    if sel.shape[0] < 5:
        sel = windows  # fall back to all beats if too few normals
    template = np.median(sel, axis=0)
    return template


def _correlate_lagged(mv, beats, base_arr, pre, post, template, max_lag):
    """Per-beat correlation to the template, maximised over a small fiducial
    shift. This makes the morphology feature robust to the detector landing on
    the R peak for some beats and the S peak for others when the QRS is biphasic
    (the shift is a few samples, not a shape change), without rescuing genuinely
    aberrant complexes (their shape mismatches at every lag)."""
    n = beats.size
    win_len = pre + post + 1
    corr = np.zeros(n)
    for i in range(n):
        r = int(beats[i])
        b = base_arr[i]
        best = -2.0
        for d in range(-max_lag, max_lag + 1):
            lo = r - pre + d
            hi = r + post + 1 + d
            if lo < 0 or hi > mv.size:
                seg = np.zeros(win_len)
                vlo = max(0, lo)
                vhi = min(mv.size, hi)
                valid = mv[vlo:vhi] - b
                seg[: valid.size] = valid
            else:
                seg = mv[lo:hi] - b
            seg = seg - seg.mean()
            norm = np.linalg.norm(seg)
            if norm > 0:
                seg = seg / norm
            c = float((seg * template).sum())
            if c > best:
                best = c
        corr[i] = best
    return corr


def extract_features(record: EcgRecord, beats: np.ndarray) -> list[BeatFeatures]:
    fs = record.fs
    mv = record.mv
    t = record.t
    n = beats.size
    if n == 0:
        return []

    pre = int(round(_MORPH_PRE_S * fs))
    post = int(round(_MORPH_POST_S * fs))
    envelope = _energy_envelope(mv, fs)
    noise_ratio = _noise_ratio(mv, fs, beats)

    # RR intervals (seconds)
    rr_before = np.full(n, np.nan)
    rr_after = np.full(n, np.nan)
    times = t[beats]
    rr_before[1:] = np.diff(times)
    rr_after[:-1] = np.diff(times)
    baseline_rr = _rolling_baseline_rr(
        rr_before if n > 1 else np.array([np.nan])
    )

    # morphology windows (R-aligned), amplitude, polarity, width
    width = np.zeros(n)
    amp = np.zeros(n)
    polarity = np.ones(n, dtype=int)
    base_arr = np.zeros(n)
    win_len = pre + post + 1
    windows = np.zeros((n, win_len))
    for i, r in enumerate(beats):
        base = _baseline_mv(mv, fs, r)
        base_arr[i] = base
        lo = r - pre
        hi = r + post + 1
        if lo < 0 or hi > mv.size:
            seg = np.zeros(win_len)
            seg_valid = mv[max(0, lo):min(mv.size, hi)] - base
            seg[: seg_valid.size] = seg_valid
        else:
            seg = mv[lo:hi] - base
        windows[i] = seg
        amp[i] = mv[r] - base
        polarity[i] = 1 if amp[i] >= 0 else -1
        width[i] = _qrs_width_ms(envelope, int(r), fs)

    # template from beats that look normal a priori: not premature, modest
    # width. Prematurity needs baseline_rr, so guard for NaNs. Use the
    # recording's DOMINANT QRS polarity rather than assuming upright -- in some
    # leads / with good electrode contact the normal beat's main deflection is
    # negative, and assuming upright would build the template from the wrong
    # (minority) beats and flag every normal beat as aberrant.
    prematurity = rr_before / baseline_rr
    median_width = np.median(width[width > 0]) if np.any(width > 0) else 0.0
    base_pool = (width <= median_width * 1.3 + 1e-9) & (~(prematurity < 0.85))
    pool = base_pool if base_pool.any() else np.ones(n, dtype=bool)
    dominant = 1 if int(np.sum(polarity[pool] > 0)) >= int(np.sum(polarity[pool] < 0)) else -1
    template_eligible = base_pool & (polarity == dominant)
    # normalise windows before templating/correlation (zero-mean, unit-norm)
    norm_windows = _normalize_rows(windows)
    template = _build_template(norm_windows, template_eligible)
    template = _unit(template - template.mean())

    # correlation to the template, matched over a small fiducial shift so a
    # biphasic-QRS beat that the detector aligned to S instead of R still
    # matches its own normal template (see _correlate_lagged)
    corr = _correlate_lagged(mv, beats, base_arr, pre, post, template, _MORPH_MAX_LAG)

    feats = []
    for i in range(n):
        feats.append(
            BeatFeatures(
                index=int(beats[i]),
                t=float(times[i]),
                rr_before=float(rr_before[i]),
                rr_after=float(rr_after[i]),
                baseline_rr=float(baseline_rr[i]),
                prematurity=float(prematurity[i]) if np.isfinite(prematurity[i]) else float("nan"),
                compensatory=float((rr_before[i] + rr_after[i]) / baseline_rr[i])
                if np.isfinite(rr_before[i]) and np.isfinite(rr_after[i]) and np.isfinite(baseline_rr[i])
                else float("nan"),
                qrs_width_ms=float(width[i]),
                amplitude_mv=float(amp[i]),
                polarity=int(polarity[i]),
                template_corr=float(corr[i]),
                noise_ratio=float(noise_ratio[i]),
            )
        )
    return feats


def _normalize_rows(w):
    centered = w - w.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(centered, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return centered / norms


def _unit(v):
    norm = np.linalg.norm(v)
    return v / norm if norm > 0 else v
