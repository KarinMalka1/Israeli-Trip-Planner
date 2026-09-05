"""
Machine-checkable half of BRIEF_shabbat_mode.md's definition of done.

Run from the repo root, after implementing the brief:

    python scripts/verify_shabbat_mode.py

Exits 0 when every check passes, 1 otherwise, printing one line per check.
This is a gate, not a test suite: it asserts the things that are true of the
*whole assembled system* and that unit tests with fixtures cannot see — that
the committed matrix matches the committed seed, that every region can
actually produce a day on every weekday, that the frontend and the contract
agree on which fields exist.

It deliberately does not import pytest or replace it. Run both.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.domain import schedule  # noqa: E402
from api.domain.distance import DistanceMatrix  # noqa: E402
from api.models import Region, Weekday  # noqa: E402
from api.planner.rule_based import RuleBasedPlanner  # noqa: E402
from api.repository.places import PlaceRepository  # noqa: E402

WEB_SRC = REPO_ROOT / "web" / "src"
SPEC = REPO_ROOT / "SPEC.md"

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """Record one check and print it as it runs, so a long run is readable."""
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


# --------------------------------------------------------------------------
# Part 1 — seed and matrix agree
# --------------------------------------------------------------------------


def check_matrix_covers_seed(places: PlaceRepository, matrix: DistanceMatrix) -> None:
    """
    Every seeded place must have a matrix row.

    This is the check that would have caught the stale matrix: build_matrix.py
    read a places.json that section 12 had already split away, so 14 places
    silently had no row and the whole south region was unplannable.
    """
    check("matrix is not empty", not matrix.is_empty())

    missing = sorted(places.known_ids() - matrix.known_ids())
    check(
        "every seeded place has a matrix row",
        not missing,
        f"{len(missing)} missing: {', '.join(missing[:5])}",
    )

    orphans = sorted(pid for pid in matrix.known_ids() if not matrix.reachable_from(pid, 90))
    check(
        "no place is isolated at the 90-minute cap",
        not orphans,
        f"{len(orphans)} isolated: {', '.join(orphans[:5])} "
        "(bad coordinates, wrong region, or a genuinely remote region needing more places)",
    )


def check_matrix_is_symmetric(matrix: DistanceMatrix) -> None:
    """travel_min(a, b) must equal travel_min(b, a); the planner assumes it."""
    asymmetric: list[str] = []
    for origin in matrix.known_ids():
        for dest in matrix.reachable_from(origin, 10_000):
            if matrix.travel_min(origin, dest) != matrix.travel_min(dest, origin):
                asymmetric.append(f"{origin}->{dest}")
    check("matrix is symmetric", not asymmetric, ", ".join(asymmetric[:5]))


# --------------------------------------------------------------------------
# Parts 2 and 3 — every region plans on every weekday, both shabbat modes
# --------------------------------------------------------------------------


def check_every_region_and_weekday_plans(planner: RuleBasedPlanner, places: PlaceRepository) -> None:
    """
    3 regions x 7 weekdays x 2 shabbat modes = 42 real plan calls.

    A region that falls through to fallback step 2 is not an error the API
    should raise (SPEC section 4 says a sparse region degrades to cards), but
    it *is* a failure of this gate: shipping a demo where a whole region shows
    unscheduled cards is the thing we are trying not to do.
    """
    fell_back: list[str] = []
    invalid: list[str] = []

    for region in Region:
        for weekday in Weekday:
            for observant in (True, False):
                itinerary = planner.plan(
                    itinerary_id="verify",
                    region=region,
                    max_leg_min=45,
                    weekday=weekday,
                    seed=1,
                    shabbat_observant=observant,
                )
                label = f"{region.value}/{weekday.value}/observant={observant}"

                if not itinerary.days or not itinerary.days[0].stops:
                    fell_back.append(label)
                    continue

                problems = schedule.validate_itinerary(itinerary, places.known_ids())
                if problems:
                    invalid.append(f"{label}: {problems[0]}")

    check(
        "every region plans a real day on every weekday, in both shabbat modes",
        not fell_back,
        f"{len(fell_back)} fell back to cards: {', '.join(fell_back[:6])}",
    )
    check(
        "every planned itinerary passes validate_itinerary",
        not invalid,
        "; ".join(invalid[:3]),
    )


def check_friday_deadline_is_enforced(planner: RuleBasedPlanner) -> None:
    """
    The Friday rule, from both sides.

    Observant: the whole day must be over by FRIDAY_LATEST_CLOSE. Not
    observant: Friday is an ordinary day, so at least one region should be
    able to run past that time — if none can, the flag is not actually wired
    through and both branches are doing the same thing.
    """
    deadline = schedule.to_minutes("15:00")
    late_for_observant: list[str] = []
    any_late_when_not_observant = False

    for region in Region:
        observant_day = planner.plan(
            itinerary_id="verify",
            region=region,
            max_leg_min=90,
            weekday=Weekday.FRI,
            seed=1,
            shabbat_observant=True,
        )
        if observant_day.days and observant_day.days[0].stops:
            if schedule.to_minutes(observant_day.days[0].ends_at) > deadline:
                late_for_observant.append(f"{region.value} ends {observant_day.days[0].ends_at}")

        secular_day = planner.plan(
            itinerary_id="verify",
            region=region,
            max_leg_min=90,
            weekday=Weekday.FRI,
            seed=1,
            shabbat_observant=False,
        )
        if secular_day.days and secular_day.days[0].stops:
            if schedule.to_minutes(secular_day.days[0].ends_at) > deadline:
                any_late_when_not_observant = True

    check(
        "observant Friday itineraries end by 15:00",
        not late_for_observant,
        "; ".join(late_for_observant),
    )
    check(
        "shabbat_observant=False actually relaxes the Friday deadline",
        any_late_when_not_observant,
        "no region produced a Friday day running past 15:00 — the flag may not be "
        "threaded through, or every region is hour-bound before 15:00 anyway "
        "(check by hand before dismissing this)",
    )


def check_open_places_are_schedulable_on_friday(places: PlaceRepository) -> None:
    """
    The specific regression: an access=="open" place has seven null hours by
    design, and the old code read that as "closed on Friday".
    """
    open_places = [p for p in places.all() if p.access.value == "open"]
    check("the seed still contains access=='open' places", bool(open_places))
    if not open_places:
        return

    schedulable = [
        p for p in open_places if schedule.is_available_on(p, Weekday.FRI, 6, shabbat_observant=True)
    ]
    check(
        "open-access places are schedulable on Friday",
        bool(schedulable),
        f"0 of {len(open_places)} open places available on Friday — the section 10 "
        "daylight carve-out is not reaching the Friday branch",
    )


def check_edits_preserve_the_flag(planner: RuleBasedPlanner, places: PlaceRepository) -> None:
    """
    An itinerary must carry shabbat_observant so remove/swap/undo recompute
    under the same rule the day was built under.
    """
    itinerary = planner.plan(
        itinerary_id="verify",
        region=Region.CENTRAL,
        max_leg_min=90,
        weekday=Weekday.FRI,
        seed=1,
        shabbat_observant=False,
    )
    check(
        "Itinerary carries shabbat_observant back to the client",
        hasattr(itinerary, "shabbat_observant"),
        "field missing from the Itinerary model — edits will silently fall back to the default",
    )
    if hasattr(itinerary, "shabbat_observant"):
        check(
            "Itinerary echoes the requested shabbat_observant",
            itinerary.shabbat_observant is False,
            f"requested False, got {itinerary.shabbat_observant!r}",
        )


# --------------------------------------------------------------------------
# Part 4 — frontend
# --------------------------------------------------------------------------


def read_web_sources() -> dict[Path, str]:
    """Every .ts/.tsx file under web/src, as text, for the string checks below."""
    return {
        path: path.read_text(encoding="utf-8")
        for path in WEB_SRC.rglob("*")
        if path.suffix in {".ts", ".tsx"}
    }


def check_frontend(sources: dict[Path, str]) -> None:
    """
    The frontend checks are deliberately string-level: there is no test runner
    in /web, and a grep that fails loudly beats an untested claim in the SPEC.
    """
    joined = "\n".join(sources.values())

    check(
        "StartTimeChips.tsx is deleted",
        not (WEB_SRC / "components" / "StartTimeChips.tsx").exists(),
    )
    check(
        "no StartTimeChips references remain",
        "StartTimeChips" not in joined,
    )
    check(
        "ShabbatChips.tsx exists",
        (WEB_SRC / "components" / "ShabbatChips.tsx").exists(),
    )

    app = sources.get(WEB_SRC / "App.tsx", "")
    check("App.tsx renders ShabbatChips", "ShabbatChips" in app)
    check("App.tsx sends shabbat_observant", "shabbat_observant" in app or "shabbatObservant" in app)
    check(
        "starts_at stays in the TypeScript contract",
        "starts_at" in sources.get(WEB_SRC / "types.ts", ""),
        "the control was cut, the field was not — see brief part 4a",
    )
    check(
        "the inert-day note is present",
        "משפיע על תכנון ליום שישי בלבד" in app,
    )

    # Five labelled rows, in the brief's order. The labels are the only stable
    # handle on row order without rendering the component.
    expected_labels = [
        "אזור",
        "זמן נסיעה מקסימלי בין עצירות",
        "ארוחה",
        "אורך היום",
        "שמירת שבת",
    ]
    positions = [app.find(label) for label in expected_labels]
    check("all five control labels are present", all(p != -1 for p in positions))
    if all(p != -1 for p in positions):
        check(
            "control rows are in the brief's order",
            positions == sorted(positions),
            "shabbat must be last; region first",
        )

    # Controls panel below the itinerary: the Timeline/fallback block must
    # appear before the first chip row in document order.
    timeline_at = app.find("<Timeline")
    first_row_at = min(p for p in positions if p != -1) if any(p != -1 for p in positions) else -1
    check(
        "the controls panel renders below the itinerary",
        timeline_at != -1 and first_row_at != -1 and timeline_at < first_row_at,
        "Timeline must come first in document order so the result precedes the form",
    )

    # RTL rule 5: logical properties only.
    physical = re.findall(r'className="[^"]*\b(?:pl-|pr-|ml-|mr-|text-left|text-right)\b', joined)
    check(
        "no physical direction utilities (RTL rule 5)",
        not physical,
        f"{len(physical)} occurrences — use ps-/pe-/ms-/me-/text-start/text-end",
    )


def check_spec(spec_text: str) -> None:
    """The SPEC is a deliverable here, not documentation about one."""
    check("SPEC has section 17", bool(re.search(r"^## 17\.", spec_text, re.MULTILINE)))
    check("SPEC section 17 covers shabbat_observant", "shabbat_observant" in spec_text)
    check(
        "SPEC records the start-time control being cut",
        "StartTimeChips" in spec_text or "start time" in spec_text.lower(),
    )


# --------------------------------------------------------------------------
# Test suite
# --------------------------------------------------------------------------


def check_pytest() -> None:
    """Run the suite. A green gate with red tests would be worse than no gate."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    check(
        "pytest passes",
        result.returncode == 0,
        (result.stdout or result.stderr).strip().splitlines()[-1] if (result.stdout or result.stderr) else "",
    )


# --------------------------------------------------------------------------


def main() -> int:
    print("verifying BRIEF_shabbat_mode.md\n")

    places = PlaceRepository.load()
    matrix = DistanceMatrix.load()
    planner = RuleBasedPlanner(places, matrix)

    print("-- part 1: seed and matrix --")
    check_matrix_covers_seed(places, matrix)
    check_matrix_is_symmetric(matrix)

    print("\n-- parts 2 and 3: planning --")
    check_open_places_are_schedulable_on_friday(places)
    check_every_region_and_weekday_plans(planner, places)
    check_friday_deadline_is_enforced(planner)
    check_edits_preserve_the_flag(planner, places)

    print("\n-- part 4: frontend --")
    check_frontend(read_web_sources())

    print("\n-- spec --")
    check_spec(SPEC.read_text(encoding="utf-8"))

    print("\n-- tests --")
    check_pytest()

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
