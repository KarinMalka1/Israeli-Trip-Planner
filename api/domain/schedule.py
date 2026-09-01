"""
Time arithmetic and itinerary validation (SPEC.md rules 1-13).

This is the single module that does time math. The contract promises the
client never does any, so every arrival, every leg and every ``ends_at`` in
the system is produced by ``build_day`` here and checked by ``validate_itinerary``.

Everything is minutes-since-midnight internally and "HH:MM" strings at the
boundary. No dates, no timezones, no DST: a day trip starts at 09:00 local
and that is the whole model.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from api.domain import regions, shabbat
from api.models import AccessType, Category, Day, DayLength, Itinerary, Place, Region, Stop, Weekday

# Rule 8 (amended): the day starts at the first stop, at whatever start time
# the request chose (CreateItineraryRequest.starts_at) — there is no origin
# and no travel leg before it either way. DAY_START is only the default used
# where no caller-supplied start time is in scope (e.g. rebuild_day after an
# edit, which has no request of its own).
DAY_START = "09:00"

# SPEC section 10 amendment: an access=="open" place has no gate, so
# opening_hours is always seven nulls for it by design (not "closed every
# day" — there is simply no gate for a weekly schedule to describe). Such a
# place is scheduled by daylight instead, clamped to this fixed window.
DAYLIGHT_START = "07:00"
DAYLIGHT_END = "18:00"

# Rule 2: fewer than three stops is not a day out; more than six is more than
# a person will actually read on a phone.
MIN_STOPS = 3
MAX_STOPS = 6

# Rule 10: total elapsed time, in minutes. The hard bound — never relaxed.
MIN_DAY_MINUTES = 4 * 60
MAX_DAY_MINUTES = 9 * 60

# Rule 10 (amended): day_length narrows the search to a preferred band inside
# the hard bound above; it is never a substitute for it. Deliberately not
# contiguous — 5.5h to 6.5h is neither a short day nor a long one, so a chain
# that lands there is no more "preferred" under one label than the other.
DAY_LENGTH_BANDS: dict[DayLength, tuple[int, int]] = {
    "short": (240, 330),  # 4.0-5.5h
    "long": (390, 540),  # 6.5-9.0h
}

# Rule 4: the single meal stop must be arrived at inside this window.
MEAL_WINDOW_START = "12:00"
MEAL_WINDOW_END = "15:00"

# SPEC section 11 amendment: a season=="summer_only" place is only schedulable
# April through October, inclusive.
SUMMER_ONLY_MONTHS = frozenset(range(4, 11))


# --------------------------------------------------------------------------
# Time primitives
# --------------------------------------------------------------------------


def to_minutes(hhmm: str) -> int:
    """Parse "HH:MM" into minutes since midnight. The only place we parse a time."""
    hours, _, minutes = hhmm.partition(":")
    return int(hours) * 60 + int(minutes)


def to_hhmm(minutes: int) -> str:
    """
    Render minutes since midnight back to "HH:MM".

    Deliberately does not wrap past midnight: a day that computes to 25:00 is a
    bug we want to see in the validator, not one we want silently folded to 01:00.
    """
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def add_minutes(hhmm: str, delta: int) -> str:
    """Shift a wall-clock string by a signed number of minutes."""
    return to_hhmm(to_minutes(hhmm) + delta)


# --------------------------------------------------------------------------
# Opening hours
# --------------------------------------------------------------------------


def is_open_at(place: Place, weekday: Weekday, at: str) -> bool:
    """
    Rule 11 exactly as written: is this place open at this instant on this weekday?

    ``opening_hours[weekday] is None`` means closed that day for a ``gated``
    place, and a place closed that day is never scheduled. An ``open`` place
    (SPEC section 10 amendment) has no gate for ``opening_hours`` to describe
    at all — it is always seven nulls by design — so it is checked against
    the fixed daylight window instead, not read as permanently closed.
    """
    if place.access == AccessType.OPEN:
        return DAYLIGHT_START <= at < DAYLIGHT_END

    window = place.opening_hours.get(weekday)
    if window is None:
        return False
    opens, closes = window
    return to_minutes(opens) <= to_minutes(at) < to_minutes(closes)


def visit_fits_opening_hours(place: Place, weekday: Weekday, arrive_at: str) -> bool:
    """
    Stricter than rule 11: does the *whole* visit fit inside the opening window?

    Rule 11 only constrains ``arrive_at``, which would happily schedule a
    90-minute museum visit ten minutes before closing. The planner uses this
    tighter test when choosing stops; ``validate_itinerary`` still checks the
    literal rule, so a hand-edited itinerary is judged by the spec, not by this.

    ``open`` places use the same daylight window as ``is_open_at`` — the
    whole visit must finish by ``DAYLIGHT_END``, not merely start before it.
    """
    arrival = to_minutes(arrive_at)

    if place.access == AccessType.OPEN:
        return to_minutes(DAYLIGHT_START) <= arrival and arrival + place.duration_min <= to_minutes(
            DAYLIGHT_END
        )

    window = place.opening_hours.get(weekday)
    if window is None:
        return False
    opens, closes = window
    return to_minutes(opens) <= arrival and arrival + place.duration_min <= to_minutes(closes)


def is_available_on(place: Place, weekday: Weekday, month: int) -> bool:
    """
    Combined day-level filter: open at all that weekday, shabbat-eligible
    (rule 12), and in season (SPEC section 11 amendment).

    ``month`` is 1-12. A ``summer_only`` place is excluded outside
    April-October; a ``year_round`` place is unaffected by ``month``.
    """
    if place.season == "summer_only" and month not in SUMMER_ONLY_MONTHS:
        return False
    is_open_that_day = place.access == AccessType.OPEN or place.opening_hours.get(weekday) is not None
    return is_open_that_day and shabbat.is_shabbat_eligible(place, weekday)


# --------------------------------------------------------------------------
# Building a day
# --------------------------------------------------------------------------


def build_day(
    places: Sequence[Place],
    travel_legs: Sequence[int],
    starts_at: str = DAY_START,
) -> Day:
    """
    Lay a sequence of places onto a clock and produce the canonical ``Day``.

    ``travel_legs[i]`` is the drive into ``places[i]``; the caller passes 0 for
    the first entry because there is no leg before the first stop (rule 8).
    Arrival times chain with no slack, which is rule 9's equality as an
    assignment rather than a check:

        arrive[i+1] = arrive[i] + duration[i] + travel[i+1]

    ``ends_at`` is the moment the last visit finishes — this is the value the
    client renders and never computes.
    """
    if len(travel_legs) != len(places):
        raise ValueError("travel_legs must have exactly one entry per place")
    if places and travel_legs[0] != 0:
        raise ValueError("the first stop has no inbound leg; travel_legs[0] must be 0")

    stops: list[Stop] = []
    clock = to_minutes(starts_at)

    for index, place in enumerate(places):
        clock += travel_legs[index]
        stops.append(
            Stop(
                place_id=place.id,
                place=place,
                arrive_at=to_hhmm(clock),
                duration_min=place.duration_min,
                travel_min_from_prev=travel_legs[index],
            )
        )
        clock += place.duration_min

    return Day(stops=stops, starts_at=starts_at, ends_at=to_hhmm(clock))


def rebuild_day(day: Day, starts_at: str = DAY_START) -> Day:
    """
    Recompute every arrival and ``ends_at`` from a day's existing stops and legs.

    Used after a user edit: removing or swapping a stop invalidates every time
    downstream of it, and the contract says the server returns the fully
    recomputed itinerary rather than patching a few fields.
    """
    return build_day(
        places=[stop.place for stop in day.stops],
        travel_legs=[stop.travel_min_from_prev for stop in day.stops],
        starts_at=starts_at,
    )


def elapsed_minutes(day: Day) -> int:
    """Total length of the day, ``ends_at - starts_at``, as constrained by rule 10."""
    return to_minutes(day.ends_at) - to_minutes(day.starts_at)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate_day(
    day: Day,
    region: Region,
    weekday: Weekday,
    max_leg_min: int,
    known_place_ids: Iterable[str],
) -> list[str]:
    """
    Check one day against rules 1-12 and return every violation found.

    Returns a list rather than raising so the planner can log exactly why a
    candidate was rejected, and so tests read as an assertion on an empty list.
    Messages are English: they are internal diagnostics, never user-facing (rule 13).
    """
    problems: list[str] = []
    known = set(known_place_ids)
    stops = day.stops

    # -- Content ----------------------------------------------------------
    # Rule 1: no invented places.
    for stop in stops:
        if stop.place_id not in known:
            problems.append(f"rule 1: place_id {stop.place_id!r} is not in the seed dataset")
        if stop.place.id != stop.place_id:
            problems.append(f"rule 1: embedded place {stop.place.id!r} != place_id {stop.place_id!r}")

    # Rule 2: between 3 and 6 stops.
    if not MIN_STOPS <= len(stops) <= MAX_STOPS:
        problems.append(f"rule 2: {len(stops)} stops, expected {MIN_STOPS}-{MAX_STOPS}")

    # Rule 3: no repeats.
    seen: set[str] = set()
    for stop in stops:
        if stop.place_id in seen:
            problems.append(f"rule 3: {stop.place_id!r} appears more than once")
        seen.add(stop.place_id)

    # Rule 4: at most one meal, arrived at between 12:00 and 15:00.
    meals = [stop for stop in stops if stop.place.category == Category.MEAL]
    if len(meals) > 1:
        problems.append(f"rule 4: {len(meals)} meal stops, at most 1 allowed")
    for meal in meals:
        if not MEAL_WINDOW_START <= meal.arrive_at <= MEAL_WINDOW_END:
            problems.append(
                f"rule 4: meal {meal.place_id!r} arrives at {meal.arrive_at}, "
                f"outside {MEAL_WINDOW_START}-{MEAL_WINDOW_END}"
            )

    # -- Geography --------------------------------------------------------
    # Rule 5: one region, no exceptions.
    for stop in stops:
        if not regions.in_region(stop.place, region):
            problems.append(
                f"rule 5: {stop.place_id!r} is in {stop.place.region.value}, "
                f"itinerary is {region.value}"
            )

    # Rule 6: every leg within the cap. The first stop has no inbound leg.
    for index, stop in enumerate(stops):
        if index == 0:
            if stop.travel_min_from_prev != 0:
                problems.append(
                    f"rule 8: first stop has travel_min_from_prev="
                    f"{stop.travel_min_from_prev}, expected 0"
                )
        elif stop.travel_min_from_prev > max_leg_min:
            problems.append(
                f"rule 6: leg into {stop.place_id!r} is "
                f"{stop.travel_min_from_prev}min > cap {max_leg_min}min"
            )

    # -- Time -------------------------------------------------------------
    # Rule 8 (amended): the day starts at the first stop, at whatever
    # starts_at the day itself records — no longer a fixed 09:00.
    if stops and stops[0].arrive_at != day.starts_at:
        problems.append(f"rule 8: first stop arrives at {stops[0].arrive_at}, expected {day.starts_at}")

    # Rule 9: times chain exactly, with no unexplained gaps or overlaps.
    for index in range(len(stops) - 1):
        current, following = stops[index], stops[index + 1]
        expected = (
            to_minutes(current.arrive_at)
            + current.duration_min
            + following.travel_min_from_prev
        )
        if to_minutes(following.arrive_at) != expected:
            problems.append(
                f"rule 9: {following.place_id!r} arrives at {following.arrive_at}, "
                f"expected {to_hhmm(expected)}"
            )

    # ends_at must match the stops it claims to summarise.
    if stops:
        expected_end = to_minutes(stops[-1].arrive_at) + stops[-1].duration_min
        if to_minutes(day.ends_at) != expected_end:
            problems.append(
                f"rule 9: ends_at is {day.ends_at}, expected {to_hhmm(expected_end)}"
            )

    # Rule 10: 4 to 9 hours end to end.
    total = elapsed_minutes(day)
    if not MIN_DAY_MINUTES <= total <= MAX_DAY_MINUTES:
        problems.append(
            f"rule 10: day is {total}min, expected "
            f"{MIN_DAY_MINUTES}-{MAX_DAY_MINUTES}min"
        )

    # Rule 11: every stop open on arrival.
    for stop in stops:
        if not is_open_at(stop.place, weekday, stop.arrive_at):
            problems.append(
                f"rule 11: {stop.place_id!r} is not open at {stop.arrive_at} on {weekday.value}"
            )

    # -- Shabbat ----------------------------------------------------------
    # Rule 12.
    for stop in stops:
        if not shabbat.is_shabbat_eligible(stop.place, weekday):
            problems.append(
                f"rule 12: {stop.place_id!r} is not eligible on {weekday.value}"
            )

    return problems


def validate_itinerary(itinerary: Itinerary, known_place_ids: Iterable[str]) -> list[str]:
    """
    Validate a whole itinerary, including the MVP-shape rules from section 5.

    The API must never return an invalid itinerary, so this is the last gate
    before a response leaves the planner.
    """
    problems: list[str] = []

    # Section 5: `days` stays a list so multi-day can arrive later, but the MVP
    # emits exactly one day — or zero when fallback step 2 fired.
    if len(itinerary.days) > 1:
        problems.append(f"MVP: {len(itinerary.days)} days, expected 0 or 1")

    # Fallback step 2 is the only shape with no day, and it must carry cards
    # instead so the user never sees an empty screen (SPEC section 4).
    if not itinerary.days and not itinerary.places:
        problems.append("fallback: no days and no places — this is the forbidden empty state")

    for day in itinerary.days:
        problems.extend(
            validate_day(
                day=day,
                region=itinerary.region,
                weekday=itinerary.weekday,
                max_leg_min=itinerary.max_leg_min,
                known_place_ids=known_place_ids,
            )
        )

    return problems
