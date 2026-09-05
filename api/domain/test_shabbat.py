"""
Tests for domain/shabbat.py (SPEC.md rule 12).

Saturday and Friday are two different rules, kept apart deliberately:
Saturday excludes a ``closed_on_shabbat`` place outright; Friday is a
deadline on the visit, applied as a clamp in ``domain/schedule.py``'s
``_effective_window``, not as a filter on any place here.
"""

from __future__ import annotations

from api.domain import shabbat
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
        access=AccessType.GATED,
        lat=32.0,
        lng=34.9,
        duration_min=60,
        opening_hours={day: ("09:00", "18:00") for day in Weekday},
        hours_verified=True,
        closed_on_shabbat=False,
        kid_friendly=False,
        accessible=False,
        tags=[],
    )
    base.update(overrides)
    return Place(**base)


# --------------------------------------------------------------------------
# friday_deadline
# --------------------------------------------------------------------------


def test_friday_deadline_is_1500_for_an_observant_request():
    assert shabbat.friday_deadline(Weekday.FRI, shabbat_observant=True) == "15:00"


def test_friday_deadline_is_none_for_a_non_observant_request():
    assert shabbat.friday_deadline(Weekday.FRI, shabbat_observant=False) is None


def test_friday_deadline_defaults_to_observant():
    assert shabbat.friday_deadline(Weekday.FRI) == "15:00"


def test_friday_deadline_is_none_on_every_other_day_regardless_of_observance():
    for weekday in (Weekday.SUN, Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU, Weekday.SAT):
        assert shabbat.friday_deadline(weekday, shabbat_observant=True) is None
        assert shabbat.friday_deadline(weekday, shabbat_observant=False) is None


# --------------------------------------------------------------------------
# is_shabbat_eligible — Saturday only now; Friday never rejects a place here
# --------------------------------------------------------------------------


def test_closed_on_shabbat_place_is_rejected_on_saturday():
    place = _place(closed_on_shabbat=True)
    assert shabbat.is_shabbat_eligible(place, Weekday.SAT) is False


def test_closed_on_shabbat_place_is_accepted_on_thursday():
    place = _place(closed_on_shabbat=True)
    assert shabbat.is_shabbat_eligible(place, Weekday.THU) is True


def test_open_on_shabbat_place_is_accepted_on_saturday():
    place = _place(closed_on_shabbat=False)
    assert shabbat.is_shabbat_eligible(place, Weekday.SAT) is True


def test_closed_on_shabbat_place_is_never_rejected_by_is_shabbat_eligible_on_friday():
    """
    Friday's rule is a deadline (schedule._effective_window), not a place-level
    exclusion — closed_on_shabbat is a Saturday-only flag and must not leak
    into Friday eligibility here, regardless of its value.
    """
    for flag in (True, False):
        place = _place(closed_on_shabbat=flag)
        assert shabbat.is_shabbat_eligible(place, Weekday.FRI) is True


# --------------------------------------------------------------------------
# shabbat_rejection_reason_he
# --------------------------------------------------------------------------


def test_rejection_reason_is_none_for_an_eligible_place():
    place = _place(closed_on_shabbat=False)
    assert shabbat.shabbat_rejection_reason_he(place, Weekday.SAT) is None
    assert shabbat.shabbat_rejection_reason_he(place, Weekday.FRI) is None


def test_rejection_reason_on_saturday_is_the_shabbat_message():
    place = _place(closed_on_shabbat=True)
    assert shabbat.shabbat_rejection_reason_he(place, Weekday.SAT) == "סגור בשבת"


def test_rejection_reason_on_friday_when_the_place_opens_after_the_deadline():
    """
    A place open 16:00-20:00 on Friday has no window left once the 15:00
    deadline clamps it — the message names that, not a false "closes late".
    """
    place = _place(opening_hours={**{day: None for day in Weekday}, Weekday.FRI: ("16:00", "20:00")})
    assert (
        shabbat.shabbat_rejection_reason_he(place, Weekday.FRI, shabbat_observant=True)
        == "לא ניתן לסיים את הביקור לפני 15:00"
    )


def test_rejection_reason_on_friday_is_none_when_visitable_before_the_deadline():
    place = _place(opening_hours={**{day: None for day in Weekday}, Weekday.FRI: ("09:00", "18:00")})
    assert shabbat.shabbat_rejection_reason_he(place, Weekday.FRI, shabbat_observant=True) is None


def test_rejection_reason_on_friday_is_none_when_not_observant_even_if_late_opening():
    """Without the deadline, a place opening at 16:00 on an ordinary Friday is fine."""
    place = _place(opening_hours={**{day: None for day in Weekday}, Weekday.FRI: ("16:00", "20:00")})
    assert shabbat.shabbat_rejection_reason_he(place, Weekday.FRI, shabbat_observant=False) is None
