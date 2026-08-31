"""Tests for report_region_drift.py, entirely against inline fixtures."""

from __future__ import annotations

from api.domain import regions
from api.models import Region
from api.scripts import report_region_drift as report


def _row(**overrides) -> dict:
    base = {
        "id": "central-seed-test",
        "name_he": "מקום בדיקה",
        "region": "central",
        "lat": 32.10,
        "lng": 34.90,
    }
    base.update(overrides)
    return base


def test_unchanged_place_is_not_reported():
    rows = [_row()]
    assert report.find_region_drift(rows) == []


def test_valley_place_stored_as_central_is_reported_as_north():
    """The exact real bug: a Beit Shean-valley place stored as central under the old single-seam rule."""
    rows = [_row(id="central-seed-5ca1b60977", name_he="גן לאומי בית שאן", lat=32.5005838532157, lng=35.500253230019474)]
    drift = report.find_region_drift(rows)
    assert drift == [("central-seed-5ca1b60977", "גן לאומי בית שאן", "central", "north")]


def test_place_with_no_coordinate_is_not_reported():
    """No lat/lng and no override — nothing to recompute from, so it's silently skipped, not flagged."""
    rows = [_row(lat=None, lng=None)]
    assert report.find_region_drift(rows) == []


def test_override_wins_and_is_reported(monkeypatch):
    monkeypatch.setitem(regions.REGION_OVERRIDES_HE, "מקום בדיקה", Region.SOUTH)
    rows = [_row()]  # lat/lng resolve to central; override says south
    drift = report.find_region_drift(rows)
    assert drift == [("central-seed-test", "מקום בדיקה", "central", "south")]


def test_run_prints_no_changes_message(tmp_path, capsys):
    import json

    places_path = tmp_path / "places.json"
    places_path.write_text(json.dumps({"places": [_row()]}), encoding="utf-8")
    report.run(places_path)
    assert "no region changes" in capsys.readouterr().out


def test_run_prints_each_drifted_place(tmp_path, capsys):
    import json

    places_path = tmp_path / "places.json"
    row = _row(id="central-seed-5ca1b60977", name_he="גן לאומי בית שאן", lat=32.5005838532157, lng=35.500253230019474)
    places_path.write_text(json.dumps({"places": [row]}, ensure_ascii=False), encoding="utf-8")
    report.run(places_path)
    out = capsys.readouterr().out
    assert "central-seed-5ca1b60977" in out
    assert "central -> north" in out
