"""
Tests for planner/edits.py — mostly ItineraryEditor.annotate(), which stamps
can_remove/can_swap onto every Stop so the client can disable a button before
the click instead of toasting an explanation after it (SPEC section 7: the
client does zero rule evaluation).
"""

from __future__ import annotations

from api.domain import schedule
from api.domain.distance import DistanceMatrix
from api.models import AccessType, Category, DescriptionSource, Itinerary, Place, Region, Weekday
from api.planner.edits import ItineraryEditor
from api.repository.places import PlaceRepository


def _open_place(index: int, region: Region = Region.CENTRAL, duration_min: int = 60) -> Place:
    return Place(
        id=f"{region.value}-p{index}",
        name_he=f"מקום {index}",
        description_he="",
        tip_he="",
        description_source=DescriptionSource.GENERATED,
        category=Category.NATURE,
        region=region,
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


def _dense_matrix(places: list[Place], leg_minutes: int = 15) -> DistanceMatrix:
    """Every place reachable from every other in a flat `leg_minutes`, well under any cap."""
    minutes = {
        origin.id: {dest.id: leg_minutes for dest in places if dest.id != origin.id}
        for origin in places
    }
    return DistanceMatrix(minutes)


def _itinerary(day_places: list[Place], matrix: DistanceMatrix, *, max_leg_min: int = 45) -> Itinerary:
    """Build a valid one-day itinerary directly from an ordered place list, bypassing the planner."""
    legs = [0] + [matrix.travel_min(a.id, b.id) for a, b in zip(day_places, day_places[1:])]
    day = schedule.build_day(day_places, legs, starts_at="09:00")
    return Itinerary(
        id="test-itinerary",
        region=day_places[0].region,
        max_leg_min=max_leg_min,
        weekday=Weekday.TUE,
        days=[day],
        seed=1,
        meal_included=False,
        length_matched=True,
    )


# --------------------------------------------------------------------------
# can_remove
# --------------------------------------------------------------------------


def test_three_stop_day_returns_can_remove_false_on_every_stop():
    """Removing any one of exactly 3 stops would drop the day below MIN_STOPS."""
    places = [_open_place(i) for i in range(3)]
    matrix = _dense_matrix(places)
    itinerary = _itinerary(places, matrix)
    editor = ItineraryEditor(PlaceRepository(places), matrix)

    annotated = editor.annotate(itinerary)

    assert [stop.can_remove for stop in annotated.days[0].stops] == [False, False, False]


def test_five_stop_day_returns_can_remove_true_on_every_stop():
    places = [_open_place(i) for i in range(5)]
    matrix = _dense_matrix(places)
    itinerary = _itinerary(places, matrix)
    editor = ItineraryEditor(PlaceRepository(places), matrix)

    annotated = editor.annotate(itinerary)

    assert all(stop.can_remove for stop in annotated.days[0].stops)


def test_remove_result_recomputes_can_remove_at_the_new_floor():
    """4 stops -> can_remove true; after removing one, 3 remain -> can_remove flips to false."""
    places = [_open_place(i) for i in range(4)]
    matrix = _dense_matrix(places)
    itinerary = ItineraryEditor(PlaceRepository(places), matrix).annotate(_itinerary(places, matrix))
    assert all(stop.can_remove for stop in itinerary.days[0].stops)

    editor = ItineraryEditor(PlaceRepository(places), matrix)
    edited = editor.remove(itinerary, places[0].id)

    assert edited is not None
    assert len(edited.days[0].stops) == 3
    assert all(stop.can_remove is False for stop in edited.days[0].stops)


# --------------------------------------------------------------------------
# can_swap
# --------------------------------------------------------------------------


def test_stop_with_no_valid_alternative_returns_can_swap_false():
    """Only the 3 places used in the day exist at all — no spare candidate anywhere."""
    places = [_open_place(i) for i in range(3)]
    matrix = _dense_matrix(places)
    itinerary = _itinerary(places, matrix)
    editor = ItineraryEditor(PlaceRepository(places), matrix)

    annotated = editor.annotate(itinerary)

    assert all(stop.can_swap is False for stop in annotated.days[0].stops)


def test_stop_with_a_valid_alternative_returns_can_swap_true():
    places = [_open_place(i) for i in range(3)]
    spare = _open_place(99)
    matrix = _dense_matrix(places + [spare])
    itinerary = _itinerary(places, matrix)
    editor = ItineraryEditor(PlaceRepository(places + [spare]), matrix)

    annotated = editor.annotate(itinerary)

    assert any(stop.can_swap for stop in annotated.days[0].stops)


def test_can_swap_false_even_when_a_candidate_fits_the_leg_cap_but_reschedule_still_fails():
    """
    A candidate that satisfies _alternatives' leg-cap filter can still fail
    _reschedule's opening-hours check — can_swap must reflect the real
    reschedule outcome, not the looser "fits the cap" approximation.
    """
    places = [_open_place(i) for i in range(3)]
    # Gated, open 07:00-07:05 -- long closed by the time the day even starts
    # (09:00), so it fails opening hours at every possible index/arrival,
    # while still fitting the leg cap easily (same dense matrix).
    narrow = places[0].model_copy(
        update={
            "id": "central-narrow",
            "access": AccessType.GATED,
            "opening_hours": {day: ("07:00", "07:05") for day in Weekday},
            "hours_verified": True,
        }
    )
    matrix = _dense_matrix(places + [narrow])
    itinerary = _itinerary(places, matrix)
    editor = ItineraryEditor(PlaceRepository(places + [narrow]), matrix)

    annotated = editor.annotate(itinerary)

    assert all(stop.can_swap is False for stop in annotated.days[0].stops)


def test_swap_result_flags_match_a_fresh_annotate_call():
    """
    swap() must return a day whose can_remove/can_swap already reflect the
    post-swap state — not flags left over from before the swap. Comparing
    against a fresh annotate() call (the same method create_itinerary and
    remove() also go through) is the literal "same logic" check.
    """
    places = [_open_place(i) for i in range(3)]
    spare = _open_place(99)
    matrix = _dense_matrix(places + [spare])
    editor = ItineraryEditor(PlaceRepository(places + [spare]), matrix)
    itinerary = editor.annotate(_itinerary(places, matrix))

    edited = editor.swap(itinerary, places[0].id)
    assert edited is not None

    reannotated = editor.annotate(edited)
    actual = [(stop.can_remove, stop.can_swap) for stop in edited.days[0].stops]
    expected = [(stop.can_remove, stop.can_swap) for stop in reannotated.days[0].stops]
    assert actual == expected
