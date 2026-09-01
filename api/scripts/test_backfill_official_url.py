"""Tests for backfill_official_url.py — pure functions, no disk or network."""

from __future__ import annotations

from api.scripts import backfill_official_url as backfill


def test_should_backfill_true_for_http_source_and_empty_official_url():
    assert backfill.should_backfill({"_source": "https://www.parks.org.il/x/"}) is True


def test_should_backfill_false_for_manual_source():
    assert backfill.should_backfill({"_source": "manual"}) is False


def test_should_backfill_false_for_openstreetmap_source():
    assert backfill.should_backfill({"_source": "openstreetmap"}) is False


def test_should_backfill_false_for_osm_node_id_source():
    assert backfill.should_backfill({"_source": "osm:node/123456"}) is False


def test_should_backfill_false_when_official_url_already_set():
    row = {"_source": "https://www.parks.org.il/x/", "official_url": "https://existing.example/"}
    assert backfill.should_backfill(row) is False


def test_should_backfill_false_when_source_missing():
    assert backfill.should_backfill({}) is False


def test_backfill_row_copies_source_into_official_url():
    row = {"id": "x", "name_he": "מקום", "_source": "https://www.parks.org.il/x/"}
    updated = backfill.backfill_row(row)
    assert updated["official_url"] == "https://www.parks.org.il/x/"
    assert updated["_source"] == "https://www.parks.org.il/x/"  # untouched


def test_backfill_row_does_not_mutate_original():
    row = {"id": "x", "name_he": "מקום", "_source": "https://www.parks.org.il/x/"}
    backfill.backfill_row(row)
    assert "official_url" not in row


def test_backfill_row_returns_unchanged_when_not_eligible():
    row = {"id": "x", "name_he": "מקום", "_source": "manual"}
    updated = backfill.backfill_row(row)
    assert updated == row
    assert "official_url" not in updated


def test_backfill_file_fills_eligible_rows_and_reports_the_rest():
    raw = {
        "places": [
            {"id": "a", "name_he": "א", "_source": "https://parks.org.il/a/"},
            {"id": "b", "name_he": "ב", "_source": "manual"},
            {"id": "c", "name_he": "ג", "_source": "openstreetmap"},
        ]
    }
    updated_raw, result = backfill.backfill_file(raw)
    assert updated_raw["places"][0]["official_url"] == "https://parks.org.il/a/"
    assert "official_url" not in updated_raw["places"][1]
    assert "official_url" not in updated_raw["places"][2]
    assert result.filled == [("a", "א")]
    assert result.left_empty == [("b", "ב", "manual"), ("c", "ג", "openstreetmap")]


def test_backfill_file_skips_rows_that_already_have_official_url():
    raw = {
        "places": [
            {
                "id": "a", "name_he": "א",
                "_source": "https://parks.org.il/a/", "official_url": "https://custom.example/",
            },
        ]
    }
    updated_raw, result = backfill.backfill_file(raw)
    assert updated_raw["places"][0]["official_url"] == "https://custom.example/"
    assert result.filled == []
    assert result.left_empty == []
