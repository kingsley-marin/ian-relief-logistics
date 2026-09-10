"""Road-closure time-window logic. Pure python so it can be unit-tested without PostGIS.

The SQL twin of `is_active` is the `closures_active_at(t)` function in sql/schema.sql — keep them in
agreement (test_closures.py checks the python side; the db-marked test checks SQL gives the same answer).

Semantics
    A closure is active at instant t when   start_utc <= t < end_utc
    An open-ended closure (end_utc is None) is active for every t >= start_utc.
    Boundaries: the start instant IS closed; the end instant is NOT (half-open interval). This avoids a
    road being both closed and open at the same second when one closure ends as another begins.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable


def _utc(t: datetime) -> datetime:
    if t.tzinfo is None:
        raise ValueError("naive datetime passed to closures logic; all times must be timezone-aware UTC")
    return t.astimezone(timezone.utc)


def is_active(closure: dict, t: datetime) -> bool:
    t = _utc(t)
    start = _utc(closure["start_utc"])
    end = closure.get("end_utc")
    if t < start:
        return False
    return end is None or t < _utc(end)


def active_at(closures: Iterable[dict], t: datetime) -> list[dict]:
    return [c for c in closures if is_active(c, t)]


def overlaps_window(closure: dict, window_start: datetime, window_end: datetime) -> bool:
    """True if the closure is active at any instant in [window_start, window_end).

    Useful for a planning horizon: "will this road be closed at any point during my 12-hour shift?"
    """
    ws, we = _utc(window_start), _utc(window_end)
    if we <= ws:
        raise ValueError("window_end must be after window_start")
    start = _utc(closure["start_utc"])
    end = closure.get("end_utc")
    if start >= we:
        return False
    return end is None or _utc(end) > ws


def next_change(closures: Iterable[dict], t: datetime) -> datetime | None:
    """The next instant after t when some closure starts or ends (i.e. when a plan should be re-run)."""
    t = _utc(t)
    candidates = []
    for c in closures:
        for k in ("start_utc", "end_utc"):
            v = c.get(k)
            if v is not None and _utc(v) > t:
                candidates.append(_utc(v))
    return min(candidates) if candidates else None
