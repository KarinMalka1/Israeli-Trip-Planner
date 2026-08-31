"""
Phase 3 (BRIEF_data_acquisition.md): import_museums.py

An importer, not a scraper — reads a file the user downloads from
data.gov.il dataset 510 (מוזאונים מוכרים, the official list of museums
recognised under חוק המוזאונים) and writes seed entries to
seed_names.json. Does not crawl museums.gov.il — see the brief for why
that tail isn't worth a crawler.

Expected input: api/data/raw/museums_gov_510.csv (the user places this
file by hand; it is not fetched here).

Column names in Israeli government CSV exports vary release to release,
so this matches the header row against a short list of known Hebrew
variants per field (see NAME_COLUMN_CANDIDATES / CITY_COLUMN_CANDIDATES)
rather than hardcoding a column position or index. If neither list finds
a match, this raises loudly rather than silently reading the wrong
field — a data.gov.il export is not something to guess at. The exact
candidate lists here are a best-effort guess made without the real file
in hand; the first run against the actual CSV may need one of them
extended.

Every row becomes: category="museum", access="gated",
opening_hours=null, hours_verified=false — museums land unschedulable
by design, per the brief. Phase 4 hand-fills hours for the ~15 museums
worth planning a day around.

Region comes from the row's city/settlement field through
CITY_TO_REGION, an explicit dict — never guessed from a string, never
derived from the city name's spelling. A city not in the dict is logged
and the row is dropped, not assigned a best-guess region.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path
from typing import Optional

from api.scripts import _scrape_common as common
from api.scripts import curate_osm_open as curate
from api.scripts import fetch_osm

logger = logging.getLogger(__name__)

DEFAULT_CSV_PATH = fetch_osm.DATA_DIR / "raw" / "museums_gov_510.csv"
DEFAULT_SEED_NAMES_PATH = fetch_osm.DEFAULT_SEED_NAMES_PATH
SOURCE_LABEL = "data.gov.il/dataset/510"

# --------------------------------------------------------------------------
# Column resolution — matched by candidate header names, not position.
# --------------------------------------------------------------------------

NAME_COLUMN_CANDIDATES = [
    "שם המוזיאון", "שם מוסד", "שם המוסד", "שם", "museum_name", "name",
]
CITY_COLUMN_CANDIDATES = [
    "ישוב", "יישוב", "עיר", "רשות מקומית", "city", "settlement",
]


def _resolve_column(fieldnames: list[str], candidates: list[str], label: str) -> str:
    for candidate in candidates:
        if candidate in fieldnames:
            return candidate
    raise ValueError(
        f"could not find a {label} column in {fieldnames!r} — "
        f"tried {candidates!r}. Add the real column name to the candidate "
        f"list in import_museums.py."
    )


# --------------------------------------------------------------------------
# City -> region — explicit, not guessed. Extend as unmapped cities surface.
# Assignments follow this project's own region seams (REGION_BBOXES: north
# starts at lat 32.60, central/south split at 31.55), not official Israeli
# district boundaries, so a city colloquially called "the north" can still
# land in "central" here (e.g. Hadera, Beit She'an) if its latitude says so.
# --------------------------------------------------------------------------

CITY_TO_REGION: dict[str, str] = {
    # north
    "חיפה": "north", "נהריה": "north", "עכו": "north", "טבריה": "north",
    "צפת": "north", "כרמיאל": "north", "קרית שמונה": "north", "נצרת": "north",
    "נוף הגליל": "north", "מגדל העמק": "north", "יקנעם עילית": "north",
    "קרית אתא": "north", "קרית ביאליק": "north", "קרית מוצקין": "north",
    "קרית ים": "north", "טירת כרמל": "north", "מעלות-תרשיחא": "north",
    "שלומי": "north", "מטולה": "north", "ראש פינה": "north", "קצרין": "north",
    "עפולה": "north", "קרית טבעון": "north", "בית שאן": "central",
    # central
    "תל אביב-יפו": "central", "תל אביב יפו": "central", "תל אביב": "central",
    "ירושלים": "central", "ראשון לציון": "central", "פתח תקווה": "central",
    "אשדוד": "central", "נתניה": "central", "בני ברק": "central",
    "חולון": "central", "רמת גן": "central", "רחובות": "central",
    "בת ים": "central", "בית שמש": "central", "כפר סבא": "central",
    "הרצליה": "central", "חדרה": "central", "מודיעין-מכבים-רעות": "central",
    "מודיעין": "central", "לוד": "central", "רמלה": "central",
    "רעננה": "central", "יבנה": "central", "ראש העין": "central",
    "גבעתיים": "central", "אור עקיבא": "central", "זכרון יעקב": "central",
    "בנימינה": "central", "פרדס חנה-כרכור": "central", "אום אל-פחם": "central",
    "באקה אל-גרביה": "central", "טייבה": "central", "טירה": "central",
    "אשקלון": "central", "קרית גת": "central", "קרית מלאכי": "central",
    "נס ציונה": "central", "אריאל": "central",
    # south
    "באר שבע": "south", "אילת": "south", "דימונה": "south", "ערד": "south",
    "שדרות": "south", "נתיבות": "south", "אופקים": "south",
    "מצפה רמון": "south", "ירוחם": "south",
}


def region_for_city(city: str) -> Optional[str]:
    return CITY_TO_REGION.get(city.strip())


# --------------------------------------------------------------------------
# CSV -> seed entries
# --------------------------------------------------------------------------


def load_museum_rows(csv_path: Path) -> list[dict[str, str]]:
    """Read the CSV, resolving name/city columns to a stable ``name_he``/``city`` shape."""
    if not csv_path.exists():
        raise FileNotFoundError(
            f"{csv_path} not found — download data.gov.il dataset 510 "
            f"(מוזאונים מוכרים) and place it there."
        )
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        name_col = _resolve_column(fieldnames, NAME_COLUMN_CANDIDATES, "museum name")
        city_col = _resolve_column(fieldnames, CITY_COLUMN_CANDIDATES, "city")
        return [
            {"name_he": (row.get(name_col) or "").strip(), "city": (row.get(city_col) or "").strip()}
            for row in reader
        ]


def build_seed_entries(rows: list[dict[str, str]], *, scraped_on: str) -> tuple[list[dict], list[str]]:
    """
    Turn museum rows into seed_names.json entries.

    Returns (entries, unmapped_city_log). A row with no name, or a city not
    in CITY_TO_REGION, is logged and dropped rather than assigned a
    best-guess region.
    """
    entries: list[dict] = []
    unmapped: list[str] = []
    for row in rows:
        name_he = row["name_he"]
        city = row["city"]
        if not name_he:
            continue
        region = region_for_city(city)
        if region is None:
            unmapped.append(f"{name_he} ({city!r})")
            continue
        entries.append(
            common.emit_seed_entry(
                name_he=name_he,
                region=region,
                category="museum",
                access="gated",
                opening_hours=None,
                hours_verified=False,
                source=SOURCE_LABEL,
                scraped_on=scraped_on,
            )
        )
    return entries, unmapped


def run(*, csv_path: Path, seed_names_path: Path, scraped_on: str) -> None:
    rows = load_museum_rows(csv_path)
    entries, unmapped = build_seed_entries(rows, scraped_on=scraped_on)

    for line in unmapped:
        logger.warning("unmapped city, dropped: %s", line)

    existing = curate.load_seed_names_raw(seed_names_path)
    merged, added_count = curate.merge_into_seed_names(existing, entries)

    seed_names_path.parent.mkdir(parents=True, exist_ok=True)
    seed_names_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"rows={len(rows)} mapped={len(entries)} unmapped={len(unmapped)}")
    print(f"wrote {seed_names_path} — {added_count} new entries added, {len(merged)} total")


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--seed-names", type=Path, default=DEFAULT_SEED_NAMES_PATH)
    parser.add_argument("--scraped-on", type=str, required=True, help="ISO date, e.g. 2026-08-31")
    args = parser.parse_args(argv)

    run(csv_path=args.csv, seed_names_path=args.seed_names, scraped_on=args.scraped_on)


if __name__ == "__main__":
    main()
