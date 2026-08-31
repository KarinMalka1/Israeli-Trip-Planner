"""
Resolve api/data/seed_names.json into api/data/places.json using OpenStreetMap.

Run with:

    python -m api.scripts.fetch_osm                  # enrich every seed name
    python -m api.scripts.fetch_osm --region north    # enrich just one region
    python -m api.scripts.fetch_osm --discover --region north --limit 40
    python -m api.scripts.fetch_osm --discover --all

An offline batch tool (BRIEF_data_pipeline.md). Never imported by the running
API.

This is v2 of the script, restructured from discovery to enrichment after a
live run on v1 surfaced a structural problem: bulk category-tag queries over
a whole region bbox mostly return obscure, low-value OSM noise (tels, gates,
minor nodes), with almost none of it carrying real opening hours. The human
already knows which ~100 places belong in the seed; OSM's job is to fill in
facts about a place someone has already named, not to nominate places itself.

  * ``api/data/seed_names.json`` is now the master list — a hand-curated JSON
    array of ``{name_he, region, category, access}``, populated by hand from
    official sources. ``region``/``category``/``access`` come from this file
    and NEVER from OSM: a bbox only narrows where to search for a name, it
    never assigns a region.
  * The default mode enriches that list: for each entry, search OSM by name
    within its region's bbox. Exactly one match fills lat/lng/tags/hours.
    Zero or multiple matches write the place anyway, with lat/lng left
    ``null`` and a log line asking for manual coordinate entry — an
    unresolved place is still worth having in the seed with a Hebrew name
    and a category, not something to silently drop.
  * ``--discover`` is a separate, secondary mode that keeps the old bulk
    category-tag scan, now solely for surfacing candidate names a human
    might have missed. It writes ``api/data/candidates.json`` and must never
    touch ``places.json`` — a candidate has not been curated yet.

Two more divergences from a literal reading of the original brief, carried
over from v1 and still true here:

  * Overpass's ``out bb;`` mode, not ``out center;`` — see ``element_area_m2``.
  * A place id slugs ``name:en``/``int_name`` when OSM has one, and falls
    back to a short hash of ``name_he`` otherwise (never the OSM element id,
    since an unresolved entry — zero/multiple matches — has no element to
    key off). An id is assigned once, on first creation, and never changes
    on a later re-run even if that run finds a nicer name — see
    ``merge_places``, which never touches ``id`` after creation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests
from pydantic import BaseModel, Field as PydanticField

from api.models import AccessType, Category, DescriptionSource, Place, Region, Weekday
from api.scripts.build_matrix import haversine_km

logger = logging.getLogger(__name__)

OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
USER_AGENT = "IsraeliDayTripPlanner-fetch_osm/0.2 (offline seed data pipeline)"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_DIR = DATA_DIR / ".cache"
DEFAULT_PLACES_PATH = DATA_DIR / "places.json"
DEFAULT_SEED_NAMES_PATH = DATA_DIR / "seed_names.json"
DEFAULT_CANDIDATES_PATH = DATA_DIR / "candidates.json"

# Query bbox per region: (south, west, north, east). These are search-area
# filters only — region/category/access always come from seed_names.json,
# never from which bbox a search happened to run in.
#
# central's south bound and south's north bound both sit at 31.55, touching
# rather than overlapping. They were previously 31.40/31.40 (central going
# too far south), which put the Dead Sea — Ein Gedi included, at lat
# ~31.4666 — inside central's search area, so a name search for an
# Ein Gedi-area place run under region="south" would find nothing there and
# incorrectly conclude "unresolved". Both bounds move together so the seam
# has no gap and no overlap.
REGION_BBOXES: dict[Region, tuple[float, float, float, float]] = {
    Region.NORTH: (32.60, 34.90, 33.35, 35.90),
    Region.CENTRAL: (31.55, 34.50, 32.60, 35.55),
    Region.SOUTH: (29.45, 34.20, 31.55, 35.50),
}

MIN_NAME_LENGTH = 3
DEDUP_DISTANCE_M = 150

# --- Discovery-only constants (used by --discover, never by enrichment) ---

# (key, value) pairs bulk-queried in discover mode (BRIEF_data_pipeline.md,
# extended by BRIEF_data_acquisition.md Phase 2 with natural=water and
# route=hiking). route=hiking is a relation-only tag in real OSM data — the
# node/way clauses the generic query builder still emits for it just come
# back empty, which costs nothing but a slightly larger query.
OVERPASS_DISCOVER_TAGS: list[tuple[str, str]] = [
    ("tourism", "museum"),
    ("tourism", "attraction"),
    ("tourism", "viewpoint"),
    ("tourism", "zoo"),
    ("tourism", "aquarium"),
    ("historic", "archaeological_site"),
    ("historic", "ruins"),
    ("historic", "castle"),
    ("leisure", "park"),
    ("leisure", "nature_reserve"),
    ("natural", "spring"),
    ("natural", "cave_entrance"),
    ("natural", "beach"),
    ("natural", "water"),
    ("waterway", "waterfall"),
    ("route", "hiking"),
]

# leisure=park and tourism=attraction match huge numbers of minor OSM
# entries in Israel (neighbourhood gardens, single benches). In discover
# mode only, these two tags additionally require a significance signal.
# Irrelevant to enrichment: a seed_names.json entry was already curated by a
# human, so it never needs a significance check.
LOW_SIGNAL_TAGS = {("leisure", "park"), ("tourism", "attraction")}
SIGNIFICANT_AREA_M2 = 20_000  # ~2 hectares — a genuine city/regional park

# Category resolution order for discover mode. A list, not a dict, because
# priority is the whole point: an element carrying two of these tags at once
# (a nature reserve that is also an archaeological site, common in Israel)
# must resolve deterministically, and that ordering has to be visible data,
# not an accident of dict-literal or insertion order. First match wins.
# Order: museum/zoo/aquarium, then viewpoint, then the nature tags, then the
# historic tags.
CATEGORY_PRIORITY: list[tuple[str, str, Category]] = [
    ("tourism", "museum", Category.MUSEUM),
    ("tourism", "zoo", Category.MUSEUM),
    ("tourism", "aquarium", Category.MUSEUM),
    ("tourism", "viewpoint", Category.VIEWPOINT),
    ("leisure", "nature_reserve", Category.NATURE),
    ("leisure", "park", Category.NATURE),
    ("natural", "spring", Category.NATURE),
    ("natural", "cave_entrance", Category.NATURE),
    ("natural", "beach", Category.NATURE),
    ("natural", "water", Category.NATURE),
    ("waterway", "waterfall", Category.NATURE),
    ("historic", "archaeological_site", Category.HISTORIC),
    ("historic", "ruins", Category.HISTORIC),
    ("historic", "castle", Category.HISTORIC),
    ("route", "hiking", Category.HIKE),
]

# tourism=attraction alone says nothing about what kind of place this is —
# it needs its own heuristic rather than a CATEGORY_PRIORITY entry.
KIDS_ATTRACTION_VALUES = {
    "playground", "water_slide", "carousel", "amusement_ride",
    "train", "big_wheel", "maze", "animal",
}
ATTRACTION_KIDS_NAME_HINTS = ("פארק", "מתחם משפחות", "ילדים", "אטרקצי")

# access="gated" when fee=yes, OR the element matches one of these.
# Discovery-only: enrichment takes access from seed_names.json directly.
GATED_IF_FEE_OR = {
    ("tourism", "museum"),
    ("tourism", "zoo"),
    ("tourism", "aquarium"),
    ("leisure", "nature_reserve"),
}

# --- Shared constants (used by both modes) ---

# kid_friendly=True for these tags regardless of category. Deliberately not
# natural=spring or natural=beach: OSM carries no depth, supervision or
# lifeguard data, and marking an unsupervised body of water kid-friendly is
# a safety claim this script cannot support. Left False for a human to set
# via review_places.py.
KID_FRIENDLY_TAGS = {
    ("leisure", "park"),
    ("tourism", "zoo"),
    ("tourism", "aquarium"),
}

# Fallback visit length per category when nothing more specific is known
# (BRIEF_data_pipeline.md). "hike" never results from an OSM tag directly —
# kept so the table stays complete if a hiking-route tag is added later.
DEFAULT_DURATION_MIN: dict[Category, int] = {
    Category.MUSEUM: 120,
    Category.HISTORIC: 60,
    Category.NATURE: 90,
    Category.VIEWPOINT: 30,
    Category.KIDS: 120,
    Category.HIKE: 150,
}

_HEBREW_RE = re.compile("[֐-׿]")
_SLUG_INVALID_RE = re.compile(r"[^a-z0-9]+")

# Recognised subset of the OSM opening_hours mini-language: Sunday-first to
# match Weekday, since that is the order simple ranges expand across.
_WEEKDAY_ORDER = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]
_WEEKDAY_OSM_CODES: dict[str, Weekday] = {
    "Su": Weekday.SUN, "Mo": Weekday.MON, "Tu": Weekday.TUE, "We": Weekday.WED,
    "Th": Weekday.THU, "Fr": Weekday.FRI, "Sa": Weekday.SAT,
}
_DAY_CODE = r"Mo|Tu|We|Th|Fr|Sa|Su"
_SIMPLE_RANGE_RE = re.compile(
    rf"^(?P<start>{_DAY_CODE})(-(?P<end>{_DAY_CODE}))?\s+"
    rf"(?P<open>\d{{2}}:\d{{2}})-(?P<close>\d{{2}}:\d{{2}})$"
)
_OFF_DAY_RE = re.compile(rf"^(?P<day>{_DAY_CODE})\s+off$")


class SeedName(BaseModel):
    """
    One entry from seed_names.json — the unit of enrichment.

    ``name_he``/``region``/``category``/``access`` are the only fields a
    human ever has to type by hand; everything below is optional and lets an
    upstream source that already did real verification (parks.org.il, an
    OSM discover candidate) hand fetch_osm.py a pre-resolved entry. When
    ``lat``/``lng`` are already present, ``enrich_seed_entry`` trusts them
    outright and skips the Overpass name search entirely for that entry —
    the source that produced them already did the verification OSM search
    would otherwise be trying to redo.
    """

    name_he: str
    region: Region
    category: Category
    access: AccessType
    season: str = "year_round"
    # An explicit hand-set visit length (BRIEF_data_acquisition.md: a water
    # park is ~240min, nothing like the "kids" category default of 120).
    # ``None`` means "use DEFAULT_DURATION_MIN[category]" — enrich_seed_entry
    # never overwrites an explicit value with the category default.
    duration_min: Optional[int] = None

    lat: Optional[float] = None
    lng: Optional[float] = None
    opening_hours: Optional[dict[Weekday, Optional[tuple[str, str]]]] = None
    hours_verified: bool = False
    closed_on_shabbat: bool = False
    kid_friendly: bool = False
    accessible: bool = False
    tags: list[str] = PydanticField(default_factory=list)

    # Provenance (BRIEF_data_acquisition.md rule 5): every row a scraper
    # emits carries these; a minimal hand-typed entry may omit them.
    source: Optional[str] = None
    scraped_on: Optional[str] = None


# --------------------------------------------------------------------------
# Geometry (shared)
# --------------------------------------------------------------------------


def element_coordinates(element: Optional[dict]) -> Optional[tuple[float, float]]:
    """
    A representative (lat, lng) for any element type, or ``None`` for ``None``/no geometry.

    A node's own coordinate for a node; the bounding-box midpoint for a
    way/relation, which is what ``out center;`` would have handed us
    directly. Returns ``None`` rather than guessing a location.
    """
    if element is None:
        return None
    if element.get("type") == "node":
        lat, lon = element.get("lat"), element.get("lon")
        return (lat, lon) if lat is not None and lon is not None else None

    bounds = element.get("bounds")
    if not bounds:
        return None
    return (
        (bounds["minlat"] + bounds["maxlat"]) / 2,
        (bounds["minlon"] + bounds["maxlon"]) / 2,
    )


def element_area_m2(element: dict) -> Optional[float]:
    """
    A crude footprint size for the discover-mode low-signal filter, or ``None`` for a node.

    A node is a point and has no area — a single point can never be "large",
    so a node matching a low-signal tag must clear the significance bar via
    wikidata/wikipedia instead. For a way/relation, the bounding-box area
    overstates an irregular shape's true footprint, which is fine: this is a
    significance signal, not a scheduling fact.
    """
    if element.get("type") == "node":
        return None
    bounds = element.get("bounds")
    if not bounds:
        return None

    lat_span = bounds["maxlat"] - bounds["minlat"]
    lng_span = bounds["maxlon"] - bounds["minlon"]
    mean_lat = (bounds["maxlat"] + bounds["minlat"]) / 2
    meters_per_deg_lat = 111_320.0
    meters_per_deg_lng = 111_320.0 * math.cos(math.radians(mean_lat))
    return abs(lat_span * meters_per_deg_lat) * abs(lng_span * meters_per_deg_lng)


# --------------------------------------------------------------------------
# Name / Hebrew helpers (shared)
# --------------------------------------------------------------------------


def has_hebrew(text: str) -> bool:
    """Whether ``text`` contains at least one Hebrew character."""
    return bool(_HEBREW_RE.search(text))


def extract_name_he(osm_tags: dict[str, str]) -> Optional[str]:
    """
    ``name:he`` if present, else ``name`` only if it actually contains Hebrew.

    Discovery-only (enrichment already has name_he from seed_names.json). A
    Latin-only ``name`` is not a Hebrew name; an element with neither is
    dropped by the caller.
    """
    name_he = osm_tags.get("name:he")
    if name_he:
        return name_he
    name = osm_tags.get("name")
    if name and has_hebrew(name):
        return name
    return None


def slugify(text: str) -> str:
    """Lowercase, hyphen-separated ASCII slug. Returns "" if nothing ASCII survives."""
    return _SLUG_INVALID_RE.sub("-", text.strip().lower()).strip("-")


def _name_hash(name_he: str) -> str:
    """Short, stable, filesystem- and id-safe hash of a Hebrew string."""
    return hashlib.sha1(name_he.encode("utf-8")).hexdigest()[:10]


def seed_id_stub(name_he: str, matched_element: Optional[dict]) -> str:
    """
    An ASCII stub for a place id, preferring a real Latin name over a fabricated one.

    Used only when an id is first assigned — see the module docstring:
    ``merge_places`` never recomputes an id on a later run, so a name that
    goes unresolved today and resolves next week does not change identity,
    only its (still-stable) id may look less polished than it could.

    Prefers name:en/int_name from the matched OSM element when exactly one
    match exists. Otherwise falls back to a short hash of name_he — not the
    OSM element id, since an unresolved entry (zero/multiple matches) has no
    element to key off, and inventing a transliteration of Hebrew would be
    inventing a spelling.
    """
    if matched_element is not None:
        osm_tags = matched_element.get("tags") or {}
        for key in ("name:en", "int_name", "name"):
            candidate = osm_tags.get(key)
            if candidate and not has_hebrew(candidate):
                slug = slugify(candidate)
                if slug:
                    return slug
    return f"seed-{_name_hash(name_he)}"


def normalize_name(name_he: str) -> str:
    """Strip punctuation and collapse whitespace for name-based dedup comparison."""
    stripped = re.sub(r"[^\w\s]", "", name_he, flags=re.UNICODE)
    return re.sub(r"\s+", " ", stripped).strip().lower()


def names_similar(a: str, b: str) -> bool:
    """A deliberately simple "similar name" test: containment either direction (discover-only)."""
    return bool(a) and bool(b) and (a in b or b in a)


def assign_unique_id(base_id: str, used_ids: set[str]) -> str:
    """Return ``base_id``, or ``base_id-2``, ``base_id-3``, ... on collision."""
    if base_id not in used_ids:
        used_ids.add(base_id)
        return base_id
    suffix = 2
    while f"{base_id}-{suffix}" in used_ids:
        suffix += 1
    unique = f"{base_id}-{suffix}"
    used_ids.add(unique)
    return unique


# --------------------------------------------------------------------------
# Field classification (shared unless noted)
# --------------------------------------------------------------------------


def classify_category_and_tag(
    osm_tags: dict[str, str], name_he: str
) -> tuple[Optional[Category], Optional[tuple[str, str]]]:
    """
    Discover-mode only. Resolve a category AND which (key, value) pair caused the match.

    First match wins, so an element carrying tags from two priority bands
    resolves to whichever band comes first in the list, not whichever
    happens to be inserted first in some other structure. The matched pair
    is what lets curate_osm_open.py tell e.g. a nature_reserve candidate
    apart from a spring/beach/water one — several distinct OSM tags collapse
    onto the same NATURE category here, but Phase 2 needs to treat them
    differently (a nature reserve needs a "no fee tag" check the others
    don't).
    """
    for key, value, category in CATEGORY_PRIORITY:
        if osm_tags.get(key) == value:
            return category, (key, value)
    if osm_tags.get("tourism") == "attraction":
        return classify_attraction(name_he, osm_tags), ("tourism", "attraction")
    return None, None


def classify_category(osm_tags: dict[str, str], name_he: str) -> Optional[Category]:
    """Enrichment never calls this — category comes from seed_names.json. Discover-mode only."""
    category, _ = classify_category_and_tag(osm_tags, name_he)
    return category


def classify_attraction(name_he: str, osm_tags: dict[str, str]) -> Category:
    """
    Discover-mode only. Split tourism=attraction into kids vs historic.

    Checks OSM's own ``attraction=*`` key and ``leisure=playground`` — not
    ``tourism``, which is always the literal string "attraction" on every
    element this function sees and so carries no information. Falls back to
    a small Hebrew name-hint list, then to historic.
    """
    if osm_tags.get("attraction") in KIDS_ATTRACTION_VALUES:
        return Category.KIDS
    if osm_tags.get("leisure") == "playground":
        return Category.KIDS
    name = name_he.lower()
    if any(hint in name for hint in ATTRACTION_KIDS_NAME_HINTS):
        return Category.KIDS
    return Category.HISTORIC


def is_significant(osm_tags: dict[str, str], area_m2: Optional[float]) -> bool:
    """Discover-mode only. Whether a low-signal-tagged element clears the significance bar."""
    if osm_tags.get("wikidata") or osm_tags.get("wikipedia"):
        return True
    return area_m2 is not None and area_m2 >= SIGNIFICANT_AREA_M2


def classify_access(osm_tags: dict[str, str]) -> AccessType:
    """Discover-mode only. fee=yes, or one of GATED_IF_FEE_OR, makes a place gated; else open."""
    if osm_tags.get("fee") == "yes":
        return AccessType.GATED
    if any(osm_tags.get(key) == value for key, value in GATED_IF_FEE_OR):
        return AccessType.GATED
    return AccessType.OPEN


def classify_kid_friendly(osm_tags: dict[str, str]) -> bool:
    """True only for the tags in KID_FRIENDLY_TAGS — see that constant's comment."""
    return any(osm_tags.get(key) == value for key, value in KID_FRIENDLY_TAGS)


def classify_accessible(osm_tags: dict[str, str]) -> bool:
    """True only when OSM explicitly says wheelchair=yes; False otherwise, including unknown."""
    return osm_tags.get("wheelchair") == "yes"


def derive_tags(osm_tags: dict[str, str]) -> list[str]:
    """
    English tag vocabulary derived only from OSM tags with a direct, reliable
    correlate (places.template.json's vocabulary). Everything softer — shade,
    easy-walk, picnic, parking-onsite — needs a human's judgement and is left
    for review_places.py rather than guessed here.
    """
    derived: list[str] = []
    if osm_tags.get("natural") == "spring":
        derived.append("spring")
    if osm_tags.get("waterway") == "waterfall":
        derived.append("waterfall")
    if osm_tags.get("natural") == "beach":
        derived.append("coast")
    if osm_tags.get("historic") in ("archaeological_site", "ruins"):
        derived.append("archaeology")
    if osm_tags.get("tourism") in ("zoo", "aquarium"):
        derived.append("animals")
    if osm_tags.get("leisure") == "playground" or osm_tags.get("attraction") == "playground":
        derived.append("playground")
    if osm_tags.get("fee") == "yes":
        derived.append("paid")
    elif osm_tags.get("fee") == "no":
        derived.append("free")
    return derived


# --------------------------------------------------------------------------
# opening_hours mini-parser (shared)
# --------------------------------------------------------------------------


@dataclass
class ParsedHours:
    """Result of parsing one OSM ``opening_hours`` string."""

    hours: dict[Weekday, Optional[tuple[str, str]]]
    closed_days: set[Weekday] = field(default_factory=set)
    ok: bool = False


def _all_closed() -> dict[Weekday, Optional[tuple[str, str]]]:
    """A fresh all-null opening_hours dict — the safe default on any parse failure."""
    return {day: None for day in Weekday}


def parse_opening_hours(raw: Optional[str]) -> ParsedHours:
    """
    Parse a small, safe subset of the OSM opening_hours mini-language.

    Handles only: ``24/7``, a single weekday range with a single time range
    (``Mo-Fr 08:00-17:00``), and explicit ``<day> off`` clauses, optionally
    combined with ``;``. Anything else — multiple ranges per day, seasonal
    qualifiers, public holidays — yields every day null and ``ok=False``.
    Partial correctness here is worthless: a wrong gate time is the exact
    failure this project exists to prevent (BRIEF_data_pipeline.md).
    """
    if not raw:
        return ParsedHours(hours=_all_closed(), ok=False)

    raw = raw.strip()
    if raw == "24/7":
        return ParsedHours(hours={day: ("00:00", "23:59") for day in Weekday}, ok=True)

    clauses = [clause.strip() for clause in raw.split(";") if clause.strip()]
    if not clauses:
        return ParsedHours(hours=_all_closed(), ok=False)

    hours = _all_closed()
    closed_days: set[Weekday] = set()

    for clause in clauses:
        off_match = _OFF_DAY_RE.match(clause)
        if off_match:
            day = _WEEKDAY_OSM_CODES[off_match["day"]]
            hours[day] = None
            closed_days.add(day)
            continue

        range_match = _SIMPLE_RANGE_RE.match(clause)
        if not range_match:
            return ParsedHours(hours=_all_closed(), ok=False)

        start_idx = _WEEKDAY_ORDER.index(range_match["start"])
        end_idx = _WEEKDAY_ORDER.index(range_match["end"] or range_match["start"])
        if end_idx < start_idx:
            # A wrapping range (Fr-Mo) is outside "the simple cases".
            return ParsedHours(hours=_all_closed(), ok=False)

        open_time, close_time = range_match["open"], range_match["close"]
        if open_time >= close_time:
            return ParsedHours(hours=_all_closed(), ok=False)

        for idx in range(start_idx, end_idx + 1):
            hours[_WEEKDAY_OSM_CODES[_WEEKDAY_ORDER[idx]]] = (open_time, close_time)

    return ParsedHours(hours=hours, closed_days=closed_days, ok=True)


# --------------------------------------------------------------------------
# Overpass queries + caching
# --------------------------------------------------------------------------


def _escape_overpass_string(value: str) -> str:
    """Escape a value for embedding in an Overpass QL double-quoted string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_discover_query(bbox: tuple[float, float, float, float]) -> str:
    """
    Discover-mode query: every OVERPASS_DISCOVER_TAGS tag, bulk, over one region's bbox.

    Requests ``out bb;`` — see element_area_m2's docstring and the module
    docstring's divergence note for why this replaces ``out center;``.
    """
    south, west, north, east = bbox
    bbox_str = f"{south},{west},{north},{east}"
    clauses = "\n".join(
        f'  node["{key}"="{value}"]({bbox_str});\n'
        f'  way["{key}"="{value}"]({bbox_str});\n'
        f'  relation["{key}"="{value}"]({bbox_str});'
        for key, value in OVERPASS_DISCOVER_TAGS
    )
    return f"[out:json][timeout:180];\n(\n{clauses}\n);\nout bb;\n"


def build_name_query(name_he: str, bbox: tuple[float, float, float, float]) -> str:
    """
    Enrichment-mode query: search for one exact name (name:he or name) inside one bbox.

    Overpass's union ``( ... );`` de-duplicates by element id, so an element
    that happens to satisfy both clauses (name:he and name both equal to the
    search string) is still returned once.
    """
    south, west, north, east = bbox
    bbox_str = f"{south},{west},{north},{east}"
    escaped = _escape_overpass_string(name_he)
    return (
        "[out:json][timeout:60];\n"
        "(\n"
        f'  node["name:he"="{escaped}"]({bbox_str});\n'
        f'  way["name:he"="{escaped}"]({bbox_str});\n'
        f'  relation["name:he"="{escaped}"]({bbox_str});\n'
        f'  node["name"="{escaped}"]({bbox_str});\n'
        f'  way["name"="{escaped}"]({bbox_str});\n'
        f'  relation["name"="{escaped}"]({bbox_str});\n'
        ");\n"
        "out bb;\n"
    )


OVERPASS_MAX_RETRIES = 5
OVERPASS_DEFAULT_RETRY_SECONDS = 30  # used when the server gives no Retry-After


# 429 = rate limited; 502/503/504 = the public Overpass instance overloaded
# or timed out upstream — all transient, all worth a retry rather than
# aborting. Seen in practice: 305 sequential single-relation trailhead
# queries triggered both a 429 and later a 504 in the same run.
OVERPASS_RETRYABLE_STATUSES = {429, 502, 503, 504}


def overpass_post(query: str) -> dict:
    """
    The one place that actually talks to Overpass.

    Retries with backoff on a transient server error, honouring Retry-After
    when the server sends one, rather than crashing a whole curation run
    over a single flaky request.
    """
    for attempt in range(1, OVERPASS_MAX_RETRIES + 1):
        response = requests.post(
            OVERPASS_ENDPOINT,
            data={"data": query},
            headers={"User-Agent": USER_AGENT},
            timeout=180,
        )
        if response.status_code in OVERPASS_RETRYABLE_STATUSES and attempt < OVERPASS_MAX_RETRIES:
            wait = int(response.headers.get("Retry-After", OVERPASS_DEFAULT_RETRY_SECONDS))
            logger.warning(
                "Overpass %d (attempt %d/%d) — waiting %ds",
                response.status_code, attempt, OVERPASS_MAX_RETRIES, wait,
            )
            time.sleep(wait)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"Overpass kept failing ({OVERPASS_RETRYABLE_STATUSES}) after all retries")


def fetch_discover_raw(region: Region, *, refresh: bool = False) -> dict:
    """Raw Overpass response for discover mode's bulk scan of one region, cached per region."""
    cache_file = CACHE_DIR / f"overpass_discover_{region.value}.json"
    if cache_file.exists() and not refresh:
        logger.info("using cached discover response for %s (%s)", region.value, cache_file)
        return json.loads(cache_file.read_text(encoding="utf-8"))

    logger.info("querying Overpass (discover) for %s", region.value)
    payload = overpass_post(build_discover_query(REGION_BBOXES[region]))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def fetch_matches_for_name(name_he: str, region: Region, *, refresh: bool = False) -> dict:
    """Raw Overpass response for one seed_names.json entry, cached per (region, name)."""
    cache_file = CACHE_DIR / f"overpass_name_{region.value}_{_name_hash(name_he)}.json"
    if cache_file.exists() and not refresh:
        logger.info("using cached match response for %r in %s", name_he, region.value)
        return json.loads(cache_file.read_text(encoding="utf-8"))

    logger.info("querying Overpass (enrich) for %r in %s", name_he, region.value)
    payload = overpass_post(build_name_query(name_he, REGION_BBOXES[region]))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


# --------------------------------------------------------------------------
# Discover mode: bulk scan -> candidates.json (never places.json)
# --------------------------------------------------------------------------


@dataclass
class DiscoverStats:
    """Counters for the discover-mode summary table."""

    fetched: int = 0
    dropped: int = 0
    accepted: int = 0
    low_signal: int = 0
    hours_parseable: int = 0


def discover_region(
    region: Region,
    raw: dict,
) -> tuple[list[dict], DiscoverStats]:
    """
    Bulk-scan one region's raw Overpass response for candidate names.

    A candidate is explicitly NOT a Place: it has not been curated by a
    human yet, so it carries only what OSM itself suggests (a guessed
    category/access, raw tags) for review, never hours_verified or
    description_source. Used only by --discover.
    """
    stats = DiscoverStats()
    accepted: list[tuple[dict, str, float, float]] = []  # (record, name_norm, lat, lng)

    for element in raw.get("elements", []):
        stats.fetched += 1
        osm_tags: dict[str, str] = element.get("tags") or {}
        osm_type, osm_id = element.get("type"), element.get("id")

        def drop(reason: str) -> None:
            stats.dropped += 1
            logger.info("dropped %s/%s (%s): %s", osm_type, osm_id, osm_tags.get("name", "?"), reason)

        name_he = extract_name_he(osm_tags)
        if not name_he:
            drop("no_hebrew_name")
            continue
        if len(name_he) < MIN_NAME_LENGTH:
            drop("name_too_short")
            continue

        coordinates = element_coordinates(element)
        if coordinates is None:
            drop("no_coordinate")
            continue
        lat, lng = coordinates

        category, matched_tag = classify_category_and_tag(osm_tags, name_he)
        if category is None:
            drop("unclassifiable")
            continue

        # Computed once for every candidate, not just LOW_SIGNAL_TAGS ones:
        # curate_osm_open.py (Phase 2) applies its own, broader significance
        # requirement on top of whatever this function already accepted, and
        # needs these without access to the raw osm_tags dict.
        area_m2 = element_area_m2(element)
        has_wikidata_or_wikipedia = bool(osm_tags.get("wikidata") or osm_tags.get("wikipedia"))
        # Distinct from the combined flag above: curate_osm_open.py's Phase 2
        # significance bar requires an actual Wikipedia article specifically.
        # A bare wikidata tag with no article turned out to be bulk-import
        # noise for natural=spring (a WikiProject cataloguing nearly every
        # named spring in Israel), not a real notability signal.
        has_wikipedia = bool(osm_tags.get("wikipedia"))

        if any(osm_tags.get(key) == value for key, value in LOW_SIGNAL_TAGS):
            if not is_significant(osm_tags, area_m2):
                stats.low_signal += 1
                drop("low_signal")
                continue

        name_norm = normalize_name(name_he)
        duplicate = next(
            (
                rec for rec, norm, a_lat, a_lng in accepted
                if norm == name_norm
                or (names_similar(name_norm, norm) and haversine_km(lat, lng, a_lat, a_lng) * 1000 <= DEDUP_DISTANCE_M)
            ),
            None,
        )
        if duplicate is not None:
            drop("duplicate")
            continue

        opening_hours_raw = osm_tags.get("opening_hours")
        if parse_opening_hours(opening_hours_raw).ok:
            stats.hours_parseable += 1

        record = {
            "name_he": name_he,
            "region": region.value,
            "guessed_category": category.value,
            "guessed_access": classify_access(osm_tags).value,
            # Raw fee tag, distinct from guessed_access above: classify_access
            # always calls leisure=nature_reserve gated regardless of a fee
            # tag, but curate_osm_open.py needs the raw tag itself to tell a
            # nature reserve with an explicit fee apart from one with none
            # (BRIEF_data_acquisition.md Phase 2).
            "fee": osm_tags.get("fee"),
            # Which specific (key, value) resolved this category — several
            # distinct tags collapse onto the same guessed_category above.
            "matched_tag": f"{matched_tag[0]}={matched_tag[1]}" if matched_tag else None,
            "area_m2": area_m2,
            "has_wikidata_or_wikipedia": has_wikidata_or_wikipedia,
            "has_wikipedia": has_wikipedia,
            "lat": lat,
            "lng": lng,
            "opening_hours_raw": opening_hours_raw,
            "tags": derive_tags(osm_tags),
            "_osm_id": osm_id,
            "_osm_type": osm_type,
        }
        accepted.append((record, name_norm, lat, lng))
        stats.accepted += 1

    return [rec for rec, _, _, _ in accepted], stats


def print_discover_summary(stats_by_region: dict[Region, DiscoverStats]) -> None:
    """Per-region fetched/dropped/accepted/low_signal/hours_parseable table."""
    header = f"{'region':<10}{'fetched':>9}{'dropped':>9}{'accepted':>10}{'low_sig':>9}{'hrs_ok':>8}"
    print(header)
    print("-" * len(header))
    for region, s in stats_by_region.items():
        print(
            f"{region.value:<10}{s.fetched:>9}{s.dropped:>9}{s.accepted:>10}"
            f"{s.low_signal:>9}{s.hours_parseable:>8}"
        )


def run_discover(regions: list[Region], *, limit: Optional[int], refresh: bool, out_path: Path) -> None:
    """The --discover pipeline. Writes only candidates.json — never places.json."""
    all_candidates: list[dict] = []
    stats_by_region: dict[Region, DiscoverStats] = {}

    for region in regions:
        raw = fetch_discover_raw(region, refresh=refresh)
        candidates, stats = discover_region(region, raw)
        if limit is not None:
            candidates = candidates[:limit]
        all_candidates.extend(candidates)
        stats_by_region[region] = stats

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"candidates": all_candidates}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print_discover_summary(stats_by_region)
    print(f"wrote {out_path} — {len(all_candidates)} candidates for review, NOT written to places.json")


# --------------------------------------------------------------------------
# Enrichment mode: seed_names.json -> places.json (the default pipeline)
# --------------------------------------------------------------------------


@dataclass
class EnrichStats:
    """Counters for the enrichment-mode summary table."""

    total: int = 0
    pre_supplied: int = 0
    matched: int = 0
    zero_matches: int = 0
    multiple_matches: int = 0
    hours_verified: int = 0


def enrich_seed_entry(
    seed: SeedName,
    raw: Optional[dict],
    used_ids: set[str],
) -> tuple[dict, str]:
    """
    Resolve one seed_names.json entry, either from ``seed`` directly or via Overpass.

    If ``seed`` already carries lat/lng (an upstream source — parks.org.il,
    an OSM discover candidate — already verified them), those facts are
    trusted outright and ``raw`` is ignored entirely: no Overpass name search
    happens for this entry. Otherwise ``raw`` (an Overpass name-search
    response, passed in rather than fetched here so this stays a pure
    function to test) resolves it: exactly one element fills
    lat/lng/tags/hours; zero or multiple elements write the place anyway
    with lat/lng left null. region, category and access always come from
    ``seed``, never from OSM.

    Returns (record, outcome) where outcome is "pre_supplied", "matched",
    "zero_matches" or "multiple_matches" — the caller logs/counts it.
    """
    if seed.lat is not None and seed.lng is not None:
        return _build_place_record(seed, matched_element=None, used_ids=used_ids), "pre_supplied"

    elements = (raw or {}).get("elements", [])
    if len(elements) == 1:
        matched_element: Optional[dict] = elements[0]
        outcome = "matched"
    elif len(elements) == 0:
        matched_element = None
        outcome = "zero_matches"
    else:
        matched_element = None
        outcome = "multiple_matches"

    return _build_place_record(seed, matched_element, used_ids), outcome


def _build_place_record(
    seed: SeedName,
    matched_element: Optional[dict],
    used_ids: set[str],
) -> dict:
    """
    Build the Place-shaped dict for one seed entry.

    Shared by both branches of ``enrich_seed_entry``. Two independent trust
    questions, not one: ``pre_supplied`` (coordinates already known) decides
    where lat/lng come from; ``trust_seed_facts`` (coordinates known, OR the
    seed names a ``source``) decides where hours/tags/kid_friendly/accessible
    come from. They diverge exactly for a seed like Phase 1's parks.org.il
    entries with no coordinates on their page: coordinates still need an OSM
    name search, but the hours that scraper already verified must not be
    thrown away just because the coordinate lookup is a separate step.
    """
    pre_supplied = seed.lat is not None and seed.lng is not None
    # A seed carrying its own `source` came from a real upstream provider
    # (scrape_parks.py, curate_osm_open.py, a hand-typed Phase 4 entry) that
    # already verified its own facts — even when it has no coordinates yet.
    # BRIEF_data_acquisition.md Phase 1 explicitly allows this: hours get
    # scraped, coordinates get resolved later by name search. Gating fact
    # trust on coordinates alone silently discarded a real, hours_verified
    # parks.org.il record with null lat/lng — found on a live run (Ein Gedi
    # Nahal Arugot, whose page has no coordinates at all).
    trust_seed_facts = pre_supplied or bool(seed.source)
    osm_tags: dict[str, str] = (matched_element.get("tags") or {}) if matched_element else {}

    if pre_supplied:
        lat, lng = seed.lat, seed.lng
    else:
        coordinates = element_coordinates(matched_element)
        lat, lng = coordinates if coordinates is not None else (None, None)

    if seed.access == AccessType.OPEN:
        # An open place has no gate for opening_hours to describe (SPEC.md
        # section 10) — always null, regardless of any stray OSM tag.
        opening_hours = _all_closed()
        hours_verified = False
        closed_on_shabbat = False
    elif trust_seed_facts:
        opening_hours = seed.opening_hours or _all_closed()
        hours_verified = seed.hours_verified
        closed_on_shabbat = seed.closed_on_shabbat
    elif matched_element is not None:
        parsed_hours = parse_opening_hours(osm_tags.get("opening_hours"))
        opening_hours = parsed_hours.hours
        hours_verified = parsed_hours.ok
        closed_on_shabbat = Weekday.SAT in parsed_hours.closed_days
        if not parsed_hours.ok and osm_tags.get("opening_hours"):
            logger.info(
                "%s: could not parse opening_hours %r", seed.name_he, osm_tags.get("opening_hours")
            )
    else:
        # Gated but unresolved: we have no element to read hours from.
        opening_hours = _all_closed()
        hours_verified = False
        closed_on_shabbat = False

    place = Place(
        id=assign_unique_id(f"{seed.region.value}-{seed_id_stub(seed.name_he, matched_element)}", used_ids),
        name_he=seed.name_he,
        description_he="",
        tip_he="",
        description_source=DescriptionSource.GENERATED,
        category=seed.category,
        region=seed.region,
        access=seed.access,
        lat=lat,
        lng=lng,
        duration_min=seed.duration_min if seed.duration_min is not None else DEFAULT_DURATION_MIN.get(seed.category, 60),
        opening_hours=opening_hours,
        hours_verified=hours_verified,
        closed_on_shabbat=closed_on_shabbat,
        kid_friendly=seed.kid_friendly if trust_seed_facts else (classify_kid_friendly(osm_tags) if matched_element else False),
        accessible=seed.accessible if trust_seed_facts else (classify_accessible(osm_tags) if matched_element else False),
        tags=seed.tags if trust_seed_facts else (derive_tags(osm_tags) if matched_element else []),
        season=seed.season,
    )
    record = place.model_dump(mode="json")
    if trust_seed_facts:
        if seed.source:
            record["_source"] = seed.source
        if seed.scraped_on:
            record["_scraped_on"] = seed.scraped_on
    # Independent of trust_seed_facts: a seed with its own `source` can still
    # have had its coordinate resolved by an OSM name search this run (no
    # lat/lng supplied yet, so matched_element is real) — that match's id is
    # worth keeping too. matched_element is only ever set when not pre_supplied.
    if matched_element is not None:
        record["_osm_id"] = matched_element.get("id")
        record["_osm_type"] = matched_element.get("type")

    return record


def print_enrich_summary(stats: EnrichStats) -> None:
    """total/pre_supplied/matched/zero_matches/multiple_matches/hours_verified, one line."""
    print(
        f"{'total':<8}{'pre_sup':>9}{'matched':>9}{'zero_match':>12}{'multi_match':>13}{'hrs_ok':>8}\n"
        f"{stats.total:<8}{stats.pre_supplied:>9}{stats.matched:>9}{stats.zero_matches:>12}"
        f"{stats.multiple_matches:>13}{stats.hours_verified:>8}"
    )


def _seed_sanity_warnings(seed: SeedName) -> list[str]:
    """
    Non-fatal seed sanity checks (BRIEF_data_acquisition.md). Warns, never fixes.

    There is no new Category for water parks/zoos — they are ``category:
    "kids"`` plus tags, so a ``summer_only`` place outside ``kids`` and a
    ``"swimming"`` tag outside ``summer_only`` are both suspicious enough to
    flag for a human, without being wrong often enough to justify auto-fixing.
    """
    warnings: list[str] = []
    if seed.season == "summer_only" and seed.category != Category.KIDS:
        warnings.append(
            f"{seed.name_he!r} ({seed.region.value}): season=summer_only but "
            f"category={seed.category.value!r}, expected 'kids'"
        )
    if "swimming" in seed.tags and seed.season == "year_round":
        warnings.append(
            f"{seed.name_he!r} ({seed.region.value}): tags include 'swimming' "
            f"but season='year_round'"
        )
    return warnings


def load_seed_names(path: Path) -> list[SeedName]:
    """Read and validate seed_names.json — a bare JSON array, not wrapped in an object."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} must be a JSON array of {{name_he, region, category, access}}")
    seed_names = [SeedName.model_validate(row) for row in raw]
    for seed in seed_names:
        for warning in _seed_sanity_warnings(seed):
            print(f"WARNING: {warning}")
    return seed_names


def run_enrich(
    seed_names: list[SeedName],
    *,
    region_filter: Optional[set[Region]],
    refresh: bool,
    out_path: Path,
) -> None:
    """The default pipeline: resolve seed_names.json entries into places.json."""
    if region_filter is not None:
        seed_names = [s for s in seed_names if s.region in region_filter]

    existing = load_existing_places(out_path)
    used_ids: set[str] = {row["id"] for row in existing}

    fresh: list[dict] = []
    stats = EnrichStats()
    for seed in seed_names:
        # A seed with lat/lng already supplied (parks.org.il, an OSM discover
        # candidate) was already verified upstream — no Overpass call needed.
        pre_supplied = seed.lat is not None and seed.lng is not None
        raw = None
        if not pre_supplied:
            try:
                raw = fetch_matches_for_name(seed.name_he, seed.region, refresh=refresh)
            except Exception:
                # A single flaky connection must not lose the whole batch —
                # observed in practice (a bare ConnectionError, not even an
                # HTTP error status). Treated the same as a genuine
                # zero-match: lat/lng left null, logged for manual entry or
                # a later re-run.
                logger.warning(
                    "Overpass request failed for %r in %s; leaving unresolved",
                    seed.name_he, seed.region.value, exc_info=True,
                )
                raw = {"elements": []}
        record, outcome = enrich_seed_entry(seed, raw, used_ids)
        fresh.append(record)
        stats.total += 1
        if outcome == "pre_supplied":
            stats.pre_supplied += 1
        elif outcome == "matched":
            stats.matched += 1
        elif outcome == "zero_matches":
            stats.zero_matches += 1
            logger.warning(
                "no OSM match for %r in %s — needs manual lat/lng", seed.name_he, seed.region.value
            )
        else:
            stats.multiple_matches += 1
            logger.warning(
                "%d OSM matches for %r in %s — needs manual lat/lng",
                len((raw or {}).get("elements", [])), seed.name_he, seed.region.value,
            )
        if record["hours_verified"]:
            stats.hours_verified += 1

    processed_regions = {r.value for r in (region_filter or {s.region for s in seed_names})}
    merged, notes = merge_places(existing, fresh, processed_regions)
    for note in notes:
        logger.info(note)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"places": merged}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print_enrich_summary(stats)
    print(f"wrote {out_path} — {len(merged)} places total")


# --------------------------------------------------------------------------
# Merge with existing places.json
# --------------------------------------------------------------------------


def load_existing_places(path: Path) -> list[dict]:
    """Read the current seed's ``places`` list, or [] if the file doesn't exist yet."""
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("places", [])


# Fields an unlocked row refreshes from a fresh OSM enrichment. Curator
# fields (category/access/duration_min) are handled separately, outside the
# lock, since seed_names.json is authoritative for those regardless of
# hours_verified — see merge_places's docstring.
_OSM_DERIVED_FIELDS = (
    "lat", "lng", "opening_hours", "hours_verified", "closed_on_shabbat",
    "kid_friendly", "accessible", "tags",
)


def merge_places(
    existing: list[dict],
    fresh: list[dict],
    processed_regions: set[str],
) -> tuple[list[dict], list[str]]:
    """
    Merge freshly enriched records into the existing seed.

    Identity is (region, name_he) — the pair that comes straight from
    seed_names.json, not from any OSM element. This is what makes identity
    stable across runs: an entry that resolves to zero OSM matches today and
    exactly one next run is still recognised as the same place, and its
    ``id`` (assigned once, on first creation) never changes.

    Two independent things can be true of an existing row:
      * "locked" (hours_verified=True or description_source="human") — OSM-
        derived facts (lat/lng/hours/tags/kid_friendly/accessible) are never
        overwritten.
      * category/access/duration_min ALWAYS refresh from the fresh seed,
        locked or not — seed_names.json is the human-curated authority for
        those fields regardless of what OSM did or didn't verify.

    ``description_he``/``tip_he``/``description_source``/``id`` are never
    touched by an update — only set when a row is first created.

    Staleness (``_stale: true``) is only evaluated for existing rows whose
    region is in ``processed_regions``: running ``--region north`` must
    never flag central/south rows just because this invocation didn't
    mention them. A row is also flagged stale if its region was processed
    but no seed_names.json entry with the same name exists any more —
    including a hand-added place that was never added to seed_names.json at
    all. It is never deleted, only flagged, and adding it to seed_names.json
    (or clearing the flag by hand) resolves it.
    """
    by_key: dict[tuple[str, str], dict] = {(row["region"], row["name_he"]): row for row in existing}
    notes: list[str] = []
    seen_keys: set[tuple[str, str]] = set()

    for fresh_row in fresh:
        key = (fresh_row["region"], fresh_row["name_he"])
        seen_keys.add(key)
        existing_row = by_key.get(key)

        if existing_row is None:
            by_key[key] = fresh_row
            notes.append(f"new: {fresh_row['id']}")
            continue

        updated = dict(existing_row)
        updated["category"] = fresh_row["category"]
        updated["access"] = fresh_row["access"]
        updated["duration_min"] = fresh_row["duration_min"]

        locked = existing_row.get("hours_verified") or existing_row.get("description_source") == "human"
        if not locked:
            for field_name in _OSM_DERIVED_FIELDS:
                updated[field_name] = fresh_row[field_name]
            if "_osm_id" in fresh_row:
                updated["_osm_id"] = fresh_row["_osm_id"]
                updated["_osm_type"] = fresh_row["_osm_type"]
            else:
                updated.pop("_osm_id", None)
                updated.pop("_osm_type", None)

        updated.pop("_stale", None)
        if updated != existing_row:
            notes.append(f"updated: {updated['id']}")
        by_key[key] = updated

    for key, row in list(by_key.items()):
        region_value, _ = key
        if key not in seen_keys and region_value in processed_regions and not row.get("_stale"):
            row = dict(row)
            row["_stale"] = True
            by_key[key] = row
            notes.append(f"stale: {row.get('id')}")

    return list(by_key.values()), notes


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """
    Default (no --discover): enrich seed_names.json, optionally limited to --region.
    --discover: bulk-scan for candidates; requires exactly one of --region/--all.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--discover", action="store_true",
        help="bulk-scan a region for candidate names; writes candidates.json, never places.json",
    )
    parser.add_argument("--region", choices=[r.value for r in Region])
    parser.add_argument("--all", action="store_true", help="--discover only: scan all three regions")
    parser.add_argument("--limit", type=int, default=None, help="--discover only: cap candidates per region")
    parser.add_argument("--refresh", action="store_true", help="bypass the Overpass cache")
    parser.add_argument("--seed-names", type=Path, default=DEFAULT_SEED_NAMES_PATH)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.discover:
        if bool(args.region) == bool(args.all):
            parser.error("--discover requires exactly one of --region <north|central|south> or --all")
    elif args.all:
        parser.error("--all only applies to --discover")

    return args


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args(argv)

    if args.discover:
        regions = list(Region) if args.all else [Region(args.region)]
        run_discover(
            regions,
            limit=args.limit,
            refresh=args.refresh,
            out_path=args.out or DEFAULT_CANDIDATES_PATH,
        )
    else:
        seed_names = load_seed_names(args.seed_names)
        region_filter = {Region(args.region)} if args.region else None
        run_enrich(
            seed_names,
            region_filter=region_filter,
            refresh=args.refresh,
            out_path=args.out or DEFAULT_PLACES_PATH,
        )


if __name__ == "__main__":
    main()
