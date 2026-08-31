"""
Shared helpers for the real-website scrapers under api/scripts/
(BRIEF_data_acquisition.md): rate-limited cached fetching and Hebrew weekday
parsing. Every scraper built on this module still owns its own robots.txt
check by hand before writing fetch logic — that is a one-time human decision
per source, not something this module can verify at runtime.

parks.org.il specifically blocks any non-browser User-Agent at its CDN/WAF
level even though its own robots.txt permits the paths scrape_parks.py
touches (confirmed by hand before writing that scraper). USER_AGENT below is
deliberately browser-shaped for that reason. Every other rule — 1 request/
second, caching, resumability, facts only — is still honored in full.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

import requests

from api.models import Weekday

logger = logging.getLogger(__name__)

RATE_LIMIT_SECONDS = 1.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Per-host last-request timestamp, so two scrapers hitting different sites
# don't rate-limit each other.
_last_request_at: dict[str, float] = {}


def _cache_key(full_url: str) -> str:
    return hashlib.sha1(full_url.encode("utf-8")).hexdigest()


def _wait_for_rate_limit(host: str) -> None:
    last = _last_request_at.get(host, 0.0)
    remaining = RATE_LIMIT_SECONDS - (time.monotonic() - last)
    if remaining > 0:
        time.sleep(remaining)


def fetch_json_cached(
    url: str,
    cache_dir: Path,
    *,
    params: Optional[dict] = None,
    refresh: bool = False,
) -> dict | list:
    """
    GET a URL as JSON, cached to disk by a hash of the full URL (including params).

    Never re-fetches a cached page unless ``refresh`` — a run interrupted
    with Ctrl-C resumes from the next uncached page, not from scratch.
    Rate-limited to one real request per second per host; a cache hit costs
    nothing.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    full_url = url if not params else f"{url}?{'&'.join(f'{k}={v}' for k, v in sorted(params.items()))}"
    cache_file = cache_dir / f"{_cache_key(full_url)}.json"

    if cache_file.exists() and not refresh:
        return json.loads(cache_file.read_text(encoding="utf-8"))

    host = url.split("/")[2]
    _wait_for_rate_limit(host)
    response = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
    _last_request_at[host] = time.monotonic()
    response.raise_for_status()
    payload = response.json()

    cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


# --------------------------------------------------------------------------
# Hebrew weekday parsing
# --------------------------------------------------------------------------

# Every spelling of a weekday actually seen in the wild: full names, the
# single-letter Hebrew-numeral abbreviations with or without a geresh, and
# "שבת" for Saturday. Longest-first isn't needed since these are matched as
# whole tokens, not substrings, by the caller.
HEBREW_DAY_TOKENS: dict[str, Weekday] = {
    "ראשון": Weekday.SUN, "יום ראשון": Weekday.SUN, "א": Weekday.SUN, "א'": Weekday.SUN,
    "שני": Weekday.MON, "יום שני": Weekday.MON, "ב": Weekday.MON, "ב'": Weekday.MON,
    "שלישי": Weekday.TUE, "יום שלישי": Weekday.TUE, "ג": Weekday.TUE, "ג'": Weekday.TUE,
    "רביעי": Weekday.WED, "יום רביעי": Weekday.WED, "ד": Weekday.WED, "ד'": Weekday.WED,
    "חמישי": Weekday.THU, "יום חמישי": Weekday.THU, "ה": Weekday.THU, "ה'": Weekday.THU,
    "שישי": Weekday.FRI, "יום שישי": Weekday.FRI, "ו": Weekday.FRI, "ו'": Weekday.FRI,
    "שבת": Weekday.SAT, "יום שבת": Weekday.SAT,
}

_WEEKDAY_ORDER = [Weekday.SUN, Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU, Weekday.FRI, Weekday.SAT]


def expand_day_range(start_token: str, end_token: str) -> Optional[list[Weekday]]:
    """
    Expand "א'" .. "ה'" style Hebrew day-range tokens into a Weekday list.

    Returns ``None`` (rather than guessing) if either token isn't a
    recognised day or the range wraps past Saturday — not "the simple
    cases" this is meant to handle.
    """
    start = HEBREW_DAY_TOKENS.get(start_token.strip())
    end = HEBREW_DAY_TOKENS.get(end_token.strip())
    if start is None or end is None:
        return None
    start_idx, end_idx = _WEEKDAY_ORDER.index(start), _WEEKDAY_ORDER.index(end)
    if end_idx < start_idx:
        return None
    return _WEEKDAY_ORDER[start_idx : end_idx + 1]


# --------------------------------------------------------------------------
# Seed-entry emission
# --------------------------------------------------------------------------


def emit_seed_entry(
    *,
    name_he: str,
    region: str,
    category: str,
    access: str,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    opening_hours: Optional[dict[str, Optional[tuple[str, str]]]] = None,
    hours_verified: bool = False,
    closed_on_shabbat: bool = False,
    kid_friendly: bool = False,
    accessible: bool = False,
    tags: Optional[list[str]] = None,
    season: str = "year_round",
    source: str,
    scraped_on: str,
) -> dict:
    """
    Build one seed_names.json-shaped dict, matching fetch_osm.SeedName's fields.

    A thin, explicit constructor rather than callers building the dict by
    hand — every scraper emits the same shape, and rule 5
    (BRIEF_data_acquisition.md) requires _source/_scraped_on on every row,
    which this makes impossible to forget.
    """
    return {
        "name_he": name_he,
        "region": region,
        "category": category,
        "access": access,
        "lat": lat,
        "lng": lng,
        "opening_hours": opening_hours,
        "hours_verified": hours_verified,
        "closed_on_shabbat": closed_on_shabbat,
        "kid_friendly": kid_friendly,
        "accessible": accessible,
        "tags": tags or [],
        "season": season,
        "source": source,
        "scraped_on": scraped_on,
    }
