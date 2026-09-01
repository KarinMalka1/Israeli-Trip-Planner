"""
One-off migration: copy _source into official_url wherever _source is
itself a real page and official_url is still empty.

Run with:

    python -m api.scripts.backfill_official_url

official_url (models.Place) was added after _source, so every place
enriched by the acquisition pipeline already has its real page recorded —
just under the wrong field for the UI to read (StopCard.tsx links name_he
via official_url, never _source, since _source is internal provenance that
happens to sometimes double as a Commons/OSM id rather than a page). This
script exists to backfill that gap once; it is not part of the recurring
pipeline (fetch_osm.py/scrape_parks.py already write official_url directly
going forward) and is not expected to need re-running.

A row is filled only when BOTH:
  * _source is a string starting with "http" — "manual", "openstreetmap",
    an OSM element id, are provenance notes, not pages a user could open;
  * official_url is empty/absent — this never overwrites a value someone
    (or a scraper) already set on purpose.

_source itself is never touched — it stays the provenance record;
official_url is the separate, user-facing field.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from api.models import Place
from api.repository.places import DEFAULT_PLACES_DIR

logger = logging.getLogger(__name__)


def should_backfill(row: dict) -> bool:
    """Whether row["official_url"] should be set from row["_source"]."""
    if row.get("official_url"):
        return False
    source = row.get("_source")
    return isinstance(source, str) and source.startswith("http")


def backfill_row(row: dict) -> dict:
    """row with official_url copied from _source, or row unchanged if should_backfill is False."""
    if not should_backfill(row):
        return row
    updated = dict(row)
    updated["official_url"] = row["_source"]
    return updated


@dataclass
class BackfillResult:
    filled: list[tuple[str, str]]  # (id, name_he)
    left_empty: list[tuple[str, str, Optional[str]]]  # (id, name_he, _source)


def backfill_file(raw: dict) -> tuple[dict, BackfillResult]:
    """Pure: raw file dict -> (updated raw dict, result). Never touches disk."""
    filled: list[tuple[str, str]] = []
    left_empty: list[tuple[str, str, Optional[str]]] = []
    updated_rows: list[dict] = []

    for row in raw.get("places", []):
        if row.get("official_url"):
            updated_rows.append(row)
            continue
        if should_backfill(row):
            new_row = backfill_row(row)
            updated_rows.append(new_row)
            filled.append((new_row["id"], new_row["name_he"]))
        else:
            updated_rows.append(row)
            left_empty.append((row["id"], row["name_he"], row.get("_source")))

    updated_raw = dict(raw)
    updated_raw["places"] = updated_rows
    return updated_raw, BackfillResult(filled=filled, left_empty=left_empty)


def run(places_dir: Path) -> None:
    total_filled: list[tuple[str, str]] = []
    total_left_empty: list[tuple[str, str, Optional[str]]] = []

    for file_path in sorted(places_dir.glob("*.json")):
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        updated_raw, result = backfill_file(raw)

        if result.filled:
            for row in updated_raw["places"]:
                if row.get("official_url"):
                    Place.model_validate(row)  # fail loudly before writing anything
            file_path.write_text(
                json.dumps(updated_raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            logger.info("wrote %s", file_path)

        total_filled.extend(result.filled)
        total_left_empty.extend(result.left_empty)

    print(f"filled: {len(total_filled)}")
    for place_id, name_he in total_filled:
        print(f"  {place_id}: {name_he}")

    print(f"left empty: {len(total_left_empty)}")
    for place_id, name_he, source in total_left_empty:
        print(f"  {place_id}: {name_he}  (_source={source!r})")


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--places-dir", type=Path, default=DEFAULT_PLACES_DIR)
    args = parser.parse_args(argv)
    run(args.places_dir)


if __name__ == "__main__":
    main()
