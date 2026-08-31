"""Tests for merge_manual.py, entirely against inline fixtures — never the network."""

from __future__ import annotations

import json

from api.scripts import merge_manual


def _row(**overrides) -> dict:
    base = {
        "name_he": "מקום בדיקה",
        "region": "central",
        "category": "meal",
        "access": "gated",
        "opening_hours": {day: ["10:00", "18:00"] for day in ("sun", "mon", "tue", "wed", "thu", "fri")}
        | {"sat": None},
        "closed_on_shabbat": True,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# Merge / dedup
# --------------------------------------------------------------------------


def test_new_manual_entry_is_appended():
    existing = [_row(name_he="קיים")]
    manual = [_row(name_he="חדש")]
    merged, collisions = merge_manual.merge_manual_entries(existing, manual)
    assert [r["name_he"] for r in merged] == ["קיים", "חדש"]
    assert collisions == []


def test_manual_entry_wins_collision_with_existing_row():
    existing = [_row(name_he="מקום", category="meal", access="gated")]
    manual = [_row(name_he="מקום", category="museum", access="open")]
    merged, collisions = merge_manual.merge_manual_entries(existing, manual)
    assert len(merged) == 1
    assert merged[0]["category"] == "museum"
    assert merged[0]["access"] == "open"
    assert len(collisions) == 1
    assert "מקום" in collisions[0]


def test_collision_keeps_original_position():
    existing = [_row(name_he="א"), _row(name_he="ב"), _row(name_he="ג")]
    manual = [_row(name_he="ב", category="museum")]
    merged, _ = merge_manual.merge_manual_entries(existing, manual)
    assert [r["name_he"] for r in merged] == ["א", "ב", "ג"]
    assert merged[1]["category"] == "museum"


def test_same_name_different_region_is_not_a_collision():
    existing = [_row(name_he="מקום", region="central")]
    manual = [_row(name_he="מקום", region="north")]
    merged, collisions = merge_manual.merge_manual_entries(existing, manual)
    assert len(merged) == 2
    assert collisions == []


def test_duplicate_manual_entries_last_one_wins():
    existing: list[dict] = []
    manual = [_row(name_he="מקום", category="meal"), _row(name_he="מקום", category="museum")]
    merged, collisions = merge_manual.merge_manual_entries(existing, manual)
    assert len(merged) == 1
    assert merged[0]["category"] == "museum"
    assert any("duplicate manual entry" in c for c in collisions)


# --------------------------------------------------------------------------
# Validation: opening_hours placeholder
# --------------------------------------------------------------------------


def test_placeholder_closed_hours_is_flagged():
    row = _row(opening_hours={"sun": ["00:00", "00:00"], "mon": None, "tue": None, "wed": None, "thu": None, "fri": None, "sat": ["00:00", "00:00"]})
    problems = merge_manual.find_placeholder_closed_hours([row])
    assert len(problems) == 2
    assert any("sun" in p for p in problems)
    assert any("sat" in p for p in problems)


def test_real_closed_hours_are_not_flagged():
    row = _row(opening_hours={"sun": ["10:00", "18:00"], "mon": None, "tue": None, "wed": None, "thu": None, "fri": None, "sat": None})
    assert merge_manual.find_placeholder_closed_hours([row]) == []


# --------------------------------------------------------------------------
# Validation: category
# --------------------------------------------------------------------------


def test_invalid_category_is_flagged():
    row = _row(category="restaurant")  # not a real Category value
    problems = merge_manual.find_invalid_categories([row])
    assert len(problems) == 1
    assert "restaurant" in problems[0]


def test_valid_category_is_not_flagged():
    row = _row(category="meal")
    assert merge_manual.find_invalid_categories([row]) == []


# --------------------------------------------------------------------------
# Validation: closed_on_shabbat vs opening_hours['sat']
# --------------------------------------------------------------------------


def test_closed_on_shabbat_true_with_real_sat_hours_is_flagged():
    """The exact bug in manual_new.json: closed_on_shabbat=true but sat is ["00:00","00:00"], not null."""
    row = _row(closed_on_shabbat=True, opening_hours={"sun": None, "mon": None, "tue": None, "wed": None, "thu": None, "fri": None, "sat": ["00:00", "00:00"]})
    problems = merge_manual.find_shabbat_disagreements([row])
    assert len(problems) == 1


def test_closed_on_shabbat_false_with_null_sat_is_flagged():
    row = _row(closed_on_shabbat=False, opening_hours={"sun": None, "mon": None, "tue": None, "wed": None, "thu": None, "fri": None, "sat": None})
    problems = merge_manual.find_shabbat_disagreements([row])
    assert len(problems) == 1


def test_open_access_place_with_null_opening_hours_is_not_flagged():
    """An open-access place's opening_hours is null in its entirety by design — nothing to compare 'sat' against."""
    row = _row(access="open", closed_on_shabbat=False, opening_hours=None)
    assert merge_manual.find_shabbat_disagreements([row]) == []


def test_consistent_shabbat_flag_and_hours_are_not_flagged():
    closed_row = _row(closed_on_shabbat=True, opening_hours={"sun": None, "mon": None, "tue": None, "wed": None, "thu": None, "fri": None, "sat": None})
    open_row = _row(closed_on_shabbat=False, opening_hours={"sun": None, "mon": None, "tue": None, "wed": None, "thu": None, "fri": None, "sat": ["10:00", "18:00"]})
    assert merge_manual.find_shabbat_disagreements([closed_row, open_row]) == []


# --------------------------------------------------------------------------
# run(): end-to-end against temp files
# --------------------------------------------------------------------------


def test_run_writes_merged_file_and_prints_summary(tmp_path, capsys):
    manual_path = tmp_path / "manual_new.json"
    seed_path = tmp_path / "seed_names.json"
    manual_path.write_text(json.dumps([_row(name_he="חדש")], ensure_ascii=False), encoding="utf-8")
    seed_path.write_text(json.dumps([_row(name_he="קיים")], ensure_ascii=False), encoding="utf-8")

    merge_manual.run(manual_path=manual_path, seed_names_path=seed_path)

    written = json.loads(seed_path.read_text(encoding="utf-8"))
    assert [r["name_he"] for r in written] == ["קיים", "חדש"]
    out = capsys.readouterr().out
    assert "manual=1" in out
    assert "existing=1" in out


def test_run_prints_placeholder_hours_warning(tmp_path, capsys):
    manual_path = tmp_path / "manual_new.json"
    seed_path = tmp_path / "seed_names.json"
    bad_row = _row(name_he="בעיה", opening_hours={"sun": ["00:00", "00:00"], "mon": None, "tue": None, "wed": None, "thu": None, "fri": None, "sat": None})
    manual_path.write_text(json.dumps([bad_row], ensure_ascii=False), encoding="utf-8")
    seed_path.write_text("[]", encoding="utf-8")

    merge_manual.run(manual_path=manual_path, seed_names_path=seed_path)

    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "00:00" in out


def test_run_with_missing_seed_names_starts_empty(tmp_path, capsys):
    manual_path = tmp_path / "manual_new.json"
    seed_path = tmp_path / "seed_names.json"  # never created
    manual_path.write_text(json.dumps([_row()], ensure_ascii=False), encoding="utf-8")

    merge_manual.run(manual_path=manual_path, seed_names_path=seed_path)

    assert seed_path.exists()
    assert len(json.loads(seed_path.read_text(encoding="utf-8"))) == 1
