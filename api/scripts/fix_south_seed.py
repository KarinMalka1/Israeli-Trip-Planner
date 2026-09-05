"""
One-off migration: repair the five hand-added south rows so the seed loads.

Run with:

    python -m api.scripts.fix_south_seed --dry-run    # report only, writes nothing
    python -m api.scripts.fix_south_seed              # apply

Same shape as clamp_overnight_hours.py and backfill_official_url.py: an
offline tool run once against the already-curated set, never a recurring
source of new rows, never imported by the API.

WHAT IT FIXES, AND WHAT IT REFUSES TO
--------------------------------------
Five rows were added to api/data/places/south.json by hand while other work
was in flight, and _validate_dataset() rejects two of them. Everything this
script changes is a *schema contradiction* — a row saying two things that
cannot both be true — resolved in the direction the row's own other fields
already point. It never supplies a fact the row did not already contain:

  1. access=="open" carrying real opening_hours. "open" means there is no
     gate, so opening_hours must be seven nulls and hours_verified says
     nothing about the place (SPEC section 10). The hours are dropped, not
     migrated to a gated place — the row's own description_he says the site
     is open around the clock, so the gate is the false half.

  2. tags outside places.template.json's _tag_vocabulary. Mapped only where
     the vocabulary already has a term meaning the same thing; a tag with
     no equivalent is dropped rather than added to the vocabulary, because
     nothing reads tags today (they filter nothing in the planner and render
     nowhere in the UI) and the information survives in description_he.

  3. A missing season field. Written as the schema's own default, explicitly,
     so all twelve southern rows read the same way.

  4. images[] pointing at files that are not in web/public/images/. The
     schema checks that an image carries a credit and a source_url, not that
     the file exists, so these validate and then render as five broken
     images — worse than the grey-initial fallback StopCard already has. The
     claim is removed; see "IMAGES" below for how to make it true instead.

It deliberately does NOT touch:

  * description_source. Whether a description was written from the supplied
    fields or from someone's knowledge of the place is a claim about how a
    human worked, and no script can check it. See the report it prints.
  * opening_hours on any gated place. A wrong gate time is the exact failure
    this project exists to prevent, and there is no source here to check one
    against.
  * hours_verified on any gated place, for the same reason.

IMAGES
------
This script removes image entries whose files are missing. It does not go
and find replacements, because api/scripts/fetch_commons_images.py already
does exactly that, better: a five-source waterfall (Wikidata P18, Wikipedia
page images, Commons category, Openverse, geosearch) with license checking
and a needs_review flag on the weakest source. Writing a second, worse image
fetcher here would be the kind of duplicate pipeline this repo has already
deleted once.

After running this, run:

    python -m api.scripts.fetch_commons_images --region south --dry-run
    python -m api.scripts.fetch_commons_images --region south

It backs off on 429s automatically (three retries, exponential). If it still
gives up, wait and re-run — its cache means a second run resumes rather than
restarting. Expect a low hit rate: a municipal monument or a regional park
often has no freely-licensed photo at all, and an empty images list is the
common case the fallback panel was built for.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_SOUTH_PATH = DATA_DIR / "places" / "south.json"
DEFAULT_IMAGES_DIR = Path(__file__).resolve().parent.parent.parent / "web" / "public" / "images"
TAG_VOCABULARY_PATH = Path(__file__).resolve().parent.parent.parent / "places.template.json"

WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")

# Tags seen in the new rows that are not in the vocabulary, mapped to the
# vocabulary term that means the same thing. A tag mapped to None has no
# equivalent and is dropped: adding a vocabulary entry to accommodate one
# row would be the tail wagging the dog, given nothing reads tags yet.
TAG_REPLACEMENTS: dict[str, list[str]] = {
    # A lakeside desert park with pine groves and picnic tables, per its own
    # description_he — these three are what the vocabulary can actually say
    # about it.
    "park": ["picnic", "easy-walk", "shade"],
    "lake": [],  # no equivalent; the lake is described in description_he
    "birds": [],  # no equivalent; likewise
}


def load_vocabulary(path: Path) -> set[str]:
    """The single source of truth for legal tags — the same file the repository reads."""
    return set(json.loads(path.read_text(encoding="utf-8"))["_tag_vocabulary"])


def existing_image_files(images_dir: Path) -> set[str]:
    """Filenames actually present on disk, so a missing file is detectable."""
    if not images_dir.exists():
        return set()
    return {entry.name for entry in images_dir.iterdir() if entry.is_file()}


def fix_open_access_hours(place: dict[str, Any]) -> list[str]:
    """
    An access=="open" place carries no opening hours and no hours claim.

    Returns a list of human-readable change descriptions, empty when the row
    was already correct.
    """
    if place.get("access") != "open":
        return []

    changes: list[str] = []
    hours = place.get("opening_hours") or {}
    if any(hours.get(day) is not None for day in WEEKDAYS):
        stated = {day: hours[day] for day in WEEKDAYS if hours.get(day) is not None}
        place["opening_hours"] = {day: None for day in WEEKDAYS}
        changes.append(f"dropped opening_hours on an open-access place: {stated}")

    # hours_verified means "a human read official hours". An open place has
    # no gate and no hours, so the flag can only ever be false for it —
    # true would be a claim about a document that does not exist.
    if place.get("hours_verified") is not False:
        place["hours_verified"] = False
        changes.append("hours_verified -> false (no gate, so nothing to verify)")

    return changes


def fix_tags(place: dict[str, Any], vocabulary: set[str]) -> list[str]:
    """Map or drop every tag outside the vocabulary, preserving order and uniqueness."""
    original = list(place.get("tags") or [])
    unknown = [tag for tag in original if tag not in vocabulary]
    if not unknown:
        return []

    rebuilt: list[str] = []
    dropped: list[str] = []
    for tag in original:
        if tag in vocabulary:
            replacements = [tag]
        elif tag in TAG_REPLACEMENTS:
            replacements = TAG_REPLACEMENTS[tag]
            if not replacements:
                dropped.append(tag)
        else:
            # An unknown tag with no mapping. Dropping silently would hide a
            # typo, so this is reported loudly and left for a human.
            return [f"UNMAPPED TAG {tag!r} — add a mapping to TAG_REPLACEMENTS or fix the row by hand"]

        for replacement in replacements:
            if replacement not in rebuilt:
                rebuilt.append(replacement)

    place["tags"] = rebuilt
    note = f"tags {original} -> {rebuilt}"
    if dropped:
        note += f" (dropped with no vocabulary equivalent: {dropped})"
    return [note]


def fix_missing_season(place: dict[str, Any]) -> list[str]:
    """Write the schema default explicitly so every row reads the same way."""
    if "season" in place:
        return []
    place["season"] = "year_round"
    return ["season -> 'year_round' (was absent; schema default made explicit)"]


def fix_missing_images(place: dict[str, Any], on_disk: set[str]) -> list[str]:
    """Drop image entries whose files are not in web/public/images/."""
    images = place.get("images") or []
    if not images:
        return []

    kept = [image for image in images if Path(image["url"]).name in on_disk]
    missing = [Path(image["url"]).name for image in images if Path(image["url"]).name not in on_disk]
    if not missing:
        return []

    place["images"] = kept
    return [f"removed {len(missing)} image(s) with no file on disk: {missing}"]


def report_unverifiable_claims(place: dict[str, Any]) -> list[str]:
    """
    Things a human has to decide, printed but never changed.

    description_source=="generated" asserts the text came out of
    generate_descriptions.py, which is constrained to the supplied fields
    only. A description written elsewhere that states a specific fact — a
    count, a date, a construction detail — is not "generated" in that sense,
    and mislabelling it hides an unchecked claim inside a provenance field.
    """
    notes: list[str] = []
    if place.get("description_source") == "generated" and place.get("_source") != "generated":
        text = f"{place.get('description_he', '')} {place.get('tip_he', '')}"
        if any(character.isdigit() for character in text):
            notes.append(
                "description_source is 'generated' but the text contains a specific "
                "figure — if a person read it from the official page, set 'human'; "
                "if not, verify it or cut the detail"
            )
    return notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT_SOUTH_PATH)
    parser.add_argument("--images-dir", type=Path, default=DEFAULT_IMAGES_DIR)
    parser.add_argument("--vocabulary", type=Path, default=TAG_VOCABULARY_PATH)
    parser.add_argument("--dry-run", action="store_true", help="report every change, write nothing")
    args = parser.parse_args()

    vocabulary = load_vocabulary(args.vocabulary)
    on_disk = existing_image_files(args.images_dir)
    document = json.loads(args.path.read_text(encoding="utf-8"))
    places = document["places"]

    changed_rows = 0
    blockers: list[str] = []
    review: list[str] = []

    for place in places:
        changes: list[str] = []
        changes += fix_open_access_hours(place)
        changes += fix_tags(place, vocabulary)
        changes += fix_missing_season(place)
        changes += fix_missing_images(place, on_disk)

        unmapped = [change for change in changes if change.startswith("UNMAPPED")]
        blockers += [f"{place['id']}: {change}" for change in unmapped]

        if changes:
            changed_rows += 1
            print(f"\n{place['id']}  ({place.get('name_he', '')})")
            for change in changes:
                print(f"  - {change}")

        for note in report_unverifiable_claims(place):
            review.append(f"{place['id']}: {note}")

    print(f"\n{changed_rows} of {len(places)} rows changed.")

    if blockers:
        print("\nBLOCKED — these need a human decision, nothing was written:")
        for blocker in blockers:
            print(f"  - {blocker}")
        return 1

    if args.dry_run:
        print("\n--dry-run: no file written.")
    else:
        args.path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {args.path}")

    if review:
        print("\nNeeds a human, not a script — nothing above touched these:")
        for note in review:
            print(f"  - {note}")

    print(
        "\nNext:\n"
        "  python -c \"from api.repository.places import PlaceRepository; "
        "print(len(PlaceRepository.load()), 'places')\"\n"
        "  python -m api.scripts.build_matrix\n"
        "  python -m api.scripts.fetch_commons_images --region south --dry-run"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
