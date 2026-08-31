"""
Phase 2 (BRIEF_data_acquisition.md): curate free, open-access places from
fetch_osm.py's --discover candidates into seed_names.json.

Run after (re-)discovering with the extended tag set:

    python -m api.scripts.fetch_osm --discover --all --refresh
    python -m api.scripts.curate_osm_open

These are ``access: "open"`` places — no gate, no hours, no verification
bottleneck. A candidate qualifies only if it carries an actual ``wikipedia``
tag — nothing else counts, and a bare ``wikidata`` tag with no article does
not count either. Two earlier, looser versions were tried and rejected on
real data: accepting a large footprint or route membership let through ~900
places (reservoirs, unnamed trail segments); accepting any ``wikidata`` tag
still yielded 445, mostly ``natural=spring`` entries tagged by a WikiProject
that stubbed out nearly every named spring in Israel — not a real notability
signal. An actual Wikipedia article is the narrowest signal that still means
"a place a person has heard of," reusing the field ``discover_region``
already computed rather than re-deriving it from raw OSM tags this script
never sees.

Two things need handling beyond what a candidate record already carries:

  * ``leisure=nature_reserve`` is unconditionally "gated" under
    ``fetch_osm.classify_access`` — that heuristic is tuned for
    enrichment's default guess, and a reserve with a real gate is the
    common case. Phase 2 wants exactly the complementary subset: reserves
    with NO fee tag at all. This script reads the candidate's raw ``fee``
    field for that, not ``guessed_access``.
  * ``route=hiking`` candidates carry a relation's bbox midpoint as their
    coordinate (what ``discover_region`` uses for everything else) — usually
    a hillside with no parking. This script re-queries Overpass per
    candidate for the relation's own members and uses the first member
    way's first node as the trailhead instead. Best-effort: it does not
    check a member's "role" (e.g. "backward") against the way's own node
    order, so an occasional trailhead may come out at the wrong end of a
    route that runs opposite to its member order.

Writes only api/data/seed_names.json, deduped against existing entries by
(region, name_he) — never places.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from api.scripts import fetch_osm

logger = logging.getLogger(__name__)

DEFAULT_CANDIDATES_PATH = fetch_osm.DEFAULT_CANDIDATES_PATH
DEFAULT_SEED_NAMES_PATH = fetch_osm.DEFAULT_SEED_NAMES_PATH

# The specific OSM tags Phase 2 curates. Several of these collapse onto the
# same guessed_category (NATURE) in candidates.json, which is exactly why
# discover_region records `matched_tag` — this script needs to know which
# one it actually is, not just the category it was filed under.
PHASE_2_TAGS = {
    "natural=spring",
    "natural=water",
    "waterway=waterfall",
    "natural=beach",
    "leisure=nature_reserve",
    "route=hiking",
    "tourism=viewpoint",
}

CATEGORY_BY_MATCHED_TAG = {
    "natural=spring": "nature",
    "natural=water": "nature",
    "waterway=waterfall": "nature",
    "natural=beach": "nature",
    "leisure=nature_reserve": "nature",
    "route=hiking": "hike",
    "tourism=viewpoint": "viewpoint",
}


def load_candidates(path: Path) -> list[dict]:
    """Read fetch_osm.py --discover's output."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `python -m api.scripts.fetch_osm --discover --all` first"
        )
    return json.loads(path.read_text(encoding="utf-8")).get("candidates", [])


def is_significant_for_curation(candidate: dict) -> bool:
    """
    Phase 2's significance bar: an actual Wikipedia article, full stop.

    A first version accepted a large footprint or membership in a named
    hiking route; both turned out to be noise (a hiking relation's bbox is
    huge almost regardless of the trail's real notability, and "sits on a
    named route" let through any spring a route happened to pass near). A
    second version accepted a bare `wikidata` tag too, which still yielded
    445 places against a 40-60 target — most from `natural=spring`, whose
    `wikidata` tags trace to a WikiProject that catalogued nearly every named
    spring in Israel with a stub, not real trip-worthy notability. Requiring
    a real Wikipedia article (not just a linked wikidata ID) is the signal
    that's actually specific to "a place a person has heard of." route=hiking
    gets no exemption: a route relation needs its own article like everything
    else, even though discover_region already required it to have a Hebrew
    name.
    """
    return bool(candidate["has_wikipedia"])


def is_open_nature_reserve(candidate: dict) -> bool:
    """A nature_reserve candidate qualifies for Phase 2 only without an explicit fee tag."""
    return candidate["fee"] != "yes"


# --------------------------------------------------------------------------
# route=hiking: resolve the trailhead, not the bbox midpoint
# --------------------------------------------------------------------------


def _route_members_batch_query(osm_ids: list[int]) -> str:
    """Fetch many relations plus their immediate members (ways get full geometry) in one call."""
    ids_str = ",".join(str(i) for i in osm_ids)
    return f"[out:json][timeout:180];\nrelation(id:{ids_str});\n(._;>;);\nout geom;\n"


def _first_way_or_node_coordinate(
    relation: dict, ways_by_id: dict[int, dict], nodes_by_id: dict[int, dict]
) -> Optional[tuple[float, float]]:
    """The relation's own trailhead heuristic, given an already-parsed element index."""
    for member in relation.get("members", []):
        if member.get("type") == "way":
            way = ways_by_id.get(member.get("ref"))
            geometry = way.get("geometry") if way else None
            if geometry:
                first = geometry[0]
                return first["lat"], first["lon"]
        elif member.get("type") == "node":
            node = nodes_by_id.get(member.get("ref"))
            if node and node.get("lat") is not None:
                return node["lat"], node["lon"]
    return None


def resolve_hiking_route_starts(
    osm_ids: list[int], *, refresh: bool = False
) -> dict[int, Optional[tuple[float, float]]]:
    """
    Trailhead coordinates for many hiking-route relations, in ONE Overpass call.

    A trailhead is the first node of the first member (in the relation's own
    member order), preferring a way member's first node and falling back to
    a directly-referenced node member — a best-effort heuristic, not a
    directional analysis of the route (see the module docstring).

    Batched deliberately: an earlier per-relation implementation issued one
    live query per candidate — 305 of them in a real run — which is not how
    Overpass is meant to be used at that scale and repeatedly tripped its
    rate limit. Overpass supports ``relation(id:1,2,3,...)`` directly, so
    every route in a region resolves in a single round trip.

    Cached by a hash of the sorted id list. A network failure logs and
    returns an empty mapping rather than raising — every route then falls
    back to its candidate's bbox midpoint instead of losing the whole
    curation batch over one bad request.
    """
    if not osm_ids:
        return {}

    cache_key = hashlib.sha1(",".join(str(i) for i in sorted(osm_ids)).encode()).hexdigest()[:16]
    cache_file = fetch_osm.CACHE_DIR / f"overpass_route_starts_{cache_key}.json"
    if cache_file.exists() and not refresh:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        try:
            payload = fetch_osm.overpass_post(_route_members_batch_query(osm_ids))
        except Exception:
            logger.warning("batch trailhead query failed for %d relations", len(osm_ids), exc_info=True)
            return {}
        fetch_osm.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    elements = payload.get("elements", [])
    relations_by_id = {e["id"]: e for e in elements if e.get("type") == "relation"}
    ways_by_id = {e["id"]: e for e in elements if e.get("type") == "way"}
    nodes_by_id = {e["id"]: e for e in elements if e.get("type") == "node"}

    return {
        osm_id: (
            _first_way_or_node_coordinate(relations_by_id[osm_id], ways_by_id, nodes_by_id)
            if osm_id in relations_by_id
            else None
        )
        for osm_id in osm_ids
    }


# --------------------------------------------------------------------------
# Curation
# --------------------------------------------------------------------------


def curate(
    candidates: list[dict],
    *,
    refresh: bool = False,
) -> tuple[list[dict], dict[str, int]]:
    """
    Filter candidates into seed_names.json entries.

    Two passes: first accept/reject every candidate on tag, fee and
    significance alone (no network); then resolve every accepted
    route=hiking candidate's trailhead in a single batched Overpass call,
    rather than one call per candidate — see resolve_hiking_route_starts.
    """
    stats = {"considered": 0, "gated_reserve": 0, "not_significant": 0, "accepted": 0}

    accepted: list[dict] = []
    for candidate in candidates:
        matched_tag = candidate.get("matched_tag")
        if matched_tag not in PHASE_2_TAGS:
            continue
        stats["considered"] += 1

        if matched_tag == "leisure=nature_reserve" and not is_open_nature_reserve(candidate):
            stats["gated_reserve"] += 1
            continue

        if not is_significant_for_curation(candidate):
            stats["not_significant"] += 1
            continue

        accepted.append(candidate)

    hiking_ids = [c["_osm_id"] for c in accepted if c.get("matched_tag") == "route=hiking"]
    trailheads = resolve_hiking_route_starts(hiking_ids, refresh=refresh)

    entries: list[dict] = []
    for candidate in accepted:
        matched_tag = candidate["matched_tag"]
        lat, lng = candidate["lat"], candidate["lng"]
        if matched_tag == "route=hiking":
            start = trailheads.get(candidate["_osm_id"])
            if start is not None:
                lat, lng = start
            else:
                logger.warning(
                    "could not resolve a trailhead for %r (osm relation %s); keeping bbox midpoint",
                    candidate["name_he"], candidate["_osm_id"],
                )

        entries.append(
            {
                "name_he": candidate["name_he"],
                "region": candidate["region"],
                "category": CATEGORY_BY_MATCHED_TAG[matched_tag],
                "access": "open",
                "lat": lat,
                "lng": lng,
                "tags": candidate["tags"],
                "source": f"osm:{candidate['_osm_type']}/{candidate['_osm_id']}",
            }
        )
        stats["accepted"] += 1

    return entries, stats


def load_seed_names_raw(path: Path) -> list[dict]:
    """Read seed_names.json as plain dicts (not validated SeedName models) for dedup/merge."""
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} must be a JSON array")
    return raw


def merge_into_seed_names(existing: list[dict], new_entries: list[dict]) -> tuple[list[dict], int]:
    """Append entries not already present, matched by (region, name_he)."""
    existing_keys = {(row["region"], row["name_he"]) for row in existing}
    added = [e for e in new_entries if (e["region"], e["name_he"]) not in existing_keys]
    return existing + added, len(added)


def run(
    *,
    candidates_path: Path,
    seed_names_path: Path,
    refresh: bool,
) -> None:
    candidates = load_candidates(candidates_path)
    entries, stats = curate(candidates, refresh=refresh)

    existing = load_seed_names_raw(seed_names_path)
    merged, added_count = merge_into_seed_names(existing, entries)

    seed_names_path.parent.mkdir(parents=True, exist_ok=True)
    seed_names_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

    print(
        f"considered={stats['considered']} gated_reserve_excluded={stats['gated_reserve']} "
        f"not_significant={stats['not_significant']} accepted={stats['accepted']}"
    )
    print(f"wrote {seed_names_path} — {added_count} new entries added, {len(merged)} total")


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES_PATH)
    parser.add_argument("--seed-names", type=Path, default=DEFAULT_SEED_NAMES_PATH)
    parser.add_argument("--refresh", action="store_true", help="bypass the route-start cache")
    args = parser.parse_args(argv)

    run(
        candidates_path=args.candidates,
        seed_names_path=args.seed_names,
        refresh=args.refresh,
    )


if __name__ == "__main__":
    main()
