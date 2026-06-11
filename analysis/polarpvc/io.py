"""Loading the app's ECG CSV format.

Files written by WriteData.kt (2026-06-11 onward):
  # device=... app_version=... sample_rate_hz=130 ecg_units=uV
  time,ecg_uV
  <epoch_nanoseconds>,<integer_microvolts>
  ...

`time` is nanoseconds since the Unix epoch (the app adds Polar's
2000-epoch offset back on). Samples are nominally 130 Hz but the stream
has gaps wherever the strap disconnected, so the record is exposed as a
set of contiguous segments rather than one uniform array.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# A jump larger than this many sample intervals starts a new segment.
_GAP_FACTOR = 3.0
DEFAULT_SAMPLE_RATE_HZ = 130.0


@dataclass
class Segment:
    """A gap-free run of samples."""

    t: np.ndarray  # seconds since epoch, float
    mv: np.ndarray  # millivolts, float
    start_index: int  # index of first sample within the parent record


@dataclass
class EcgRecord:
    t: np.ndarray  # seconds since epoch (whole file, may contain gaps)
    mv: np.ndarray  # millivolts
    fs: float  # nominal sample rate (Hz)
    metadata: dict = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)

    @property
    def n_samples(self) -> int:
        return int(self.t.size)

    @property
    def duration_s(self) -> float:
        return float(self.t[-1] - self.t[0]) if self.t.size else 0.0


def _parse_metadata(line: str) -> dict:
    meta = {}
    for token in line.lstrip("#").strip().split():
        if "=" in token:
            key, value = token.split("=", 1)
            meta[key] = value
    return meta


def split_segments(t: np.ndarray, mv: np.ndarray, fs: float) -> list[Segment]:
    """Break the signal wherever the sample spacing jumps (a dropout)."""
    if t.size == 0:
        return []
    dt = np.diff(t)
    gap_threshold = _GAP_FACTOR / fs
    # indices after which a gap occurs -> segment boundaries
    boundaries = np.flatnonzero(dt > gap_threshold) + 1
    starts = np.concatenate(([0], boundaries))
    ends = np.concatenate((boundaries, [t.size]))
    segments = []
    for s, e in zip(starts, ends):
        if e - s >= 2:  # a lone sample isn't analysable
            segments.append(Segment(t=t[s:e], mv=mv[s:e], start_index=int(s)))
    return segments


def load_ecg_csv(path: str) -> EcgRecord:
    """Load an ecg_*.csv into an EcgRecord (microvolts -> millivolts)."""
    metadata: dict = {}
    with open(path, "r") as fh:
        first = fh.readline()
        if first.startswith("#"):
            metadata = _parse_metadata(first)
            header_line = fh.readline()
        else:
            header_line = first  # legacy: no metadata line

    units = metadata.get("ecg_units", "uV")
    fs = float(metadata.get("sample_rate_hz", DEFAULT_SAMPLE_RATE_HZ))

    # the header line we consumed above is "time,ecg_uV" (or legacy "time,ecg")
    header = header_line.strip().split(",")
    skip = 2 if metadata else 1

    raw = np.loadtxt(path, delimiter=",", skiprows=skip, comments="#")
    if raw.ndim == 1:
        raw = raw.reshape(1, -1)

    t_ns = raw[:, 0]
    volt = raw[:, 1]

    t = t_ns / 1e9  # ns -> s
    # normalise to millivolts regardless of stored units
    is_microvolt = "uV" in units or (len(header) > 1 and "uV" in header[1])
    mv = volt / 1000.0 if is_microvolt else volt.astype(float)

    record = EcgRecord(t=t, mv=mv, fs=fs, metadata=metadata)
    record.segments = split_segments(t, mv, fs)
    return record
