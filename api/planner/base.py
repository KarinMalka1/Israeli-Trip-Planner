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

from api.models import Itinerary, Region, Weekday


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
        prompt_he: Optional[str] = None,
        chip: Optional[str] = None,
    ) -> Itinerary:
        """
        Build one itinerary for the given region, leg cap and weekday.

        ``prompt_he`` and ``chip`` are the free-text and preset hooks reserved
        for the LLM planner. The rule-based planner accepts and ignores them so
        the API contract does not have to change when ``llm.py`` lands.

        Implementations must not raise on a sparse region: too few places is a
        normal outcome that the section 4 fallback ladder handles.
        """
        raise NotImplementedError
