"""
Tests for planner/rule_based.py. Focused on the fallback-100%-of-the-time bug:
RuleBasedPlanner must return a real Day, not fallback step 2, when a region
has plenty of open-access places within the leg cap.
"""

from __future__ import annotations

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
