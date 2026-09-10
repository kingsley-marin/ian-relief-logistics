"""Closure time-window logic (pure python)."""
from __future__ import annotations

import pytest

from ianrelief import closures

from conftest import utc

SANIBEL = {"closure_id": "CL-SANIBEL-CSWY", "start_utc": utc("2022-09-28T20:00:00Z"), "end_utc": utc("2022-10-19T14:00:00Z")}
OPEN_ENDED = {"closure_id": "CL-X", "start_utc": utc("2022-09-28T20:00:00Z"), "end_utc": None}
MYAKKA = {"closure_id": "CL-I75-MYAKKA-RIVER", "start_utc": utc("2022-10-01T12:00:00Z"), "end_utc": utc("2022-10-02T20:00:00Z")}


def test_before_start_is_not_active():
    assert not closures.is_active(SANIBEL, utc("2022-09-28T19:59:59Z"))


def test_start_instant_is_active_half_open():
    assert closures.is_active(SANIBEL, utc("2022-09-28T20:00:00Z"))


def test_inside_window_is_active():
    assert closures.is_active(SANIBEL, utc("2022-10-05T00:00:00Z"))


def test_end_instant_is_not_active_half_open():
    assert not closures.is_active(SANIBEL, utc("2022-10-19T14:00:00Z"))
    assert closures.is_active(SANIBEL, utc("2022-10-19T13:59:59Z"))


def test_open_ended_closure_stays_active():
    assert closures.is_active(OPEN_ENDED, utc("2030-01-01T00:00:00Z"))
    assert not closures.is_active(OPEN_ENDED, utc("2022-09-01T00:00:00Z"))


def test_active_at_filters_list():
    at = utc("2022-10-01T15:00:00Z")
    ids = {c["closure_id"] for c in closures.active_at([SANIBEL, MYAKKA, OPEN_ENDED], at)}
    assert ids == {"CL-SANIBEL-CSWY", "CL-I75-MYAKKA-RIVER", "CL-X"}
    ids = {c["closure_id"] for c in closures.active_at([SANIBEL, MYAKKA], utc("2022-10-03T00:00:00Z"))}
    assert ids == {"CL-SANIBEL-CSWY"}


def test_naive_datetime_is_refused():
    from datetime import datetime

    with pytest.raises(ValueError):
        closures.is_active(SANIBEL, datetime(2022, 10, 1, 12, 0, 0))


def test_overlaps_planning_window():
    # a 12h shift starting 2022-10-01 06:00Z overlaps the Myakka closure that starts at 12:00Z
    assert closures.overlaps_window(MYAKKA, utc("2022-10-01T06:00:00Z"), utc("2022-10-01T18:00:00Z"))
    # a shift that ends exactly when the closure starts does not overlap (half-open)
    assert not closures.overlaps_window(MYAKKA, utc("2022-10-01T00:00:00Z"), utc("2022-10-01T12:00:00Z"))
    # a shift that starts exactly when the closure ends does not overlap
    assert not closures.overlaps_window(MYAKKA, utc("2022-10-02T20:00:00Z"), utc("2022-10-03T08:00:00Z"))
    with pytest.raises(ValueError):
        closures.overlaps_window(MYAKKA, utc("2022-10-01T12:00:00Z"), utc("2022-10-01T12:00:00Z"))


def test_next_change_finds_next_boundary():
    assert closures.next_change([SANIBEL, MYAKKA], utc("2022-09-29T12:00:00Z")) == utc("2022-10-01T12:00:00Z")
    assert closures.next_change([SANIBEL, MYAKKA], utc("2022-10-01T13:00:00Z")) == utc("2022-10-02T20:00:00Z")
    assert closures.next_change([SANIBEL, MYAKKA], utc("2022-10-20T00:00:00Z")) is None


def test_synthetic_closures_match_narrative(inputs):
    by_id = {c["closure_id"]: c for c in inputs["closures"]}
    assert set(by_id) == {"CL-SANIBEL-CSWY", "CL-MATLACHA-PINE-ISLAND-RD", "CL-I75-MYAKKA-RIVER", "CL-CAPE-CORAL-BRIDGES-INSPECT"}
    day_after = utc("2022-09-29T12:00:00Z")
    active = {c["closure_id"] for c in closures.active_at(inputs["closures"], day_after)}
    assert "CL-SANIBEL-CSWY" in active and "CL-MATLACHA-PINE-ISLAND-RD" in active
    assert "CL-I75-MYAKKA-RIVER" not in active  # delayed inland flooding: starts Oct 1
    # every closure explains itself
    for c in inputs["closures"]:
        assert c["source_note"] and c["time_confidence"] in ("reported", "approximate", "assumed")
