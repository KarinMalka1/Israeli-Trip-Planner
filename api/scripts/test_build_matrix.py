"""
Tests for build_matrix.py's region-directory rebuild.

SPEC section 12 split the seed into one *.json file per region; this script
must read that directory through PlaceRepository.load() rather than a single
places.json file (which no longer exists), so the matrix and the API can
never read different seeds.
"""

from __future__ import annotations

import json
from pathlib import Path

from api.repository.places import PlaceRepository
from api.scripts import build_matrix


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
        "lat": 32.0,
        "lng": 34.9,
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
# No cross-region cells
# --------------------------------------------------------------------------


def test_two_region_fixture_produces_no_cross_region_cells(tmp_path):
    _write(
        tmp_path,
        "central.json",
        [
            _place_row(id="central-a", name_he="א", region="central", lat=32.00, lng=34.90),
            _place_row(id="central-b", name_he="ב", region="central", lat=32.01, lng=34.91),
        ],
    )
    _write(
        tmp_path,
        "north.json",
        [
            _place_row(id="north-a", name_he="ג", region="north", lat=33.00, lng=35.50),
            _place_row(id="north-b", name_he="ד", region="north", lat=33.01, lng=35.51),
        ],
    )

    places = PlaceRepository.load(tmp_path).all()
    matrix = build_matrix.build_matrix(places)

    assert "central-b" not in matrix["north-a"]
    assert "north-a" not in matrix["central-b"]
    assert set(matrix["central-a"]) == {"central-b"}
    assert set(matrix["north-a"]) == {"north-b"}


# --------------------------------------------------------------------------
# A place with lat is None gets no cells and is an orphan
# --------------------------------------------------------------------------


def test_place_with_null_lat_gets_no_cells_and_is_an_orphan():
    places_with_unresolved_coords = [
        _get_place(_place_row(id="central-a", name_he="א", region="central", lat=32.00, lng=34.90)),
        _get_place(_place_row(id="central-c", name_he="ג", region="central", lat=32.01, lng=34.91)),
        _get_place(_place_row(id="central-b", name_he="ב", region="central", lat=None, lng=None)),
    ]

    matrix = build_matrix.build_matrix(places_with_unresolved_coords)

    assert matrix["central-b"] == {}
    assert "central-b" not in matrix["central-a"]
    assert "central-b" not in matrix["central-c"]
    orphans = [place_id for place_id, row in matrix.items() if not row]
    assert orphans == ["central-b"]


def _get_place(row: dict):
    from api.models import Place

    return Place.model_validate(row)


# --------------------------------------------------------------------------
# Symmetry
# --------------------------------------------------------------------------


def test_matrix_is_symmetric_for_every_pair():
    places = [
        _get_place(_place_row(id="central-a", name_he="א", region="central", lat=32.00, lng=34.90)),
        _get_place(_place_row(id="central-b", name_he="ב", region="central", lat=32.05, lng=34.95)),
        _get_place(_place_row(id="central-c", name_he="ג", region="central", lat=32.10, lng=35.00)),
    ]

    matrix = build_matrix.build_matrix(places)

    for origin_id, row in matrix.items():
        for destination_id, minutes in row.items():
            assert matrix[destination_id][origin_id] == minutes


# --------------------------------------------------------------------------
# MIN_LEG_MINUTES floors near-identical coordinates
# --------------------------------------------------------------------------


def test_min_leg_minutes_floors_near_identical_coordinates():
    a = _get_place(_place_row(id="central-a", name_he="א", region="central", lat=32.0000, lng=34.9000))
    b = _get_place(_place_row(id="central-b", name_he="ב", region="central", lat=32.0001, lng=34.9001))

    minutes = build_matrix.estimate_drive_minutes(a, b)

    assert minutes == build_matrix.MIN_LEG_MINUTES


# --------------------------------------------------------------------------
# Reads the same directory PlaceRepository would, given $PLACES_DIR
# --------------------------------------------------------------------------


def test_main_reads_places_dir_env_var_the_same_way_place_repository_does(tmp_path, monkeypatch):
    """
    ``main()`` is given no ``--places-dir``, only ``$PLACES_DIR`` — the same
    resolution order ``PlaceRepository.load()`` itself implements (explicit
    arg -> env var -> default), so the matrix and the API can never read
    different seeds.
    """
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    _write(
        seed_dir,
        "central.json",
        [_place_row(id="central-a", name_he="א", region="central", lat=32.00, lng=34.90)],
    )
    monkeypatch.setenv("PLACES_DIR", str(seed_dir))

    out_path = tmp_path / "distance_matrix.json"
    monkeypatch.setattr("sys.argv", ["build_matrix", "--out", str(out_path)])

    build_matrix.main()

    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert list(written.keys()) == ["central-a"]
