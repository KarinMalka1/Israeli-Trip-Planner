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


def _open_place(index: int) -> Place:
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
        duration_min=60,
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
