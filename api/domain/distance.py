"""
Distance matrix loading and lookup (SPEC.md rule 7).

Travel times come from the precomputed matrix in ``data/distance_matrix.json``
and from nowhere else. They are never estimated by a model at request time and
never fetched from a live routing API (section 5). This module only reads the
file; ``scripts/build_matrix.py`` is what writes it.

Matrix format, symmetric and dense over the seeded places:

    { "north-a": { "north-b": 24, ... }, "north-b": { "north-a": 24, ... } }

A missing pair means "we have no travel time for this leg", which the planner
must treat as unreachable rather than as zero.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default location, resolved relative to this file so the working directory
# does not matter. Overridable so tests and smoke runs can load a fixture
# matrix without overwriting the generated one.
DEFAULT_MATRIX_PATH = Path(__file__).resolve().parent.parent / "data" / "distance_matrix.json"
MATRIX_PATH_ENV_VAR = "DISTANCE_MATRIX_FILE"


class DistanceMatrix:
    """
    An immutable lookup table of drive times in minutes, keyed by place id pair.

    Constructed once at startup and shared: the planner queries it thousands of
    times while searching, so lookups must be plain dict hits.
    """

    def __init__(self, minutes: dict[str, dict[str, int]]) -> None:
        """Wrap an already-parsed ``{from_id: {to_id: minutes}}`` mapping."""
        self._minutes = minutes

    @classmethod
    def load(cls, path: Path | str | None = None) -> "DistanceMatrix":
        """
        Read the matrix from disk, tolerating its absence.

        Resolution order matches the place repository: explicit argument, then
        ``$DISTANCE_MATRIX_FILE``, then the default path.

        A missing file yields an empty matrix rather than an exception: every
        leg then looks unreachable, the planner falls through to fallback step 2,
        and the user gets a list of places instead of a crash (section 4). The
        warning is how an operator finds out that `build_matrix.py` never ran.
        """
        path = Path(path or os.environ.get(MATRIX_PATH_ENV_VAR) or DEFAULT_MATRIX_PATH)
        if not path.exists():
            logger.warning(
                "distance matrix not found at %s — run scripts/build_matrix.py; "
                "all legs will be treated as unreachable",
                path,
            )
            return cls({})

        raw = json.loads(path.read_text(encoding="utf-8"))
        # Coerce to int here so a hand-edited float in the file cannot leak
        # fractional minutes into arrival times.
        minutes = {
            origin: {dest: int(round(value)) for dest, value in destinations.items()}
            for origin, destinations in raw.items()
        }
        return cls(minutes)

    def travel_min(self, from_id: str, to_id: str) -> Optional[int]:
        """
        Drive time between two places, or ``None`` when the pair is not in the matrix.

        ``None`` means unreachable, not free — callers must not coerce it to 0.
        A place to itself is 0, which keeps degenerate lookups harmless.
        """
        if from_id == to_id:
            return 0
        return self._minutes.get(from_id, {}).get(to_id)

    def reachable_from(self, from_id: str, within_min: int) -> set[str]:
        """
        Every place id reachable from ``from_id`` in at most ``within_min`` minutes.

        This is the planner's inner loop: filtering the candidate set by rule 6
        before scoring anything is far cheaper than scoring and then rejecting.
        """
        return {
            dest
            for dest, value in self._minutes.get(from_id, {}).items()
            if value <= within_min
        }

    def is_empty(self) -> bool:
        """True when no matrix was loaded, so callers can surface a clear diagnostic."""
        return not self._minutes

    def known_ids(self) -> set[str]:
        """Place ids the matrix has rows for — used to detect seed/matrix drift."""
        return set(self._minutes)
