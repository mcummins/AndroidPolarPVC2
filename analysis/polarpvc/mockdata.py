"""Synthetic ECG generation with ground-truth beat labels.

Produces a signal in the exact format the app writes (metadata line,
epoch-nanosecond timestamps, integer microvolts) so the loader and
labeler can be exercised and validated end-to-end without a real
recording. Each beat's time and type (normal/PVC) are returned as ground
truth for scoring the labeler.

The model is deliberately simple but captures what the classifier keys
on: a PQRST normal beat, and PVCs that are premature, wide, aberrant
(often inverted, no P wave) and followed by a compensatory pause.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

FS = 130.0
# Polar timestamps are ns since 2000-01-01; the app adds this offset to get
# ns since the Unix epoch. Mock files use a fixed plausible epoch start.
_EPOCH_START_NS = 1_750_000_000_000_000_000  # ~2025

# Gaussian PQRST waves: (amplitude_mV, center_s_relative_to_R, sigma_s)
_NORMAL_WAVES = [
    (0.12, -0.20, 0.025),   # P
    (-0.15, -0.025, 0.008),  # Q
    (1.20, 0.0, 0.011),      # R
    (-0.25, 0.025, 0.010),   # S
    (0.30, 0.16, 0.040),     # T
]

# A few PVC morphologies. Each has a tall main deflection (steep enough
# to be detected like a real PVC) plus a broad shoulder that makes the QRS
# wide, no P wave, and a large discordant T. Some are inverted.
_PVC_MORPHOLOGIES = [
    # inverted main deflection, broad shoulder, upright discordant T
    [(-1.70, 0.0, 0.020), (-0.70, 0.030, 0.050), (0.55, 0.20, 0.070)],
    # upright main deflection, broad shoulder, inverted discordant T
    [(1.80, 0.0, 0.020), (0.65, 0.032, 0.052), (-0.55, 0.22, 0.075)],
    # tall slurred upright complex with inverted discordant T
    [(1.90, 0.005, 0.022), (0.60, 0.035, 0.055), (-0.50, 0.20, 0.065)],
]


@dataclass
class MockBeat:
    t: float  # seconds since epoch
    is_pvc: bool


@dataclass
class MockRecord:
    t: np.ndarray
    mv: np.ndarray
    beats: list[MockBeat]
    fs: float = FS

    @property
    def pvc_times(self) -> np.ndarray:
        return np.array([b.t for b in self.beats if b.is_pvc])

    @property
    def beat_times(self) -> np.ndarray:
        return np.array([b.t for b in self.beats])


def _add_waves(mv, t, r_time, waves, half_window_s=0.7):
    # render each beat only into a local window; the Gaussians are
    # negligible far from the beat, and touching the whole signal per beat
    # makes long (multi-hour) sessions quadratic and unusably slow
    n = mv.size
    i0 = max(0, int((r_time - half_window_s) * FS))
    i1 = min(n, int((r_time + half_window_s) * FS) + 1)
    if i1 <= i0:
        return
    tw = t[i0:i1]
    contrib = np.zeros(i1 - i0)
    for amp, center, sigma in waves:
        contrib += amp * np.exp(-((tw - (r_time + center)) ** 2) / (2.0 * sigma ** 2))
    mv[i0:i1] += contrib


def generate(
    duration_s: float = 600.0,
    mean_hr: float = 65.0,
    pvc_burden: float = 0.08,
    seed: int = 0,
    noise_mv: float = 0.025,
    baseline_wander_mv: float = 0.05,
) -> MockRecord:
    """Generate a mock ECG.

    pvc_burden is the approximate fraction of beats made PVCs. PVCs are not
    placed back-to-back, and never on the first or last beat.
    """
    rng = np.random.default_rng(seed)
    n_samples = int(round(duration_s * FS))
    t = np.arange(n_samples) / FS
    mv = np.zeros(n_samples)

    mean_rr = 60.0 / mean_hr

    # underlying sinus schedule with gentle HRV (respiratory sinus arrhythmia)
    beat_times = []
    cur = 1.0
    k = 0
    while cur < duration_s - 1.0:
        beat_times.append(cur)
        hrv = 1.0 + 0.06 * np.sin(2 * np.pi * 0.25 * cur) + rng.normal(0, 0.02)
        cur += mean_rr * max(0.5, hrv)
        k += 1
    beat_times = np.array(beat_times)

    # choose PVC beats (not first/last, not adjacent)
    n = beat_times.size
    is_pvc = np.zeros(n, dtype=bool)
    n_pvc_target = int(round(pvc_burden * n))
    candidates = list(range(2, n - 2))
    rng.shuffle(candidates)
    chosen = []
    for c in candidates:
        if len(chosen) >= n_pvc_target:
            break
        if any(abs(c - x) <= 1 for x in chosen):
            continue
        chosen.append(c)
    for c in chosen:
        is_pvc[c] = True

    beats: list[MockBeat] = []
    for i in range(n):
        if is_pvc[i]:
            # premature: pull this beat earlier toward the previous beat;
            # the next sinus beat stays on schedule -> compensatory pause
            prev_t = beat_times[i - 1]
            sched_t = beat_times[i]
            coupling = rng.uniform(0.60, 0.78)
            r_time = prev_t + coupling * (sched_t - prev_t)
            morph = _PVC_MORPHOLOGIES[rng.integers(len(_PVC_MORPHOLOGIES))]
            _add_waves(mv, t, r_time, morph)
            beats.append(MockBeat(t=r_time, is_pvc=True))
        else:
            r_time = beat_times[i]
            # small beat-to-beat amplitude variation
            scale = 1.0 + rng.normal(0, 0.05)
            waves = [(a * scale, c, s) for (a, c, s) in _NORMAL_WAVES]
            _add_waves(mv, t, r_time, waves)
            beats.append(MockBeat(t=r_time, is_pvc=False))

    # baseline wander + broadband noise
    mv += baseline_wander_mv * np.sin(2 * np.pi * 0.15 * t + rng.uniform(0, 6.28))
    mv += rng.normal(0, noise_mv, size=n_samples)

    # shift everything onto the same epoch timebase as the signal, so the
    # ground-truth beat times line up with the loaded ECG timestamps
    offset = _EPOCH_START_NS / 1e9
    t_epoch = t + offset
    for b in beats:
        b.t += offset
    return MockRecord(t=t_epoch, mv=mv, beats=beats)


def write_ecg_csv(record: MockRecord, path: str) -> None:
    """Write in the app's ecg_*.csv format (metadata line, ns, integer uV)."""
    t_ns = np.round(record.t * 1e9).astype(np.int64)
    uv = np.round(record.mv * 1000.0).astype(np.int64)
    with open(path, "w") as fh:
        fh.write(
            "# device=MOCK app_version=mock sample_rate_hz=130 ecg_units=uV\n"
        )
        fh.write("time,ecg_uV\n")
        for ts, v in zip(t_ns, uv):
            fh.write(f"{ts},{v}\n")


def write_truth_csv(record: MockRecord, path: str) -> None:
    with open(path, "w") as fh:
        fh.write("time_s,is_pvc\n")
        for b in record.beats:
            fh.write(f"{b.t:.6f},{int(b.is_pvc)}\n")


# ---------------------------------------------------------------------------
# Richer session generator for the exploration report demo.
#
# Models a recording with time-varying heart rate (rest / activity /
# exercise blocks) and an HR-dependent PVC burden, plus a tendency to fall
# into sustained bigeminy/trigeminy runs at higher rates. This produces the
# structure the report is meant to surface (exercise association,
# time-of-day pattern, day-to-day variability, rhythm clustering) so the
# tooling can be demonstrated before real data exists. It is NOT used for
# classifier validation — generate() is.
# ---------------------------------------------------------------------------


def _pvc_prob(hr: float, base: float, peak: float, midpoint: float = 80.0,
              scale: float = 8.0) -> float:
    """Logistic rise in isolated-PVC probability with heart rate."""
    return base + (peak - base) / (1.0 + np.exp(-(hr - midpoint) / scale))


def generate_session(
    blocks: list[tuple[float, float]],
    start_epoch_s: float,
    seed: int = 0,
    base_burden: float = 0.04,
    peak_burden: float = 0.30,
    noise_mv: float = 0.03,
    baseline_wander_mv: float = 0.06,
) -> MockRecord:
    """Generate one continuous recording from (duration_s, target_hr) blocks.

    PVC probability rises with HR; at higher rates the rhythm intermittently
    locks into bigeminy (N P N P) or trigeminy (N N P) runs. PVCs are
    premature with a compensatory pause, and use the same aberrant
    morphologies as generate(), so the real detect/classify pipeline labels
    them.
    """
    rng = np.random.default_rng(seed)

    # build a per-beat schedule first (times relative to 0), then render
    total_s = sum(d for d, _ in blocks)
    n_samples = int(round(total_s * FS))
    t = np.arange(n_samples) / FS
    mv = np.zeros(n_samples)

    beats: list[MockBeat] = []
    # `cur` is the sinus-schedule time of the current slot. A PVC replaces
    # the beat in its slot, placed early; the sinus node is not reset, so the
    # next slot stays on schedule and the PVC gets a compensatory pause for
    # free. The schedule always advances by one rr per slot.
    cur = 1.0
    block_idx = 0
    block_elapsed = 0.0
    state = "calm"        # "calm", "bigeminy", "trigeminy"
    state_left = 0
    phase = 0             # position within a bi-/trigeminy cycle

    def hr_at(elapsed_into_block, dur, target):
        # ease in/out of each block so HR transitions are smooth
        ramp = min(1.0, elapsed_into_block / 20.0, (dur - elapsed_into_block) / 20.0)
        ramp = max(0.2, ramp)
        rsa = 1.0 + 0.04 * np.sin(2 * np.pi * 0.25 * cur)
        return target * (0.85 + 0.15 * ramp) * rsa + rng.normal(0, 1.5)

    while cur < total_s - 1.0 and block_idx < len(blocks):
        dur, target_hr = blocks[block_idx]
        hr = max(40.0, hr_at(block_elapsed, dur, target_hr))
        rr = 60.0 / hr

        # decide rhythm state transitions (more irritable at higher HR)
        if state_left <= 0:
            p_big = _pvc_prob(hr, 0.0, 0.05)
            p_tri = _pvc_prob(hr, 0.0, 0.03)
            roll = rng.random()
            if roll < p_big:
                state, state_left, phase = "bigeminy", int(rng.integers(20, 60)), 0
            elif roll < p_big + p_tri:
                state, state_left, phase = "trigeminy", int(rng.integers(20, 60)), 0
            else:
                state, state_left = "calm", int(rng.integers(10, 40))

        # is the beat in THIS slot a PVC?
        if state == "bigeminy":
            is_pvc = (phase % 2 == 1)
            phase += 1
        elif state == "trigeminy":
            is_pvc = (phase % 3 == 2)
            phase += 1
        else:
            is_pvc = rng.random() < _pvc_prob(hr, base_burden, peak_burden)
        state_left -= 1

        if not beats:
            is_pvc = False  # never start on a PVC

        if is_pvc:
            # premature: fire early within this slot. Previous beat is ~rr
            # behind and the next sinus beat is at cur+rr, so rr_before and
            # rr_after sum to ~2*rr (full compensatory pause).
            coupling = rng.uniform(0.55, 0.78)
            r_time = cur - (1.0 - coupling) * rr
            morph = _PVC_MORPHOLOGIES[rng.integers(len(_PVC_MORPHOLOGIES))]
            _add_waves(mv, t, r_time, morph)
            beats.append(MockBeat(t=r_time + start_epoch_s, is_pvc=True))
        else:
            scale = 1.0 + rng.normal(0, 0.05)
            waves = [(a * scale, c, s) for (a, c, s) in _NORMAL_WAVES]
            _add_waves(mv, t, cur, waves)
            beats.append(MockBeat(t=cur + start_epoch_s, is_pvc=False))

        cur += rr
        block_elapsed += rr
        if block_elapsed >= dur:
            block_idx += 1
            block_elapsed = 0.0

    mv += baseline_wander_mv * np.sin(2 * np.pi * 0.15 * t + rng.uniform(0, 6.28))
    mv += rng.normal(0, noise_mv, size=n_samples)

    t_epoch = t + start_epoch_s
    return MockRecord(t=t_epoch, mv=mv, beats=beats)


def diurnal_blocks(rng) -> list[tuple[float, float]]:
    """A full waking day of (duration_s, target_hr) activity blocks: rest,
    activity, a couple of exercise bouts, evening wind-down, with random
    variation so days differ. ~15 h, so the time-of-day chart is populated."""
    M = 60.0
    jitter = lambda x, f=0.12: x * (1.0 + rng.normal(0, f))
    return [
        (jitter(90 * M), jitter(60)),    # morning rest
        (jitter(60 * M), jitter(80)),    # breakfast / light activity
        (jitter(35 * M), jitter(128)),   # morning exercise bout
        (jitter(75 * M), jitter(72)),    # recovery / desk work
        (jitter(60 * M), jitter(85)),    # midday activity
        (jitter(90 * M), jitter(70)),    # afternoon rest
        (jitter(80 * M), jitter(90)),    # afternoon activity
        (jitter(25 * M), jitter(138)),   # late-day exercise bout
        (jitter(120 * M), jitter(74)),   # evening activity
        (jitter(150 * M), jitter(63)),   # evening wind-down / rest
    ]
