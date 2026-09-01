"""
Tests for repository/places.py's directory-based loader.

api/data/places/ holds one *.json file per region rather than one shared
places.json, so two people editing different regions never collide on the
same file. Every scenario here uses a fresh tmp_path directory rather than
the real seed, so these stay independent of whatever is actually curated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.repository.places import PlaceRepository


def _place_row(**overrides) -> dict:
    base = {
        "id": "central-test-1",
        "name_he": "מקום בדיקה",
        "description_he": "",
        "tip_he": "",
        "description_source": "generated",
        "category": "nature",
        "region": "central",
        "access": "open",
        "duration_min": 60,
        "opening_hours": {day: None for day in ("sun", "mon", "tue", "wed", "thu", "fri", "sat")},
        "hours_verified": False,
        "closed_on_shabbat": False,
        "kid_friendly": False,
        "accessible": False,
        "tags": [],
    }
    base.update(overrides)
    return base


def _write(dir_path: Path, filename: str, rows: list[dict]) -> None:
    (dir_path / filename).write_text(json.dumps({"places": rows}, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------
# Combined load across files
# --------------------------------------------------------------------------


def test_two_fixture_files_load_as_one_combined_repository(tmp_path):
    _write(tmp_path, "central.json", [_place_row(id="central-a", name_he="א", region="central")])
    _write(tmp_path, "north.json", [_place_row(id="north-a", name_he="ב", region="north", category="hike")])

    repository = PlaceRepository.load(tmp_path)

    assert len(repository) == 2
    assert repository.get("central-a") is not None
    assert repository.get("north-a") is not None


def test_missing_directory_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        PlaceRepository.load(tmp_path / "does-not-exist")


def test_empty_files_load_to_an_empty_repository(tmp_path):
    _write(tmp_path, "central.json", [])
    _write(tmp_path, "north.json", [])

    repository = PlaceRepository.load(tmp_path)
    assert len(repository) == 0


# --------------------------------------------------------------------------
# Fail loudly: duplicate (region, name_he) across two files
# --------------------------------------------------------------------------


def test_duplicate_across_files_raises_and_names_both_files(tmp_path):
    # "central.json" sorts before "central2.json", so the first file is read
    # (and passes its own region-matches-filename check) before the second
    # file's matching row is reached and caught as a duplicate.
    _write(tmp_path, "central.json", [_place_row(id="central-a", name_he="כפילות", region="central")])
    _write(tmp_path, "central2.json", [_place_row(id="central-b", name_he="כפילות", region="central")])

    with pytest.raises(ValueError) as excinfo:
        PlaceRepository.load(tmp_path)

    message = str(excinfo.value)
    assert "central.json" in message
    assert "central2.json" in message
    assert "כפילות" in message


def test_same_name_different_region_is_not_a_duplicate(tmp_path):
    _write(tmp_path, "central.json", [_place_row(id="central-a", name_he="שם משותף", region="central")])
    _write(tmp_path, "north.json", [_place_row(id="north-a", name_he="שם משותף", region="north")])

    repository = PlaceRepository.load(tmp_path)
    assert len(repository) == 2


# --------------------------------------------------------------------------
# Fail loudly: an entry's region doesn't match its filename
# --------------------------------------------------------------------------


def test_entry_in_wrong_file_raises(tmp_path):
    _write(tmp_path, "central.json", [_place_row(id="misfiled", name_he="במקום הלא נכון", region="north")])

    with pytest.raises(ValueError) as excinfo:
        PlaceRepository.load(tmp_path)

    message = str(excinfo.value)
    assert "misfiled" in message
    assert "central.json" in message
    assert "north" in message


# --------------------------------------------------------------------------
# id / duration_min: unchanged — still read straight from the row, not
# recomputed by the loader.
# --------------------------------------------------------------------------


def test_id_and_duration_min_are_read_from_the_row(tmp_path):
    _write(tmp_path, "central.json", [_place_row(id="central-fixed-id", duration_min=145, region="central")])

    repository = PlaceRepository.load(tmp_path)
    place = repository.get("central-fixed-id")

    assert place is not None
    assert place.duration_min == 145
