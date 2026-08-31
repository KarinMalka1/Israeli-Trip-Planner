"""
Tests for curate_osm_open.py (BRIEF_data_acquisition.md Phase 2), entirely
against inline fixtures — never the network. resolve_hiking_route_start is
tested via a pre-seeded cache file, exercising the cache-hit path only.
"""

from __future__ import annotations

import json

import pytest

from api.scripts import curate_osm_open as curate
from api.scripts import fetch_osm


def _candidate(**overrides) -> dict:
    base = {
        "name_he": "מעיין בדיקה",
        "region": "north",
        "guessed_category": "nature",
        "guessed_access": "open",
        "fee": None,
        "matched_tag": "natural=spring",
        "area_m2": None,
        "has_wikidata_or_wikipedia": False,
        "has_wikipedia": False,
        "lat": 32.9,
        "lng": 35.4,
        "opening_hours_raw": None,
        "tags": ["spring"],
        "_osm_id": 1001,
        "_osm_type": "node",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# Tag filtering
# --------------------------------------------------------------------------


def test_only_phase_2_tags_are_considered():
    museum = _candidate(matched_tag="tourism=museum", has_wikidata_or_wikipedia=True)
    entries, stats = curate.curate([museum])
    assert entries == []
    assert stats["considered"] == 0


# --------------------------------------------------------------------------
# nature_reserve fee gate
# --------------------------------------------------------------------------


def test_nature_reserve_with_fee_is_excluded():
    reserve = _candidate(
        matched_tag="leisure=nature_reserve", fee="yes", has_wikipedia=True
    )
    entries, stats = curate.curate([reserve])
    assert entries == []
    assert stats["gated_reserve"] == 1


def test_nature_reserve_without_fee_is_included_when_significant():
    reserve = _candidate(
        matched_tag="leisure=nature_reserve", fee=None, has_wikipedia=True
    )
    entries, _ = curate.curate([reserve])
    assert len(entries) == 1
    assert entries[0]["access"] == "open"
    assert entries[0]["category"] == "nature"


# --------------------------------------------------------------------------
# Significance signals — an actual Wikipedia article, nothing else counts
# --------------------------------------------------------------------------


def test_significant_via_wikipedia_is_accepted():
    spring = _candidate(has_wikipedia=True)
    entries, stats = curate.curate([spring])
    assert len(entries) == 1
    assert stats["accepted"] == 1


def test_bare_wikidata_without_wikipedia_is_not_significant():
    """A second version accepted any wikidata tag — dropped, it accepted 445 places, mostly
    natural=spring stubs from a WikiProject that catalogued nearly every named spring."""
    spring = _candidate(has_wikidata_or_wikipedia=True, has_wikipedia=False)
    entries, stats = curate.curate([spring])
    assert entries == []
    assert stats["not_significant"] == 1


def test_area_alone_is_not_significant():
    """A first version accepted a large footprint on its own — dropped, it let through reservoirs."""
    viewpoint = _candidate(
        matched_tag="tourism=viewpoint",
        has_wikipedia=False,
        area_m2=fetch_osm.SIGNIFICANT_AREA_M2 + 1,
    )
    entries, stats = curate.curate([viewpoint])
    assert entries == []
    assert stats["not_significant"] == 1


def test_insignificant_candidate_is_dropped():
    spring = _candidate(has_wikipedia=False, area_m2=None)
    entries, stats = curate.curate([spring])
    assert entries == []
    assert stats["not_significant"] == 1


def test_hiking_route_without_wikipedia_is_dropped():
    """A first version exempted every named route=hiking relation — dropped, it accepted ~300 trail segments."""
    route = _candidate(
        matched_tag="route=hiking", has_wikipedia=False, area_m2=None,
        _osm_type="relation", _osm_id=9999,
    )
    entries, stats = curate.curate([route])
    assert entries == []
    assert stats["not_significant"] == 1


def test_hiking_route_with_wikipedia_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_osm, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(fetch_osm, "overpass_post", lambda query: {"elements": []})
    route = _candidate(
        matched_tag="route=hiking", has_wikipedia=True, area_m2=None,
        _osm_type="relation", _osm_id=9999,
    )
    entries, stats = curate.curate([route])
    assert stats["not_significant"] == 0
    assert len(entries) == 1


def test_named_route_membership_no_longer_confers_significance():
    """A first version treated 'sits on a named route' as significance — dropped, it was noise."""
    spring = _candidate(_osm_type="node", _osm_id=555, has_wikipedia=False, area_m2=None)
    entries, stats = curate.curate([spring])
    assert entries == []
    assert stats["not_significant"] == 1


# --------------------------------------------------------------------------
# route=hiking: trailhead resolution
# --------------------------------------------------------------------------


def test_resolve_hiking_route_starts_prefers_first_way_members_first_node(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_osm, "CACHE_DIR", tmp_path)
    payload = {
        "elements": [
            {
                "type": "relation", "id": 7001,
                "members": [
                    {"type": "way", "ref": 501, "role": ""},
                    {"type": "way", "ref": 502, "role": ""},
                ],
            },
            {"type": "way", "id": 501, "geometry": [{"lat": 32.70, "lon": 35.40}, {"lat": 32.71, "lon": 35.41}]},
            {"type": "way", "id": 502, "geometry": [{"lat": 32.72, "lon": 35.42}]},
        ]
    }
    monkeypatch.setattr(fetch_osm, "overpass_post", lambda query: payload)

    starts = curate.resolve_hiking_route_starts([7001], refresh=False)
    assert starts[7001] == (32.70, 35.40)  # first node of the FIRST member way, not the second


def test_resolve_hiking_route_starts_falls_back_to_node_member(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_osm, "CACHE_DIR", tmp_path)
    payload = {
        "elements": [
            {
                "type": "relation", "id": 7002,
                "members": [{"type": "node", "ref": 601, "role": "trailhead"}],
            },
            {"type": "node", "id": 601, "lat": 32.80, "lon": 35.50},
        ]
    }
    monkeypatch.setattr(fetch_osm, "overpass_post", lambda query: payload)

    starts = curate.resolve_hiking_route_starts([7002], refresh=False)
    assert starts[7002] == (32.80, 35.50)


def test_resolve_hiking_route_starts_resolves_many_in_one_call(tmp_path, monkeypatch):
    """The whole point of batching: one query, N answers, and it's actually ONE call."""
    payload = {
        "elements": [
            {"type": "relation", "id": 1, "members": [{"type": "way", "ref": 11, "role": ""}]},
            {"type": "way", "id": 11, "geometry": [{"lat": 30.0, "lon": 35.0}]},
            {"type": "relation", "id": 2, "members": [{"type": "way", "ref": 22, "role": ""}]},
            {"type": "way", "id": 22, "geometry": [{"lat": 31.0, "lon": 36.0}]},
        ]
    }
    monkeypatch.setattr(fetch_osm, "CACHE_DIR", tmp_path)
    call_count = {"n": 0}

    def fake_post(query):
        call_count["n"] += 1
        return payload

    monkeypatch.setattr(fetch_osm, "overpass_post", fake_post)

    starts = curate.resolve_hiking_route_starts([1, 2], refresh=False)
    assert starts == {1: (30.0, 35.0), 2: (31.0, 36.0)}
    assert call_count["n"] == 1


def test_curate_uses_trailhead_not_bbox_midpoint_for_hiking_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_osm, "CACHE_DIR", tmp_path)
    payload = {
        "elements": [
            {"type": "relation", "id": 8001, "members": [{"type": "way", "ref": 701, "role": ""}]},
            {"type": "way", "id": 701, "geometry": [{"lat": 33.10, "lon": 35.60}]},
        ]
    }
    monkeypatch.setattr(fetch_osm, "overpass_post", lambda query: payload)

    route = _candidate(
        matched_tag="route=hiking", _osm_type="relation", _osm_id=8001,
        has_wikipedia=True,  # route=hiking must carry its own wikipedia article now
        lat=32.00, lng=35.00,  # the (wrong) bbox midpoint discover_region would have used
    )
    entries, _ = curate.curate([route])
    assert len(entries) == 1
    assert entries[0]["lat"] == 33.10 and entries[0]["lng"] == 35.60
    assert entries[0]["category"] == "hike"


# --------------------------------------------------------------------------
# Merge / dedup against seed_names.json
# --------------------------------------------------------------------------


def test_merge_dedupes_by_region_and_name():
    existing = [{"name_he": "מעיין בדיקה", "region": "north", "category": "nature", "access": "open"}]
    new_entries = [
        {"name_he": "מעיין בדיקה", "region": "north", "category": "nature", "access": "open"},  # dup
        {"name_he": "מעיין אחר", "region": "north", "category": "nature", "access": "open"},  # new
    ]
    merged, added = curate.merge_into_seed_names(existing, new_entries)
    assert added == 1
    assert len(merged) == 2
