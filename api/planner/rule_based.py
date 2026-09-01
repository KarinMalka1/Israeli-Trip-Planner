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
import random
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
from api.models import MAX_LEG_STEPS, Category, Day, DayLength, Itinerary, Place, Region, StartsAt, Weekday
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

# Range for a seed minted when the request doesn't supply one. Just needs to
# be large enough that two back-to-back requests essentially never collide;
# it is not a security value.
_RANDOM_SEED_UPPER_BOUND = 2**31 - 1


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
        seed: Optional[int] = None,
        with_meal: bool = True,
        starts_at: StartsAt = "09:00",
        day_length: DayLength = "long",
    ) -> Itinerary:
        """
        Build an itinerary, degrading through the section 4 ladder as needed.

        ``month`` resolves the section 11 season amendment (``summer_only``
        places excluded outside April-October); ``None`` defaults to the
        current calendar month, resolved by the repository.

        ``prompt_he`` and ``chip`` are accepted and ignored: they belong to the
        future LLM planner, and the contract carries them today so it will not
        need to change when that lands.

        ``with_meal`` and ``day_length`` are both preferences, never hard
        constraints. They combine as an ordered list of attempts, each
        running its own full leg-cap relaxation ladder, tried until one
        succeeds: (meal + band), (meal, no band), (no meal, band), (no meal,
        no band). Meal preference outranks length preference: a
        meal-inclusive day outside the target length band is preferred over a
        meal-free day inside it. Either preference can still need to relax
        the leg cap along the way; ``relaxed_to`` reflects whichever attempt
        actually produced the returned day.
        """
        if seed is None:
            seed = random.randrange(_RANDOM_SEED_UPPER_BOUND)
        rng = random.Random(seed)

        candidates = self._places.candidates(region, weekday, month)
        logger.debug(
            "planning %s/%s cap=%dmin seed=%d with_meal=%s starts_at=%s day_length=%s: "
            "%d candidate places",
            region.value,
            weekday.value,
            max_leg_min,
            seed,
            with_meal,
            starts_at,
            day_length,
            len(candidates),
        )

        target_band = schedule.DAY_LENGTH_BANDS[day_length]
        meal_free_candidates = [c for c in candidates if c.category != Category.MEAL]

        # Priority order: try to satisfy both preferences, then drop length,
        # then drop meal (implies dropping length too, since a meal-free pool
        # is already the fallback), then drop everything but validity.
        attempts: list[tuple[bool, Optional[tuple[int, int]]]] = []
        if with_meal:
            attempts.append((True, target_band))
            attempts.append((True, None))
        attempts.append((False, target_band))
        attempts.append((False, None))

        result: Optional[tuple[Day, int]] = None
        meal_included = False
        length_matched = False

        for require_meal, band in attempts:
            pool = candidates if require_meal else meal_free_candidates
            result = self._search_across_ladder(
                pool, max_leg_min, weekday, rng, starts_at, require_meal, band
            )
            if result is not None:
                meal_included = require_meal
                length_matched = band is not None
                break

        if result is not None:
            day, attempt_cap = result
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
                seed=seed,
                meal_included=meal_included,
                length_matched=length_matched,
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
            seed=seed,
            meal_included=False,
            length_matched=False,
        )

    def _search_across_ladder(
        self,
        candidates: Sequence[Place],
        max_leg_min: int,
        weekday: Weekday,
        rng: random.Random,
        starts_at: str,
        require_meal: bool,
        target_band: Optional[tuple[int, int]],
    ) -> Optional[tuple[Day, int]]:
        """Walk the relaxation ladder once, returning the first (day, cap used) that succeeds."""
        for attempt_cap in self._relaxation_ladder(max_leg_min):
            day = self._search_day(candidates, attempt_cap, weekday, rng, starts_at, require_meal, target_band)
            if day is not None:
                return day, attempt_cap
        return None

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
        rng: random.Random,
        starts_at: str,
        require_meal: bool,
        target_band: Optional[tuple[int, int]],
    ) -> Optional[Day]:
        """
        Find one valid day among ``candidates`` at this leg cap, or ``None``.

        Tries each preferred stop count in turn and, within a count, each start
        place in randomized order. The first complete chain wins — with the
        constraints applied during the walk there is no partial-credit case to
        compare against, so ranking whole days would be effort spent on a
        choice the search has already made.

        The stop-count preference order is jittered per call (lever 3 of 3 for
        variety): still biased toward 4-5 stops on average since that shuffle
        starts from ``PREFERRED_STOP_COUNTS``' own order, but a 3- or 6-stop
        day is no longer permanently deprioritised.

        ``require_meal`` rejects any completed chain with no meal stop — see
        ``_extend``. Combined with ``_meal_is_placeable`` (at most one meal,
        rule 4), this means "require_meal=True" finds a chain with exactly
        one meal stop, never more, never fewer.

        ``target_band``, when given, additionally rejects a completed chain
        whose elapsed time falls outside it — on top of, never instead of,
        the hard rule 10 bound checked unconditionally in ``_extend``.
        """
        if len(candidates) < MIN_STOPS:
            return None

        starts = self._start_order(candidates, weekday, rng, starts_at)
        budget = [self._search_budget]

        stop_counts = list(PREFERRED_STOP_COUNTS)
        rng.shuffle(stop_counts)

        for target_stops in stop_counts:
            if target_stops > len(candidates):
                continue
            for start in starts:
                # Rule 8 (amended): the day begins at starts_at at the first
                # stop, with no inbound leg, so the first arrival is the day
                # start itself. `_start_order` has already dropped anything
                # not open then; this only rejects a same-time lunch (rule 4).
                if not self._meal_is_placeable(start, starts_at, meal_used=False):
                    continue

                found = self._extend(
                    chain=[start],
                    legs=[0],
                    finish_min=schedule.to_minutes(starts_at) + start.duration_min,
                    candidates=candidates,
                    target_stops=target_stops,
                    max_leg_min=max_leg_min,
                    weekday=weekday,
                    budget=budget,
                    rng=rng,
                    starts_at=starts_at,
                    require_meal=require_meal,
                    target_band=target_band,
                )
                if found is not None:
                    chain, legs = found
                    return schedule.build_day(chain, legs, starts_at=starts_at)
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
        rng: random.Random,
        starts_at: str,
        require_meal: bool,
        target_band: Optional[tuple[int, int]],
    ) -> Optional[tuple[list[Place], list[int]]]:
        """
        Depth-first extension of a partial chain, one stop at a time.

        ``finish_min`` is when the last chosen visit ends — the clock the next
        leg departs on. ``budget`` is a one-element list used as a shared
        mutable counter across the whole recursion.

        Returns the completed ``(places, legs)`` pair, or ``None`` if no
        extension of this prefix reaches ``target_stops`` legally.
        """
        elapsed_so_far = finish_min - schedule.to_minutes(starts_at)

        # Rule 10, upper half: a chain already past nine hours cannot be
        # rescued by adding stops, so prune the whole subtree.
        if elapsed_so_far > MAX_DAY_MINUTES:
            return None

        # day_length preference, pruned early rather than only at completion:
        # once the partial chain already exceeds the band's own upper bound,
        # no further stop (every duration and leg is positive) can bring it
        # back inside. Without this, a target_stops value the band can never
        # satisfy (e.g. 4 stops when the band only leaves room for 3) burns
        # the whole search budget fully exploring every such chain to the end
        # before ever trying the stop count that could actually fit — found
        # in practice via a flaky "short" test that kept coming back "long".
        if target_band is not None and elapsed_so_far > target_band[1]:
            return None

        if len(chain) == target_stops:
            # Rule 10, lower half (the hard bound — always enforced,
            # regardless of target_band): only checked on a complete chain,
            # because a short prefix is not a failure — it is simply unfinished.
            if not (MIN_DAY_MINUTES <= elapsed_so_far <= MAX_DAY_MINUTES):
                return None
            # day_length preference: narrows the accepted range further, but
            # only ever inside the hard bound just checked above. The upper
            # half was already pruned early; only the lower half is left.
            if target_band is not None and elapsed_so_far < target_band[0]:
                return None
            if require_meal and not any(place.category == Category.MEAL for place in chain):
                return None
            return chain, legs

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
            rng=rng,
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
                rng=rng,
                starts_at=starts_at,
                require_meal=require_meal,
                target_band=target_band,
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
        rng: random.Random,
    ) -> list[tuple[Place, int, int]]:
        """
        Every legal next stop from ``last``, as ``(place, travel_min, arrive_min)``.

        Applies rules 3, 4, 6, 7 and 11 up front so the recursion only ever
        walks into states that are still valid. Ordering is the quality knob:
        see ``_option_sort_key``. Shuffling before the (stable) sort means
        options that tie on ``_option_sort_key`` come out in random order
        instead of always the same one — lever 2 of 3 for variety.
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

        rng.shuffle(options)
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

        No id tiebreak: ``_next_options`` shuffles before sorting, and Python's
        sort is stable, so options tied on all three preferences keep their
        (already randomized) relative order instead of always resolving to the
        same place alphabetically.
        """
        place, travel_min, arrive_min = option
        arrive_at = schedule.to_hhmm(arrive_min)

        wants_meal_now = (
            place.category == Category.MEAL
            and not meal_used
            and MEAL_WINDOW_START <= arrive_at <= MEAL_WINDOW_END
        )
        repeats_category = place.category == last.category

        return (0 if wants_meal_now else 1, 1 if repeats_category else 0, travel_min)

    def _start_order(
        self,
        candidates: Sequence[Place],
        weekday: Weekday,
        rng: random.Random,
        starts_at: str,
    ) -> list[Place]:
        """
        Every valid first stop, in random order (lever 1 of 3 for variety).

        A prior version always tried the best-connected start first — good for
        search speed, but it meant the same request always returned the same
        itinerary. ``_search_day`` tries every start in this list until one
        completes, so shuffling costs nothing but determinism: a small or
        sparse region still finds a day, just not always via the same anchor.
        Meal places are not filtered out here — a same-time lunch is simply
        rejected a moment later by rule 4's own check in ``_search_day``.

        Filtering against ``starts_at`` (rule 8, amended) is what makes an
        early start actually exclude a place that isn't open yet: a gated
        place whose hours begin at 09:00 can never be the first stop of an
        08:00 day, because it never passes ``visit_fits_opening_hours`` here.
        """
        openable = [
            place
            for place in candidates
            if schedule.visit_fits_opening_hours(place, weekday, starts_at)
        ]
        rng.shuffle(openable)
        return openable
