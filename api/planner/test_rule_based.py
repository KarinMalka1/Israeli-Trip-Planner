"""
Tests for planner/rule_based.py. Focused on the fallback-100%-of-the-time bug:
RuleBasedPlanner must return a real Day, not fallback step 2, when a region
has plenty of open-access places within the leg cap.
"""

from __future__ import annotations

import pytest

from api.domain import schedule
from api.domain.distance import DistanceMatrix
from api.models import AccessType, Category, DescriptionSource, Place, Region, Weekday
from api.planner.rule_based import RuleBasedPlanner
from api.repository.places import PlaceRepository

# Cycle categories so the planner's "prefer a different category from the
# previous stop" heuristic has real choices, without ever using MEAL (that
# has its own placement window and isn't what this test is about).
_CATEGORIES = [Category.NATURE, Category.HIKE, Category.VIEWPOINT]


def _open_place(index: int, duration_min: int = 60) -> Place:
    return Place(
        id=f"central-open-{index}",
        name_he=f"מקום פתוח {index}",
        description_he="",
        tip_he="",
        description_source=DescriptionSource.GENERATED,
        category=_CATEGORIES[index % len(_CATEGORIES)],
        region=Region.CENTRAL,
        access=AccessType.OPEN,
        lat=32.0 + index * 0.01,
        lng=34.9 + index * 0.01,
        duration_min=duration_min,
        opening_hours={day: None for day in Weekday},
        hours_verified=False,
        closed_on_shabbat=False,
        kid_friendly=False,
        accessible=False,
        tags=[],
    )


def _meal_place(index: int = 0, duration_min: int = 60) -> Place:
    return Place(
        id=f"central-meal-{index}",
        name_he=f"מסעדה {index}",
        description_he="",
        tip_he="",
        description_source=DescriptionSource.GENERATED,
        category=Category.MEAL,
        region=Region.CENTRAL,
        access=AccessType.OPEN,
        lat=32.05 + index * 0.01,
        lng=34.95 + index * 0.01,
        duration_min=duration_min,
        opening_hours={day: None for day in Weekday},
        hours_verified=False,
        closed_on_shabbat=False,
        kid_friendly=False,
        accessible=False,
        tags=[],
    )


def _gated_place(
    index: int, opens: str = "09:00", closes: str = "18:00", duration_min: int = 60
) -> Place:
    return Place(
        id=f"central-gated-{index}",
        name_he=f"מקום עם שער {index}",
        description_he="",
        tip_he="",
        description_source=DescriptionSource.GENERATED,
        category=_CATEGORIES[index % len(_CATEGORIES)],
        region=Region.CENTRAL,
        access=AccessType.GATED,
        lat=32.1 + index * 0.01,
        lng=34.95 + index * 0.01,
        duration_min=duration_min,
        opening_hours={day: (opens, closes) for day in Weekday},
        hours_verified=True,
        closed_on_shabbat=False,
        kid_friendly=False,
        accessible=False,
        tags=[],
    )


def _dense_matrix(places: list[Place], leg_minutes: int) -> DistanceMatrix:
    """Every place reachable from every other in a flat `leg_minutes`, well under any cap."""
    minutes = {
        origin.id: {dest.id: leg_minutes for dest in places if dest.id != origin.id}
        for origin in places
    }
    return DistanceMatrix(minutes)


def test_planner_builds_a_real_day_from_twenty_open_access_places():
    """
    The reported bug: GET /api/places?region=central shows 67 schedulable
    open-access places, but POST /api/itinerary always returned days: [].
    20 open places, all 15 minutes apart, comfortably satisfies rules 2 and 6
    at a 45-minute cap — a real day must come back, not the fallback.
    """
    places = [_open_place(i) for i in range(20)]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    itinerary = planner.plan(
        itinerary_id="test-itinerary",
        region=Region.CENTRAL,
        max_leg_min=45,
        weekday=Weekday.TUE,
    )

    assert len(itinerary.days) == 1, "expected a real day, got fallback step 2 (empty days)"
    assert itinerary.places == []
    day = itinerary.days[0]
    assert 3 <= len(day.stops) <= 6

    problems = schedule.validate_itinerary(itinerary, {place.id for place in places})
    assert problems == [], f"planner produced an invalid itinerary: {problems}"


def test_summer_only_place_dropped_from_candidates_outside_season():
    """A summer_only place must never reach the search when month is outside April-October."""
    places = [_open_place(i) for i in range(20)]
    places[0] = places[0].model_copy(update={"season": "summer_only"})
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    itinerary = planner.plan(
        itinerary_id="test-itinerary-winter",
        region=Region.CENTRAL,
        max_leg_min=45,
        weekday=Weekday.TUE,
        month=11,
    )

    day = itinerary.days[0]
    assert places[0].id not in {stop.place_id for stop in day.stops}


# --------------------------------------------------------------------------
# Meal preference: with_meal is a preference, not a hard constraint — only 3
# meal places exist nationwide, so requiring one would often fail the search.
# --------------------------------------------------------------------------


def test_with_meal_false_returns_a_day_with_zero_meal_stops():
    places = [_open_place(i) for i in range(11)] + [_meal_place()]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    itinerary = planner.plan(
        itinerary_id="no-meal",
        region=Region.CENTRAL,
        max_leg_min=45,
        weekday=Weekday.TUE,
        with_meal=False,
    )

    assert len(itinerary.days) == 1
    day = itinerary.days[0]
    assert all(stop.place.category != Category.MEAL for stop in day.stops)
    assert itinerary.meal_included is False

    problems = schedule.validate_itinerary(itinerary, {place.id for place in places})
    assert problems == []


def test_with_meal_true_and_reachable_meal_returns_exactly_one_in_window():
    places = [_open_place(i) for i in range(11)] + [_meal_place()]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    itinerary = planner.plan(
        itinerary_id="with-meal",
        region=Region.CENTRAL,
        max_leg_min=45,
        weekday=Weekday.TUE,
        with_meal=True,
    )

    assert len(itinerary.days) == 1
    day = itinerary.days[0]
    meal_stops = [stop for stop in day.stops if stop.place.category == Category.MEAL]
    assert len(meal_stops) == 1
    assert schedule.MEAL_WINDOW_START <= meal_stops[0].arrive_at <= schedule.MEAL_WINDOW_END
    assert itinerary.meal_included is True

    problems = schedule.validate_itinerary(itinerary, {place.id for place in places})
    assert problems == []


def test_with_meal_true_and_no_reachable_meal_still_returns_a_valid_day():
    """No meal place exists at all — the preference must degrade to a valid meal-free day, not fail."""
    places = [_open_place(i) for i in range(20)]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    itinerary = planner.plan(
        itinerary_id="meal-unreachable",
        region=Region.CENTRAL,
        max_leg_min=45,
        weekday=Weekday.TUE,
        with_meal=True,
    )

    assert len(itinerary.days) == 1, "expected a valid fallback day, not fallback step 2"
    assert itinerary.meal_included is False

    problems = schedule.validate_itinerary(itinerary, {place.id for place in places})
    assert problems == []


# --------------------------------------------------------------------------
# starts_at / day_length: rule 8 and rule 10 amendments. Both are
# preferences (day_length) or a direct request field (starts_at) — the hard
# 4-9h bound from rule 10 never relaxes.
# --------------------------------------------------------------------------


def test_starts_at_puts_first_stop_at_requested_time():
    places = [_open_place(i) for i in range(20)]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    itinerary = planner.plan(
        itinerary_id="early-start",
        region=Region.CENTRAL,
        max_leg_min=45,
        weekday=Weekday.TUE,
        starts_at="08:00",
        with_meal=False,
    )

    assert len(itinerary.days) == 1
    day = itinerary.days[0]
    assert day.starts_at == "08:00"
    assert day.stops[0].arrive_at == "08:00"

    problems = schedule.validate_itinerary(itinerary, {place.id for place in places})
    assert problems == []


def test_gated_place_opening_after_start_is_not_first_stop():
    """A gate that opens 09:00 can never be the first stop of an 08:00 day."""
    early_places = [_open_place(i) for i in range(5)]  # open-access, daylight 07:00-18:00
    late_gate = _gated_place(99, opens="09:00", closes="18:00")
    places = early_places + [late_gate]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    itinerary = planner.plan(
        itinerary_id="early-start-with-late-gate",
        region=Region.CENTRAL,
        max_leg_min=45,
        weekday=Weekday.TUE,
        starts_at="08:00",
        with_meal=False,
    )

    assert len(itinerary.days) == 1
    assert itinerary.days[0].stops[0].place_id != late_gate.id

    problems = schedule.validate_itinerary(itinerary, {place.id for place in places})
    assert problems == []


def test_short_day_length_returns_fewer_stops_than_long():
    """
    duration_min=90, leg=15min: 3 stops = 300min (fits 'short' 240-330, not
    'long'), 4 stops = 405min and 5 stops = 510min (both fit 'long' 390-540,
    neither fits 'short'). So 'short' can only ever complete at 3 stops and
    'long' only at 4 or 5 — always strictly more than 'short'.
    """
    places = [_open_place(i, duration_min=90) for i in range(10)]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)
    known_ids = {place.id for place in places}

    short_itinerary = planner.plan(
        itinerary_id="short",
        region=Region.CENTRAL,
        max_leg_min=45,
        weekday=Weekday.TUE,
        day_length="short",
        with_meal=False,
    )
    long_itinerary = planner.plan(
        itinerary_id="long",
        region=Region.CENTRAL,
        max_leg_min=45,
        weekday=Weekday.TUE,
        day_length="long",
        with_meal=False,
    )

    assert len(short_itinerary.days) == 1 and len(long_itinerary.days) == 1
    assert short_itinerary.length_matched is True
    assert long_itinerary.length_matched is True
    assert len(short_itinerary.days[0].stops) < len(long_itinerary.days[0].stops)

    assert schedule.validate_itinerary(short_itinerary, known_ids) == []
    assert schedule.validate_itinerary(long_itinerary, known_ids) == []


def test_late_start_plus_long_returns_valid_day_with_length_matched_false():
    """
    Every place closes at 16:00. Starting at 11:00, only 5 hours of daylight
    remain — nowhere near the 6.5-9h 'long' band — but 4 stops (60min visits,
    15min legs = 285min) clears the hard 4h minimum and finishes at 15:45,
    before closing. Must degrade to that valid day, not fail.
    """
    places = [_gated_place(i, opens="09:00", closes="16:00") for i in range(6)]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    itinerary = planner.plan(
        itinerary_id="late-start-long-day",
        region=Region.CENTRAL,
        max_leg_min=45,
        weekday=Weekday.TUE,
        starts_at="11:00",
        day_length="long",
        with_meal=False,
    )

    assert len(itinerary.days) == 1, "expected a valid degraded day, not fallback step 2"
    assert itinerary.length_matched is False

    problems = schedule.validate_itinerary(itinerary, {place.id for place in places})
    assert problems == []


# --------------------------------------------------------------------------
# shabbat_observant (SPEC section 17): a planner input selecting which of
# two documented rule-12 Friday behaviours applies. Threaded into candidates()
# and every opening-hours check, never into the search algorithm itself.
# --------------------------------------------------------------------------


def test_itinerary_echoes_the_requested_shabbat_observant():
    places = [_open_place(i) for i in range(20)]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    observant = planner.plan(
        itinerary_id="observant", region=Region.CENTRAL, max_leg_min=45, weekday=Weekday.TUE
    )
    secular = planner.plan(
        itinerary_id="secular",
        region=Region.CENTRAL,
        max_leg_min=45,
        weekday=Weekday.TUE,
        shabbat_observant=False,
    )

    assert observant.shabbat_observant is True  # default
    assert secular.shabbat_observant is False


def test_itinerary_echoes_shabbat_observant_on_fallback_step_2_too():
    """The flag must be persisted even when no day could be built at all."""
    places = [_open_place(0)]  # fewer than MIN_STOPS: always falls back
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    itinerary = planner.plan(
        itinerary_id="fallback",
        region=Region.CENTRAL,
        max_leg_min=45,
        weekday=Weekday.TUE,
        shabbat_observant=False,
    )

    assert itinerary.days == []
    assert itinerary.shabbat_observant is False


def test_observant_friday_day_ends_by_1500():
    """
    Fri 09:00-20:00, duration 90min, 15min legs: an observant request must
    never produce a day ending after the 15:00 deadline.
    """
    # Exactly 5 candidates, not 10: PREFERRED_STOP_COUNTS includes 6, and
    # with a uniform-duration pool larger than 6 the search can burn its
    # whole budget fully exploring every impossible 6-stop combination
    # before ever trying 4 or 5 (day_length="long"'s upper bound coincides
    # with rule 10's hard 9h ceiling, so unlike "short" there is no early
    # prune to save it — see _extend's own comment on the "short" case this
    # bit the search before). Capping candidates at 5 makes target_stops=6
    # skip immediately (`target_stops > len(candidates)`), so this test
    # exercises the flag deterministically rather than occasionally.
    places = [_gated_place(i, opens="09:00", closes="20:00", duration_min=90) for i in range(5)]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    itinerary = planner.plan(
        itinerary_id="fri-observant",
        region=Region.CENTRAL,
        max_leg_min=45,
        weekday=Weekday.FRI,
        with_meal=False,
        shabbat_observant=True,
    )

    assert len(itinerary.days) == 1
    assert itinerary.days[0].ends_at <= "15:00"

    problems = schedule.validate_itinerary(itinerary, {place.id for place in places})
    assert problems == []


def test_non_observant_friday_can_end_after_1500():
    """
    Same seed as the test above, but shabbat_observant=False: the same
    region must now be able to build an ordinary "long" day that runs past
    15:00 — proof the flag actually reaches the search, not just a default
    that happens not to matter.
    """
    # Exactly 5 candidates, not 10: PREFERRED_STOP_COUNTS includes 6, and
    # with a uniform-duration pool larger than 6 the search can burn its
    # whole budget fully exploring every impossible 6-stop combination
    # before ever trying 4 or 5 (day_length="long"'s upper bound coincides
    # with rule 10's hard 9h ceiling, so unlike "short" there is no early
    # prune to save it — see _extend's own comment on the "short" case this
    # bit the search before). Capping candidates at 5 makes target_stops=6
    # skip immediately (`target_stops > len(candidates)`), so this test
    # exercises the flag deterministically rather than occasionally.
    places = [_gated_place(i, opens="09:00", closes="20:00", duration_min=90) for i in range(5)]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    itinerary = planner.plan(
        itinerary_id="fri-secular",
        region=Region.CENTRAL,
        max_leg_min=45,
        weekday=Weekday.FRI,
        with_meal=False,
        day_length="long",
        shabbat_observant=False,
    )

    assert len(itinerary.days) == 1
    assert itinerary.days[0].ends_at > "15:00"
    assert itinerary.length_matched is True

    problems = schedule.validate_itinerary(itinerary, {place.id for place in places})
    assert problems == []


@pytest.mark.xfail(
    strict=False,
    reason=(
        "planner/rule_based.py budget-exhaustion bug, not a shabbat_observant bug: "
        "day_length='long' has no early-pruning benefit (its upper bound coincides "
        "with rule 10's hard 9h ceiling, unlike 'short'), so a bad shuffle of "
        "PREFERRED_STOP_COUNTS can burn the whole 40_000 search budget fully "
        "exploring every impossible 6-stop chain before ever trying the 4- or "
        "5-stop chains that would succeed. Out of scope for BRIEF_shabbat_mode.md "
        "(search algorithm is explicitly not to be touched there) — SPEC.md "
        "section 17 records this as a known limitation. Fails ~1 run in 9; "
        "strict=False so neither an occasional real failure nor an occasional "
        "lucky pass breaks the suite."
    ),
)
def test_long_day_length_with_ten_uniform_candidates_sometimes_fails_to_find_a_day():
    """
    Reproduces the exact case that made test_non_observant_friday_can_end_after_1500
    flaky before its fixture was narrowed to 5 candidates: 10 uniform-duration,
    always-open gated places, day_length="long", with_meal=False. A valid 4- or
    5-stop day always exists here (elapsed 405min/510min both land inside the
    390-540 "long" band) — the planner should always find one, but occasionally
    does not, because of the budget-exhaustion mechanism described above rather
    than any actual absence of a valid day.
    """
    places = [_gated_place(i, opens="09:00", closes="20:00", duration_min=90) for i in range(10)]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    itinerary = planner.plan(
        itinerary_id="fri-secular-ten-candidates",
        region=Region.CENTRAL,
        max_leg_min=45,
        weekday=Weekday.FRI,
        with_meal=False,
        day_length="long",
        shabbat_observant=False,
    )

    assert len(itinerary.days) == 1, "a valid 4- or 5-stop day exists but the search failed to find it"


def test_friday_observant_day_length_is_capped_below_the_long_band():
    """
    Friday + observant + the 09:00 default start caps the day at 6.0h
    (09:00-15:00), below the "long" band's 6.5h floor (domain/schedule
    .DAY_LENGTH_BANDS). BRIEF_shabbat_mode.md is explicit that
    length_matched must be False here — the existing "preference degrades,
    never fails" shape working correctly, not a bug to "fix" by relaxing
    rule 10.
    """
    # Exactly 5 candidates, not 10: PREFERRED_STOP_COUNTS includes 6, and
    # with a uniform-duration pool larger than 6 the search can burn its
    # whole budget fully exploring every impossible 6-stop combination
    # before ever trying 4 or 5 (day_length="long"'s upper bound coincides
    # with rule 10's hard 9h ceiling, so unlike "short" there is no early
    # prune to save it — see _extend's own comment on the "short" case this
    # bit the search before). Capping candidates at 5 makes target_stops=6
    # skip immediately (`target_stops > len(candidates)`), so this test
    # exercises the flag deterministically rather than occasionally.
    places = [_gated_place(i, opens="09:00", closes="20:00", duration_min=90) for i in range(5)]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    itinerary = planner.plan(
        itinerary_id="fri-observant-long",
        region=Region.CENTRAL,
        max_leg_min=45,
        weekday=Weekday.FRI,
        with_meal=False,
        day_length="long",
        shabbat_observant=True,
    )

    assert len(itinerary.days) == 1, "expected a valid degraded day, not fallback step 2"
    assert itinerary.days[0].ends_at <= "15:00"
    assert itinerary.length_matched is False

    problems = schedule.validate_itinerary(itinerary, {place.id for place in places})
    assert problems == []


def test_saturday_still_excludes_closed_on_shabbat_regardless_of_shabbat_observant():
    """Saturday's exclusion is a fact about the place, never conditional on the flag (Part 3's non-decision)."""
    open_places = [_open_place(i) for i in range(6)]
    closed_on_shabbat_place = _open_place(99).model_copy(update={"closed_on_shabbat": True})
    places = open_places + [closed_on_shabbat_place]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    for observant in (True, False):
        itinerary = planner.plan(
            itinerary_id=f"sat-{observant}",
            region=Region.CENTRAL,
            max_leg_min=45,
            weekday=Weekday.SAT,
            with_meal=False,
            shabbat_observant=observant,
        )
        used_ids = {stop.place_id for stop in itinerary.days[0].stops} if itinerary.days else set()
        assert closed_on_shabbat_place.id not in used_ids
