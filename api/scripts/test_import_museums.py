"""
Tests for import_museums.py (BRIEF_data_acquisition.md Phase 3), entirely
against a local fixture CSV — never the network, since this script never
touches one (it's a file importer, not a scraper).

fixtures/museums_gov_510_sample.csv is a best-effort mirror of a plausible
data.gov.il dataset-510 export shape (Hebrew headers: museum name, city,
address). The real file's actual column names are unverified until it's
downloaded — see import_museums.py's module docstring.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.scripts import import_museums

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_CSV = FIXTURES / "museums_gov_510_sample.csv"


# --------------------------------------------------------------------------
# City -> region
# --------------------------------------------------------------------------


def test_region_for_known_city():
    assert import_museums.region_for_city("ירושלים") == "central"
    assert import_museums.region_for_city("חיפה") == "north"
    assert import_museums.region_for_city("באר שבע") == "south"


def test_region_for_unmapped_city_is_none():
    assert import_museums.region_for_city("כפר לא קיים בדיקה") is None


def test_region_for_city_strips_whitespace():
    assert import_museums.region_for_city("  ירושלים  ") == "central"


# --------------------------------------------------------------------------
# CSV loading and column resolution
# --------------------------------------------------------------------------


def test_load_museum_rows_resolves_known_columns():
    rows = import_museums.load_museum_rows(SAMPLE_CSV)
    assert len(rows) == 4
    assert rows[0]["name_he"] == "מוזיאון ישראל"
    assert rows[0]["city"] == "ירושלים"


def test_load_museum_rows_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        import_museums.load_museum_rows(FIXTURES / "does_not_exist.csv")


def test_resolve_column_raises_on_unknown_headers():
    with pytest.raises(ValueError, match="could not find"):
        import_museums._resolve_column(["foo", "bar"], import_museums.NAME_COLUMN_CANDIDATES, "museum name")


# --------------------------------------------------------------------------
# Row -> seed entry
# --------------------------------------------------------------------------


def test_build_seed_entries_maps_known_cities():
    rows = import_museums.load_museum_rows(SAMPLE_CSV)
    entries, unmapped = import_museums.build_seed_entries(rows, scraped_on="2026-08-31")

    assert len(entries) == 2  # ירושלים and תל אביב-יפו rows; unmapped city and blank name dropped
    assert entries[0]["name_he"] == "מוזיאון ישראל"
    assert entries[0]["region"] == "central"
    assert entries[0]["category"] == "museum"
    assert entries[0]["access"] == "gated"
    assert entries[0]["opening_hours"] is None
    assert entries[0]["hours_verified"] is False
    assert entries[0]["source"] == "data.gov.il/dataset/510"
    assert entries[0]["scraped_on"] == "2026-08-31"


def test_build_seed_entries_logs_unmapped_city_without_dropping_run():
    rows = import_museums.load_museum_rows(SAMPLE_CSV)
    entries, unmapped = import_museums.build_seed_entries(rows, scraped_on="2026-08-31")
    assert len(unmapped) == 1
    assert "כפר לא קיים בדיקה" in unmapped[0]


def test_build_seed_entries_drops_blank_name_row():
    rows = [{"name_he": "", "city": "באר שבע"}]
    entries, unmapped = import_museums.build_seed_entries(rows, scraped_on="2026-08-31")
    assert entries == []
    assert unmapped == []


# --------------------------------------------------------------------------
# End-to-end run() — dedup against existing seed_names.json
# --------------------------------------------------------------------------


def test_run_dedupes_against_existing_seed_names(tmp_path):
    seed_names_path = tmp_path / "seed_names.json"
    seed_names_path.write_text(
        json.dumps([{"name_he": "מוזיאון ישראל", "region": "central", "category": "museum", "access": "gated"}]),
        encoding="utf-8",
    )

    import_museums.run(csv_path=SAMPLE_CSV, seed_names_path=seed_names_path, scraped_on="2026-08-31")

    merged = json.loads(seed_names_path.read_text(encoding="utf-8"))
    assert len(merged) == 2  # the pre-existing dup + the one genuinely new (Eretz Israel Museum)
    names = {row["name_he"] for row in merged}
    assert "מוזיאון ארץ ישראל" in names
