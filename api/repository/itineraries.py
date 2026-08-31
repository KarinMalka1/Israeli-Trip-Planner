"""
Itinerary persistence and undo history.

Not in the SPEC.md section 8 file tree, but three endpoints need it and none
of them belong in ``main.py``: US-3 resume needs an itinerary to survive the
tab closing, and ``/undo`` needs the version before the last edit.

In-memory for the MVP. Section 5 rules out accounts and a login, not storage,
so the natural next step is swapping the dicts below for a table keyed by the
same session-scoped UUID — the interface here is deliberately the one a
database-backed version would expose.

Consequence to be aware of: restarting the server drops every saved itinerary,
so US-3 holds within a server lifetime only.
"""

from __future__ import annotations

import threading
from typing import Optional

from api.models import Itinerary

# How many versions back ``/undo`` can walk. The toast offers one undo, so a
# short stack is plenty; the cap keeps a long editing session bounded.
MAX_HISTORY_PER_ITINERARY = 20


class ItineraryStore:
    """
    Current version plus an undo stack, per itinerary id.

    Guarded by a lock because a FastAPI worker serves requests concurrently and
    the remove/swap flow is read-modify-write.
    """

    def __init__(self) -> None:
        """Start empty. Nothing is loaded from disk; the store is per-process."""
        self._current: dict[str, Itinerary] = {}
        self._history: dict[str, list[Itinerary]] = {}
        self._lock = threading.Lock()

    def save(self, itinerary: Itinerary) -> Itinerary:
        """
        Store a freshly planned itinerary, clearing any history under that id.

        Planning is a new start, not an edit, so there is nothing to undo back to.
        """
        with self._lock:
            self._current[itinerary.id] = itinerary
            self._history[itinerary.id] = []
        return itinerary

    def get(self, itinerary_id: str) -> Optional[Itinerary]:
        """Fetch the current version — the read behind ``GET /api/itinerary/{id}`` (US-3)."""
        with self._lock:
            return self._current.get(itinerary_id)

    def update(self, itinerary: Itinerary) -> Itinerary:
        """
        Replace the current version, pushing the one it replaces onto the undo stack.

        Called by ``/remove`` and ``/swap``. The stack is trimmed from the
        oldest end so memory stays bounded during a long editing session.
        """
        with self._lock:
            previous = self._current.get(itinerary.id)
            if previous is not None:
                history = self._history.setdefault(itinerary.id, [])
                history.append(previous)
                if len(history) > MAX_HISTORY_PER_ITINERARY:
                    del history[0]
            self._current[itinerary.id] = itinerary
        return itinerary

    def undo(self, itinerary_id: str) -> Optional[Itinerary]:
        """
        Pop one version off the stack and make it current again.

        Returns ``None`` when there is nothing to undo, which the route turns
        into a plain "no change" rather than an error — US-2 promises undo never
        raises a dialog at the user.
        """
        with self._lock:
            history = self._history.get(itinerary_id)
            if not history:
                return None
            restored = history.pop()
            self._current[itinerary_id] = restored
            return restored

    def can_undo(self, itinerary_id: str) -> bool:
        """Whether the undo toast has anything to offer for this itinerary."""
        with self._lock:
            return bool(self._history.get(itinerary_id))
