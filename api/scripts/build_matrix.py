"""
Generate ``data/distance_matrix.json`` from the ``data/places/`` region directory.

Run after every seed change:

    python -m api.scripts.build_matrix

SPEC section 5 rules out live routing APIs, so drive times are estimated
offline: great-circle distance, inflated by a winding factor because roads are
not straight lines, divided by an average speed. That is crude, and it is
crude *once*, at build time, in a file a human can read and correct by hand.
Rule 7's point is that the request path never estimates — not that the estimate
must come from Google.

Legs are computed within a region only. Cross-region itineraries do not exist
(rule 5), so a Metula-to-Eilat cell would be a number nothing may ever use.

Places are loaded through ``PlaceRepository.load()`` (SPEC section 12) rather
than read directly, so the matrix and the API can never disagree about what
the seed contains — the same directory resolution (explicit arg -> $PLACES_DIR
-> default) and the same fail-loudly validation apply here too.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from api.domain import regions
from api.models import MAX_LEG_STEPS, Place
from api.repository.places import PlaceRepository

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_MATRIX_PATH = DATA_DIR / "distance_matrix.json"

EARTH_RADIUS_KM = 6371.0

# Israeli roads between attractions are rarely direct — mountain switchbacks in
# the Galilee, a single road along the Dead Sea. 1.35 is a middling multiplier
# on straight-line distance; raise it if the north reads optimistically.
ROAD_WINDING_FACTOR = 1.35

# Average door-to-door speed including the slow parts: town streets, junctions,
# a car park at each end. Not a highway cruising speed.
AVERAGE_SPEED_KMH = 65.0

# Every leg costs at least this, even between two neighbouring car parks.
# Without it, clustered places produce zero-minute legs and the planner builds
# a physically impossible day.
MIN_LEG_MINUTES = 5


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in kilometres between two WGS84 points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def estimate_drive_minutes(origin: Place, destination: Place) -> int:
    """
    Estimate one leg in whole minutes, rounded up.

    Rounding up rather than to nearest keeps the estimate conservative: a day
    that runs slightly early is a pleasant surprise, one that runs late means a
    site closed before the user arrived.
    """
    straight_km = haversine_km(origin.lat, origin.lng, destination.lat, destination.lng)
    road_km = straight_km * ROAD_WINDING_FACTOR
    minutes = (road_km / AVERAGE_SPEED_KMH) * 60
    return max(MIN_LEG_MINUTES, math.ceil(minutes))


def build_matrix(places: list[Place]) -> dict[str, dict[str, int]]:
    """
    Build the dense symmetric matrix, one block per region.

    Written symmetrically so ``travel_min(a, b)`` and ``travel_min(b, a)`` are
    both plain dict hits — the planner does thousands of these while searching
    and should not have to normalise key order.
    """
    matrix: dict[str, dict[str, int]] = {place.id: {} for place in places}

    for i, origin in enumerate(places):
        for destination in places[i + 1 :]:
            # Rule 5: no cross-region legs, so no cross-region cells.
            if origin.region != destination.region:
                continue
            # A place pending manual coordinate entry (BRIEF_data_pipeline.md
            # v2) has no lat/lng to measure from — leave it out of the matrix
            # entirely rather than inventing a distance.
            if origin.lat is None or destination.lat is None:
                continue
            minutes = estimate_drive_minutes(origin, destination)
            matrix[origin.id][destination.id] = minutes
            matrix[destination.id][origin.id] = minutes

    return matrix


def _print_region_table(places: list[Place], matrix: dict[str, dict[str, int]]) -> None:
    """
    Per-region coverage: place count, plus how many of them have at least one
    same-region neighbour at each leg cap. A stale or misfiled seed shows up
    here as a region with places but zero (or few) reachable neighbours —
    exactly the bug this script was rebuilt to catch (BRIEF section "The bug").
    """
    header = f"{'region':<10}{'places':>8}" + "".join(f"{'<=' + str(cap) + 'min':>10}" for cap in MAX_LEG_STEPS)
    print(header)
    for region in regions.ALL_REGIONS:
        region_places = [place for place in places if place.region == region]
        counts = []
        for cap in MAX_LEG_STEPS:
            reachable = sum(
                1
                for place in region_places
                if any(minutes <= cap for minutes in matrix.get(place.id, {}).values())
            )
            counts.append(reachable)
        row = f"{region.value:<10}{len(region_places):>8}" + "".join(f"{count:>10}" for count in counts)
        print(row)


def main() -> None:
    """Read the seed, build the matrix, write it, and report what it covers."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--places-dir",
        type=Path,
        default=None,
        help="Seed directory (default: $PLACES_DIR, or api/data/places/ — see PlaceRepository.load)",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_MATRIX_PATH)
    args = parser.parse_args()

    places = PlaceRepository.load(args.places_dir).all()
    matrix = build_matrix(places)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(matrix, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    legs = sum(len(row) for row in matrix.values()) // 2
    print(f"wrote {args.out} — {len(places)} places, {legs} legs")

    # An isolated place can never appear in an itinerary, and that is almost
    # always a bad lat/lng or a wrong region rather than genuine remoteness.
    orphans = [place_id for place_id, row in matrix.items() if not row]
    if orphans:
        print(f"warning: {len(orphans)} places have no same-region neighbours: {', '.join(orphans)}")

    _print_region_table(places, matrix)


if __name__ == "__main__":
    main()
