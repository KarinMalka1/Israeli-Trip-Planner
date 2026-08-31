"""
Shabbat eligibility (SPEC.md rule 12).

Two distinct rules, often confused:

  * Saturday — a place flagged ``closed_on_shabbat`` is never scheduled at all,
    regardless of what its ``opening_hours["sat"]`` happens to say. The flag is
    the stronger statement and wins.
  * Friday  — every place must close before 15:00 in its own hours, so the day
    is over before Shabbat comes in. This applies to every place, not only the
    shabbat-observant ones.

The MVP has no calendar date and no geolocation, so real candle-lighting times
are out of scope. 15:00 is a fixed, deliberately conservative stand-in.
"""

from __future__ import annotations

from api.models import Place, Weekday

# Latest permitted closing time on a Friday. A flat constant, not a computed
# candle-lighting time: without a date and a city there is nothing to compute.
FRIDAY_LATEST_CLOSE = "15:00"


def is_shabbat(weekday: Weekday) -> bool:
    """True on Saturday, the day the ``closed_on_shabbat`` flag applies to."""
    return weekday == Weekday.SAT


def is_erev_shabbat(weekday: Weekday) -> bool:
    """True on Friday, when the early-closing rule applies to every place."""
    return weekday == Weekday.FRI


def is_shabbat_eligible(place: Place, weekday: Weekday) -> bool:
    """
    Rule 12: may this place be scheduled at all on this weekday?

    Sunday through Thursday impose nothing, so those days always pass. Note
    this is a filter on eligibility only — whether the place is actually open
    at a given hour is rule 11's job, in ``schedule.is_open_at``.
    """
    if is_shabbat(weekday):
        return not place.closed_on_shabbat

    if is_erev_shabbat(weekday):
        friday_hours = place.opening_hours.get(Weekday.FRI)
        if friday_hours is None:
            # Closed on Friday anyway; rule 11 would reject it regardless.
            return False
        _, closes_at = friday_hours
        # Zero-padded "HH:MM" compares correctly as a string.
        return closes_at <= FRIDAY_LATEST_CLOSE

    return True


def shabbat_rejection_reason_he(place: Place, weekday: Weekday) -> str | None:
    """
    Explain a rule-12 rejection in Hebrew, for the fallback screen's one-line note.

    Returns ``None`` when the place is eligible, so the caller can use it both
    as a predicate and as a message source.
    """
    if is_shabbat_eligible(place, weekday):
        return None
    if is_shabbat(weekday):
        return "סגור בשבת"
    return f"נסגר אחרי {FRIDAY_LATEST_CLOSE} בערב שבת"
