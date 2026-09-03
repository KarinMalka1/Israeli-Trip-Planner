"""
Batch-fill Place.images for every place that has none — a 5-source waterfall,
not a single Commons geosearch.

Run with:

    python -m api.scripts.fetch_commons_images --dry-run           # report only
    python -m api.scripts.fetch_commons_images                     # fetch + write
    python -m api.scripts.fetch_commons_images --region north
    python -m api.scripts.fetch_commons_images --refresh --limit 5

An offline batch tool. Never called at request time — see models.PlaceImage
and StopCard.tsx, which only ever read the local /images/ path this script
writes; the running API never talks to any of these services.

WHY NOT JUST COMMONS GEOSEARCH
-------------------------------
A radius geosearch returns whatever was photographed near a point, not
photos *of* the place at that point — a spring's geosearch can just as
easily surface someone's hiking-trail selfie 400m away. So this tries, in
order, the sources most likely to be an exact match for the place itself,
and only falls back to geosearch — flagged ``needs_review`` — when nothing
more precise exists:

  1. Wikidata P18 — a human curated "this is the image of this item" claim.
     The place's Wikidata item is found by searching its Hebrew label
     (``wbsearchentities``); our seed data has no stored Wikidata QID (the
     OSM import only kept a has_wikidata_or_wikipedia boolean, not the tag
     value itself — see fetch_osm.py), so this is a live name search, not a
     tag lookup.
  2. Wikipedia's own "page image" (``prop=pageimages``), Hebrew Wikipedia
     first. The Hebrew article title is taken from the Wikidata item's
     hewiki sitelink when we have one (more reliable than assuming name_he
     is the exact article title); without a Wikidata match, name_he is
     tried directly as a last-ditch guess. English Wikipedia is only tried
     via the *same* Wikidata item's enwiki sitelink — there is no English
     name field in our data to search with directly.
  3. The Commons category a Wikidata item names via P373, listing files
     directly in it (not descending into subcategories — some categories
     turn out to hold only subcats and nothing here, which is an accepted
     part of the low hit rate, not a bug to work around).
  4. Openverse (api.openverse.org) — aggregates Flickr, Wikimedia and
     museum sources under one API, no key required. Queried with
     ``license=cc0,by,by-sa`` directly, then re-checked against the same
     license policy as everything else (belt and suspenders: an API-side
     filter is not a substitute for verifying the license string we
     actually got back).
  5. Commons geosearch — the original strategy, radius around lat/lng.
     Last resort only, and every match from this source alone is marked
     needs_review: True in the printed report (never in the JSON written
     to the place — the schema doesn't carry that field) so a human
     double-checks it actually depicts the place before it ships.

The waterfall STOPS at the first source that produces at least one usable
image (see ``fetch_images_for_place``) — it does not keep querying sources
2-5 just to pad a match from source 1 up to 3 photos. Wikidata's P18 is a
single-value claim (never more than one photo), so requiring 3 images from
it before accepting it would mean it could never win in practice; requiring
only "at least one" is what makes the priority order mean anything. A
place's images therefore always come from exactly one source, never mixed.

ATTRIBUTION AND LICENSING (identical rule, regardless of source)
------------------------------------------------------------------
Every image requires BOTH a creator and a license string before it is
considered — we cannot attribute what we don't know, and the Place model
(PlaceImage) rejects an image missing either. Only CC0, CC BY, CC BY-SA or
public domain is accepted (``_license_allowed``) — never CC BY-NC or
CC BY-ND. Openverse's short license codes ("by-sa", "cc0", ...) are mapped
to a Commons-style LicenseShortName first (``_openverse_license_short_name``)
specifically so both paths run through the exact same ``_license_allowed``
check — one policy, not two copies of it that could drift apart. Only an
actual JPEG is downloaded (Commons' File namespace and Openverse both also
surface SVGs, PDFs and other non-photo media; every downloaded file is
named ``<place_id>-<n>.jpg``, so anything else would make that extension a
lie).

This never overwrites a place that already has images, and never silently
reaches past a rejected candidate for a "backup" one within the same
source beyond ``candidate_limit`` (default 3) — the printed report exists
precisely so a human eyeballs every match, including the needs_review ones,
before the seed files are committed. A wrong photo with a technically
correct credit is still wrong.

RATE LIMITING, RETRIES, CRASH SAFETY
--------------------------------------
Wikimedia's own User-Agent policy (https://meta.wikimedia.org/wiki/User-
Agent_policy) rejects generic/anonymous clients regardless of request rate
— USER_AGENT below names the project and links the repo, not a personal
contact, since an email has no reason to leave this machine for a third
party. It is sent on every request to every host (Wikidata, Wikipedia,
Commons, Openverse, and the image download itself) — all of them share the
one rate limiter and the one retry helper (``_http_get``), so a run making
dozens of calls across four different APIs is still never faster than one
request per second, combined across every host and call type, not per one.

A 429 or 5xx response is retried up to 3 times with exponential backoff
(2s, 4s, 8s), honouring a Retry-After header when the server sends one.
Each place's entire waterfall is wrapped in one try/except in ``run()`` — a
429 that outlasted every retry, a timeout, a malformed response, anything —
logs that place and moves on; it never aborts the batch. The final report
always prints matched/skipped/failed counts, a breakdown by source, and the
needs_review list, so a batch of failures shows up as numbers to
investigate rather than a stack trace with no summary of what completed.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

from api.models import Place
from api.repository.places import DEFAULT_PLACES_DIR

# Real-world attribution text (a Wikidata monolingual qualifier, an
# Openverse creator name, ...) can carry characters outside whatever
# codepage a Windows console defaults stdout to (cp1255 on a Hebrew-locale
# machine, for instance) — seen live: printing a perfectly normal credit
# string crashed the whole run with UnicodeEncodeError on the final report,
# after every download and write had already succeeded. Reconfiguring here
# means the run can never lose its own summary to a console encoding quirk
# unrelated to the actual work.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logger = logging.getLogger(__name__)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_API_TMPL = "https://{lang}.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
OPENVERSE_API = "https://api.openverse.org/v1/images/"

# Wikimedia's User-Agent policy wants a client name plus real contact info —
# a repo URL, confirmed to satisfy the policy without sending anything
# personal to a third party (the user was asked and chose this over
# including an email). Sent to Openverse too, even though it doesn't
# enforce the same policy — one header, every host.
USER_AGENT = "IsraeliTripPlanner/0.1 (https://github.com/KarinMalka1/Israeli-Trip-Planner)"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_DIR = DATA_DIR / ".cache" / "commons_images"
DEFAULT_IMAGES_DIR = Path(__file__).resolve().parent.parent.parent / "web" / "public" / "images"

RATE_LIMIT_SECONDS = 1.0
DEFAULT_RADIUS_M = 500
DEFAULT_CANDIDATE_LIMIT = 3

# 429 (rate limited) or any 5xx (server-side trouble) get retried; anything
# else (404, a malformed response, a timeout) is not retryable and is left
# to the caller's try/except to log and skip that one place.
RETRY_BACKOFF_SECONDS = (2, 4, 8)

# Only a real photo gets downloaded and renamed to a fixed .jpg extension —
# see the module docstring for why this is JPEG-only rather than also
# accepting PNG/WEBP under the same .jpg name.
ALLOWED_MIME_TYPES = {"image/jpeg"}

SOURCE_WIKIDATA = "wikidata"
SOURCE_WIKIPEDIA_HE = "wikipedia_he"
SOURCE_WIKIPEDIA_EN = "wikipedia_en"
SOURCE_COMMONS_CATEGORY = "commons_category"
SOURCE_OPENVERSE = "openverse"
SOURCE_COMMONS_GEOSEARCH = "commons_geosearch"

# Priority order the waterfall tries sources in (module docstring).
SOURCE_ORDER = (
    SOURCE_WIKIDATA,
    SOURCE_WIKIPEDIA_HE,
    SOURCE_WIKIPEDIA_EN,
    SOURCE_COMMONS_CATEGORY,
    SOURCE_OPENVERSE,
    SOURCE_COMMONS_GEOSEARCH,
)

# A match from this source alone is never precise enough to trust outright —
# see the module docstring's point 5.
NEEDS_REVIEW_SOURCES = {SOURCE_COMMONS_GEOSEARCH}

_last_request_at = 0.0


# --------------------------------------------------------------------------
# License / attribution rules — one policy, shared by every source (see
# _openverse_license_short_name, which maps Openverse's codes into this
# same vocabulary rather than duplicating the allow-list).
# --------------------------------------------------------------------------


def _license_allowed(license_short_name: str) -> bool:
    """
    True only for CC0, CC BY, CC BY-SA, or public domain — never CC BY-NC or CC BY-ND.

    Matched on the leading token(s) of the LicenseShortName string (e.g.
    "CC BY-SA 4.0", "CC0 1.0", "Public domain"), not a substring search — a
    substring check for "CC BY" would also match "CC BY-NC", which is
    exactly the license family this must reject.
    """
    tokens = license_short_name.strip().upper().split()
    if not tokens:
        return False
    if tokens[0] == "CC0":
        return True
    if tokens[0] == "CC" and len(tokens) >= 2 and tokens[1] in ("BY", "BY-SA"):
        return True
    lowered = license_short_name.strip().lower()
    return lowered.startswith("public domain") or lowered.startswith("pd")


_TAG_RE = re.compile(r"<[^>]+>")

# Some Commons files — composited historical maps in particular — carry an
# Artist field that is a multi-paragraph sourcing note, not a name (seen
# live: "Sources for historical series of maps as follows:\n\nPEF Survey of
# Palestine\n..."). That is technically present but useless as a UI credit
# caption, so it is treated the same as a missing Artist rather than
# printed under a photo.
MAX_ARTIST_LENGTH = 120


def _clean_artist(raw_artist: str) -> str:
    """Strip the HTML Commons wraps Artist values in (usually a user-page link) and unescape entities."""
    return html.unescape(_TAG_RE.sub("", raw_artist)).strip()


def _looks_like_a_credit_name(artist: str) -> bool:
    """False for a multi-line or implausibly long Artist value — a sourcing note, not a name."""
    return "\n" not in artist and len(artist) <= MAX_ARTIST_LENGTH


_OPENVERSE_LICENSE_MAP = {
    "cc0": "CC0 1.0",
    "by": "CC BY 4.0",
    "by-sa": "CC BY-SA 4.0",
}


def _openverse_license_short_name(license_code: Optional[str]) -> Optional[str]:
    """Map Openverse's short license code onto the same LicenseShortName vocabulary _license_allowed checks."""
    if not license_code:
        return None
    return _OPENVERSE_LICENSE_MAP.get(license_code.strip().lower())


# --------------------------------------------------------------------------
# Candidate extraction (pure — takes already-fetched data, so it's testable
# against fixtures with no network involved)
# --------------------------------------------------------------------------


@dataclass
class ImageCandidate:
    """One photo that passed the mime/attribution/license gate, ready to download."""

    title: str
    direct_url: str
    file_page_url: str
    credit: str
    source: str


def extract_image_candidate(title: str, imageinfo: dict, *, source: str) -> Optional[ImageCandidate]:
    """
    Build an ``ImageCandidate`` from one Commons title's raw imageinfo, or ``None`` to skip it.

    Used for every source that ultimately resolves to a Commons File:
    title — Wikidata P18, both Wikipedia pageimages, the P373 category, and
    geosearch alike — since a Commons file's imageinfo is always the source
    of truth for its own attribution and license, however the title itself
    was found. Openverse is the one source with no Commons title at all;
    see extract_openverse_candidate for its separate path.
    """
    extmetadata = imageinfo.get("extmetadata") or {}
    artist_raw = (extmetadata.get("Artist") or {}).get("value")
    license_short = (extmetadata.get("LicenseShortName") or {}).get("value")
    direct_url = imageinfo.get("url")
    if not artist_raw or not license_short or not direct_url:
        return None
    if not _license_allowed(license_short):
        return None
    artist = _clean_artist(artist_raw)
    if not _looks_like_a_credit_name(artist):
        return None

    return ImageCandidate(
        title=title,
        direct_url=direct_url,
        file_page_url=f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}",
        credit=f"{artist}, {license_short}",
        source=source,
    )


def _skip_reason(imageinfo: dict) -> str:
    """Human-readable reason a Commons candidate that reached extract_image_candidate was rejected."""
    extmetadata = imageinfo.get("extmetadata") or {}
    artist_raw = (extmetadata.get("Artist") or {}).get("value")
    license_short = (extmetadata.get("LicenseShortName") or {}).get("value")
    if not artist_raw or not license_short:
        return "missing Artist or LicenseShortName metadata — cannot attribute"
    if not _license_allowed(license_short):
        return f"license not allowed: {license_short!r}"
    return "Artist metadata isn't a plain name (too long or multi-line) — cannot use as a credit"


def validate_commons_titles(
    titles: list[str],
    imageinfo_by_title: dict[str, Optional[dict]],
    *,
    source: str,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> tuple[list[ImageCandidate], list[tuple[str, str]]]:
    """
    Pure core shared by every Commons-title-based source: titles + fetched imageinfo -> (matched, skipped).

    Considers at most ``limit`` titles and never backfills from beyond that
    limit when one is rejected — see the module docstring for why.
    """
    matched: list[ImageCandidate] = []
    skipped: list[tuple[str, str]] = []

    for title in titles[:limit]:
        imageinfo = imageinfo_by_title.get(title)
        if imageinfo is None:
            skipped.append((title, "no imageinfo returned for this title"))
            continue
        if imageinfo.get("mime") not in ALLOWED_MIME_TYPES:
            skipped.append((title, f"not a JPEG (mime={imageinfo.get('mime')!r})"))
            continue
        candidate = extract_image_candidate(title, imageinfo, source=source)
        if candidate is None:
            skipped.append((title, _skip_reason(imageinfo)))
            continue
        matched.append(candidate)

    return matched, skipped


def _openverse_is_jpeg(item: dict) -> bool:
    """Openverse's own filetype field when present, else a best-effort check of the image URL's extension."""
    filetype = (item.get("filetype") or "").strip().lower()
    if filetype:
        return filetype in ("jpg", "jpeg")
    url = (item.get("url") or "").strip().lower()
    return url.endswith(".jpg") or url.endswith(".jpeg")


def extract_openverse_candidate(item: dict) -> Optional[ImageCandidate]:
    """
    Build an ``ImageCandidate`` from one Openverse search result, or ``None`` to skip it.

    Skipped when creator, license or the image URL is missing (same "we
    cannot attribute what we don't know" rule as Commons), when the license
    isn't in the CC0/BY/BY-SA family (re-checked here even though the
    Openverse query itself already filters on license — an API-side filter
    is not proof of what actually came back), or when the file isn't a
    JPEG.
    """
    creator = (item.get("creator") or "").strip()
    direct_url = item.get("url")
    if not creator or not direct_url:
        return None
    license_short = _openverse_license_short_name(item.get("license"))
    if license_short is None or not _license_allowed(license_short):
        return None
    if not _openverse_is_jpeg(item):
        return None

    title = item.get("title") or item.get("id") or "openverse image"
    source_url = item.get("foreign_landing_url") or direct_url
    return ImageCandidate(
        title=title, direct_url=direct_url, file_page_url=source_url,
        credit=f"{creator}, {license_short}", source=SOURCE_OPENVERSE,
    )


def validate_openverse_items(
    items: list[dict], *, limit: int = DEFAULT_CANDIDATE_LIMIT
) -> tuple[list[ImageCandidate], list[tuple[str, str]]]:
    """Pure: Openverse search results -> (matched, skipped), same shape as validate_commons_titles."""
    matched: list[ImageCandidate] = []
    skipped: list[tuple[str, str]] = []

    for item in items[:limit]:
        title = item.get("title") or item.get("id") or "(untitled)"
        candidate = extract_openverse_candidate(item)
        if candidate is None:
            creator = item.get("creator")
            license_code = item.get("license")
            if not creator or not item.get("url"):
                skipped.append((title, "missing creator or image URL — cannot attribute"))
            elif _openverse_license_short_name(license_code) is None or not _license_allowed(
                _openverse_license_short_name(license_code) or ""
            ):
                skipped.append((title, f"license not allowed: {license_code!r}"))
            else:
                skipped.append((title, "not a JPEG"))
            continue
        matched.append(candidate)

    return matched, skipped


# --------------------------------------------------------------------------
# Wikidata claim/sitelink extraction (pure)
# --------------------------------------------------------------------------


def extract_p18_filename(entity: dict) -> Optional[str]:
    """The Commons File: title of a Wikidata item's P18 (image) claim, or None if it has none."""
    try:
        value = entity["claims"]["P18"][0]["mainsnak"]["datavalue"]["value"]
    except (KeyError, IndexError, TypeError):
        return None
    return f"File:{value}" if value else None


def extract_p373_category(entity: dict) -> Optional[str]:
    """The Commons Category: title of a Wikidata item's P373 claim, or None if it has none."""
    try:
        value = entity["claims"]["P373"][0]["mainsnak"]["datavalue"]["value"]
    except (KeyError, IndexError, TypeError):
        return None
    return f"Category:{value}" if value else None


def extract_sitelink_title(entity: dict, site: str) -> Optional[str]:
    """The page title of a Wikidata item's sitelink to `site` (e.g. "hewiki", "enwiki"), or None."""
    try:
        return entity["sitelinks"][site]["title"]
    except (KeyError, TypeError):
        return None


# --------------------------------------------------------------------------
# needs_images / build_image_entries
# --------------------------------------------------------------------------


def needs_images(row: dict) -> bool:
    """Whether a place row has no images yet — missing key or an empty list, both count."""
    return not row.get("images")


def build_image_entries(place_id: str, matched: list[ImageCandidate]) -> list[dict]:
    """
    The ``images`` field to write, in download order: url/credit/source_url per PlaceImage.

    Exactly these three fields — needs_review is a report-only concept (see
    module docstring); the Place/PlaceImage schema has no field for it.
    Filenames are 1-indexed (``<place_id>-1.jpg``, ``-2``, ``-3``).
    """
    return [
        {
            "url": f"/images/{place_id}-{index}.jpg",
            "credit": candidate.credit,
            "source_url": candidate.file_page_url,
        }
        for index, candidate in enumerate(matched, start=1)
    ]


# --------------------------------------------------------------------------
# Network (rate-limited, cached across every host) — kept thin and separate
# from the pure logic above so nothing here needs mocking in tests.
# --------------------------------------------------------------------------


def _wait_for_rate_limit() -> None:
    global _last_request_at
    remaining = RATE_LIMIT_SECONDS - (time.monotonic() - _last_request_at)
    if remaining > 0:
        time.sleep(remaining)


def _is_retryable(status_code: int) -> bool:
    """429 (rate limited) or any 5xx — a transient, server-side condition worth retrying."""
    return status_code == 429 or status_code >= 500


def _http_get(url: str, *, params: Optional[dict] = None, timeout: int) -> requests.Response:
    """
    GET url with the shared rate limit and the shared retry policy.

    Every attempt — including retries — goes through _wait_for_rate_limit
    first, so a retried request is still throttled the same as a fresh one,
    and this is the ONE function every host (Wikidata, Wikipedia, Commons,
    Openverse, image downloads) goes through, so the 1 req/sec limit is
    combined across all of them, not per host. On a 429/5xx, retries up to
    len(RETRY_BACKOFF_SECONDS) times, honouring a Retry-After header when
    the server sends one, exponential backoff otherwise. Raises
    requests.HTTPError if the last attempt still failed, or whatever
    requests itself raises on a timeout/connection error — both are the
    caller's job to catch (fetch_images_for_place / download_image's caller
    in run()), never this function's.
    """
    global _last_request_at
    response: Optional[requests.Response] = None
    for attempt in range(len(RETRY_BACKOFF_SECONDS) + 1):
        _wait_for_rate_limit()
        response = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        _last_request_at = time.monotonic()

        if _is_retryable(response.status_code) and attempt < len(RETRY_BACKOFF_SECONDS):
            wait = int(response.headers.get("Retry-After", RETRY_BACKOFF_SECONDS[attempt]))
            logger.warning(
                "%d (attempt %d/%d) for %s — waiting %ds",
                response.status_code, attempt + 1, len(RETRY_BACKOFF_SECONDS) + 1, url, wait,
            )
            time.sleep(wait)
            continue
        break

    assert response is not None  # the loop always runs at least once
    response.raise_for_status()
    return response


def _cache_key(base_url: str, params: dict) -> str:
    return hashlib.sha1(json.dumps([base_url, params], sort_keys=True).encode("utf-8")).hexdigest()


def _api_get_cached(base_url: str, params: dict, *, refresh: bool) -> dict:
    """GET any of the four APIs, cached to disk by a hash of (base_url, full param set)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{_cache_key(base_url, params)}.json"
    if cache_file.exists() and not refresh:
        return json.loads(cache_file.read_text(encoding="utf-8"))

    response = _http_get(base_url, params=params, timeout=30)
    payload = response.json()

    cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def search_wikidata_qid(name_he: str, *, refresh: bool = False) -> Optional[str]:
    """The top Wikidata item ID matching name_he as a Hebrew label, or None."""
    params = {
        "action": "wbsearchentities", "search": name_he, "language": "he",
        "type": "item", "limit": "1", "format": "json",
    }
    data = _api_get_cached(WIKIDATA_API, params, refresh=refresh)
    hits = data.get("search") or []
    return hits[0]["id"] if hits else None


def fetch_wikidata_entity(qid: str, *, refresh: bool = False) -> dict:
    """Claims (for P18/P373) and sitelinks (for hewiki/enwiki titles) for one Wikidata item."""
    params = {"action": "wbgetentities", "ids": qid, "props": "claims|sitelinks", "format": "json"}
    data = _api_get_cached(WIKIDATA_API, params, refresh=refresh)
    return (data.get("entities") or {}).get(qid) or {}


def fetch_wikipedia_pageimage_title(lang: str, page_title: str, *, refresh: bool = False) -> Optional[str]:
    """The Commons File: title of `page_title`'s lead image on `lang`.wikipedia.org, or None."""
    params = {
        "action": "query", "titles": page_title, "prop": "pageimages",
        "piprop": "name", "redirects": "1", "format": "json",
    }
    data = _api_get_cached(WIKIPEDIA_API_TMPL.format(lang=lang), params, refresh=refresh)
    pages = (data.get("query") or {}).get("pages") or {}
    for page in pages.values():
        name = page.get("pageimage")
        if name:
            return f"File:{name}"
    return None


def fetch_commons_category_titles(
    category_title: str, *, limit: int = 6, refresh: bool = False
) -> list[str]:
    """File: titles listed directly in a Commons category (not descending into subcategories)."""
    params = {
        "action": "query", "list": "categorymembers", "cmtitle": category_title,
        "cmnamespace": "6", "cmlimit": str(limit), "format": "json",
    }
    data = _api_get_cached(COMMONS_API, params, refresh=refresh)
    return [row["title"] for row in (data.get("query") or {}).get("categorymembers", [])]


def search_openverse(name_he: str, *, page_size: int = 6, refresh: bool = False) -> list[dict]:
    """Openverse image search results for name_he, pre-filtered to cc0/by/by-sa (re-checked afterwards regardless)."""
    params = {"q": name_he, "license": "cc0,by,by-sa", "page_size": str(page_size)}
    data = _api_get_cached(OPENVERSE_API, params, refresh=refresh)
    return data.get("results") or []


def search_commons_by_coordinates(
    lat: float, lng: float, *, radius_m: int = DEFAULT_RADIUS_M, limit: int = 6, refresh: bool = False
) -> list[str]:
    """File-namespace titles geotagged within radius_m metres of (lat, lng). Ordered by distance. Last-resort source."""
    params = {
        "action": "query", "list": "geosearch", "gscoord": f"{lat}|{lng}",
        "gsradius": str(radius_m), "gsnamespace": "6", "gslimit": str(limit), "format": "json",
    }
    data = _api_get_cached(COMMONS_API, params, refresh=refresh)
    return [row["title"] for row in data.get("query", {}).get("geosearch", [])]


def fetch_imageinfo(title: str, *, refresh: bool = False) -> Optional[dict]:
    """url/mime/extmetadata for one Commons File: title, or None if Commons returned nothing usable."""
    params = {
        "action": "query", "titles": title, "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime", "format": "json",
    }
    data = _api_get_cached(COMMONS_API, params, refresh=refresh)
    for page in data.get("query", {}).get("pages", {}).values():
        infos = page.get("imageinfo")
        if infos:
            return infos[0]
    return None


def _fetch_and_validate_commons_titles(
    titles: list[str], *, source: str, limit: int, refresh: bool
) -> tuple[list[ImageCandidate], list[tuple[str, str]]]:
    """Thin fetch+validate wrapper: title list -> imageinfo per title -> validate_commons_titles."""
    imageinfo_by_title = {title: fetch_imageinfo(title, refresh=refresh) for title in titles[:limit]}
    return validate_commons_titles(titles, imageinfo_by_title, source=source, limit=limit)


def download_image(url: str, dest_path: Path) -> None:
    """Fetch one image's bytes and write them to dest_path — same shared rate limit and retry policy as every other request."""
    response = _http_get(url, timeout=60)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(response.content)


# --------------------------------------------------------------------------
# Per-place orchestration — the waterfall
# --------------------------------------------------------------------------


@dataclass
class SourceAttempt:
    """One source's outcome for one place, kept for the eyeball report even when it found nothing."""

    source: str
    considered: list[str]
    matched: list[ImageCandidate] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class PlaceResult:
    """One place's outcome, enough to both write the seed file and print the eyeball report."""

    place_id: str
    name_he: str
    region: str
    matched: list[ImageCandidate]
    attempts: list[SourceAttempt]
    # Set only when this place's fetch or download raised — a 429 that
    # survived all retries, a timeout, a malformed response. matched is
    # always [] when this is set: a failed place writes nothing.
    failed_reason: Optional[str] = None

    @property
    def source(self) -> Optional[str]:
        """The one source that won the waterfall, or None if nothing matched anywhere."""
        return self.matched[0].source if self.matched else None

    @property
    def needs_review(self) -> bool:
        return self.source in NEEDS_REVIEW_SOURCES


def fetch_images_for_place(
    row: dict, *, radius_m: int = DEFAULT_RADIUS_M, candidate_limit: int = DEFAULT_CANDIDATE_LIMIT, refresh: bool = False
) -> PlaceResult:
    """
    The network-touching waterfall: try each source in SOURCE_ORDER, stop at the first with >=1 match.

    Raises on a network failure (a 429/5xx that survived _http_get's
    retries, a timeout, a connection error) — the caller (run()) is the one
    that decides a single place's failure must not abort the batch.
    """
    name_he = row["name_he"]
    lat, lng = row.get("lat"), row.get("lng")
    attempts: list[SourceAttempt] = []

    def _finish(matched: list[ImageCandidate]) -> PlaceResult:
        return PlaceResult(
            place_id=row["id"], name_he=name_he, region=row["region"], matched=matched, attempts=attempts,
        )

    qid = search_wikidata_qid(name_he, refresh=refresh)
    entity = fetch_wikidata_entity(qid, refresh=refresh) if qid else {}

    # 1. Wikidata P18
    p18_title = extract_p18_filename(entity)
    if p18_title:
        matched, skipped = _fetch_and_validate_commons_titles(
            [p18_title], source=SOURCE_WIKIDATA, limit=candidate_limit, refresh=refresh
        )
        attempts.append(SourceAttempt(SOURCE_WIKIDATA, [p18_title], matched, skipped))
        if matched:
            return _finish(matched)
    else:
        reason = "no matching Wikidata item for this name" if not qid else "Wikidata item has no P18 (image) claim"
        attempts.append(SourceAttempt(SOURCE_WIKIDATA, [], [], [(qid or "(no item found)", reason)]))

    # 2. Wikipedia pageimages — Hebrew (via the Wikidata sitelink when we have one, else name_he itself), then English
    he_page_title = extract_sitelink_title(entity, "hewiki") or name_he
    he_image_title = fetch_wikipedia_pageimage_title("he", he_page_title, refresh=refresh)
    if he_image_title:
        matched, skipped = _fetch_and_validate_commons_titles(
            [he_image_title], source=SOURCE_WIKIPEDIA_HE, limit=candidate_limit, refresh=refresh
        )
        attempts.append(SourceAttempt(SOURCE_WIKIPEDIA_HE, [he_image_title], matched, skipped))
        if matched:
            return _finish(matched)
    else:
        attempts.append(
            SourceAttempt(SOURCE_WIKIPEDIA_HE, [], [], [(he_page_title, "no Wikipedia pageimage found")])
        )

    en_page_title = extract_sitelink_title(entity, "enwiki")
    if en_page_title:
        en_image_title = fetch_wikipedia_pageimage_title("en", en_page_title, refresh=refresh)
        if en_image_title:
            matched, skipped = _fetch_and_validate_commons_titles(
                [en_image_title], source=SOURCE_WIKIPEDIA_EN, limit=candidate_limit, refresh=refresh
            )
            attempts.append(SourceAttempt(SOURCE_WIKIPEDIA_EN, [en_image_title], matched, skipped))
            if matched:
                return _finish(matched)
        else:
            attempts.append(
                SourceAttempt(SOURCE_WIKIPEDIA_EN, [], [], [(en_page_title, "no Wikipedia pageimage found")])
            )
    else:
        attempts.append(
            SourceAttempt(SOURCE_WIKIPEDIA_EN, [], [], [("(no enwiki sitelink)", "no English Wikipedia article linked from Wikidata")])
        )

    # 3. Commons category via Wikidata P373
    category = extract_p373_category(entity)
    if category:
        titles = fetch_commons_category_titles(category, limit=6, refresh=refresh)
        matched, skipped = _fetch_and_validate_commons_titles(
            titles, source=SOURCE_COMMONS_CATEGORY, limit=candidate_limit, refresh=refresh
        )
        attempts.append(SourceAttempt(SOURCE_COMMONS_CATEGORY, titles[:candidate_limit], matched, skipped))
        if matched:
            return _finish(matched)
        if not titles:
            attempts[-1].skipped.append((category, "category has no files listed directly in it"))
    else:
        attempts.append(
            SourceAttempt(SOURCE_COMMONS_CATEGORY, [], [], [("(no P373 claim)", "Wikidata item has no Commons category claim")])
        )

    # 4. Openverse
    items = search_openverse(name_he, page_size=6, refresh=refresh)
    matched, skipped = validate_openverse_items(items, limit=candidate_limit)
    considered = [item.get("title") or item.get("id") or "(untitled)" for item in items[:candidate_limit]]
    if not items:
        skipped = [(name_he, "no Openverse results")]
    attempts.append(SourceAttempt(SOURCE_OPENVERSE, considered, matched, skipped))
    if matched:
        return _finish(matched)

    # 5. Commons geosearch — last resort, flagged needs_review in the report
    if lat is not None and lng is not None:
        geo_titles = search_commons_by_coordinates(lat, lng, radius_m=radius_m, refresh=refresh)
        matched, skipped = _fetch_and_validate_commons_titles(
            geo_titles, source=SOURCE_COMMONS_GEOSEARCH, limit=candidate_limit, refresh=refresh
        )
        attempts.append(SourceAttempt(SOURCE_COMMONS_GEOSEARCH, geo_titles[:candidate_limit], matched, skipped))
    else:
        attempts.append(
            SourceAttempt(SOURCE_COMMONS_GEOSEARCH, [], [], [("(no coordinates)", "place has no lat/lng for geosearch")])
        )

    return _finish(matched if attempts[-1].matched else [])


def _failed_result(row: dict, error: BaseException) -> PlaceResult:
    """A PlaceResult for a place whose fetch or download raised — logged and reported, never crashes the run."""
    return PlaceResult(
        place_id=row.get("id", "?"), name_he=row.get("name_he", "?"), region=row.get("region", "?"),
        matched=[], attempts=[], failed_reason=str(error),
    )


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def summarize(results: list[PlaceResult]) -> tuple[int, int, int]:
    """(matched, skipped, failed) counts — skipped means "processed, no image found anywhere", not an error."""
    matched = sum(1 for r in results if r.matched)
    failed = sum(1 for r in results if r.failed_reason is not None)
    skipped = len(results) - matched - failed
    return matched, skipped, failed


def summarize_by_source(results: list[PlaceResult]) -> dict[str, int]:
    """Count of matched places per winning source, in SOURCE_ORDER (only sources with >=1 match appear)."""
    counts: dict[str, int] = {}
    for r in results:
        if r.source:
            counts[r.source] = counts.get(r.source, 0) + 1
    return {source: counts[source] for source in SOURCE_ORDER if source in counts}


def needs_review_places(results: list[PlaceResult]) -> list[tuple[str, str]]:
    """(place_id, name_he) for every place whose only match came from Commons geosearch."""
    return [(r.place_id, r.name_he) for r in results if r.matched and r.needs_review]


def print_report(results: list[PlaceResult], *, dry_run: bool) -> None:
    """Every place considered — matches with source/title/license, misses with every source's skip reason — then summaries."""
    matched, skipped, failed = summarize(results)
    verb = "would download" if dry_run else "downloaded"
    print(f"matched: {matched} ({verb})  skipped: {skipped}  failed: {failed}  (considered: {len(results)})")
    print()

    for r in results:
        print(f"{r.place_id} — {r.name_he} ({r.region})")
        if r.failed_reason is not None:
            print(f"      FAILED  {r.failed_reason}")
            print()
            continue
        if r.matched:
            review_note = "  [NEEDS REVIEW — geosearch match, verify it's really this place]" if r.needs_review else ""
            print(f"      matched via {r.source}{review_note}")
            for index, candidate in enumerate(r.matched, start=1):
                print(f"  [{index}] {candidate.title}  —  {candidate.credit}")
        else:
            print("      no usable image found in any source")
        for attempt in r.attempts:
            if attempt.matched:
                continue
            for title, reason in attempt.skipped:
                print(f"      {attempt.source}: {title}  —  {reason}")
        print()

    by_source = summarize_by_source(results)
    print("by source:")
    if by_source:
        for source, count in by_source.items():
            print(f"  {source}: {count}")
    else:
        print("  (no matches)")
    print()

    review_list = needs_review_places(results)
    print(f"needs review ({len(review_list)}) — geosearch fallback, verify these actually depict the place:")
    for place_id, name_he in review_list:
        print(f"  {place_id} — {name_he}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def run(
    *,
    places_dir: Path,
    images_dir: Path,
    region_filter: Optional[str],
    radius_m: int,
    candidate_limit: int,
    refresh: bool,
    dry_run: bool,
    limit: Optional[int],
) -> None:
    file_paths = sorted(places_dir.glob("*.json"))
    if region_filter:
        file_paths = [p for p in file_paths if p.stem == region_filter]

    results: list[PlaceResult] = []
    processed = 0

    for file_path in file_paths:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        rows = raw.get("places", [])
        file_dirty = False

        for row in rows:
            if not needs_images(row):
                continue
            if limit is not None and processed >= limit:
                break
            processed += 1

            try:
                result = fetch_images_for_place(
                    row, radius_m=radius_m, candidate_limit=candidate_limit, refresh=refresh
                )
            except Exception as error:
                # A 429 that outlasted every retry, a timeout, a malformed
                # response — one place's network trouble must not lose the
                # rest of the batch.
                logger.warning("fetch failed for %s (%s): %s", row.get("id"), row.get("name_he"), error)
                results.append(_failed_result(row, error))
                continue

            if not result.matched or dry_run:
                results.append(result)
                continue

            entries = build_image_entries(row["id"], result.matched)
            try:
                for index, candidate in enumerate(result.matched, start=1):
                    dest_path = images_dir / f"{row['id']}-{index}.jpg"
                    download_image(candidate.direct_url, dest_path)
            except Exception as error:
                logger.warning("download failed for %s (%s): %s", row["id"], row["name_he"], error)
                results.append(_failed_result(row, error))
                continue

            row["images"] = entries
            Place.model_validate(row)  # fail loudly before a single file is written
            file_dirty = True
            results.append(result)

        if file_dirty:
            file_path.write_text(
                json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            logger.info("wrote %s", file_path)

    print_report(results, dry_run=dry_run)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", choices=["north", "central", "south"])
    parser.add_argument("--places-dir", type=Path, default=DEFAULT_PLACES_DIR)
    parser.add_argument("--images-dir", type=Path, default=DEFAULT_IMAGES_DIR)
    parser.add_argument("--radius", type=int, default=DEFAULT_RADIUS_M, help="geosearch radius, metres (source 5 only)")
    parser.add_argument("--candidates", type=int, default=DEFAULT_CANDIDATE_LIMIT, help="max candidates per source")
    parser.add_argument("--refresh", action="store_true", help="bypass the response cache")
    parser.add_argument("--limit", type=int, default=None, help="cap total places processed (debugging)")
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would be downloaded; write nothing"
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args(argv)
    run(
        places_dir=args.places_dir,
        images_dir=args.images_dir,
        region_filter=args.region,
        radius_m=args.radius,
        candidate_limit=args.candidates,
        refresh=args.refresh,
        dry_run=args.dry_run,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
