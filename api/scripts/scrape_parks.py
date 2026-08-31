"""
Phase 1 (BRIEF_data_acquisition.md): scrape parks.org.il — רשות הטבע והגנים
— into api/data/seed_names.json.

Run with:

    python -m api.scripts.scrape_parks
    python -m api.scripts.scrape_parks --limit 10
    python -m api.scripts.scrape_parks --refresh

robots.txt (checked by hand before writing this module — see the summary in
this session's conversation) permits every path this scraper touches:

    User-Agent: *
    Disallow: /dist
    Disallow: /wp-admin
    Allow: /wp-content/uploads/
    Disallow: /wp-content/plugins/
    Disallow: /readme.html
    Disallow: /ar/

The site's CDN (CloudFront) separately blocks any non-browser User-Agent at
the WAF level, which would make an honest, descriptive UA (rule 2's default)
unable to fetch anything at all. _scrape_common.USER_AGENT is a real browser
string for that reason — a deliberate, explicit choice, not a default, made
after confirming robots.txt allows the paths and getting a human go-ahead.
Every other rule (1 req/sec, caching, resumability, facts only) is followed
in full.

Data source and shape, found by hand-inspecting the live site before writing
any of this:

  * The site is WordPress with a custom post type ``reserve-park`` (REST
    base ``rp``), exposed at ``/wp-json/wp/v2/rp`` — a structured JSON API,
    not an HTML page to scrape. This is far more reliable than parsing
    rendered HTML and is used instead of the brief's literal "extract every
    site page URL" instruction, which described the mechanism (find pages),
    not the outcome (enumerate every reserve/park) — the REST API satisfies
    the outcome directly.
  * ``lat``/``lon`` on each post are Israeli ITM grid coordinates (EPSG:2039)
    with the site's field names swapped from the usual lat/lon convention —
    confirmed empirically: transforming (x=post['lat'], y=post['lon']) under
    EPSG:2039 -> EPSG:4326 lands on the real, known location; the "obvious"
    (x=lon, y=lat) pairing does not. ``pyproj`` does the actual projection
    math rather than a hand-derived Transverse Mercator inverse, which is
    easy to get subtly wrong without a reference to check against.
  * Structured per-weekday hour fields (``Summer_Opening_Hours_s`` etc.) were
    empty on every post inspected. The real hours live as free-text HTML in
    ``Special_Opening_hours_s``, under "שעות קיץ"/"שעות חורף" (summer/winter)
    headers, as ``<span class="time">CLOSE – OPEN</span>`` (closing time
    first, per the page's RTL rendering). See parse_special_hours_html for
    exactly how much of this we parse and where we deliberately give up.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import logging
import re
from pathlib import Path
from typing import Optional

from pyproj import Transformer

from api.domain import regions
from api.models import Region, Weekday
from api.scripts import _scrape_common as common
from api.scripts import fetch_osm
from api.scripts.curate_osm_open import load_seed_names_raw, merge_into_seed_names

logger = logging.getLogger(__name__)

API_BASE = "https://www.parks.org.il/wp-json/wp/v2/rp"
CACHE_DIR = fetch_osm.CACHE_DIR / "parks_org_il"
DEFAULT_SEED_NAMES_PATH = fetch_osm.DEFAULT_SEED_NAMES_PATH
PAGE_SIZE = 100

_ITM_TO_WGS84 = Transformer.from_crs("EPSG:2039", "EPSG:4326", always_xy=True)

# Every reserve-park entry is under real management — a real gate, a ticket
# booth or at minimum staff presence (BRIEF_data_acquisition.md Phase 1 step
# 3), so ACCESS is unconditional. CATEGORY defaults to "nature" but a handful
# of רשות הטבע והגנים sites are actually museums (e.g. מוזיאון השומרוני הטוב,
# מוזיאון 'עקבות בעמק') — "מוזיאון" in the title is רט"ג's own unambiguous
# word for that, not a guess.
CATEGORY = "nature"
ACCESS = "gated"


def category_for_title(name_he: str) -> str:
    return "museum" if "מוזיאון" in name_he else CATEGORY


def itm_to_wgs84(itm_x: float, itm_y: float) -> tuple[float, float]:
    """
    Convert the site's (lat, lon) field pair — Israeli ITM grid, fields
    swapped from the usual naming — to (lat, lng) in WGS84.

    See the module docstring for how this mapping was confirmed.
    """
    lng, lat = _ITM_TO_WGS84.transform(itm_x, itm_y)
    return lat, lng


# A loose bbox around Israel — wide enough to accept every real reserve-park
# site, narrow enough to catch a source-data typo before it becomes a wrong
# place on the map. Found in practice: גן לאומי גבעות מרר's page carries
# lat="1799799" (compare a real ITM northing, ~500,000-800,000) — the same
# wrong value appears in the page's own "נ"צ" text, so this is bad data at
# the source, not a scraping bug — and converts to a coordinate in Iran.
ISRAEL_BBOX_LAT = (29.4, 33.4)
ISRAEL_BBOX_LNG = (34.2, 35.9)


def _looks_like_israel(lat: float, lng: float) -> bool:
    return ISRAEL_BBOX_LAT[0] <= lat <= ISRAEL_BBOX_LAT[1] and ISRAEL_BBOX_LNG[0] <= lng <= ISRAEL_BBOX_LNG[1]


# Fallback region source for a post with no usable coordinate: the site's
# own "trip-area" taxonomy (a tourism sub-region facet, term ids confirmed
# by fetching /wp-json/wp/v2/trip-area by hand). Used only when coordinates
# are unavailable — coordinates are the primary, more precise source.
# Deliberately excludes Judea/Samaria/West Bank terms (4690 יו"ש, 4695
# שומרון): that area doesn't map cleanly onto SPEC.md's three-region model
# of Israel proper, and a post whose only trip-area terms are these is
# skipped rather than guessed into a region.
TRIP_AREA_TO_REGION: dict[int, str] = {
    4698: "north",    # צפון
    4708: "north",    # גליל עליון
    4707: "north",    # גליל תחתון ועמקים
    4709: "north",    # גליל מערבי
    4706: "north",    # רמת הגולן והכנרת
    4710: "north",    # כרמל והסביבה
    4694: "north",    # בקעת הירדן צפון ים המלח
    4680: "central",  # מרכז
    4682: "central",  # השרון וגוש דן
    4697: "central",  # ירושלים והשפלה
    4696: "central",  # גוש עציון והסביבה
    4699: "south",    # דרום
    4702: "south",    # ארץ המכתשים ודרכי הבשמים
    4701: "south",    # בירת הנגב והמדבר האותנטי
    4700: "south",    # ארץ ים המלח
    4703: "south",    # דרך הערבה
    4704: "south",    # הרי אילת והים האדום
    4705: "south",    # נגב מערבי
    4681: "south",    # מישור חוף דרומי
}


def region_from_trip_area(trip_area_ids: list[int]) -> Optional[str]:
    """First recognised trip-area term mapped to a region, or None if none match."""
    for term_id in trip_area_ids:
        region = TRIP_AREA_TO_REGION.get(term_id)
        if region is not None:
            return region
    return None


# Below the main north/central seam (32.60) but at or above this latitude,
# a single lat threshold is wrong on its own: this band holds both the
# Sharon coast (חדרה/זכרון — central) and the Jezreel/Beit Shean valleys
# (עפולה/בית שאן — an Israeli calls these north: SPEC.md section 2 lists
# עמקים under north). Longitude splits the two: the valleys sit inland,
# east of roughly 35.10, the coast west of it. Confirmed against a live-run
# bug where עין חרוד, גן לאומי בית שאן, מעיין חרוד, שמורת טבע גלבוע, תל
# מגידו, כוכב הירדן, בית אלפא and גן השלושה — all lat 32.48-32.60, lng
# 35.18-35.52 — landed in central under the old single-seam rule.
_INLAND_VALLEY_LAT_SEAM = 32.35
_INLAND_VALLEY_LNG_SEAM = 35.10


def region_from_coordinates(lat: float, lng: float) -> str:
    """
    Region from a coordinate, using fetch_osm.py's bbox seams (31.55 / 32.60)
    plus a longitude split for the inland-valley band between them.

    A park site is a real point inside Israel, not an arbitrary bbox query,
    so this is a strict partition rather than REGION_BBOXES' overlapping
    Overpass query filters. See ``_INLAND_VALLEY_LAT_SEAM``'s comment for why
    latitude alone isn't enough between 32.35 and 32.60.
    """
    north_south_seam = fetch_osm.REGION_BBOXES[Region.NORTH][0]  # 32.60
    central_south_seam = fetch_osm.REGION_BBOXES[Region.CENTRAL][0]  # 31.55
    if lat >= north_south_seam:
        return "north"
    if lat >= _INLAND_VALLEY_LAT_SEAM:
        return "north" if lng >= _INLAND_VALLEY_LNG_SEAM else "central"
    if lat >= central_south_seam:
        return "central"
    return "south"


# --------------------------------------------------------------------------
# Hours parsing
# --------------------------------------------------------------------------

_TIME_SPAN_RE = re.compile(
    r'<span class="time">\s*(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})\s*</span>'
)
_SUMMER_HEADER_RE = re.compile(r"קיץ")
_WINTER_HEADER_RE = re.compile(r"חורף")
_MONTH_QUALIFIER_RE = re.compile(r"בחודשים")  # "in these months" — a sub-override we don't model
_DAY_RANGE_RE = re.compile(r"([א-ת]'?)\s*[-–]\s*([א-ת]'?)")
_FRIDAY_RE = re.compile(r"שישי")
_SATURDAY_RE = re.compile(r"שבת")


def _normalize_time(raw: str) -> str:
    hour, _, minute = raw.partition(":")
    return f"{int(hour):02d}:{minute}"


def _extract_chunks(html_text: str) -> list[str]:
    """
    Split on the block-level tags that separate one hours line from the next.

    Missing ``<li>``/``</li>``/``<ul>``/``</ul>`` was a real bug, not just an
    omission: עין בוקק's page lists Sun-Thu-and-Saturday and Friday as two
    separate ``<li>`` lines with no ``<p>``/``<br>``/``<h4>`` between them, so
    both stayed one merged chunk and ``_TIME_SPAN_RE.search`` (first match
    only) silently kept the Sun-Thu span and discarded Friday's — the page
    genuinely gives Friday hours, but hours_verified came out False anyway.
    """
    return [
        c for c in re.split(r"</?p>|<br\s*/?>|</?h4>|</?strong>|</?ul>|</?li>", html_text) if c.strip()
    ]


def parse_special_hours_html(raw_html: str) -> tuple[Optional[dict], bool]:
    """
    Parse the free-text ``Special_Opening_hours_s`` field into a weekday schedule.

    Deliberately narrow, matching BRIEF_data_acquisition.md rule 6's spirit
    ("never guess, never interpolate"): only recognises a Sun-Thu day-range
    line and a Friday line, each with one ``class="time"`` span, optionally
    duplicated under "קיץ"/"חורף" (summer/winter) headers. A month-qualified
    sub-override ("בחודשים אלה...") is skipped, not merged in. Saturday is
    never inferred from prose like "same as regular hours" — it is either
    given its own explicit time span or left null.

    When both a summer and a winter window exist for the same day, this
    takes the intersection (latest open, earliest close) — the schedule
    that is never wrong regardless of which season it actually is, since
    this seed has no per-visit date to pick the right season with.

    Returns (opening_hours, hours_verified). Anything outside this pattern
    — and there is real variety across ~80 real pages this was not tuned
    against individually — yields (None, False), which is the honest
    outcome BRIEF_data_acquisition.md asks for rather than a guess.
    """
    if not raw_html or not raw_html.strip():
        return None, False

    text = html.unescape(raw_html)
    chunks = _extract_chunks(text)

    # weekday -> list of (open, close) windows found (one per season, or one total)
    windows: dict[Weekday, list[tuple[str, str]]] = {}
    season = None  # tracked only for logging; windows already separate by weekday

    for chunk in chunks:
        if _SUMMER_HEADER_RE.search(chunk) and "<span" not in chunk:
            season = "summer"
            continue
        if _WINTER_HEADER_RE.search(chunk) and "<span" not in chunk:
            season = "winter"
            continue

        span_match = _TIME_SPAN_RE.search(chunk)
        if not span_match:
            continue
        if _MONTH_QUALIFIER_RE.search(chunk):
            continue  # a dated sub-override — not part of the base weekly schedule

        close_raw, open_raw = span_match.group(1), span_match.group(2)
        try:
            open_time, close_time = _normalize_time(open_raw), _normalize_time(close_raw)
        except ValueError:
            continue
        if open_time >= close_time:
            continue

        day_range = _DAY_RANGE_RE.search(chunk)
        days: list[Weekday] = []
        if day_range:
            expanded = common.expand_day_range(day_range.group(1), day_range.group(2))
            if expanded:
                days = expanded
        elif _FRIDAY_RE.search(chunk):
            days = [Weekday.FRI]
        elif _SATURDAY_RE.search(chunk):
            days = [Weekday.SAT]

        for day in days:
            windows.setdefault(day, []).append((open_time, close_time))

    if not windows:
        return None, False

    result: dict[str, Optional[tuple[str, str]]] = {day.value: None for day in Weekday}
    for day, day_windows in windows.items():
        latest_open = max(w[0] for w in day_windows)
        earliest_close = min(w[1] for w in day_windows)
        if latest_open < earliest_close:
            result[day.value] = (latest_open, earliest_close)

    # Verified only if the base week (Sun-Thu) and Friday all resolved —
    # Saturday is a bonus if present, never required.
    base_week = (Weekday.SUN, Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU, Weekday.FRI)
    verified = all(result[day.value] is not None for day in base_week)
    return result, verified


# --------------------------------------------------------------------------
# Per-post extraction
# --------------------------------------------------------------------------


def extract_seed_entry(post: dict, *, scraped_on: str) -> Optional[dict]:
    """
    One reserve-park REST post -> one seed_names.json entry, or None if unusable.

    A post with no usable ITM coordinates is NOT dropped: per
    BRIEF_data_acquisition.md Phase 1 step 4, it is still emitted with
    lat/lng left null, for fetch_osm.py to resolve by name later. Region
    still has to come from somewhere, though (SeedName requires it) — this
    falls back to the site's own "trip-area" taxonomy in that case. Only a
    post with neither a coordinate nor a recognised trip-area term is
    dropped, logged with a reason.
    """
    name_he = html.unescape(str(post.get("title", ""))).strip()
    url = post.get("link", "")
    if not name_he:
        logger.warning("post %s has no title; skipping", post.get("id"))
        return None

    lat: Optional[float]
    lng: Optional[float]
    raw_lat, raw_lon = post.get("lat"), post.get("lon")
    try:
        itm_x, itm_y = float(raw_lat), float(raw_lon)
        lat, lng = itm_to_wgs84(itm_x, itm_y)
    except (TypeError, ValueError):
        lat, lng = None, None
    else:
        if not _looks_like_israel(lat, lng):
            logger.warning(
                "%s (%s): ITM coordinate (נ\"צ %s/%s) resolves to (%.4f, %.4f), outside Israel — "
                "treating as no coordinate, source data looks wrong",
                name_he, url, raw_lat, raw_lon, lat, lng,
            )
            lat, lng = None, None

    override = regions.region_override(name_he)
    if override is not None:
        region = override.value
    elif lat is not None:
        region = region_from_coordinates(lat, lng)
    else:
        region = region_from_trip_area(post.get("trip-area") or [])
        if region is None:
            logger.warning(
                "%s (%s): no coordinates and no recognised trip-area term; skipping", name_he, url
            )
            return None
        logger.info("%s (%s): no coordinates — region from trip-area, lat/lng left null", name_he, url)

    hours_field = post.get("Park_information_on_time") or {}
    special_html = hours_field.get("Special_Opening_hours_s", "") if isinstance(hours_field, dict) else ""
    opening_hours, hours_verified = parse_special_hours_html(special_html)
    if special_html and not hours_verified:
        logger.info("%s (%s): could not fully parse hours from Special_Opening_hours_s", name_he, url)

    accessible = bool(post.get("accessible_f"))

    return common.emit_seed_entry(
        name_he=name_he,
        region=region,
        category=category_for_title(name_he),
        access=ACCESS,
        lat=lat,
        lng=lng,
        opening_hours=opening_hours,
        hours_verified=hours_verified,
        accessible=accessible,
        source=url,
        scraped_on=scraped_on,
    )


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def fetch_all_posts(*, refresh: bool = False, limit: Optional[int] = None) -> list[dict]:
    """Page through the reserve-park REST endpoint until an empty page or `limit`."""
    posts: list[dict] = []
    page = 1
    while True:
        batch = common.fetch_json_cached(
            API_BASE, CACHE_DIR, params={"page": page, "per_page": PAGE_SIZE}, refresh=refresh
        )
        if not batch:
            break
        posts.extend(batch)
        if limit is not None and len(posts) >= limit:
            return posts[:limit]
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    return posts


def run(*, seed_names_path: Path, refresh: bool, limit: Optional[int]) -> None:
    scraped_on = dt.date.today().isoformat()
    posts = fetch_all_posts(refresh=refresh, limit=limit)
    logger.info("fetched %d reserve-park posts", len(posts))

    entries: list[dict] = []
    dropped = 0
    hours_ok = 0
    for post in posts:
        entry = extract_seed_entry(post, scraped_on=scraped_on)
        if entry is None:
            dropped += 1
            continue
        entries.append(entry)
        if entry["hours_verified"]:
            hours_ok += 1

    existing = load_seed_names_raw(seed_names_path)
    merged, added = merge_into_seed_names(existing, entries)

    seed_names_path.parent.mkdir(parents=True, exist_ok=True)
    seed_names_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"fetched={len(posts)} dropped={dropped} extracted={len(entries)} hours_verified={hours_ok}")
    print(f"wrote {seed_names_path} — {added} new entries added, {len(merged)} total")


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-names", type=Path, default=DEFAULT_SEED_NAMES_PATH)
    parser.add_argument("--refresh", action="store_true", help="bypass the page cache")
    parser.add_argument("--limit", type=int, default=None, help="cap total posts fetched (debugging)")
    args = parser.parse_args(argv)

    run(seed_names_path=args.seed_names, refresh=args.refresh, limit=args.limit)


if __name__ == "__main__":
    main()
