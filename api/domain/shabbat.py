"""
Shabbat rules (SPEC.md rule 12).

Two distinct rules, easy to confuse and deliberately kept apart here:

  * Saturday — a place flagged ``closed_on_shabbat`` is never scheduled at
    all, regardless of what its ``opening_hours["sat"]`` happens to say. The
    flag is a fact about the place and applies to every user; it is never
    conditional on ``shabbat_observant``.
  * Friday   — not an eligibility question about any one place, but a
    deadline on the *visit*: an observant user must be done and home before
    Shabbat comes in. ``friday_deadline`` names that deadline; applying it to
    a place's hours is ``domain/schedule.py``'s ``_effective_window`` job, not
    this module's — a place open 09:00-18:00 on Friday is perfectly
    schedulable for a 09:00-11:00 visit, so Friday can never be answered with
    a plain yes/no about the place the way Saturday can.

The MVP has no calendar date and no geolocation, so real candle-lighting times
are out of scope. 15:00 is a fixed, deliberately conservative stand-in.
"""

from __future__ import annotations

from api.models import Place, Weekday

# Latest permitted end-of-visit time on a Friday, for a shabbat-observant
# request. A flat constant, not a computed candle-lighting time: without a
# date and a city there is nothing to compute.
FRIDAY_LATEST_CLOSE = "15:00"


def is_shabbat(weekday: Weekday) -> bool:
    """True on Saturday, the day the ``closed_on_shabbat`` flag applies to."""
    return weekday == Weekday.SAT


def is_erev_shabbat(weekday: Weekday) -> bool:
    """True on Friday, the day the deadline in ``friday_deadline`` applies to."""
    return weekday == Weekday.FRI


def friday_deadline(weekday: Weekday, shabbat_observant: bool = True) -> str | None:
    """
    Latest a visit may end on this weekday, or ``None`` when no deadline applies.

    Only Friday, and only for an observant request — every other day/flag
    combination imposes nothing here (an unobservant Friday is an ordinary
    weekday; Saturday's rule is a place-level exclusion, handled entirely by
    ``is_shabbat_eligible`` instead of a deadline).
    """
    if is_erev_shabbat(weekday) and shabbat_observant:
        return FRIDAY_LATEST_CLOSE
    return None


def is_shabbat_eligible(place: Place, weekday: Weekday) -> bool:
    """
    Rule 12's Saturday half: a ``closed_on_shabbat`` place is never scheduled
    on Saturday.

    Unconditional and the same for every user — it is a fact about the place,
    not a preference of the person, so it takes no ``shabbat_observant``
    argument at all. Sunday through Friday impose nothing here; Friday's
    deadline lives in ``friday_deadline`` instead, since it clamps a visit's
    end time rather than excluding a place outright.
    """
    if is_shabbat(weekday):
        return not place.closed_on_shabbat
    return True


def shabbat_rejection_reason_he(
    place: Place, weekday: Weekday, shabbat_observant: bool = True
) -> str | None:
    """
    Explain a rule-12 rejection in Hebrew, for the fallback screen's one-line note.

    Returns ``None`` when the place is eligible. Saturday is still a plain
    place-level exclusion. Friday no longer rejects a place for closing
    late — a place that closes well after 15:00 can still be visited and
    left before the deadline — so the Friday message now names the actual
    failure: the deadline leaves the place's window empty (via
    ``domain/schedule.py``'s ``_effective_window``), most often because it
    opens too late in the day to finish before 15:00 at all.
    """
    if not is_shabbat_eligible(place, weekday):
        return "סגור בשבת"

    if is_erev_shabbat(weekday):
        from api.domain import schedule  # local import: schedule.py imports this module

        if schedule._effective_window(place, weekday, shabbat_observant) is None:
            return "לא ניתן לסיים את הביקור לפני 15:00"

    return None
