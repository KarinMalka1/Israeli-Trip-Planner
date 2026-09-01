"""
The planner interface (SPEC.md section 8).

One method, so ``rule_based.py`` today and ``llm.py`` later are drop-in
substitutes. The interface takes the two user controls and the weekday and
returns a complete ``Itinerary`` — including the section 4 fallbacks, which are
part of planning rather than something the route layer bolts on afterwards.

The contract every implementation owes the caller:

  * The returned itinerary is valid per rules 1-13, or it carries no day at
    all and a non-empty ``places`` list instead (fallback step 2).
  * It never returns ``days: []`` together with ``places: []``. That is the
    forbidden empty state.
  * It never invents a place, an opening hour or a travel time. Places come
    from the repository, travel times from the precomputed matrix.

An LLM implementation is bound by all three. It may choose and order stops;
it may not do the arithmetic or supply the data.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from api.models import DayLength, Itinerary, Region, StartsAt, Weekday


class ItineraryPlanner(ABC):
    """Base class for anything that turns the two chips into a day."""

    @abstractmethod
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
        Build one itinerary for the given region, leg cap and weekday.

        ``month`` (1-12) resolves the section 11 season amendment; ``None``
        defaults to the current calendar month, since the request carries a
        weekday but no date.

        ``prompt_he`` and ``chip`` are the free-text and preset hooks reserved
        for the LLM planner. The rule-based planner accepts and ignores them so
        the API contract does not have to change when ``llm.py`` lands.

        ``seed`` picks among equally-valid options so repeating a request need
        not return the same day twice; omitting it means "pick one for me."
        Randomness only ever chooses between valid options — it never relaxes
        a rule — and the seed actually used is always returned on the
        ``Itinerary`` so a specific day can be reproduced.

        ``with_meal`` is a preference, not a hard constraint: True asks the
        implementation to prefer a day with exactly one meal stop (rule 4)
        over one without, but a valid meal-free day is still a success when no
        meal fits — never a reason to fail down to the section 4 fallback.
        False excludes meal places from consideration entirely. Either way,
        ``Itinerary.meal_included`` reports what actually happened.

        ``starts_at`` (rule 8, amended) is when the first stop arrives — the
        user picks this now instead of a fixed 09:00. ``ends_at`` is still
        always computed server-side; the user never picks that.

        ``day_length`` (rule 10, amended) is a preference for a band inside
        the hard 4-9h bound (domain/schedule.DAY_LENGTH_BANDS) — "short"
        targets 4.0-5.5h, "long" targets 6.5-9.0h. The hard bound itself never
        relaxes: implementations return the closest valid day when no chain
        lands in the target band (e.g. a late ``starts_at`` with "long" is
        often impossible once opening hours bite) and report that via
        ``Itinerary.length_matched``, never by failing down to fallback step 2.

        Implementations must not raise on a sparse region: too few places is a
        normal outcome that the section 4 fallback ladder handles.
        """
        raise NotImplementedError
