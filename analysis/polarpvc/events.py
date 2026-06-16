"""Parse session event logs (events_*.csv[.gz]) for the report.

Right now this extracts the wearer's activity tags (event == "activity",
logged from the app's Tag button) with their start times, so the report can
break PVC burden down by what the wearer was doing. Only the start of each
activity is logged; the end is inferred as the next tag or a break in
recording coverage (see windows.burden_by_activity).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone

from .io import _open_text


@dataclass
class ActivityTag:
    t_start: float  # epoch seconds
    label: str


def _parse_iso(s: str) -> float:
    """Parse the event-log timestamp (java Instant, e.g.
    2026-06-15T15:51:39.850596Z) to epoch seconds, tolerating a trailing Z and
    a variable number of fractional digits."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1]
    if "." in s:
        head, frac = s.split(".", 1)
        frac = (frac + "000000")[:6]  # pad/truncate to microseconds
        dt = datetime.strptime(head + "." + frac, "%Y-%m-%dT%H:%M:%S.%f")
    else:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    return dt.replace(tzinfo=timezone.utc).timestamp()


def load_activity_tags(paths: list[str]) -> list[ActivityTag]:
    """Read activity tags from one or more events_*.csv[.gz] files."""
    tags: list[ActivityTag] = []
    for path in paths:
        try:
            with _open_text(path) as fh:
                for row in csv.DictReader(fh):
                    if (row.get("event") or "").strip() != "activity":
                        continue
                    label = (row.get("detail") or "").strip()
                    if label:
                        tags.append(ActivityTag(_parse_iso(row["time"]), label))
        except Exception:
            continue  # a malformed/partial event file shouldn't sink the report
    tags.sort(key=lambda x: x.t_start)
    return tags
