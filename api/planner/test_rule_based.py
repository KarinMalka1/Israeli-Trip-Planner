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


# --------------------------------------------------------------------------
# Seeded randomness: repeating a request should produce variety, but a given
# seed must reproduce exactly, and validity is never negotiable.
# --------------------------------------------------------------------------


def test_different_seeds_produce_different_stop_sets():
    places = [_open_place(i) for i in range(30)]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    first = planner.plan(
        itinerary_id="a", region=Region.CENTRAL, max_leg_min=45, weekday=Weekday.TUE, seed=1
    )
    second = planner.plan(
        itinerary_id="b", region=Region.CENTRAL, max_leg_min=45, weekday=Weekday.TUE, seed=2
    )

    stops_a = {stop.place_id for stop in first.days[0].stops}
    stops_b = {stop.place_id for stop in second.days[0].stops}
    assert stops_a != stops_b
    assert first.seed == 1
    assert second.seed == 2


def test_same_seed_produces_identical_itinerary():
    places = [_open_place(i) for i in range(30)]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    first = planner.plan(
        itinerary_id="a", region=Region.CENTRAL, max_leg_min=45, weekday=Weekday.TUE, seed=7
    )
    second = planner.plan(
        itinerary_id="b", region=Region.CENTRAL, max_leg_min=45, weekday=Weekday.TUE, seed=7
    )

    stops_a = [(s.place_id, s.arrive_at) for s in first.days[0].stops]
    stops_b = [(s.place_id, s.arrive_at) for s in second.days[0].stops]
    assert stops_a == stops_b


def test_seed_is_generated_and_returned_when_absent():
    places = [_open_place(i) for i in range(20)]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)

    itinerary = planner.plan(
        itinerary_id="a", region=Region.CENTRAL, max_leg_min=45, weekday=Weekday.TUE
    )
    assert isinstance(itinerary.seed, int)


def test_fifty_random_seeds_all_produce_valid_itineraries():
    places = [_open_place(i) for i in range(30)]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)
    known_ids = {place.id for place in places}

    for seed in range(50):
        itinerary = planner.plan(
            itinerary_id=f"seed-{seed}",
            region=Region.CENTRAL,
            max_leg_min=45,
            weekday=Weekday.TUE,
            seed=seed,
        )
        assert itinerary.seed == seed
        problems = schedule.validate_itinerary(itinerary, known_ids)
        assert problems == [], f"seed={seed}: {problems}"


def test_small_repository_still_returns_valid_itinerary_not_a_failure():
    """
    Exactly MIN_STOPS candidates leaves no room for variety, but it must not
    raise or fall back. duration_min=90 (not the default 60) so 3 stops plus
    two 15-minute legs clears the 4-hour day minimum (rule 10) — otherwise
    this fixture would *correctly* fall back regardless of the seed logic.
    """
    places = [_open_place(i, duration_min=90) for i in range(3)]
    repository = PlaceRepository(places)
    matrix = _dense_matrix(places, leg_minutes=15)
    planner = RuleBasedPlanner(repository, matrix)
    known_ids = {place.id for place in places}

    for seed in (1, 2, 3):
        itinerary = planner.plan(
            itinerary_id=f"small-{seed}",
            region=Region.CENTRAL,
            max_leg_min=45,
            weekday=Weekday.TUE,
            seed=seed,
        )
        assert len(itinerary.days) == 1, "expected a valid day even with no room for variety"
        problems = schedule.validate_itinerary(itinerary, known_ids)
        assert problems == []
