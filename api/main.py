"""
FastAPI app: routes only (SPEC.md section 8).

Nothing in this file does time arithmetic, filters places, or decides what a
valid itinerary is. Routes parse a request, call one collaborator, and return
the full recomputed ``Itinerary``. Anything more interesting than that belongs
in ``domain/``, ``planner/`` or ``repository/``.

Error bodies carry a Hebrew ``detail`` because the client renders it directly
(rule 13). Status codes and field names stay English.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware

from api.domain import schedule
from api.domain.distance import DistanceMatrix
from api.models import (
    CreateItineraryRequest,
    Itinerary,
    Place,
    PlaceRefRequest,
    Region,
)
from api.planner.base import ItineraryPlanner
from api.planner.edits import EditNotPossible, ItineraryEditor
from api.planner.rule_based import RuleBasedPlanner
from api.repository.itineraries import ItineraryStore
from api.repository.places import PlaceRepository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Load the seed and the matrix once at startup and hang them off app state.

    Both are read-only and shared by every request. Loading eagerly means a
    malformed seed file fails the boot rather than the hundredth request, which
    is the behaviour we want given the seed is the product.
    """
    places = PlaceRepository.load()
    matrix = DistanceMatrix.load()

    if matrix.is_empty():
        logger.warning("starting with an empty distance matrix — every plan will fall back")
    else:
        # Seed/matrix drift is the likeliest cause of a region that suddenly
        # cannot be planned, so name the missing ids at boot.
        missing = places.known_ids() - matrix.known_ids()
        if missing:
            logger.warning(
                "%d places have no matrix row (run scripts/build_matrix.py): %s",
                len(missing),
                ", ".join(sorted(missing)[:10]),
            )

    app.state.places = places
    app.state.matrix = matrix
    app.state.planner = RuleBasedPlanner(places, matrix)
    app.state.editor = ItineraryEditor(places, matrix)
    app.state.itineraries = ItineraryStore()
    yield


app = FastAPI(title="Israeli Day Trip Planner", version="0.1.0", lifespan=lifespan)

# The web client is a separate origin in development. MVP has no auth and no
# cookies, so there is nothing here for a permissive CORS policy to leak.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------


def get_places(request: Request) -> PlaceRepository:
    """The shared place repository, loaded at startup."""
    return request.app.state.places


def get_planner(request: Request) -> ItineraryPlanner:
    """The active planner. Typed as the interface so ``llm.py`` can replace it."""
    return request.app.state.planner


def get_editor(request: Request) -> ItineraryEditor:
    """The remove/swap editor."""
    return request.app.state.editor


def get_store(request: Request) -> ItineraryStore:
    """The itinerary store backing US-3 resume and the undo stack."""
    return request.app.state.itineraries


# --------------------------------------------------------------------------
# Places
# --------------------------------------------------------------------------


@app.get("/api/places", response_model=list[Place])
def list_places(
    region: Region | None = None,
    places: PlaceRepository = Depends(get_places),
) -> list[Place]:
    """
    Every place, optionally narrowed to one region.

    Exists so the web side can render real cards on day one without waiting for
    the planner. Omitting ``region`` returns the whole seed, which is what the
    fallback screen and local debugging want.
    """
    return places.by_region(region) if region else places.all()


# --------------------------------------------------------------------------
# Itineraries
# --------------------------------------------------------------------------


@app.post("/api/itinerary", response_model=Itinerary, status_code=status.HTTP_201_CREATED)
def create_itinerary(
    body: CreateItineraryRequest,
    planner: ItineraryPlanner = Depends(get_planner),
    places: PlaceRepository = Depends(get_places),
    store: ItineraryStore = Depends(get_store),
) -> Itinerary:
    """
    Plan a day from the two chips plus the weekday (US-1).

    The id is minted here rather than taken from the body: ``session_id``
    identifies the browser, and one browser may plan several itineraries. The
    planner handles both fallback steps internally, so a sparse region returns
    200 with cards, never an error.
    """
    itinerary = planner.plan(
        itinerary_id=str(uuid.uuid4()),
        region=body.region,
        max_leg_min=body.max_leg_min,
        weekday=body.weekday,
        month=body.month,
        prompt_he=body.prompt_he,
        chip=body.chip,
        seed=body.seed,
        with_meal=body.with_meal,
        starts_at=body.starts_at,
        day_length=body.day_length,
    )

    _reject_if_invalid(itinerary, places)
    return store.save(itinerary)


@app.get("/api/itinerary/{itinerary_id}", response_model=Itinerary)
def get_itinerary(
    itinerary_id: str,
    store: ItineraryStore = Depends(get_store),
) -> Itinerary:
    """
    Resume a saved itinerary (US-3).

    The client holds only a UUID in localStorage; everything else lives here.
    A 404 is the signal to start fresh — see the store's note about in-memory
    state not surviving a restart.
    """
    itinerary = store.get(itinerary_id)
    if itinerary is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="המסלול לא נמצא")
    return itinerary


@app.post("/api/itinerary/{itinerary_id}/remove", response_model=Itinerary)
def remove_stop(
    itinerary_id: str,
    body: PlaceRefRequest,
    editor: ItineraryEditor = Depends(get_editor),
    store: ItineraryStore = Depends(get_store),
) -> Itinerary:
    """
    Remove one stop and return the fully recomputed itinerary (US-2).

    The previous version goes onto the undo stack, which is what the toast's
    undo action walks back.
    """
    itinerary = _require(store, itinerary_id)

    try:
        edited = editor.remove(itinerary, body.place_id)
    except EditNotPossible as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=error.message_he) from error

    if edited is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="העצירה לא נמצאה במסלול")
    return store.update(edited)


@app.post("/api/itinerary/{itinerary_id}/swap", response_model=Itinerary)
def swap_stop(
    itinerary_id: str,
    body: PlaceRefRequest,
    editor: ItineraryEditor = Depends(get_editor),
    store: ItineraryStore = Depends(get_store),
) -> Itinerary:
    """
    Swap one stop for an alternative and return the recomputed itinerary (US-2).

    When no alternative fits, the editor returns the itinerary unchanged and
    this still responds 200 — the client compares and toasts. A swap with
    nothing to swap to is not an error condition.
    """
    itinerary = _require(store, itinerary_id)

    try:
        edited = editor.swap(itinerary, body.place_id)
    except EditNotPossible as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=error.message_he) from error

    if edited is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="העצירה לא נמצאה במסלול")
    return store.update(edited)


@app.post("/api/itinerary/{itinerary_id}/undo", response_model=Itinerary)
def undo_edit(
    itinerary_id: str,
    store: ItineraryStore = Depends(get_store),
) -> Itinerary:
    """
    Walk back one edit (US-2).

    With nothing left to undo this returns the current itinerary unchanged
    rather than 4xx: the undo toast must never produce an error state.
    """
    itinerary = _require(store, itinerary_id)
    restored = store.undo(itinerary_id)
    return restored if restored is not None else itinerary


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


@app.get("/api/health")
def health(request: Request) -> dict:
    """
    Liveness plus the two facts that actually break this service.

    An empty seed or an empty matrix produces a technically-correct API that
    can never plan anything, so both counts are surfaced here rather than left
    to be discovered through unexplained fallbacks.
    """
    places: PlaceRepository = request.app.state.places
    matrix: DistanceMatrix = request.app.state.matrix
    return {
        "status": "ok",
        "places": len(places),
        "matrix_loaded": not matrix.is_empty(),
    }


# --------------------------------------------------------------------------
# Shared route helpers
# --------------------------------------------------------------------------


def _require(store: ItineraryStore, itinerary_id: str) -> Itinerary:
    """Fetch an itinerary or raise the standard Hebrew 404. Used by every mutating route."""
    itinerary = store.get(itinerary_id)
    if itinerary is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="המסלול לא נמצא")
    return itinerary


def _reject_if_invalid(itinerary: Itinerary, places: PlaceRepository) -> None:
    """
    Last gate before a planned itinerary leaves the server.

    Rule 3's opening line: the API must never return an invalid itinerary. The
    planner builds valid days by construction, so a violation here is a bug in
    the planner or a broken seed — either way, a 500 with the reasons logged
    beats shipping a wrong schedule to the user.

    The exception is fallback step 2 with an empty region: there is genuinely
    nothing to show, which means the dataset is incomplete rather than the code
    being wrong, so it gets its own 503 and its own Hebrew message.
    """
    if not itinerary.days and not itinerary.places:
        logger.error("region %s has no places at all", itinerary.region.value)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="עדיין אין מקומות באזור הזה",
        )

    problems = schedule.validate_itinerary(itinerary, places.known_ids())
    if problems:
        logger.error("planner produced an invalid itinerary: %s", "; ".join(problems))
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="לא הצלחנו לבנות מסלול תקין",
        )
