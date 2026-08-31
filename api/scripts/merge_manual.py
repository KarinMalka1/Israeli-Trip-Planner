"""
Merge api/data/manual_new.json (hand-entered restaurants/museums/etc.) into
api/data/seed_names.json.

manual_new.json already existed but nothing read it, so every hand-entered
place sat unused — it never reached fetch_osm.py's enrichment pass, and so
never reached the app. This is the missing link: same raw-dict shape as
seed_names.json already uses (a SeedName, per fetch_osm.py), merged by
(region, name_he), with a manual entry always winning a collision — it was
hand-typed on purpose, almost always to correct or flesh out that exact row.

Fixtures only. No network, nothing scraped.

Also validates every row in the merged result and prints what it finds —
never auto-fixes, since guessing the right value would be worse than leaving
a human to fix it by hand:

  * an opening_hours weekday value of ["00:00", "00:00"] — almost certainly
    meant to be null (closed). Left as-is it would fail Place's own
    opens<closes validator the moment fetch_osm.py enriches it.
  * a category outside the Category enum.
  * closed_on_shabbat disagreeing with whether opening_hours["sat"] is null
    — one says "never schedule Saturday", the other says "open Saturday".

Run with:

    python -m api.scripts.merge_manual
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Optional

from api.models import Category
from api.scripts import fetch_osm

logger = logging.getLogger(__name__)

DEFAULT_MANUAL_PATH = fetch_osm.DATA_DIR / "manual_new.json"
DEFAULT_SEED_NAMES_PATH = fetch_osm.DEFAULT_SEED_NAMES_PATH

# What fetch_osm's _all_closed() produces for a genuinely closed day. A
# manual entry using this literal pair instead of null means the same thing
# in intent but fails Place's opens<closes validator (opens == closes).
_PLACEHOLDER_CLOSED_HOURS = ["00:00", "00:00"]


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def load_raw_entries(path: Path) -> list[dict]:
    """Read a bare JSON array of raw seed-shaped dicts — manual_new.json or seed_names.json."""
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} must be a JSON array")
    return raw


# --------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------


def merge_manual_entries(existing: list[dict], manual: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Merge manual entries into the existing seed, keyed by (region, name_he).

    A manual entry always wins a collision, against an existing seed_names.json
    row or against an earlier manual entry with the same key (last one in the
    file wins). Every overwrite is returned in ``collisions`` so nothing is
    silently replaced. Existing row order is preserved; brand-new manual
    entries are appended in file order.
    """
    collisions: list[str] = []

    manual_by_key: dict[tuple[str, str], dict] = {}
    for entry in manual:
        key = (entry["region"], entry["name_he"])
        if key in manual_by_key:
            collisions.append(
                f"{entry['name_he']} ({entry['region']}): duplicate manual entry, last one wins"
            )
        manual_by_key[key] = entry

    existing_keys = {(row["region"], row["name_he"]) for row in existing}

    merged: list[dict] = []
    for row in existing:
        key = (row["region"], row["name_he"])
        if key in manual_by_key:
            collisions.append(
                f"{row['name_he']} ({row['region']}): manual entry replaces existing seed_names.json row"
            )
            merged.append(manual_by_key[key])
        else:
            merged.append(row)

    for key, entry in manual_by_key.items():
        if key not in existing_keys:
            merged.append(entry)

    return merged, collisions


# --------------------------------------------------------------------------
# Validation — print-only, never auto-fixed
# --------------------------------------------------------------------------


def find_placeholder_closed_hours(rows: list[dict]) -> list[str]:
    """A weekday opening_hours value of ["00:00", "00:00"] almost certainly means "closed" (null)."""
    problems: list[str] = []
    for row in rows:
        hours = row.get("opening_hours") or {}
        for day, window in hours.items():
            if isinstance(window, list) and window == _PLACEHOLDER_CLOSED_HOURS:
                problems.append(
                    f"{row['name_he']} ({row['region']}): opening_hours[{day!r}] is "
                    f'["00:00","00:00"], should probably be null'
                )
    return problems


def find_invalid_categories(rows: list[dict]) -> list[str]:
    """A category string that doesn't match any Category enum value."""
    valid_categories = {category.value for category in Category}
    problems: list[str] = []
    for row in rows:
        category = row.get("category")
        if category is not None and category not in valid_categories:
            problems.append(
                f"{row['name_he']} ({row['region']}): category {category!r} is not a recognised Category"
            )
    return problems


def find_shabbat_disagreements(rows: list[dict]) -> list[str]:
    """
    closed_on_shabbat should agree with whether opening_hours['sat'] is null.

    Only meaningful for a gated place, where opening_hours describes a real
    gate. An open-access place's opening_hours is null in its entirety by
    design (models.py: no gate, so no hours to describe) — its 'sat' is not
    a per-weekday value at all, so there is nothing to compare here.
    """
    problems: list[str] = []
    for row in rows:
        closed_on_shabbat = row.get("closed_on_shabbat")
        if closed_on_shabbat is None:
            continue
        hours = row.get("opening_hours")
        if not isinstance(hours, dict):
            continue
        saturday_is_null = hours.get("sat") is None
        if closed_on_shabbat and not saturday_is_null:
            problems.append(
                f"{row['name_he']} ({row['region']}): closed_on_shabbat=true but "
                f"opening_hours['sat']={hours.get('sat')!r}"
            )
        elif not closed_on_shabbat and saturday_is_null:
            problems.append(
                f"{row['name_he']} ({row['region']}): closed_on_shabbat=false but "
                f"opening_hours['sat'] is null"
            )
    return problems


def print_validation_warnings(rows: list[dict]) -> None:
    for problem in find_placeholder_closed_hours(rows):
        print(f"WARNING: {problem}")
    for problem in find_invalid_categories(rows):
        print(f"WARNING: {problem}")
    for problem in find_shabbat_disagreements(rows):
        print(f"WARNING: {problem}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def run(*, manual_path: Path, seed_names_path: Path) -> None:
    manual = load_raw_entries(manual_path)
    existing = load_raw_entries(seed_names_path)

    merged, collisions = merge_manual_entries(existing, manual)

    for collision in collisions:
        logger.info(collision)

    print_validation_warnings(merged)

    seed_names_path.parent.mkdir(parents=True, exist_ok=True)
    seed_names_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

    added = len(merged) - len(existing)
    print(
        f"manual={len(manual)} existing={len(existing)} collisions={len(collisions)} "
        f"new={added}"
    )
    print(f"wrote {seed_names_path} — {len(merged)} total")


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual", type=Path, default=DEFAULT_MANUAL_PATH)
    parser.add_argument("--seed-names", type=Path, default=DEFAULT_SEED_NAMES_PATH)
    args = parser.parse_args(argv)

    run(manual_path=args.manual, seed_names_path=args.seed_names)


if __name__ == "__main__":
    main()
