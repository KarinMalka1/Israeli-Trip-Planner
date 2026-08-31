"""
The place repository (SPEC.md section 8).

JSON-backed for now, behind a narrow interface so swapping in Postgres later
touches nothing but this file. The seed file is the product (section 6), so
loading is strict: a malformed place is an error at startup, never a silently
dropped row that shows up as a thin itinerary weeks later.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Iterable, Optional

from api.domain import regions, schedule
from api.models import Place, Region, Weekday

logger = logging.getLogger(__name__)

# Seed location. Overridable so tests and local smoke runs can point at a
# fixture without touching the real dataset.
DEFAULT_PLACES_PATH = Path(__file__).resolve().parent.parent / "data" / "places.json"
PLACES_PATH_ENV_VAR = "PLACES_FILE"


class PlaceRepository:
    """
    In-memory index over the curated seed dataset.

    The whole dataset is ~100 places, so it is loaded once at startup and held
    in full. Every lookup below is a dict hit or a scan over a list that fits
    in a cache line's worth of pages — there is nothing to optimise here.
    """

    def __init__(self, places: Iterable[Place]) -> None:
        """Build the id index and the per-region buckets the planner filters on."""
        self._places: list[Place] = list(places)
        self._by_id: dict[str, Place] = {place.id: place for place in self._places}
        self._by_region: dict[Region, list[Place]] = {
            region: regions.filter_by_region(self._places, region)
            for region in regions.ALL_REGIONS
        }

    # -- Loading ----------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str | None = None) -> "PlaceRepository":
        """
        Read and validate the seed file.

        Resolution order: explicit argument, then ``$PLACES_FILE``, then the
        default path. Keys beginning with an underscore (``_README``, ``_enums``,
        ``_tag_vocabulary``) are editor notes and are ignored; the same
        convention applies to per-place ``_source`` and ``_verified_on``, which
        Pydantic drops because the model does not declare them.
        """
        resolved = Path(path or os.environ.get(PLACES_PATH_ENV_VAR) or DEFAULT_PLACES_PATH)
        if not resolved.exists():
            raise FileNotFoundError(
                f"seed dataset not found at {resolved}. "
                f"Copy places.template.json to {DEFAULT_PLACES_PATH} to start."
            )

        raw = json.loads(resolved.read_text(encoding="utf-8"))
        rows = raw.get("places", [])

        places: list[Place] = []
        for index, row in enumerate(rows):
            try:
                places.append(Place.model_validate(row))
            except Exception as error:
                # Fail loudly and name the offending row: a bad seed entry is a
                # data bug to fix by hand, not something to route around.
                place_id = row.get("id", f"<row {index}>")
                raise ValueError(f"invalid place {place_id!r} in {resolved}: {error}") from error

        repository = cls(places)
        repository._warn_on_suspect_rows()
        logger.info("loaded %d places from %s", len(places), resolved)
        return repository

    def _warn_on_suspect_rows(self) -> None:
        """
        Log seed problems that are wrong but not fatal.

        Duplicate ids would silently shadow one another in the index, and a
        latitude far outside its region's band usually means a mistyped
        ``region``. Neither should stop the server from booting, and both should
        be impossible to miss in the logs.
        """
        seen: set[str] = set()
        for place in self._places:
            if place.id in seen:
                logger.warning("duplicate place id in seed: %s", place.id)
            seen.add(place.id)
            if regions.latitude_looks_wrong(place):
                logger.warning(
                    "place %s is marked %s but sits at lat %.4f, outside that band",
                    place.id,
                    place.region.value,
                    place.lat,
                )

    # -- Queries ----------------------------------------------------------

    def all(self) -> list[Place]:
        """Every place in the seed, in file order. Copied so callers cannot mutate the index."""
        return list(self._places)

    def get(self, place_id: str) -> Optional[Place]:
        """One place by id, or ``None``. Rule 1's gate: an unknown id resolves to nothing."""
        return self._by_id.get(place_id)

    def require(self, place_id: str) -> Place:
        """Same as ``get`` but raises — for paths where an unknown id is a caller bug."""
        place = self._by_id.get(place_id)
        if place is None:
            raise KeyError(f"unknown place_id: {place_id!r}")
        return place

    def known_ids(self) -> set[str]:
        """The id set the schedule validator checks rule 1 against."""
        return set(self._by_id)

    def by_region(self, region: Region) -> list[Place]:
        """
        All places in one region (rule 5).

        This backs ``GET /api/places?region=``, which exists so the web side can
        render real cards before the planner is finished.
        """
        return list(self._by_region.get(region, []))

    def candidates(self, region: Region, weekday: Weekday) -> list[Place]:
        """
        The planner's starting set: in-region, open that weekday, shabbat-eligible.

        Applies rules 5, 11 and 12 at the day level, before any time arithmetic
        happens. Anything filtered out here can never appear in an itinerary, so
        the search never has to reconsider it.
        """
        return [
            place
            for place in self._by_region.get(region, [])
            if schedule.is_available_on(place, weekday)
        ]

    def __len__(self) -> int:
        """Number of seeded places — handy in health checks and tests."""
        return len(self._places)
