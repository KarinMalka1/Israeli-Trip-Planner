"""
The MVP planner: constraint search over the seed, plus the permanent fallback.

There is no scoring model and no LLM here. The seed is ~30 places per region,
so the day is found by depth-first search over chains of stops with rules 2, 4,
6, 9, 10 and 11 applied as pruning conditions rather than as a post-hoc filter.
A chain that violates a rule is never extended, so anything the search returns
is valid by construction — ``validate_itinerary`` then re-checks it, because
"valid by construction" is a claim worth testing rather than trusting.

Search shape, in order:

  1. Try to build a day at the requested leg cap.
  2. Failing that, walk the relaxation ladder upward (20 -> 45 -> 90) and set
     ``relaxed_to`` so the client can toast (fallback step 1).
  3. Failing that, return no day and every place in the region as cards
     (fallback step 2).

The one thing this file must never do is return an empty screen.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from api.domain import schedule
from api.domain.distance import DistanceMatrix
from api.domain.schedule import (
    MAX_DAY_MINUTES,
    MAX_STOPS,
    MEAL_WINDOW_END,
    MEAL_WINDOW_START,
    MIN_DAY_MINUTES,
    MIN_STOPS,
)
from api.models import MAX_LEG_STEPS, Category, Day, Itinerary, Place, Region, Weekday
from api.planner.base import ItineraryPlanner
from api.repository.places import PlaceRepository

logger = logging.getLogger(__name__)

# Stop counts to attempt, in preference order. Four or five stops is the shape
# of a good day out; three is thin but legal; six is the ceiling. Searching for
# an exact size beats accepting the first legal chain, which would always
# return three stops and a short day.
PREFERRED_STOP_COUNTS: tuple[int, ...] = (4, 5, 3, 6)

# Upper bound on chain extensions per planning attempt. The search is
# exponential in principle; in practice the leg cap prunes it hard. The budget
# is what guarantees a bounded response time on a dense region with a 90-minute
# cap, and exhausting it degrades to the next fallback step rather than hanging.
DEFAULT_SEARCH_BUDGET = 40_000


class RuleBasedPlanner(ItineraryPlanner):
    """Deterministic planner over the curated seed and the precomputed matrix."""

    def __init__(
        self,
        places: PlaceRepository,
        matrix: DistanceMatrix,
        search_budget: int = DEFAULT_SEARCH_BUDGET,
    ) -> None:
        """Hold the two data sources. Both are read-only and shared across requests."""
        self._places = places
        self._matrix = matrix
        self._search_budget = search_budget

    # -- Public entry point -----------------------------------------------

    def plan(
        self,
        *,
        itinerary_id: str,
        region: Region,
        max_leg_min: int,
        weekday: Weekday,
        month: Optional[int] = None,
        prompt_he: Optional[str] = None,
        chip: Optional[str] = None,
    ) -> Itinerary:
        """
        Build an itinerary, degrading through the section 4 ladder as needed.

        ``month`` resolves the section 11 season amendment (``summer_only``
        places excluded outside April-October); ``None`` defaults to the
        current calendar month, resolved by the repository.

        ``prompt_he`` and ``chip`` are accepted and ignored: they belong to the
        future LLM planner, and the contract carries them today so it will not
        need to change when that lands.
        """
        candidates = self._places.candidates(region, weekday, month)
        logger.debug(
            "planning %s/%s cap=%dmin: %d candidate places",
            region.value,
            weekday.value,
            max_leg_min,
            len(candidates),
        )

        # Step 0 and step 1 share one loop: the requested cap is simply the
        # first rung of the ladder, and every rung above it is a relaxation.
        for attempt_cap in self._relaxation_ladder(max_leg_min):
            day = self._search_day(candidates, attempt_cap, weekday)
            if day is not None:
                return Itinerary(
                    id=itinerary_id,
                    region=region,
                    max_leg_min=attempt_cap,
                    weekday=weekday,
                    days=[day],
                    places=[],
                    # Only set when we actually had to relax, so the client
                    # toasts exactly once and only when it is true.
                    relaxed_to=attempt_cap if attempt_cap != max_leg_min else None,
                )

        # Fallback step 2: no schedule is possible, so hand back the region's
        # places as unscheduled cards. Note this deliberately returns everything
        # in the region, not just this weekday's candidates — the user asked
        # what is out there, and the client explains why there is no plan.
        logger.info(
            "no day found for %s/%s at any cap; falling back to region cards",
            region.value,
            weekday.value,
        )
        return Itinerary(
            id=itinerary_id,
            region=region,
            max_leg_min=max_leg_min,
            weekday=weekday,
            days=[],
            places=self._places.by_region(region),
            relaxed_to=None,
        )

    # -- Fallback ladder ---------------------------------------------------

    @staticmethod
    def _relaxation_ladder(max_leg_min: int) -> list[int]:
        """
        The caps to try, starting at the requested one and relaxing upward.

        A request for 90 has nothing above it, so it gets a single attempt and
        then drops straight to fallback step 2. Relaxation only ever loosens the
        constraint: we never hand back a stricter day than the user asked for.
        """
        return [step for step in MAX_LEG_STEPS if step >= max_leg_min] or [max_leg_min]

    # -- Search ------------------------------------------------------------

    def _search_day(
        self,
        candidates: Sequence[Place],
        max_leg_min: int,
        weekday: Weekday,
    ) -> Optional[Day]:
        """
        Find one valid day among ``candidates`` at this leg cap, or ``None``.

        Tries each preferred stop count in turn and, within a count, each start
        place in heuristic order. The first complete chain wins — with the
        constraints applied during the walk there is no partial-credit case to
        compare against, so ranking whole days would be effort spent on a
        choice the search has already made.
        """
        if len(candidates) < MIN_STOPS:
            return None

        starts = self._start_order(candidates, max_leg_min, weekday)
        budget = [self._search_budget]

        for target_stops in PREFERRED_STOP_COUNTS:
            if target_stops > len(candidates):
                continue
            for start in starts:
                # Rule 8: the day begins at 09:00 at the first stop, with no
                # inbound leg, so the first arrival is the day start itself.
                # `_start_order` has already dropped anything not open then;
                # this only rejects a 09:00 lunch (rule 4).
                if not self._meal_is_placeable(start, schedule.DAY_START, meal_used=False):
                    continue

                found = self._extend(
                    chain=[start],
                    legs=[0],
                    finish_min=schedule.to_minutes(schedule.DAY_START) + start.duration_min,
                    candidates=candidates,
                    target_stops=target_stops,
                    max_leg_min=max_leg_min,
                    weekday=weekday,
                    budget=budget,
                )
                if found is not None:
                    chain, legs = found
                    return schedule.build_day(chain, legs)
                if budget[0] <= 0:
                    logger.warning("search budget exhausted at cap=%dmin", max_leg_min)
                    return None
        return None

    def _extend(
        self,
        chain: list[Place],
        legs: list[int],
        finish_min: int,
        candidates: Sequence[Place],
        target_stops: int,
        max_leg_min: int,
        weekday: Weekday,
        budget: list[int],
    ) -> Optional[tuple[list[Place], list[int]]]:
        """
        Depth-first extension of a partial chain, one stop at a time.

        ``finish_min`` is when the last chosen visit ends — the clock the next
        leg departs on. ``budget`` is a one-element list used as a shared
        mutable counter across the whole recursion.

        Returns the completed ``(places, legs)`` pair, or ``None`` if no
        extension of this prefix reaches ``target_stops`` legally.
        """
        # Rule 10, upper half: a chain already past nine hours cannot be
        # rescued by adding stops, so prune the whole subtree.
        if finish_min - schedule.to_minutes(schedule.DAY_START) > MAX_DAY_MINUTES:
            return None

        if len(chain) == target_stops:
            elapsed = finish_min - schedule.to_minutes(schedule.DAY_START)
            # Rule 10, lower half: only checked on a complete chain, because a
            # short prefix is not a failure — it is simply unfinished.
            if MIN_DAY_MINUTES <= elapsed <= MAX_DAY_MINUTES:
                return chain, legs
            return None

        chosen = {place.id for place in chain}
        meal_used = any(place.category == Category.MEAL for place in chain)

        for next_place, travel_min, arrive_min in self._next_options(
            last=chain[-1],
            depart_min=finish_min,
            candidates=candidates,
            chosen=chosen,
            meal_used=meal_used,
            max_leg_min=max_leg_min,
            weekday=weekday,
        ):
            budget[0] -= 1
            if budget[0] <= 0:
                return None

            found = self._extend(
                chain=chain + [next_place],
                legs=legs + [travel_min],
                finish_min=arrive_min + next_place.duration_min,
                candidates=candidates,
                target_stops=target_stops,
                max_leg_min=max_leg_min,
                weekday=weekday,
                budget=budget,
            )
            if found is not None:
                return found
        return None

    def _next_options(
        self,
        last: Place,
        depart_min: int,
        candidates: Sequence[Place],
        chosen: set[str],
        meal_used: bool,
        max_leg_min: int,
        weekday: Weekday,
    ) -> list[tuple[Place, int, int]]:
        """
        Every legal next stop from ``last``, as ``(place, travel_min, arrive_min)``.

        Applies rules 3, 4, 6, 7 and 11 up front so the recursion only ever
        walks into states that are still valid. Ordering is the quality knob:
        see ``_option_sort_key``.
        """
        options: list[tuple[Place, int, int]] = []

        for place in candidates:
            # Rule 3: no place twice.
            if place.id in chosen:
                continue

            # Rules 6 and 7: the travel time comes from the matrix and must fit
            # under the cap. A missing pair is unreachable, never zero.
            travel_min = self._matrix.travel_min(last.id, place.id)
            if travel_min is None or travel_min > max_leg_min:
                continue

            arrive_min = depart_min + travel_min
            arrive_at = schedule.to_hhmm(arrive_min)

            # Rule 11 (strengthened): the whole visit must fit inside the
            # opening window, not merely the moment of arrival.
            if not schedule.visit_fits_opening_hours(place, weekday, arrive_at):
                continue

            # Rule 4: at most one meal, and only inside the midday window.
            if not self._meal_is_placeable(place, arrive_at, meal_used):
                continue

            options.append((place, travel_min, arrive_min))

        options.sort(key=lambda option: self._option_sort_key(option, last, meal_used, depart_min))
        return options

    @staticmethod
    def _meal_is_placeable(place: Place, arrive_at: str, meal_used: bool) -> bool:
        """
        Rule 4 as a predicate on a single candidate stop.

        Non-meal places always pass — the rule caps meals, it does not require
        one, so a day with no meal stop is perfectly valid.
        """
        if place.category != Category.MEAL:
            return True
        if meal_used:
            return False
        return MEAL_WINDOW_START <= arrive_at <= MEAL_WINDOW_END

    @staticmethod
    def _option_sort_key(
        option: tuple[Place, int, int],
        last: Place,
        meal_used: bool,
        depart_min: int,
    ) -> tuple:
        """
        Order candidate next-stops so the first chain found is a good one.

        Three preferences, in priority order:

          1. Take the meal when we are inside the midday window and have not
             eaten. Deferring it usually means leaving the window and losing it.
          2. Prefer a different category from the previous stop, so a day is
             not three museums in a row.
          3. Prefer the shorter drive, which keeps the day compact and leaves
             room under the nine-hour ceiling for another stop.

        ``place.id`` breaks remaining ties, so identical inputs always produce
        the identical itinerary — a property worth having when a user reloads.
        """
        place, travel_min, arrive_min = option
        arrive_at = schedule.to_hhmm(arrive_min)

        wants_meal_now = (
            place.category == Category.MEAL
            and not meal_used
            and MEAL_WINDOW_START <= arrive_at <= MEAL_WINDOW_END
        )
        repeats_category = place.category == last.category

        return (0 if wants_meal_now else 1, 1 if repeats_category else 0, travel_min, place.id)

    def _start_order(
        self,
        candidates: Sequence[Place],
        max_leg_min: int,
        weekday: Weekday,
    ) -> list[Place]:
        """
        Order the possible first stops, best-looking start first.

        A start with many neighbours inside the cap is far more likely to lead
        to a complete chain, so trying those first usually finds a day on the
        first or second attempt instead of backtracking through dead ends. Meal
        places are pushed to the back: a 09:00 lunch is legal only in the sense
        that rule 4 would reject it anyway, and it wastes a search branch.
        """

        def sort_key(place: Place) -> tuple:
            neighbours = self._matrix.reachable_from(place.id, max_leg_min)
            reachable = sum(
                1
                for other in candidates
                if other.id != place.id and other.id in neighbours
            )
            is_meal = place.category == Category.MEAL
            # Negative count sorts descending without needing reverse=True,
            # which would also reverse the id tiebreak.
            return (1 if is_meal else 0, -reachable, place.id)

        openable = [
            place
            for place in candidates
            if schedule.visit_fits_opening_hours(place, weekday, schedule.DAY_START)
        ]
        return sorted(openable, key=sort_key)
