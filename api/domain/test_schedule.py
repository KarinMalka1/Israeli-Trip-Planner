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


# --------------------------------------------------------------------------
# SPEC section 10: a gated place with unverified hours must never be
# scheduled — an unverified claim is worse than no place at all.
# --------------------------------------------------------------------------

# Closes by 15:00 every day so Friday's early-close rule (rule 12) never
# interferes — these tests isolate hours_verified, not shabbat eligibility.
_ALWAYS_OPEN_UNTIL_1400 = {day: ("09:00", "14:00") for day in Weekday}


def test_gated_place_with_unverified_hours_is_excluded_every_weekday():
    place = _place(
        access=AccessType.GATED,
        hours_verified=False,
        opening_hours=_ALWAYS_OPEN_UNTIL_1400,
    )
    for weekday in Weekday:
        assert schedule.is_available_on(place, weekday, month=6) is False


def test_gated_place_with_verified_hours_is_included_every_weekday():
    place = _place(
        access=AccessType.GATED,
        hours_verified=True,
        opening_hours=_ALWAYS_OPEN_UNTIL_1400,
    )
    for weekday in Weekday:
        assert schedule.is_available_on(place, weekday, month=6) is True


def test_open_access_place_is_unaffected_by_hours_verified():
    """An open place has no gate and no hours to verify — hours_verified is moot for it."""
    for verified in (True, False):
        place = _place(access=AccessType.OPEN, hours_verified=verified)
        assert schedule.is_available_on(place, Weekday.TUE, month=6) is True


# --------------------------------------------------------------------------
# Friday is a deadline on the visit, not a filter on closing time (bug fix).
# --------------------------------------------------------------------------


def _fri_hours(opens: str, closes: str) -> dict:
    """All-null opening_hours except Friday, for isolating the Friday deadline."""
    return {**{day: None for day in Weekday}, Weekday.FRI: (opens, closes)}


def test_open_access_place_is_schedulable_on_friday():
    """
    The regression test for the bug: an access=="open" place has seven null
    opening hours by design (SPEC section 10) and must not be read as
    "closed on Friday" the way the old code did.
    """
    place = _place(access=AccessType.OPEN)
    assert schedule.is_available_on(place, Weekday.FRI, month=6) is True


def test_gated_place_open_friday_morning_to_evening_is_schedulable_early_not_late():
    """
    Fri 09:00-18:00, duration 120min: schedulable at 09:00 (ends 11:00, well
    before the 15:00 deadline) but not at 14:00 (would end at 16:00).
    """
    place = _place(
        access=AccessType.GATED,
        hours_verified=True,
        opening_hours=_fri_hours("09:00", "18:00"),
        duration_min=120,
    )
    assert schedule.visit_fits_opening_hours(place, Weekday.FRI, "09:00") is True
    assert schedule.visit_fits_opening_hours(place, Weekday.FRI, "14:00") is False


def test_gated_place_open_only_after_the_friday_deadline_is_not_schedulable_at_all():
    """Fri 16:00-20:00 opens after the 15:00 deadline — no visit can ever fit."""
    place = _place(
        access=AccessType.GATED,
        hours_verified=True,
        opening_hours=_fri_hours("16:00", "20:00"),
        duration_min=30,
    )
    assert schedule.is_available_on(place, Weekday.FRI, month=6) is False
    assert schedule._effective_window(place, Weekday.FRI) is None


def test_validate_day_reports_rule_12_violation_when_friday_day_ends_after_1500():
    place = _place(
        access=AccessType.GATED,
        hours_verified=True,
        opening_hours=_fri_hours("09:00", "20:00"),
        duration_min=390,  # 09:00 + 6.5h = 15:30
    )
    day = schedule.build_day([place], [0], starts_at="09:00")

    problems = schedule.validate_day(
        day, region=Region.CENTRAL, weekday=Weekday.FRI, max_leg_min=90, known_place_ids={place.id}
    )

    assert any(p.startswith("rule 12") for p in problems)


def test_validate_day_reports_no_rule_12_violation_when_friday_day_ends_by_1500():
    place = _place(
        access=AccessType.GATED,
        hours_verified=True,
        opening_hours=_fri_hours("09:00", "20:00"),
        duration_min=345,  # 09:00 + 5.75h = 14:45
    )
    day = schedule.build_day([place], [0], starts_at="09:00")

    problems = schedule.validate_day(
        day, region=Region.CENTRAL, weekday=Weekday.FRI, max_leg_min=90, known_place_ids={place.id}
    )

    assert not any(p.startswith("rule 12") for p in problems)


def test_saturday_behaviour_is_unchanged_by_the_friday_fix():
    place = _place(
        access=AccessType.GATED,
        hours_verified=True,
        opening_hours={day: ("09:00", "18:00") for day in Weekday},
        closed_on_shabbat=True,
    )
    assert schedule.is_available_on(place, Weekday.SAT, month=6) is False
    assert schedule.is_available_on(place, Weekday.THU, month=6) is True


def test_effective_window_is_unclamped_sunday_through_thursday():
    """No deadline applies outside Friday, so the window is exactly the raw hours."""
    place = _place(
        access=AccessType.GATED,
        hours_verified=True,
        opening_hours={day: ("09:00", "20:00") for day in Weekday},
    )
    for weekday in (Weekday.SUN, Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU):
        assert schedule._effective_window(place, weekday) == ("09:00", "20:00")
