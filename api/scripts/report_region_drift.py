"""
Report every place in places.json whose region would change under the
current classification rule (scrape_parks.region_from_coordinates, plus any
api/domain/regions.py override) versus what is actually stored.

Read-only: prints a diff table and writes nothing to places.json. Region is a
stored, curatable field (SPEC.md section 6) — the coordinate rule is only
ever a default for a name nobody has reviewed by hand. This script exists so
a rule change (like the inland-valley longitude split) can be reviewed
place-by-place before anyone edits the seed to match it.

Run with:

    python -m api.scripts.report_region_drift
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from api.domain import regions
from api.scripts import fetch_osm
from api.scripts.scrape_parks import region_from_coordinates

DEFAULT_PLACES_PATH = fetch_osm.DEFAULT_PLACES_PATH


def recomputed_region(name_he: str, lat: Optional[float], lng: Optional[float]) -> Optional[str]:
    """
    The region the current rule would assign, or ``None`` if there is
    nothing to recompute from — no override and no coordinate. A place
    resolved from parks.org.il's trip-area taxonomy (no lat/lng at all) can't
    be recomputed here; re-run scrape_parks.py against it instead.
    """
    override = regions.region_override(name_he)
    if override is not None:
        return override.value
    if lat is None or lng is None:
        return None
    return region_from_coordinates(lat, lng)


def find_region_drift(places: list[dict]) -> list[tuple[str, str, str, str]]:
    """(id, name_he, stored_region, recomputed_region) for every row that would change."""
    drift = []
    for row in places:
        recomputed = recomputed_region(row["name_he"], row.get("lat"), row.get("lng"))
        if recomputed is not None and recomputed != row["region"]:
            drift.append((row["id"], row["name_he"], row["region"], recomputed))
    return drift


def load_places(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found")
    return json.loads(path.read_text(encoding="utf-8")).get("places", [])


def run(places_path: Path) -> None:
    places = load_places(places_path)
    drift = find_region_drift(places)

    if not drift:
        print("no region changes")
        return

    print(f"{len(drift)} place(s) would change region:")
    for place_id, name_he, old_region, new_region in drift:
        print(f"  {place_id}: {name_he} — {old_region} -> {new_region}")


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--places", type=Path, default=DEFAULT_PLACES_PATH)
    args = parser.parse_args(argv)
    run(args.places)


if __name__ == "__main__":
    main()
