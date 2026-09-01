"""
The place repository (SPEC.md section 8).

JSON-backed for now, behind a narrow interface so swapping in Postgres later
touches nothing but this file. The seed file is the product (section 6), so
loading is strict: a malformed place is an error at startup, never a silently
dropped row that shows up as a thin itinerary weeks later.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path
from typing import Iterable, Optional

from api.domain import regions, schedule
from api.models import Place, Region, Weekday

logger = logging.getLogger(__name__)

# Seed location: one *.json file per region, not one shared file. Overridable
# so tests and local smoke runs can point at a fixture directory without
# touching the real dataset.
DEFAULT_PLACES_DIR = Path(__file__).resolve().parent.parent / "data" / "places"
PLACES_DIR_ENV_VAR = "PLACES_DIR"


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
        Read and validate every ``*.json`` file in the seed directory, concatenated
        in filename order (so ``central.json``, ``north.json``, ``south.json``).

        Resolution order: explicit argument, then ``$PLACES_DIR``, then the
        default directory. One file per region — ``north.json``, ``central.json``,
        ``south.json`` — so two people editing different regions' places never
        collide on the same file (a single ``places.json`` used to guarantee a
        merge conflict the moment both touched the seed in the same session).

        Two things fail loudly rather than silently passing through, since both
        would otherwise surface as a confusing itinerary bug days later instead
        of a startup error naming the exact rows at fault:

          * an entry whose ``region`` doesn't match the file it's in (a copy-paste
            into the wrong file);
          * the same ``(region, name_he)`` appearing in two different files (a
            real duplicate to resolve by hand, not something to silently dedupe).

        Keys beginning with an underscore (``_README``, ``_enums``,
        ``_tag_vocabulary``) are editor notes and are ignored; the same
        convention applies to per-place ``_source`` and ``_verified_on``, which
        Pydantic drops because the model does not declare them.
        """
        resolved = Path(path or os.environ.get(PLACES_DIR_ENV_VAR) or DEFAULT_PLACES_DIR)
        if not resolved.is_dir():
            raise FileNotFoundError(
                f"seed directory not found at {resolved}. "
                f"Create it with one *.json file per region — see api/data/places/."
            )

        places: list[Place] = []
        # (region, name_he) -> the file it was first seen in, so a duplicate
        # across files can name both.
        seen_keys: dict[tuple[str, str], Path] = {}

        for file_path in sorted(resolved.glob("*.json")):
            file_region = file_path.stem
            raw = json.loads(file_path.read_text(encoding="utf-8"))
            rows = raw.get("places", [])

            for index, row in enumerate(rows):
                place_id = row.get("id", f"<row {index}>")
                row_region = row.get("region")

                # Checked before the filename match below: a duplicate is the
                # more actionable problem when both are true at once (a place
                # copy-pasted into a second, wrongly-named file is still first
                # and foremost a duplicate to resolve).
                key = (row_region, row.get("name_he"))
                if key in seen_keys:
                    raise ValueError(
                        f"duplicate place (region={row_region!r}, name_he={row.get('name_he')!r}) "
                        f"in both {seen_keys[key]} and {file_path}"
                    )
                seen_keys[key] = file_path

                if row_region != file_region:
                    raise ValueError(
                        f"invalid place {place_id!r} in {file_path}: region {row_region!r} "
                        f"does not match its file (expected {file_region!r})"
                    )

                try:
                    places.append(Place.model_validate(row))
                except Exception as error:
                    # Fail loudly and name the offending row: a bad seed entry is
                    # a data bug to fix by hand, not something to route around.
                    raise ValueError(f"invalid place {place_id!r} in {file_path}: {error}") from error

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

    def candidates(self, region: Region, weekday: Weekday, month: Optional[int] = None) -> list[Place]:
        """
        The planner's starting set: in-region, open that weekday, shabbat-eligible, in season.

        Applies rules 5, 11 and 12 and the section 11 season amendment at the
        day level, before any time arithmetic happens. Anything filtered out
        here can never appear in an itinerary, so the search never has to
        reconsider it.

        ``month`` defaults to the current calendar month when omitted — a
        remove/swap edit has no request date of its own to carry forward, so
        "now" is the only sensible default for it.
        """
        effective_month = month if month is not None else dt.date.today().month
        return [
            place
            for place in self._by_region.get(region, [])
            if schedule.is_available_on(place, weekday, effective_month)
        ]

    def __len__(self) -> int:
        """Number of seeded places — handy in health checks and tests."""
        return len(self._places)
