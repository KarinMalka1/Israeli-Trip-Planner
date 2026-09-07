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
from api.repository.itineraries import ItineraryStore
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


def _meal_place(place_id: str, name_he: str, region: Region = Region.CENTRAL, duration_min: int = 60) -> Place:
    return Place(
        id=place_id,
        name_he=name_he,
        description_he="",
        tip_he="",
        description_source=DescriptionSource.GENERATED,
        category=Category.MEAL,
        region=region,
        access=AccessType.OPEN,
        lat=32.05,
        lng=34.95,
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


def _itinerary(
    day_places: list[Place],
    matrix: DistanceMatrix,
    *,
    max_leg_min: int = 45,
    weekday: Weekday = Weekday.TUE,
    shabbat_observant: bool = True,
) -> Itinerary:
    """Build a valid one-day itinerary directly from an ordered place list, bypassing the planner."""
    legs = [0] + [matrix.travel_min(a.id, b.id) for a, b in zip(day_places, day_places[1:])]
    day = schedule.build_day(day_places, legs, starts_at="09:00")
    return Itinerary(
        id="test-itinerary",
        region=day_places[0].region,
        max_leg_min=max_leg_min,
        weekday=weekday,
        days=[day],
        seed=1,
        meal_included=False,
        length_matched=True,
        shabbat_observant=shabbat_observant,
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


# --------------------------------------------------------------------------
# shabbat_observant: the editor must read it off the Itinerary it was
# handed, never from a default — an edit must not silently change the rule
# the day was built under (BRIEF_shabbat_mode.md Part 3, item 6).
# --------------------------------------------------------------------------


def test_swap_honours_the_itinerary_shabbat_observant_false_on_friday():
    """
    Three open-access (daylight 07:00-18:00) places, 170min each, 15min legs:
    p0 09:00-11:50, p1 12:05-14:55, p2 15:10-18:00. p2's 15:10 arrival is only
    legal on a Friday when shabbat_observant is False (no 15:00 deadline) —
    if the editor read a default True instead of the itinerary's own False,
    every reschedule attempt would fail the (wrongly clamped) opening-hours
    check and swap() would silently no-op instead of actually swapping.
    """
    places = [_open_place(i, duration_min=170) for i in range(3)]
    spare = _open_place(99, duration_min=170)
    matrix = _dense_matrix(places + [spare])
    editor = ItineraryEditor(PlaceRepository(places + [spare]), matrix)
    itinerary = _itinerary(places, matrix, weekday=Weekday.FRI, shabbat_observant=False)

    assert schedule.validate_itinerary(itinerary, {p.id for p in places}) == []

    edited = editor.swap(itinerary, places[0].id)

    assert edited is not None
    assert edited.days[0].stops[0].place_id == spare.id
    assert edited.days[0].stops[-1].arrive_at == "15:10"


def test_remove_honours_the_itinerary_shabbat_observant_false_on_friday():
    """Same setup as the swap test, but removing the trailing spare stop instead."""
    places = [_open_place(i, duration_min=170) for i in range(3)]
    tail = _open_place(99, duration_min=60)
    matrix = _dense_matrix(places + [tail])
    editor = ItineraryEditor(PlaceRepository(places + [tail]), matrix)
    itinerary = _itinerary(places + [tail], matrix, weekday=Weekday.FRI, shabbat_observant=False)

    edited = editor.remove(itinerary, tail.id)

    assert edited is not None
    assert [stop.place_id for stop in edited.days[0].stops] == [p.id for p in places]
    assert edited.days[0].stops[-1].arrive_at == "15:10"


def test_swap_fails_to_find_an_alternative_when_the_itinerary_is_observant():
    """
    Control for the test above: with shabbat_observant=True on the same
    Friday setup, the 15:00 deadline clamps daylight to end at 15:00, so
    p2's 15:10 arrival is illegal and no reschedule (including the identity
    one) can succeed — swap must no-op rather than silently ignoring the flag.
    """
    places = [_open_place(i, duration_min=170) for i in range(3)]
    spare = _open_place(99, duration_min=170)
    matrix = _dense_matrix(places + [spare])
    editor = ItineraryEditor(PlaceRepository(places + [spare]), matrix)
    itinerary = _itinerary(places, matrix, weekday=Weekday.FRI, shabbat_observant=True)

    edited = editor.swap(itinerary, places[0].id)

    assert edited is not None
    assert edited.days[0].stops[0].place_id == places[0].id  # unchanged: no-op


# --------------------------------------------------------------------------
# Rule 4 (production bug): a swap installed a second meal stop, outside the
# midday window, with nothing downstream to catch it. _alternatives() never
# excluded MEAL-category candidates and _reschedule() never checked rule 4
# at all, so a swap into a non-meal position could still land a meal place
# there. Reproduces the reported shape: an existing meal at 12:45 (valid),
# a swap that would add a second meal arriving 15:50 (both halves of rule 4
# broken at once) — the exact two failures reported in production, just
# against a small, deterministic fixture instead of the real seed/matrix.
# --------------------------------------------------------------------------


def test_swap_does_not_install_a_second_meal_stop_outside_the_window():
    bat_yaar = _meal_place("central-meal-bat-yaar", "מסעדת בת יער")
    magdalena = _meal_place("central-meal-magdalena", "מסעדת מגדלנה")
    p0, p1, p2, p3 = (_open_place(i) for i in range(4))

    # p0 09:00-10:00 -[15]- p1 10:15-11:15 -[90]- bat_yaar 12:45-13:45 (valid
    # meal, inside the window) -[15]- p2 14:00-15:00 -[??]- p3, where p3 is
    # about to be swapped out for magdalena.
    matrix = DistanceMatrix(
        {
            p0.id: {p1.id: 15},
            p1.id: {p0.id: 15, bat_yaar.id: 90},
            bat_yaar.id: {p1.id: 90, p2.id: 15},
            p2.id: {bat_yaar.id: 15, p3.id: 15, magdalena.id: 50},
            p3.id: {p2.id: 15},
            magdalena.id: {p2.id: 50},
        }
    )
    day_places = [p0, p1, bat_yaar, p2, p3]
    itinerary = _itinerary(day_places, matrix, max_leg_min=90)
    assert schedule.validate_itinerary(itinerary, {p.id for p in day_places} | {bat_yaar.id}) == []
    assert itinerary.days[0].stops[2].arrive_at == "12:45"  # bat_yaar, sanity-check the fixture's own timing

    # magdalena is the only candidate this small region can offer for p3's slot.
    editor = ItineraryEditor(PlaceRepository(day_places + [magdalena]), matrix)

    edited = editor.swap(itinerary, p3.id)

    assert edited is not None
    resulting_places = [stop.place_id for stop in edited.days[0].stops]
    meal_arrivals = [stop.arrive_at for stop in edited.days[0].stops if stop.place.category == Category.MEAL]

    # The swap must be rejected outright (day unchanged) rather than accepted
    # with a second meal — magdalena must not appear, bat_yaar's slot must
    # not have grown a second meal stop, and there is still only one arrival
    # time counted as a meal.
    assert magdalena.id not in resulting_places, "swap installed a second meal stop"
    assert len(meal_arrivals) == 1, f"expected exactly one meal stop, found {len(meal_arrivals)}: {meal_arrivals}"
    assert schedule.meal_rule_violations(edited.days[0].stops) == []


def test_remove_then_undo_cannot_produce_a_second_meal():
    """
    remove() only ever deletes a stop, never adds one, so it cannot introduce
    a second meal by itself — this guards the store/undo plumbing: the state
    undo restores must be exactly the pre-removal state (already valid, one
    meal), never some corrupted merge that duplicates it.
    """
    meal = _meal_place("central-meal-only", "מסעדת בת יער", duration_min=60)
    p0, p1, p2 = (_open_place(i, duration_min=170) for i in range(3))
    # p0 09:00-11:50 -[10]- meal 12:00-13:00 -[15]- p1 13:15-16:05 -[15]- p2 16:20-19:10
    matrix = DistanceMatrix(
        {
            p0.id: {meal.id: 10},
            meal.id: {p0.id: 10, p1.id: 15},
            p1.id: {meal.id: 15, p2.id: 15},
            p2.id: {p1.id: 15},
        }
    )
    day_places = [p0, meal, p1, p2]
    itinerary = _itinerary(day_places, matrix, max_leg_min=45)
    assert itinerary.days[0].stops[1].arrive_at == "12:00"  # sanity-check the fixture's own timing
    assert schedule.meal_rule_violations(itinerary.days[0].stops) == []

    store = ItineraryStore()
    store.save(itinerary)
    editor = ItineraryEditor(PlaceRepository(day_places), matrix)

    # Remove the trailing stop -- it is after the meal, so removing it can
    # never shift the meal's own arrival time.
    edited = editor.remove(itinerary, p2.id)
    assert edited is not None
    assert schedule.meal_rule_violations(edited.days[0].stops) == []
    store.update(edited)

    restored = store.undo(itinerary.id)
    assert restored is not None
    restored_meals = [stop for stop in restored.days[0].stops if stop.place.category == Category.MEAL]
    assert len(restored_meals) == 1
    assert schedule.meal_rule_violations(restored.days[0].stops) == []
