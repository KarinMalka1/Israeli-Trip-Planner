"""
Tests for domain/schedule.py (SPEC.md rules 1-13), focused on the section 10
amendment's scheduler consequence: an access=="open" place has no gate, so
its opening_hours is seven nulls by design and must never be read as
"closed every day" — it is schedulable by daylight (07:00-18:00) instead.
"""

from __future__ import annotations

from api.domain import schedule
from api.models import AccessType, Category, DescriptionSource, Place, Region, Weekday


def _place(**overrides) -> Place:
    base: dict = dict(
        id="test-place",
        name_he="מקום בדיקה",
        description_he="",
        tip_he="",
        description_source=DescriptionSource.GENERATED,
        category=Category.NATURE,
        region=Region.CENTRAL,
        access=AccessType.OPEN,
        lat=32.0,
        lng=34.9,
        duration_min=60,
        opening_hours={day: None for day in Weekday},
        hours_verified=False,
        closed_on_shabbat=False,
        kid_friendly=False,
        accessible=False,
        tags=[],
    )
    base.update(overrides)
    return Place(**base)


# --------------------------------------------------------------------------
# The reported bug: access=="open" + all-null opening_hours must still be
# schedulable, on any ordinary weekday, within the daylight window.
# --------------------------------------------------------------------------


def test_open_access_place_with_null_hours_is_open_at_10am_tuesday():
    place = _place(access=AccessType.OPEN)
    assert schedule.is_open_at(place, Weekday.TUE, "10:00") is True


def test_open_access_place_visit_fits_opening_hours_at_10am_tuesday():
    place = _place(access=AccessType.OPEN, duration_min=90)
    assert schedule.visit_fits_opening_hours(place, Weekday.TUE, "10:00") is True


def test_open_access_place_is_available_on_tuesday():
    place = _place(access=AccessType.OPEN)
    assert schedule.is_available_on(place, Weekday.TUE, month=6) is True


def test_open_access_place_outside_daylight_window_is_not_open():
    """Daylight-clamped means clamped — not open before 07:00 or after 18:00."""
    place = _place(access=AccessType.OPEN)
    assert schedule.is_open_at(place, Weekday.TUE, "06:00") is False
    assert schedule.is_open_at(place, Weekday.TUE, "18:30") is False


def test_open_access_visit_must_finish_before_daylight_ends():
    """A 3-hour visit starting at 17:00 would run past 18:00 — must not fit."""
    place = _place(access=AccessType.OPEN, duration_min=180)
    assert schedule.visit_fits_opening_hours(place, Weekday.TUE, "17:00") is False


# --------------------------------------------------------------------------
# Gated places are unaffected: still keyed off opening_hours, as before.
# --------------------------------------------------------------------------


def test_gated_place_with_null_hours_is_still_closed():
    place = _place(access=AccessType.GATED, opening_hours={day: None for day in Weekday})
    assert schedule.is_open_at(place, Weekday.TUE, "10:00") is False
    assert schedule.is_available_on(place, Weekday.TUE, month=6) is False


def test_gated_place_with_real_hours_is_open_as_before():
    place = _place(
        access=AccessType.GATED,
        hours_verified=True,
        opening_hours={**{day: None for day in Weekday}, Weekday.TUE: ("09:00", "17:00")},
    )
    assert schedule.is_open_at(place, Weekday.TUE, "10:00") is True
    assert schedule.is_available_on(place, Weekday.TUE, month=6) is True


# --------------------------------------------------------------------------
# SPEC section 11 amendment: summer_only places excluded outside April-October.
# --------------------------------------------------------------------------


def test_summer_only_place_is_rejected_in_november():
    place = _place(access=AccessType.OPEN, season="summer_only")
    assert schedule.is_available_on(place, Weekday.TUE, month=11) is False


def test_summer_only_place_is_accepted_in_july():
    place = _place(access=AccessType.OPEN, season="summer_only")
    assert schedule.is_available_on(place, Weekday.TUE, month=7) is True


def test_year_round_place_is_unaffected_by_month():
    place = _place(access=AccessType.OPEN, season="year_round")
    assert schedule.is_available_on(place, Weekday.TUE, month=11) is True
    assert schedule.is_available_on(place, Weekday.TUE, month=7) is True
