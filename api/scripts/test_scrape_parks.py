"""
Tests for scrape_parks.py, entirely against local fixtures — never the network.

fixtures/parks_rp_sample.json mirrors the real parks.org.il `rp` REST post
shape, confirmed by hand-inspecting the live site: title as a plain string
(not {"rendered": ...}), lat/lon as Israeli ITM grid values with field names
swapped from the usual convention, and real hours living as free-text HTML
in Park_information_on_time.Special_Opening_hours_s.
"""

from __future__ import annotations

import json
from pathlib import Path

from api.models import Region, Weekday
from api.scripts import _scrape_common as common
from api.scripts import scrape_parks

FIXTURES = Path(__file__).parent / "fixtures"


def _load_posts() -> list[dict]:
    return json.loads((FIXTURES / "parks_rp_sample.json").read_text(encoding="utf-8"))


def _post(post_id: int) -> dict:
    return next(p for p in _load_posts() if p["id"] == post_id)


# --------------------------------------------------------------------------
# ITM -> WGS84 conversion and region assignment
# --------------------------------------------------------------------------


def test_itm_to_wgs84_matches_known_en_gedi_location():
    """En Gedi's real site fields (lat=237322, lon=596631) resolve near its real coordinates."""
    lat, lng = scrape_parks.itm_to_wgs84(237322, 596631)
    assert 31.40 < lat < 31.55
    assert 35.30 < lng < 35.45


def test_ein_gedi_resolves_to_south():
    """The exact bug this whole session started from: Ein Gedi must be south, not central."""
    lat, _ = scrape_parks.itm_to_wgs84(237322, 596631)
    assert scrape_parks.region_from_lat(lat) == "south"


def test_region_from_lat_uses_fetch_osm_seams():
    from api.scripts import fetch_osm

    north_seam = fetch_osm.REGION_BBOXES[Region.NORTH][0]
    central_seam = fetch_osm.REGION_BBOXES[Region.CENTRAL][0]
    assert scrape_parks.region_from_lat(north_seam) == "north"
    assert scrape_parks.region_from_lat(north_seam - 0.001) == "central"
    assert scrape_parks.region_from_lat(central_seam) == "central"
    assert scrape_parks.region_from_lat(central_seam - 0.001) == "south"


# --------------------------------------------------------------------------
# Israel bbox validation — a real live-run bug: גן לאומי גבעות מרר's page
# carries lat="1799799" (a real ITM northing is ~500,000-800,000; the same
# wrong value is baked into the page's own "נ"צ" text, so this is bad
# source data, not a scraping bug), which converts to a coordinate in Iran.
# --------------------------------------------------------------------------


def test_looks_like_israel_accepts_real_coordinate():
    lat, lng = scrape_parks.itm_to_wgs84(237322, 596631)  # En Gedi
    assert scrape_parks._looks_like_israel(lat, lng) is True


def test_looks_like_israel_rejects_out_of_bounds_conversion():
    lat, lng = scrape_parks.itm_to_wgs84(1799799, 638656)  # the גבעות מרר bug
    assert scrape_parks._looks_like_israel(lat, lng) is False


def test_extract_seed_entry_drops_bad_coordinate_with_no_trip_area_fallback():
    post = _post(100006)
    entry = scrape_parks.extract_seed_entry(post, scraped_on="2026-08-31")
    assert entry is None


def test_extract_seed_entry_nulls_bad_coordinate_but_keeps_trip_area_region():
    post = _post(100007)
    entry = scrape_parks.extract_seed_entry(post, scraped_on="2026-08-31")
    assert entry is not None
    assert entry["lat"] is None and entry["lng"] is None
    assert entry["region"] == "south"


# --------------------------------------------------------------------------
# Category from title — a real live-run bug: every parks.org.il entry got
# category "nature" unconditionally, including actual museums.
# --------------------------------------------------------------------------


def test_category_for_title_detects_museum():
    assert scrape_parks.category_for_title("מוזיאון השומרוני הטוב") == "museum"


def test_category_for_title_defaults_to_nature():
    assert scrape_parks.category_for_title("שמורת טבע עין גדי") == "nature"


def test_extract_seed_entry_assigns_museum_category():
    entry = scrape_parks.extract_seed_entry(_post(100008), scraped_on="2026-08-31")
    assert entry["category"] == "museum"


# --------------------------------------------------------------------------
# Hours parsing
# --------------------------------------------------------------------------


def test_parses_clean_summer_winter_hours():
    post = _post(100001)
    html_field = post["Park_information_on_time"]["Special_Opening_hours_s"]
    hours, verified = scrape_parks.parse_special_hours_html(html_field)

    assert verified is True
    # Conservative intersection: winter's earlier close (16:00) wins over summer's 17:00.
    assert hours["sun"] == ("08:00", "16:00")
    assert hours["thu"] == ("08:00", "16:00")
    assert hours["fri"] == ("08:00", "15:00")
    assert hours["sat"] is None


def test_month_qualified_override_line_is_ignored():
    """The 'בחודשים אלה' (in these months) sub-override must not corrupt the base Friday hours."""
    post = _post(100001)
    html_field = post["Park_information_on_time"]["Special_Opening_hours_s"]
    hours, _ = scrape_parks.parse_special_hours_html(html_field)
    assert hours["fri"] == ("08:00", "15:00")  # not the 13:00 override


def test_no_restriction_text_falls_back_to_unverified():
    post = _post(100002)
    html_field = post["Park_information_on_time"]["Special_Opening_hours_s"]
    hours, verified = scrape_parks.parse_special_hours_html(html_field)
    assert hours is None
    assert verified is False


def test_empty_hours_field_falls_back_to_unverified():
    hours, verified = scrape_parks.parse_special_hours_html("")
    assert hours is None
    assert verified is False


def test_list_item_lines_with_no_separating_tags_both_parse():
    """
    The exact bug found on עין בוקק's real page: Sun-Thu-and-Saturday and
    Friday are two <li> lines with nothing between them but <li> tags —
    previously merged into one chunk, so only the first <span class="time">
    (Sun-Thu) was read and Friday's own span was silently dropped, leaving
    hours_verified False despite the page genuinely giving Friday hours.
    """
    post = _post(100009)
    html_field = post["Park_information_on_time"]["Special_Opening_hours_s"]
    hours, verified = scrape_parks.parse_special_hours_html(html_field)
    assert hours["sun"] == ("08:00", "17:00")
    assert hours["thu"] == ("08:00", "17:00")
    assert hours["fri"] == ("08:00", "16:00")
    assert verified is True


def test_partial_week_is_not_verified():
    """Only a Sun-Thu span, no Friday — must not claim verified."""
    html_field = (
        '<p>ימים א\' – ה\' פתוח: <span class="time">17:00 – 08:00</span></p>'
    )
    hours, verified = scrape_parks.parse_special_hours_html(html_field)
    assert verified is False


# --------------------------------------------------------------------------
# Hebrew weekday parsing (_scrape_common)
# --------------------------------------------------------------------------


def test_expand_day_range_sun_to_thu():
    days = common.expand_day_range("א'", "ה'")
    assert days == [Weekday.SUN, Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU]


def test_expand_day_range_rejects_wrapping_range():
    assert common.expand_day_range("ה'", "א'") is None


def test_expand_day_range_rejects_unknown_token():
    assert common.expand_day_range("א'", "ק'") is None


# --------------------------------------------------------------------------
# Per-post extraction
# --------------------------------------------------------------------------


def test_extract_seed_entry_builds_correct_entry():
    post = _post(100001)
    entry = scrape_parks.extract_seed_entry(post, scraped_on="2026-08-30")

    assert entry is not None
    assert entry["name_he"] == "שמורת בדיקה עם שעות"
    assert entry["region"] == "south"
    assert entry["category"] == "nature"
    assert entry["access"] == "gated"
    assert entry["hours_verified"] is True
    assert entry["accessible"] is True
    assert entry["source"] == post["link"]
    assert entry["scraped_on"] == "2026-08-30"


def test_extract_seed_entry_drops_post_with_no_coordinates_and_no_trip_area():
    post = _post(100003)
    entry = scrape_parks.extract_seed_entry(post, scraped_on="2026-08-30")
    assert entry is None


def test_extract_seed_entry_resolves_north_region():
    post = _post(100004)
    entry = scrape_parks.extract_seed_entry(post, scraped_on="2026-08-30")
    assert entry is not None
    assert entry["region"] == "north"


def test_extract_seed_entry_falls_back_to_trip_area_when_no_coordinates():
    """
    A marine reserve with no lat/lon (real posts like this exist on the live
    site — Caesarea's marine reserve, sea-turtle areas) still gets emitted,
    per BRIEF_data_acquisition.md Phase 1 step 4: lat/lng null, region from
    trip-area, for fetch_osm.py to resolve the coordinate by name later.
    """
    post = _post(100005)
    entry = scrape_parks.extract_seed_entry(post, scraped_on="2026-08-30")
    assert entry is not None
    assert entry["region"] == "north"
    assert entry["lat"] is None
    assert entry["lng"] is None


def test_region_from_trip_area_ignores_west_bank_terms():
    """4690 (יו"ש) and 4695 (שומרון) deliberately aren't mapped — no clean fit in the 3-region model."""
    assert scrape_parks.region_from_trip_area([4690]) is None
    assert scrape_parks.region_from_trip_area([4695]) is None
    assert scrape_parks.region_from_trip_area([4690, 4698]) == "north"  # a recognised term still wins


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------


def test_fetch_all_posts_stops_on_short_page(monkeypatch):
    """A page shorter than PAGE_SIZE means no more pages — must not fetch again."""
    posts = _load_posts()
    call_count = {"n": 0}

    def fake_fetch(url, cache_dir, *, params=None, refresh=False):
        call_count["n"] += 1
        assert params["page"] == 1  # only ever asked for page 1
        return posts

    monkeypatch.setattr(scrape_parks.common, "fetch_json_cached", fake_fetch)
    result = scrape_parks.fetch_all_posts()

    assert result == posts
    assert call_count["n"] == 1


def test_fetch_all_posts_respects_limit(monkeypatch):
    posts = _load_posts()
    monkeypatch.setattr(scrape_parks.common, "fetch_json_cached", lambda *a, **k: posts)
    result = scrape_parks.fetch_all_posts(limit=2)
    assert len(result) == 2


# --------------------------------------------------------------------------
# emit_seed_entry always carries provenance (BRIEF_data_acquisition.md rule 5)
# --------------------------------------------------------------------------


def test_emit_seed_entry_always_carries_source_and_scraped_on():
    entry = common.emit_seed_entry(
        name_he="x", region="north", category="nature", access="gated",
        source="https://example.com", scraped_on="2026-08-30",
    )
    assert entry["source"] == "https://example.com"
    assert entry["scraped_on"] == "2026-08-30"
