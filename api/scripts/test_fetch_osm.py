"""
Tests for fetch_osm.py v2 (enrichment), entirely against local fixtures/inline
data — never the network.

fixtures/overpass_sample.json is the discover-mode fixture, still used by
discover_region's tests (the bulk-scan path kept alive behind --discover).
Enrichment tests (enrich_seed_entry, merge_places) construct their raw
Overpass-shaped responses inline, since each is a tiny, scenario-specific
payload — see enrich_seed_entry's signature, which takes `raw` as a plain
argument rather than fetching it, exactly so tests can do this.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.models import AccessType, Category, DescriptionSource, Region, Weekday
from api.scripts import fetch_osm

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------
# opening_hours mini-parser
# --------------------------------------------------------------------------


def test_clean_opening_hours_parses():
    parsed = fetch_osm.parse_opening_hours("Mo-Fr 08:00-17:00; Sa off")
    assert parsed.ok is True
    assert parsed.hours[Weekday.MON] == ("08:00", "17:00")
    assert parsed.hours[Weekday.FRI] == ("08:00", "17:00")
    assert parsed.hours[Weekday.SAT] is None
    assert parsed.hours[Weekday.SUN] is None  # not mentioned -> not invented as open
    assert Weekday.SAT in parsed.closed_days


def test_exotic_opening_hours_yields_null_and_unverified():
    parsed = fetch_osm.parse_opening_hours("Apr-Oct Mo-Su 08:00-20:00")
    assert parsed.ok is False
    assert all(window is None for window in parsed.hours.values())


# --------------------------------------------------------------------------
# Region bboxes: the Dead Sea / Ein Gedi fix
# --------------------------------------------------------------------------

EIN_GEDI_LAT, EIN_GEDI_LNG = 31.4665833, 35.3878975


def _inside_bbox(lat: float, lng: float, bbox: tuple[float, float, float, float]) -> bool:
    south, west, north, east = bbox
    return south <= lat <= north and west <= lng <= east


def test_ein_gedi_coordinates_resolve_to_south_bbox():
    assert _inside_bbox(EIN_GEDI_LAT, EIN_GEDI_LNG, fetch_osm.REGION_BBOXES[Region.SOUTH])
    assert not _inside_bbox(EIN_GEDI_LAT, EIN_GEDI_LNG, fetch_osm.REGION_BBOXES[Region.CENTRAL])


def test_central_and_south_bboxes_touch_without_a_gap_or_overlap():
    central_south = fetch_osm.REGION_BBOXES[Region.CENTRAL][0]
    south_north = fetch_osm.REGION_BBOXES[Region.SOUTH][2]
    assert central_south == south_north == 31.55


def test_ein_gedi_resolves_via_enrich_seed_entry_when_region_is_south():
    """End-to-end: a name search scoped to region=south finds Ein Gedi; scoped to central would not."""
    seed = fetch_osm.SeedName(
        name_he="מעיין עין גדי", region=Region.SOUTH, category=Category.NATURE, access=AccessType.OPEN
    )
    raw = {
        "elements": [
            {
                "type": "node", "id": 278469815, "lat": EIN_GEDI_LAT, "lon": EIN_GEDI_LNG,
                "tags": {"name:he": "מעיין עין גדי", "natural": "spring"},
            }
        ]
    }
    record, outcome = fetch_osm.enrich_seed_entry(seed, raw, set())
    assert outcome == "matched"
    assert record["region"] == "south"
    assert record["lat"] == pytest.approx(EIN_GEDI_LAT)
    assert record["lng"] == pytest.approx(EIN_GEDI_LNG)


# --------------------------------------------------------------------------
# Discover mode (--discover): the retained bulk scan
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def overpass_raw() -> dict:
    return json.loads((FIXTURES / "overpass_sample.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def discovered(overpass_raw: dict) -> tuple[list[dict], "fetch_osm.DiscoverStats"]:
    return fetch_osm.discover_region(Region.NORTH, overpass_raw)


def _by_osm_id(records: list[dict], osm_id: int) -> dict:
    match = next((r for r in records if r["_osm_id"] == osm_id), None)
    assert match is not None, f"expected an accepted candidate for osm id {osm_id}"
    return match


def _dropped_ids(overpass_raw: dict, records: list[dict]) -> set[int]:
    accepted_ids = {r["_osm_id"] for r in records}
    return {el["id"] for el in overpass_raw["elements"]} - accepted_ids


@pytest.mark.parametrize(
    "osm_id,expected_category",
    [
        (1001, Category.MUSEUM),
        (8001, Category.MUSEUM),
        (8002, Category.MUSEUM),
        (1004, Category.VIEWPOINT),
        (2001, Category.NATURE),
        (2003, Category.NATURE),
        (8003, Category.NATURE),
        (8004, Category.NATURE),
        (8005, Category.NATURE),
        (8006, Category.NATURE),
        (3002, Category.HISTORIC),
        (8007, Category.HISTORIC),
        (8008, Category.HISTORIC),
        (7001, Category.KIDS),
        (7002, Category.HISTORIC),
    ],
)
def test_discover_category_mapping(discovered, osm_id, expected_category):
    records, _ = discovered
    assert _by_osm_id(records, osm_id)["guessed_category"] == expected_category.value


def test_discover_category_priority_nature_reserve_wins_over_historic(discovered):
    """An element tagged both leisure=nature_reserve and historic=archaeological_site is NATURE."""
    records, _ = discovered
    assert _by_osm_id(records, 3001)["guessed_category"] == Category.NATURE.value


def test_discover_no_hebrew_name_is_dropped(overpass_raw, discovered):
    records, _ = discovered
    assert 5001 in _dropped_ids(overpass_raw, records)


def test_discover_nearby_duplicate_produces_one_candidate(overpass_raw, discovered):
    records, _ = discovered
    dropped = _dropped_ids(overpass_raw, records)
    assert (4001 in dropped) != (4002 in dropped)


def test_discover_tiny_park_without_signal_is_dropped_low_signal(overpass_raw, discovered):
    records, stats = discovered
    assert 2002 in _dropped_ids(overpass_raw, records)
    assert stats.low_signal >= 2  # tiny park (2002) + low-signal attraction (7003)


def test_discover_point_park_is_accepted_via_wikidata(discovered):
    records, _ = discovered
    assert _by_osm_id(records, 2003)["guessed_category"] == Category.NATURE.value


def test_discover_low_signal_attraction_without_wikidata_is_dropped(overpass_raw, discovered):
    records, _ = discovered
    assert 7003 in _dropped_ids(overpass_raw, records)


def test_discover_fee_yes_forces_gated_guess(discovered):
    records, _ = discovered
    spring = _by_osm_id(records, 8003)  # natural=spring + fee=yes
    assert spring["guessed_access"] == AccessType.GATED.value
    assert "paid" in spring["tags"]


def test_discover_writes_only_candidates_never_places(overpass_raw, tmp_path, monkeypatch):
    """run_discover must write exactly the given out_path and never touch places.json."""
    monkeypatch.setattr(fetch_osm, "fetch_discover_raw", lambda region, refresh=False: overpass_raw)
    out_path = tmp_path / "candidates.json"
    places_path = tmp_path / "places.json"

    fetch_osm.run_discover([Region.NORTH], limit=None, refresh=False, out_path=out_path)

    assert out_path.exists()
    assert not places_path.exists()
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert "candidates" in written
    assert all("hours_verified" not in c for c in written["candidates"])


# --------------------------------------------------------------------------
# Enrichment mode (default): seed_names.json -> places.json
# --------------------------------------------------------------------------


def test_enrich_exactly_one_match_fills_facts():
    seed = fetch_osm.SeedName(
        name_he="גן לאומי בדיקה", region=Region.CENTRAL, category=Category.NATURE, access=AccessType.GATED
    )
    raw = {
        "elements": [
            {
                "type": "node", "id": 555, "lat": 32.05, "lon": 34.85,
                "tags": {
                    "name:he": "גן לאומי בדיקה",
                    "opening_hours": "Mo-Fr 08:00-17:00; Sa off",
                    "wheelchair": "yes",
                    "natural": "spring",
                },
            }
        ]
    }
    record, outcome = fetch_osm.enrich_seed_entry(seed, raw, set())

    assert outcome == "matched"
    assert record["region"] == "central"       # from seed, not OSM
    assert record["category"] == "nature"      # from seed, not OSM
    assert record["access"] == "gated"          # from seed, not OSM
    assert record["lat"] == 32.05
    assert record["lng"] == 34.85
    assert record["hours_verified"] is True
    assert record["opening_hours"]["sat"] is None
    assert record["accessible"] is True
    assert record["_osm_id"] == 555


def test_enrich_zero_matches_writes_null_coordinates():
    seed = fetch_osm.SeedName(
        name_he="מקום שלא קיים ב-OSM", region=Region.NORTH, category=Category.HISTORIC, access=AccessType.OPEN
    )
    record, outcome = fetch_osm.enrich_seed_entry(seed, {"elements": []}, set())

    assert outcome == "zero_matches"
    assert record["lat"] is None
    assert record["lng"] is None
    assert record["name_he"] == "מקום שלא קיים ב-OSM"
    assert record["region"] == "north"
    assert "_osm_id" not in record


def test_enrich_multiple_matches_writes_null_coordinates():
    seed = fetch_osm.SeedName(
        name_he="תצפית כפולה", region=Region.NORTH, category=Category.VIEWPOINT, access=AccessType.OPEN
    )
    raw = {
        "elements": [
            {"type": "node", "id": 4001, "lat": 32.1000, "lon": 34.80, "tags": {"name:he": "תצפית כפולה"}},
            {"type": "node", "id": 4002, "lat": 32.1004, "lon": 34.80, "tags": {"name:he": "תצפית כפולה"}},
        ]
    }
    record, outcome = fetch_osm.enrich_seed_entry(seed, raw, set())

    assert outcome == "multiple_matches"
    assert record["lat"] is None
    assert record["lng"] is None


def test_enrich_open_access_forces_null_opening_hours_even_with_osm_data():
    """SPEC section 10: access=open is scheduled by daylight, not opening_hours — always null."""
    seed = fetch_osm.SeedName(
        name_he="מפל בדיקה", region=Region.NORTH, category=Category.NATURE, access=AccessType.OPEN
    )
    raw = {
        "elements": [
            {
                "type": "node", "id": 999, "lat": 32.6, "lon": 35.2,
                "tags": {"name:he": "מפל בדיקה", "opening_hours": "Mo-Fr 08:00-17:00"},
            }
        ]
    }
    record, outcome = fetch_osm.enrich_seed_entry(seed, raw, set())
    assert outcome == "matched"
    assert all(window is None for window in record["opening_hours"].values())
    assert record["hours_verified"] is False


def test_enrich_unresolved_place_gets_no_derived_facts():
    seed = fetch_osm.SeedName(
        name_he="עדיין לא נמצא", region=Region.SOUTH, category=Category.KIDS, access=AccessType.GATED
    )
    record, outcome = fetch_osm.enrich_seed_entry(seed, {"elements": []}, set())
    assert outcome == "zero_matches"
    assert record["kid_friendly"] is False
    assert record["accessible"] is False
    assert record["tags"] == []


def test_enrich_id_falls_back_to_hash_when_unresolved_and_no_latin_name():
    seed = fetch_osm.SeedName(
        name_he="שם עברי בלבד", region=Region.NORTH, category=Category.HISTORIC, access=AccessType.OPEN
    )
    record, _ = fetch_osm.enrich_seed_entry(seed, {"elements": []}, set())
    assert record["id"].startswith("north-seed-")


def test_enrich_id_prefers_latin_osm_name_when_matched():
    seed = fetch_osm.SeedName(
        name_he="שם עברי", region=Region.NORTH, category=Category.HISTORIC, access=AccessType.OPEN
    )
    raw = {
        "elements": [
            {"type": "node", "id": 1, "lat": 32.0, "lon": 35.0, "tags": {"name:he": "שם עברי", "name:en": "Nice Place"}}
        ]
    }
    record, _ = fetch_osm.enrich_seed_entry(seed, raw, set())
    assert record["id"] == "north-nice-place"


def test_enrich_pre_supplied_coordinates_skip_osm_entirely():
    """A seed with lat/lng already set (e.g. from parks.org.il) is trusted outright, raw=None."""
    seed = fetch_osm.SeedName(
        name_he="גן לאומי בדיקה מוקדם", region=Region.CENTRAL, category=Category.NATURE,
        access=AccessType.GATED, lat=31.9, lng=34.9,
        opening_hours={d.value: ("08:00", "17:00") if d != Weekday.SAT else None for d in Weekday},
        hours_verified=True, tags=["parking-onsite"], source="https://parks.org.il/example",
        scraped_on="2026-08-30",
    )
    record, outcome = fetch_osm.enrich_seed_entry(seed, None, set())

    assert outcome == "pre_supplied"
    assert record["lat"] == 31.9 and record["lng"] == 34.9
    assert record["hours_verified"] is True
    assert record["opening_hours"]["sat"] is None
    assert record["tags"] == ["parking-onsite"]
    assert record["_source"] == "https://parks.org.il/example"
    assert record["_scraped_on"] == "2026-08-30"
    assert "_osm_id" not in record


def test_enrich_pre_supplied_open_access_still_forces_null_hours():
    seed = fetch_osm.SeedName(
        name_he="מעיין בדיקה מוקדם", region=Region.NORTH, category=Category.NATURE,
        access=AccessType.OPEN, lat=32.9, lng=35.4,
        opening_hours={d.value: ("08:00", "17:00") for d in Weekday}, hours_verified=True,
    )
    record, outcome = fetch_osm.enrich_seed_entry(seed, None, set())
    assert outcome == "pre_supplied"
    assert all(window is None for window in record["opening_hours"].values())
    assert record["hours_verified"] is False


def test_enrich_verified_hours_survive_missing_coordinates():
    """
    The exact bug this test guards against: a real live run silently dropped
    a parks.org.il entry's verified hours (Ein Gedi Nahal Arugot) because its
    page has no coordinates at all. lat/lng=None routes it through the OSM
    name-search branch of enrich_seed_entry, but the seed's own hours_verified
    facts must still win over whatever that name search finds (or fails to
    find) — a `source` is enough to trust the seed's facts even without
    coordinates.
    """
    seed = fetch_osm.SeedName(
        name_he="שמורת טבע בדיקה ללא קואורדינטות", region=Region.SOUTH, category=Category.NATURE,
        access=AccessType.GATED, lat=None, lng=None,
        opening_hours={d.value: ("08:00", "16:00") if d != Weekday.SAT else None for d in Weekday},
        hours_verified=True, accessible=True, tags=["parking-onsite"],
        source="https://www.parks.org.il/reserve-park/example/", scraped_on="2026-08-30",
    )
    # Overpass finds no match by name — must not matter for the facts below.
    record, outcome = fetch_osm.enrich_seed_entry(seed, {"elements": []}, set())

    assert outcome == "zero_matches"
    assert record["lat"] is None and record["lng"] is None
    assert record["hours_verified"] is True
    assert record["opening_hours"]["sat"] is None
    assert record["accessible"] is True
    assert record["tags"] == ["parking-onsite"]
    assert record["_source"] == "https://www.parks.org.il/reserve-park/example/"
    assert record["_scraped_on"] == "2026-08-30"


def test_enrich_sourced_seed_without_coordinates_still_records_osm_match():
    """A seed with `source` but no coordinates that DOES get a single OSM name match keeps both."""
    seed = fetch_osm.SeedName(
        name_he="שמורת טבע עם התאמה", region=Region.NORTH, category=Category.NATURE,
        access=AccessType.GATED, lat=None, lng=None,
        hours_verified=True, source="https://www.parks.org.il/reserve-park/example2/",
    )
    raw = {
        "elements": [
            {"type": "node", "id": 777, "lat": 32.5, "lon": 35.3, "tags": {"name:he": "שמורת טבע עם התאמה"}},
        ]
    }
    record, outcome = fetch_osm.enrich_seed_entry(seed, raw, set())

    assert outcome == "matched"
    assert record["lat"] == 32.5 and record["lng"] == 35.3
    assert record["hours_verified"] is True  # from the seed, not re-derived from the OSM match
    assert record["_source"] == "https://www.parks.org.il/reserve-park/example2/"
    assert record["_osm_id"] == 777


def test_enrich_seasonal_flag_flows_through():
    seed = fetch_osm.SeedName(
        name_he="פארק מים בדיקה", region=Region.CENTRAL, category=Category.KIDS,
        access=AccessType.GATED, season="summer_only",
    )
    record, _ = fetch_osm.enrich_seed_entry(seed, {"elements": []}, set())
    assert record["season"] == "summer_only"


def test_enrich_uses_explicit_duration_min():
    """A hand-set duration_min in the seed (e.g. a water park at 240min) must win over the category default."""
    seed = fetch_osm.SeedName(
        name_he="פארק מים בדיקה", region=Region.CENTRAL, category=Category.KIDS,
        access=AccessType.GATED, duration_min=240,
    )
    record, _ = fetch_osm.enrich_seed_entry(seed, {"elements": []}, set())
    assert record["duration_min"] == 240


def test_enrich_falls_back_to_category_default_duration_min():
    seed = fetch_osm.SeedName(
        name_he="מוזיאון בדיקה", region=Region.CENTRAL, category=Category.MUSEUM, access=AccessType.GATED
    )
    record, _ = fetch_osm.enrich_seed_entry(seed, {"elements": []}, set())
    assert record["duration_min"] == fetch_osm.DEFAULT_DURATION_MIN[Category.MUSEUM]


def test_rerun_does_not_overwrite_explicit_duration_min(tmp_path):
    """The reported bug: re-running fetch_osm silently lost a hand-set duration_min."""
    seed = fetch_osm.SeedName(
        name_he="פארק מים בדיקה", region=Region.CENTRAL, category=Category.KIDS,
        access=AccessType.GATED, lat=32.0, lng=34.9, duration_min=240,
    )
    out_path = tmp_path / "places.json"
    fetch_osm.run_enrich([seed], region_filter=None, refresh=False, out_path=out_path)
    fetch_osm.run_enrich([seed], region_filter=None, refresh=False, out_path=out_path)

    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["places"][0]["duration_min"] == 240


def test_run_enrich_never_fetches_overpass_for_pre_supplied_seeds(tmp_path, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("fetch_matches_for_name must not be called for a pre-supplied seed")

    monkeypatch.setattr(fetch_osm, "fetch_matches_for_name", _boom)
    seed = fetch_osm.SeedName(
        name_he="גן ידוע מראש", region=Region.SOUTH, category=Category.NATURE,
        access=AccessType.OPEN, lat=30.0, lng=34.9,
    )
    out_path = tmp_path / "places.json"
    fetch_osm.run_enrich([seed], region_filter=None, refresh=False, out_path=out_path)
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["places"][0]["lat"] == 30.0


def test_enrich_every_fresh_record_is_marked_generated():
    seed = fetch_osm.SeedName(
        name_he="מקום כלשהו", region=Region.NORTH, category=Category.NATURE, access=AccessType.OPEN
    )
    record, _ = fetch_osm.enrich_seed_entry(seed, {"elements": []}, set())
    assert record["description_source"] == DescriptionSource.GENERATED.value
    assert record["description_he"] == "" and record["tip_he"] == ""


# --------------------------------------------------------------------------
# Merge: identity is (region, name_he); locked rows still take curator fields
# --------------------------------------------------------------------------


def _existing_fixture() -> list[dict]:
    return json.loads((FIXTURES / "existing_places_sample.json").read_text(encoding="utf-8"))["places"]


def test_merge_preserves_locked_osm_facts_but_updates_curator_fields():
    existing = _existing_fixture()
    verified_before = next(r for r in existing if r["id"] == "north-shmura-atika-bdika")
    assert verified_before["hours_verified"] is True
    assert verified_before["category"] == "nature"
    assert verified_before["access"] == "gated"

    # A fresh enrichment run for the same (region, name_he), with a curator
    # correction to category/access and (deliberately) different OSM facts.
    fresh_row = {
        "id": "north-shmura-atika-bdika",  # not used for matching, only for the note text
        "name_he": "שמורה עתיקה בדיקה",
        "region": "north",
        "category": "historic",   # curator correction — should win despite the lock
        "access": "open",         # curator correction — should win despite the lock
        "duration_min": 60,
        "lat": 33.001, "lng": 35.501,   # different from the locked lat/lng
        "opening_hours": {d.value: None for d in Weekday},
        "hours_verified": False,
        "closed_on_shabbat": False,
        "kid_friendly": True,
        "accessible": True,
        "tags": ["archaeology"],
        "_osm_id": 3001, "_osm_type": "way",
    }

    merged, notes = fetch_osm.merge_places(existing, [fresh_row], {"north"})
    merged_by_id = {row["id"]: row for row in merged}
    result = merged_by_id["north-shmura-atika-bdika"]

    # Curator fields refresh even though the row is locked.
    assert result["category"] == "historic"
    assert result["access"] == "open"
    # OSM-derived facts stay exactly as verified — not overwritten.
    assert result["lat"] == 33.5
    assert result["lng"] == 36.0
    assert result["hours_verified"] is True


def test_merge_preserves_human_written_place_untouched():
    existing = _existing_fixture()
    hand_written_before = next(r for r in existing if r["id"] == "north-hand-written-restaurant")

    merged, _ = fetch_osm.merge_places(existing, [], {"north"})
    merged_by_id = {row["id"]: row for row in merged}
    # Not in `fresh` at all and description_source="human" -> flagged stale
    # (no seed_names.json entry backs it), but never mutated otherwise.
    result = merged_by_id["north-hand-written-restaurant"]
    for key, value in hand_written_before.items():
        assert result[key] == value


def test_merge_only_flags_stale_within_processed_regions():
    existing = _existing_fixture()
    # Nothing in `fresh`, and "central" is NOT in processed_regions -> the
    # north rows must be left completely alone, not flagged stale.
    merged, notes = fetch_osm.merge_places(existing, [], {"central"})
    assert not any(row.get("_stale") for row in merged)
    assert not notes


def test_merge_flags_stale_when_region_processed_and_entry_gone():
    existing = _existing_fixture()
    merged, notes = fetch_osm.merge_places(existing, [], {"north"})
    stale_ids = {row["id"] for row in merged if row.get("_stale")}
    assert "north-shmura-atika-bdika" in stale_ids
    assert "north-hand-written-restaurant" in stale_ids
    assert any(note.startswith("stale:") for note in notes)


def test_merge_adds_a_new_entry():
    fresh_row = {
        "id": "north-brand-new", "name_he": "מקום חדש", "region": "north",
        "category": "nature", "access": "open", "duration_min": 90,
        "lat": 32.9, "lng": 35.4,
        "opening_hours": {d.value: None for d in Weekday},
        "hours_verified": False, "closed_on_shabbat": False,
        "kid_friendly": False, "accessible": False, "tags": [],
    }
    merged, notes = fetch_osm.merge_places(_existing_fixture(), [fresh_row], {"north"})
    assert any(row["id"] == "north-brand-new" for row in merged)
    assert any(note.startswith("new:") for note in notes)


# --------------------------------------------------------------------------
# Seed validation pass: warns, never auto-fixes
# --------------------------------------------------------------------------


def _write_seed_names(tmp_path, rows: list[dict]) -> Path:
    path = tmp_path / "seed_names.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_seed_names_warns_on_summer_only_outside_kids(tmp_path, capsys):
    path = _write_seed_names(tmp_path, [
        {
            "name_he": "פארק מים בדיקה", "region": "central", "category": "nature",
            "access": "gated", "season": "summer_only",
        }
    ])
    fetch_osm.load_seed_names(path)
    assert "season=summer_only" in capsys.readouterr().out


def test_load_seed_names_no_warning_for_summer_only_kids(tmp_path, capsys):
    path = _write_seed_names(tmp_path, [
        {
            "name_he": "פארק מים בדיקה", "region": "central", "category": "kids",
            "access": "gated", "season": "summer_only",
        }
    ])
    fetch_osm.load_seed_names(path)
    assert capsys.readouterr().out == ""


def test_load_seed_names_warns_on_swimming_tag_year_round(tmp_path, capsys):
    path = _write_seed_names(tmp_path, [
        {
            "name_he": "בריכה בדיקה", "region": "central", "category": "kids",
            "access": "gated", "season": "year_round", "tags": ["swimming"],
        }
    ])
    fetch_osm.load_seed_names(path)
    assert "swimming" in capsys.readouterr().out


def test_load_seed_names_no_warning_for_swimming_tag_summer_only(tmp_path, capsys):
    path = _write_seed_names(tmp_path, [
        {
            "name_he": "בריכה בדיקה", "region": "central", "category": "kids",
            "access": "gated", "season": "summer_only", "tags": ["swimming"],
        }
    ])
    fetch_osm.load_seed_names(path)
    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_discover_requires_region_or_all():
    with pytest.raises(SystemExit):
        fetch_osm.parse_args(["--discover"])


def test_cli_discover_rejects_both_region_and_all():
    with pytest.raises(SystemExit):
        fetch_osm.parse_args(["--discover", "--region", "north", "--all"])


def test_cli_all_without_discover_is_rejected():
    with pytest.raises(SystemExit):
        fetch_osm.parse_args(["--all"])


def test_cli_enrich_mode_region_is_optional():
    args = fetch_osm.parse_args([])
    assert args.discover is False
    assert args.region is None


def test_cli_enrich_mode_accepts_a_region_filter():
    args = fetch_osm.parse_args(["--region", "south"])
    assert args.region == "south"
