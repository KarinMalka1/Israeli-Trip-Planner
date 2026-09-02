"""Tests for clamp_overnight_hours.py — pure functions, no disk or network."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.models import Place
from api.scripts import clamp_overnight_hours as clamp

_ALL_CLOSED = {day: None for day in ("sun", "mon", "tue", "wed", "thu", "fri", "sat")}


def _place_row(**hours_overrides) -> dict:
    return {
        "id": "south-test",
        "name_he": "מקום בדיקה",
        "description_he": "",
        "tip_he": "",
        "description_source": "generated",
        "category": "meal",
        "region": "south",
        "access": "gated",
        "duration_min": 60,
        "opening_hours": {**_ALL_CLOSED, **hours_overrides},
        "hours_verified": True,
        "closed_on_shabbat": False,
        "kid_friendly": False,
        "accessible": False,
    }


def test_should_clamp_true_when_close_is_an_early_morning_hour():
    assert clamp.should_clamp(["12:00", "01:00"]) is True


def test_should_clamp_false_when_close_after_open():
    assert clamp.should_clamp(["08:00", "17:00"]) is False


def test_should_clamp_false_for_none():
    assert clamp.should_clamp(None) is False


def test_should_clamp_false_when_reversed_but_not_overnight():
    """
    12:00-11:00 is not "open past midnight" — 11:00 is well into the next
    business day, not the small hours. This is a same-day window entered
    backwards, a different bug the script must not paper over (module
    docstring) — it should be left alone and keep failing Place's validator.
    """
    assert clamp.should_clamp(["12:00", "11:00"]) is False


def test_clamp_window_sets_close_to_2359():
    assert clamp.clamp_window(["12:00", "01:00"]) == ["12:00", "23:59"]


def test_clamp_window_returns_unchanged_when_not_eligible():
    assert clamp.clamp_window(["08:00", "17:00"]) == ["08:00", "17:00"]


def test_overnight_window_is_clamped_and_then_passes_place_validation():
    """SPEC.md section 14: a 12:00-01:00 input becomes 12:00-23:59."""
    row = _place_row(mon=["12:00", "01:00"])
    updated, clamped = clamp.clamp_row(row)

    assert updated["opening_hours"]["mon"] == ["12:00", "23:59"]
    assert len(clamped) == 1
    Place.model_validate(updated)  # does not raise


def test_reversed_same_day_window_is_left_untouched_and_still_fails_validation():
    """A 12:00-11:00 input is genuinely malformed, not overnight, and still fails."""
    row = _place_row(mon=["12:00", "11:00"])
    updated, clamped = clamp.clamp_row(row)

    assert updated == row
    assert clamped == []
    with pytest.raises(ValidationError):
        Place.model_validate(updated)


def test_clamp_row_clamps_overnight_days_and_reports_them():
    row = {
        "id": "south-goda",
        "name_he": "GODA",
        "opening_hours": {
            "sun": None,
            "mon": ["12:00", "01:00"],
            "fri": ["08:00", "16:00"],
        },
    }
    updated, clamped = clamp.clamp_row(row)

    assert updated["opening_hours"]["mon"] == ["12:00", "23:59"]
    assert updated["opening_hours"]["fri"] == ["08:00", "16:00"]
    assert updated["opening_hours"]["sun"] is None
    assert [(w.day, w.original) for w in clamped] == [("mon", ["12:00", "01:00"])]


def test_clamp_row_does_not_mutate_original():
    row = {"id": "x", "name_he": "n", "opening_hours": {"mon": ["12:00", "01:00"]}}
    clamp.clamp_row(row)
    assert row["opening_hours"]["mon"] == ["12:00", "01:00"]


def test_clamp_row_returns_unchanged_when_nothing_to_clamp():
    row = {"id": "x", "name_he": "n", "opening_hours": {"mon": ["08:00", "17:00"]}}
    updated, clamped = clamp.clamp_row(row)
    assert updated == row
    assert clamped == []


def test_clamp_file_clamps_across_rows():
    raw = {
        "places": [
            {"id": "a", "name_he": "א", "opening_hours": {"mon": ["12:00", "01:00"]}},
            {"id": "b", "name_he": "ב", "opening_hours": {"mon": ["08:00", "17:00"]}},
        ]
    }
    updated_raw, clamped = clamp.clamp_file(raw)
    assert updated_raw["places"][0]["opening_hours"]["mon"] == ["12:00", "23:59"]
    assert updated_raw["places"][1]["opening_hours"]["mon"] == ["08:00", "17:00"]
    assert len(clamped) == 1
    assert clamped[0].place_id == "a"
