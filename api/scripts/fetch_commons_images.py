"""
Batch-fill Place.images from Wikimedia Commons for every place that has none.

Run with:

    python -m api.scripts.fetch_commons_images --dry-run           # report only
    python -m api.scripts.fetch_commons_images                     # fetch + write
    python -m api.scripts.fetch_commons_images --region north
    python -m api.scripts.fetch_commons_images --refresh --limit 5

An offline batch tool. Never called at request time — see models.PlaceImage
and StopCard.tsx, which only ever read the local /images/ path this script
writes; the running API never talks to Commons.

Two independent searches per place, not one:

  * a coordinate geosearch (``list=geosearch``, a small radius around
    lat/lng) — far more reliable than name matching for Israeli sites, where
    a Commons file's title is usually in English or a transliteration that
    a literal name_he string will not match;
  * a name search (``list=search``) over the File namespace, as a fallback
    for places whose Commons coverage isn't geotagged.

Geosearch results are tried first for exactly that reason (see
``combine_candidate_titles``). At most 3 combined candidates are ever
considered per place — a candidate that fails attribution or licensing is
skipped outright, never backfilled from a 4th candidate, so a place can end
up with fewer than 3 images even when more exist on Commons. That is
deliberate: the alternative (keep searching until 3 succeed) would silently
reach further down the relevance ranking for a worse photo just to hit a
quota.

Every accepted image requires BOTH an ``Artist`` and a ``LicenseShortName``
in its imageinfo extmetadata, and the license must be CC0, CC BY, CC BY-SA
or public domain (``_license_allowed``) — never CC BY-NC or CC BY-ND, which
Commons itself does not host but which this checks explicitly anyway rather
than trusting that. A candidate is also required to be an actual JPEG
(``image/jpeg``): Commons' File namespace also holds SVG diagrams, PDF
scans and other non-photo media, and every downloaded file is named
``<place_id>-<n>.jpg`` — writing non-JPEG bytes under a ``.jpg`` name would
make that extension a lie.

This never overwrites a place that already has images (BRIEF: "with an
empty images list"), and it never picks the "best" photo automatically
beyond what's described above — the printed report exists precisely so a
human eyeballs the matched titles before the seed files are committed. A
wrong photo with a technically-correct credit is still wrong.

Wikimedia's own User-Agent policy (https://meta.wikimedia.org/wiki/User-
Agent_policy) rejects generic/anonymous clients regardless of request rate
— USER_AGENT below names the project and links the repo, not a made-up
contact, since a personal email has no reason to leave this machine for a
third party. Every request (geosearch, name search, imageinfo, and the
image download itself) goes through the one shared rate limiter and the
one shared retry helper (_http_get) — a run that fetches, say, 40 places at
3 API calls plus up to 3 downloads each is still never faster than one
request per second, combined across every call type, not per type.

A 429 or 5xx response is retried up to 3 times with exponential backoff
(2s, 4s, 8s), honouring a Retry-After header when the server sends one.
If a place still fails after retries — or hits a timeout, a malformed
response, or anything else — that place is logged and skipped; it never
aborts the run. The final report always prints matched/skipped/failed
counts, so a batch of 429s shows up as a number to investigate rather than
a stack trace with no summary of how much of the run actually completed.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from api.models import Place
from api.repository.places import DEFAULT_PLACES_DIR

logger = logging.getLogger(__name__)

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia's User-Agent policy wants a client name plus real contact info —
# a repo URL, confirmed to satisfy the policy without sending anything
# personal to a third party (the user was asked and chose this over
# including an email).
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

_last_request_at = 0.0


# --------------------------------------------------------------------------
# License / attribution rules
# --------------------------------------------------------------------------


def _license_allowed(license_short_name: str) -> bool:
    """
    True only for CC0, CC BY, CC BY-SA, or public domain — never CC BY-NC or CC BY-ND.

    Matched on the leading token(s) of Commons' own LicenseShortName string
    (e.g. "CC BY-SA 4.0", "CC0 1.0", "Public domain"), not a substring
    search — a substring check for "CC BY" would also match "CC BY-NC",
    which is exactly the license family this must reject.
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


def _clean_artist(raw_artist: str) -> str:
    """Strip the HTML Commons wraps Artist values in (usually a user-page link) and unescape entities."""
    return html.unescape(_TAG_RE.sub("", raw_artist)).strip()


# --------------------------------------------------------------------------
# Candidate selection (pure — takes already-fetched data, so it's testable
# against fixtures with no network involved)
# --------------------------------------------------------------------------


@dataclass
class ImageCandidate:
    """One Commons file that passed the mime/attribution/license gate, ready to download."""

    title: str
    direct_url: str
    file_page_url: str
    credit: str


def combine_candidate_titles(
    geo_titles: list[str], name_titles: list[str], *, limit: int = DEFAULT_CANDIDATE_LIMIT
) -> list[str]:
    """
    Geosearch titles first, then name-search titles, deduped, capped at ``limit``.

    Geosearch is tried first because it is the more reliable signal for an
    Israeli site (see module docstring) — a name-search title only fills in
    when geosearch didn't already supply enough candidates.
    """
    seen: set[str] = set()
    combined: list[str] = []
    for title in geo_titles + name_titles:
        if title in seen:
            continue
        seen.add(title)
        combined.append(title)
        if len(combined) >= limit:
            break
    return combined


def extract_image_candidate(title: str, imageinfo: dict) -> Optional[ImageCandidate]:
    """
    Build an ``ImageCandidate`` from one title's raw imageinfo, or ``None`` to skip it.

    Skipped when Artist or LicenseShortName is missing (nothing to
    attribute) or the license isn't in the allowed family. Does not check
    mime — the caller (``select_and_validate_images``) does that first, so
    it can report a distinct skip reason.
    """
    extmetadata = imageinfo.get("extmetadata") or {}
    artist_raw = (extmetadata.get("Artist") or {}).get("value")
    license_short = (extmetadata.get("LicenseShortName") or {}).get("value")
    direct_url = imageinfo.get("url")
    if not artist_raw or not license_short or not direct_url:
        return None
    if not _license_allowed(license_short):
        return None

    return ImageCandidate(
        title=title,
        direct_url=direct_url,
        file_page_url=f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}",
        credit=f"{_clean_artist(artist_raw)}, {license_short}",
    )


def _skip_reason(imageinfo: dict) -> str:
    """Human-readable reason a candidate that reached extract_image_candidate was rejected."""
    extmetadata = imageinfo.get("extmetadata") or {}
    artist = (extmetadata.get("Artist") or {}).get("value")
    license_short = (extmetadata.get("LicenseShortName") or {}).get("value")
    if not artist or not license_short:
        return "missing Artist or LicenseShortName metadata — cannot attribute"
    return f"license not allowed: {license_short!r}"


def select_and_validate_images(
    geo_titles: list[str],
    name_titles: list[str],
    imageinfo_by_title: dict[str, Optional[dict]],
    *,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> tuple[list[ImageCandidate], list[str], list[tuple[str, str]]]:
    """
    Pure core of the pipeline: candidate titles + their fetched imageinfo -> final images.

    Returns (matched, considered_titles, skipped) where skipped is a list of
    (title, reason) pairs — surfaced in the printed report so a rejection is
    never silent. See the module docstring for why a rejected candidate is
    never replaced by reaching further down the ranking.
    """
    candidates = combine_candidate_titles(geo_titles, name_titles, limit=candidate_limit)
    matched: list[ImageCandidate] = []
    skipped: list[tuple[str, str]] = []

    for title in candidates:
        imageinfo = imageinfo_by_title.get(title)
        if imageinfo is None:
            skipped.append((title, "Commons returned no imageinfo for this title"))
            continue
        if imageinfo.get("mime") not in ALLOWED_MIME_TYPES:
            skipped.append((title, f"not a JPEG (mime={imageinfo.get('mime')!r})"))
            continue
        candidate = extract_image_candidate(title, imageinfo)
        if candidate is None:
            skipped.append((title, _skip_reason(imageinfo)))
            continue
        matched.append(candidate)

    return matched, candidates, skipped


def needs_images(row: dict) -> bool:
    """Whether a place row has no images yet — missing key or an empty list, both count."""
    return not row.get("images")


def build_image_entries(place_id: str, matched: list[ImageCandidate]) -> list[dict]:
    """
    The ``images`` field to write, in download order: url/credit/source_url per PlaceImage.

    Filenames are 1-indexed (``<place_id>-1.jpg``, ``-2``, ``-3``) to match
    web/public/images/'s existing naming convention (see the עין חמד pilot).
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
# Network (rate-limited, cached) — kept thin and separate from the pure
# logic above so nothing here needs mocking in tests.
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
    first, so a retried request is still throttled the same as a fresh one.
    On a 429/5xx, retries up to len(RETRY_BACKOFF_SECONDS) times, honouring
    a Retry-After header when the server sends one, exponential backoff
    otherwise. Raises requests.HTTPError if the last attempt still failed,
    or whatever requests itself raises on a timeout/connection error — both
    are the caller's job to catch (fetch_images_for_place / download_image's
    caller in run()), never this function's.
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
                "Commons %d (attempt %d/%d) for %s — waiting %ds",
                response.status_code, attempt + 1, len(RETRY_BACKOFF_SECONDS) + 1, url, wait,
            )
            time.sleep(wait)
            continue
        break

    assert response is not None  # the loop always runs at least once
    response.raise_for_status()
    return response


def _cache_key(params: dict) -> str:
    return hashlib.sha1(json.dumps(params, sort_keys=True).encode("utf-8")).hexdigest()


def _api_get_cached(params: dict, *, refresh: bool) -> dict:
    """GET the Commons API, cached to disk by a hash of the full param set."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{_cache_key(params)}.json"
    if cache_file.exists() and not refresh:
        return json.loads(cache_file.read_text(encoding="utf-8"))

    response = _http_get(COMMONS_API, params=params, timeout=30)
    payload = response.json()

    cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def search_commons_by_name(name_he: str, *, limit: int = 6, refresh: bool = False) -> list[str]:
    """File-namespace title search by name_he. Ordered by Commons' own search relevance."""
    params = {
        "action": "query", "list": "search", "srsearch": name_he,
        "srnamespace": "6", "srlimit": str(limit), "format": "json",
    }
    data = _api_get_cached(params, refresh=refresh)
    return [row["title"] for row in data.get("query", {}).get("search", [])]


def search_commons_by_coordinates(
    lat: float, lng: float, *, radius_m: int = DEFAULT_RADIUS_M, limit: int = 6, refresh: bool = False
) -> list[str]:
    """File-namespace titles geotagged within radius_m metres of (lat, lng). Ordered by distance."""
    params = {
        "action": "query", "list": "geosearch", "gscoord": f"{lat}|{lng}",
        "gsradius": str(radius_m), "gsnamespace": "6", "gslimit": str(limit), "format": "json",
    }
    data = _api_get_cached(params, refresh=refresh)
    return [row["title"] for row in data.get("query", {}).get("geosearch", [])]


def fetch_imageinfo(title: str, *, refresh: bool = False) -> Optional[dict]:
    """url/mime/extmetadata for one File: title, or None if Commons returned nothing usable."""
    params = {
        "action": "query", "titles": title, "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime", "format": "json",
    }
    data = _api_get_cached(params, refresh=refresh)
    for page in data.get("query", {}).get("pages", {}).values():
        infos = page.get("imageinfo")
        if infos:
            return infos[0]
    return None


def download_image(url: str, dest_path: Path) -> None:
    """Fetch one image's bytes and write them to dest_path — same shared rate limit and retry policy as every other request."""
    response = _http_get(url, timeout=60)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(response.content)


# --------------------------------------------------------------------------
# Per-place orchestration
# --------------------------------------------------------------------------


@dataclass
class PlaceResult:
    """One place's outcome, enough to both write the seed file and print the eyeball report."""

    place_id: str
    name_he: str
    region: str
    matched: list[ImageCandidate]
    considered: list[str]
    skipped: list[tuple[str, str]]
    # Set only when this place's fetch or download raised — a 429 that
    # survived all retries, a timeout, a malformed response. matched is
    # always [] when this is set: a failed place writes nothing.
    failed_reason: Optional[str] = None


def fetch_images_for_place(
    row: dict, *, radius_m: int = DEFAULT_RADIUS_M, candidate_limit: int = DEFAULT_CANDIDATE_LIMIT, refresh: bool = False
) -> PlaceResult:
    """
    The network-touching half: search, fetch imageinfo for each candidate, then delegate to the pure selector.

    Raises on a network failure (a 429/5xx that survived _http_get's
    retries, a timeout, a connection error) — the caller (run()) is the one
    that decides a single place's failure must not abort the batch.
    """
    lat, lng = row.get("lat"), row.get("lng")
    geo_titles = (
        search_commons_by_coordinates(lat, lng, radius_m=radius_m, refresh=refresh)
        if lat is not None and lng is not None
        else []
    )
    name_titles = search_commons_by_name(row["name_he"], refresh=refresh)
    candidates = combine_candidate_titles(geo_titles, name_titles, limit=candidate_limit)
    imageinfo_by_title = {title: fetch_imageinfo(title, refresh=refresh) for title in candidates}

    matched, considered, skipped = select_and_validate_images(
        geo_titles, name_titles, imageinfo_by_title, candidate_limit=candidate_limit
    )
    return PlaceResult(
        place_id=row["id"], name_he=row["name_he"], region=row["region"],
        matched=matched, considered=considered, skipped=skipped,
    )


def _failed_result(row: dict, error: BaseException) -> PlaceResult:
    """A PlaceResult for a place whose fetch or download raised — logged and reported, never crashes the run."""
    return PlaceResult(
        place_id=row.get("id", "?"), name_he=row.get("name_he", "?"), region=row.get("region", "?"),
        matched=[], considered=[], skipped=[], failed_reason=str(error),
    )


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def summarize(results: list[PlaceResult]) -> tuple[int, int, int]:
    """(matched, skipped, failed) counts — skipped means "processed, no image found", not an error."""
    matched = sum(1 for r in results if r.matched)
    failed = sum(1 for r in results if r.failed_reason is not None)
    skipped = len(results) - matched - failed
    return matched, skipped, failed


def print_report(results: list[PlaceResult], *, dry_run: bool) -> None:
    """Every place considered, with its matched titles, skip reasons, or failure — the eyeball step."""
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
        if not r.considered:
            print("      no Commons candidates found (no geosearch or name-search hits)")
            print()
            continue
        for index, candidate in enumerate(r.matched, start=1):
            print(f"  [{index}] MATCHED  {candidate.title}  —  {candidate.credit}")
        for title, reason in r.skipped:
            print(f"      skipped  {title}  —  {reason}")
        print()


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
                # rest of the batch (BRIEF: "log the place and move on").
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
    parser.add_argument("--radius", type=int, default=DEFAULT_RADIUS_M, help="geosearch radius, metres")
    parser.add_argument("--candidates", type=int, default=DEFAULT_CANDIDATE_LIMIT, help="max candidates per place")
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
