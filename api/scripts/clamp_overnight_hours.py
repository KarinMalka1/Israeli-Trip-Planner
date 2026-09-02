"""
One-off migration: clamp opening_hours windows that cross midnight.

Place.opening_hours is a same-day [open, close] pair (models.Place); the
model has no way to express a window that runs past midnight, so a scraped
closing time like "01:00" sits lexicographically before its "12:00"/"11:00"
opening time and fails Place's own opens<closes validator the moment the
repository loads it.

This script does not attempt to model overnight hours properly — SPEC.md
section 14 accepts the loss on purpose, since the planner never schedules
past midnight anyway. It clamps the closing time down to "23:59" instead —
the place is still schedulable for same-day visits, just not for the
after-midnight tail the source page advertises — and logs every row it
touches so the loss is visible rather than silent.

Only a window that looks like a genuine overnight closing is touched: the
close time must be an early-morning hour (before OVERNIGHT_CLOSE_CUTOFF).
"12:00"-"01:00" qualifies (a venue open past midnight into the small hours).
"12:00"-"11:00" does not — an 11:00 close is not "the next morning", it is
a same-day window entered backwards, a different bug this script must not
paper over. That row is left alone and keeps failing Place's validator,
which points back here only for the cases this script actually fixes.

Run with:

    python -m api.scripts.clamp_overnight_hours
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from api.models import Place
from api.repository.places import DEFAULT_PLACES_DIR

logger = logging.getLogger(__name__)

CLAMPED_CLOSE = "23:59"

# A close time at or after this hour is not "past midnight into the small
# hours" — treating it as an overnight window would silently paper over a
# same-day window that was simply entered backwards (see module docstring).
OVERNIGHT_CLOSE_CUTOFF = "06:00"


def should_clamp(window: Optional[list]) -> bool:
    """Whether a [open, close] window is a genuine overnight closing, not just reversed."""
    if window is None:
        return False
    opens, closes = window
    return closes <= opens and closes < OVERNIGHT_CLOSE_CUTOFF


def clamp_window(window: list) -> list:
    """window with its close time clamped to 23:59, or unchanged if should_clamp is False."""
    if not should_clamp(window):
        return window
    opens, _closes = window
    return [opens, CLAMPED_CLOSE]


@dataclass
class ClampedWindow:
    place_id: str
    name_he: str
    day: str
    original: list


def clamp_row(row: dict) -> tuple[dict, list[ClampedWindow]]:
    """Pure: row -> (row with overnight windows clamped, list of what changed)."""
    hours = row.get("opening_hours")
    if not isinstance(hours, dict):
        return row, []

    clamped: list[ClampedWindow] = []
    updated_hours = dict(hours)
    for day, window in hours.items():
        if should_clamp(window):
            clamped.append(
                ClampedWindow(place_id=row.get("id"), name_he=row.get("name_he"), day=day, original=window)
            )
            updated_hours[day] = clamp_window(window)

    if not clamped:
        return row, []

    updated_row = dict(row)
    updated_row["opening_hours"] = updated_hours
    return updated_row, clamped


def clamp_file(raw: dict) -> tuple[dict, list[ClampedWindow]]:
    """Pure: raw file dict -> (updated raw dict, everything clamped). Never touches disk."""
    all_clamped: list[ClampedWindow] = []
    updated_rows: list[dict] = []

    for row in raw.get("places", []):
        updated_row, clamped = clamp_row(row)
        updated_rows.append(updated_row)
        all_clamped.extend(clamped)

    updated_raw = dict(raw)
    updated_raw["places"] = updated_rows
    return updated_raw, all_clamped


def run(places_dir: Path) -> None:
    total_clamped: list[ClampedWindow] = []

    for file_path in sorted(places_dir.glob("*.json")):
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        updated_raw, clamped = clamp_file(raw)

        if clamped:
            for row in updated_raw["places"]:
                Place.model_validate(row)  # fail loudly before writing anything
            file_path.write_text(
                json.dumps(updated_raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            for window in clamped:
                logger.warning(
                    "clamped overnight hours: %s (%s) %s %s -> [%s, %s]",
                    window.place_id,
                    window.name_he,
                    window.day,
                    window.original,
                    window.original[0],
                    CLAMPED_CLOSE,
                )
            logger.info("wrote %s", file_path)

        total_clamped.extend(clamped)

    print(f"clamped: {len(total_clamped)}")
    for window in total_clamped:
        print(f"  {window.place_id}: {window.name_he} [{window.day}] {window.original} -> [{window.original[0]}, {CLAMPED_CLOSE}]")

    if total_clamped:
        touched_ids = sorted({window.place_id for window in total_clamped})
        print(f"NOTE: {len(touched_ids)} place(s) lost their post-midnight tail (SPEC.md section 14): {', '.join(touched_ids)}")


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--places-dir", type=Path, default=DEFAULT_PLACES_DIR)
    args = parser.parse_args(argv)
    run(args.places_dir)


if __name__ == "__main__":
    main()
