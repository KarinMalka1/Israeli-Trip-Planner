"""
User-driven edits to an existing itinerary (US-2: remove, swap).

Kept out of ``main.py`` because both operations are time arithmetic wearing a
route's clothes: every leg downstream of the edited stop has to be re-fetched
from the matrix and every arrival recomputed. The contract's most important
line is that mutating endpoints return the full recomputed itinerary, and this
is the module that recomputes it.

A note on validity. The planner guarantees rules 1-13 at generation time. A
user removing a fourth stop can drop the day below three stops or under four
hours, and that is their explicit choice, made through a visible X with a
visible undo — not a planner error. So edits recompute honestly and return the
result rather than refusing it. The one invariant kept absolute is that we
never emit stale times: whatever the shape of the day, its clock is correct.
"""

from __future__ import annotations

import logging
from typing import Optional

from api.domain import schedule
from api.domain.distance import DistanceMatrix
from api.models import Day, Itinerary, Place
from api.repository.places import PlaceRepository

logger = logging.getLogger(__name__)


class EditNotPossible(Exception):
    """
    Raised when an edit would force us to break a rule to satisfy it.

    Two causes, both real: the matrix has no leg for a pair the edit creates
    (rule 7 forbids inventing one), or re-timing pushes a surviving stop
    outside its opening hours (rule 11). Carries a Hebrew message because it
    reaches the user as a toast — the client shows it and leaves the itinerary
    untouched, which is still not a dialog and still not a blank state.
    """

    def __init__(self, message_he: str) -> None:
        """Store the user-facing Hebrew text alongside the exception."""
        super().__init__(message_he)
        self.message_he = message_he


class ItineraryEditor:
    """Applies remove and swap to a stored itinerary and rebuilds its schedule."""

    def __init__(self, places: PlaceRepository, matrix: DistanceMatrix) -> None:
        """Hold the same two read-only data sources the planner uses."""
        self._places = places
        self._matrix = matrix

    # -- Operations --------------------------------------------------------

    def remove(self, itinerary: Itinerary, place_id: str) -> Optional[Itinerary]:
        """
        Drop one stop and re-time the rest of the day.

        Returns ``None`` when the id is not in the itinerary, which the route
        turns into a 404. Removing the stop closes the gap: the stop that
        followed it now travels from the stop that preceded it, so the leg is
        looked up fresh rather than inherited.

        Raises ``EditNotPossible`` when closing that gap would break rule 7 or
        rule 11 — removal pulls everything after it earlier, which can land a
        later stop before its own opening time.
        """
        day = self._single_day(itinerary)
        if day is None:
            return None

        remaining = [stop.place for stop in day.stops if stop.place_id != place_id]
        if len(remaining) == len(day.stops):
            return None

        rescheduled = self._reschedule(remaining, itinerary)
        if rescheduled is None:
            raise EditNotPossible("לא ניתן להסיר את העצירה הזו בלי לשבור את לוח הזמנים")
        return self._with_day(itinerary, rescheduled)

    def swap(self, itinerary: Itinerary, place_id: str) -> Optional[Itinerary]:
        """
        Replace one stop with the best alternative that fits in its position.

        "Best" is defined by ``_alternatives``: same region and weekday, not
        already in the day, reachable from both neighbours inside the cap.
        Returns ``None`` if the stop is not in the itinerary, and returns the
        itinerary unchanged if no alternative qualifies — US-2 forbids a dialog,
        so a swap with nothing to swap to is a no-op the client can toast about.
        """
        day = self._single_day(itinerary)
        if day is None:
            return None

        index = next(
            (i for i, stop in enumerate(day.stops) if stop.place_id == place_id), None
        )
        if index is None:
            return None

        current = [stop.place for stop in day.stops]
        for alternative in self._alternatives(itinerary, current, index):
            rebuilt = current[:index] + [alternative] + current[index + 1 :]
            rescheduled = self._reschedule(rebuilt, itinerary)
            # Take the first alternative whose day still holds together: every
            # leg present in the matrix, and every visit inside opening hours.
            if rescheduled is not None:
                return self._with_day(itinerary, rescheduled)

        logger.info("no swap alternative for %s in itinerary %s", place_id, itinerary.id)
        return itinerary

    # -- Rescheduling ------------------------------------------------------

    def _reschedule(self, places: list[Place], itinerary: Itinerary) -> Optional[Day]:
        """
        Rebuild a day from an ordered place list, re-fetching every leg.

        Keeps the itinerary's original ``starts_at`` (rule 8, amended): an
        edit reshuffles which stops are in the day, never when the day itself
        begins, so this reads it off the existing day rather than defaulting.

        Returns ``None`` when a consecutive pair has no entry in the matrix,
        which would mean inventing a travel time — rule 7 forbids that, so we
        refuse the edit instead. Legs over the cap are allowed through here:
        after a removal the user has effectively accepted a longer drive, and
        the recomputed number is shown to them.
        """
        starts_at = itinerary.days[0].starts_at

        if not places:
            # An empty day is still a coherent answer to "remove everything";
            # the client renders the empty timeline and the undo toast.
            return schedule.build_day([], [], starts_at=starts_at)

        legs = [0]
        for previous, current in zip(places, places[1:]):
            travel_min = self._matrix.travel_min(previous.id, current.id)
            if travel_min is None:
                logger.warning(
                    "matrix has no leg %s -> %s; refusing to reschedule", previous.id, current.id
                )
                return None
            legs.append(travel_min)

        day = schedule.build_day(places, legs, starts_at=starts_at)

        # Re-timing shifts everything after the edit, so a stop that was open
        # on the old clock can be closed on the new one. Check before returning.
        for stop in day.stops:
            if not schedule.is_open_at(stop.place, itinerary.weekday, stop.arrive_at):
                logger.info(
                    "reschedule puts %s outside opening hours at %s",
                    stop.place_id,
                    stop.arrive_at,
                )
                return None

        return day

    def _alternatives(
        self, itinerary: Itinerary, current: list[Place], index: int
    ) -> list[Place]:
        """
        Candidate replacements for the stop at ``index``, best first.

        Filters to places that are in the region, available on the weekday, not
        already used, and within the leg cap of whichever neighbours exist.
        Ordered by total travel to those neighbours, so the swap that disturbs
        the day least is offered first.
        """
        used = {place.id for place in current}
        cap = itinerary.max_leg_min
        before = current[index - 1] if index > 0 else None
        after = current[index + 1] if index + 1 < len(current) else None

        scored: list[tuple[int, str, Place]] = []
        for place in self._places.candidates(itinerary.region, itinerary.weekday):
            if place.id in used:
                continue

            total_travel = 0
            fits = True
            for neighbour, from_id, to_id in (
                (before, before.id if before else None, place.id),
                (after, place.id, after.id if after else None),
            ):
                if neighbour is None:
                    continue
                travel_min = self._matrix.travel_min(from_id, to_id)
                if travel_min is None or travel_min > cap:
                    fits = False
                    break
                total_travel += travel_min

            if fits:
                # place.id keeps the ordering deterministic across identical scores.
                scored.append((total_travel, place.id, place))

        scored.sort(key=lambda row: (row[0], row[1]))
        return [place for _, _, place in scored]

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def _single_day(itinerary: Itinerary) -> Optional[Day]:
        """
        The itinerary's one day, or ``None`` on a fallback-step-2 itinerary.

        MVP itineraries hold exactly one day; an itinerary with none is the
        unscheduled-cards fallback, which has no stops to edit.
        """
        return itinerary.days[0] if itinerary.days else None

    @staticmethod
    def _with_day(itinerary: Itinerary, day: Optional[Day]) -> Optional[Itinerary]:
        """Copy the itinerary with a new day, leaving id, region and chips untouched."""
        if day is None:
            return None
        return itinerary.model_copy(update={"days": [day]})
