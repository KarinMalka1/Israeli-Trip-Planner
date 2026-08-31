"""
Pydantic mirrors of the shared types frozen in SPEC.md section 7.

Rule 13 is enforced structurally here: every key, id and enum value is
English. Only the ``*_he`` fields carry Hebrew, and they are the only
strings that ever reach the user.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# "HH:MM" in 24h form. Every time in the contract is a wall-clock string,
# never a timestamp: the MVP has no timezone and no calendar date.
TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class Region(str, Enum):
    """The three hard region boundaries (SPEC section 2). Itineraries never cross one."""

    NORTH = "north"
    CENTRAL = "central"
    SOUTH = "south"


class Category(str, Enum):
    """Place categories. Only ``MEAL`` gets special treatment from the planner (rule 4)."""

    MUSEUM = "museum"
    NATURE = "nature"
    HIKE = "hike"
    VIEWPOINT = "viewpoint"
    MEAL = "meal"
    KIDS = "kids"
    HISTORIC = "historic"


class AccessType(str, Enum):
    """
    Whether a place has a gate a scheduler can trust (BRIEF_data_pipeline.md).

    ``GATED`` means there is a real gate, ticket desk or staffed entrance, so
    ``opening_hours`` is a claim about that gate. ``OPEN`` means a free-access
    trail, spring or viewpoint with no gate to close — ``opening_hours`` is
    always null for these, and they are daylight-bound rather than hour-bound.
    """

    GATED = "gated"
    OPEN = "open"


class DescriptionSource(str, Enum):
    """
    Provenance of ``description_he``/``tip_he`` (BRIEF_data_pipeline.md).

    ``GENERATED`` means an LLM wrote the prose from verified factual fields
    and nothing else. ``HUMAN`` means a person wrote or reviewed it. This is
    provenance metadata for the review tooling, not a claim about the facts
    elsewhere on the place — those are governed by ``hours_verified``.
    """

    GENERATED = "generated"
    HUMAN = "human"


class Weekday(str, Enum):
    """
    Weekday keys for ``Place.opening_hours``, Sunday-first as the Israeli week runs.

    These are the literal JSON keys in the seed file, so the values must stay
    lowercase three-letter abbreviations.
    """

    SUN = "sun"
    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"

    @classmethod
    def from_date(cls, day: dt.date) -> "Weekday":
        """
        Map a calendar date onto a seed-file weekday key.

        ``date.weekday()`` is Monday-indexed (Mon=0 .. Sun=6), so we index a
        Monday-first tuple with it rather than reordering the enum itself.
        """
        monday_first = (cls.MON, cls.TUE, cls.WED, cls.THU, cls.FRI, cls.SAT, cls.SUN)
        return monday_first[day.weekday()]


# The three drive-time chips. There is deliberately no "unlimited" option
# (SPEC section 2), and the tuple order doubles as the relaxation ladder
# used by fallback step 1 (SPEC section 4).
MaxLegMin = Literal[20, 45, 90]
MAX_LEG_STEPS: tuple[int, int, int] = (20, 45, 90)
DEFAULT_MAX_LEG_MIN: int = 45


class Place(BaseModel):
    """
    One curated place from the seed dataset.

    This is the only source of truth for names, hours and durations: the
    planner may select places but must never invent or adjust their fields.
    """

    id: str
    name_he: str
    description_he: str
    tip_he: str
    # Provenance of description_he/tip_he (BRIEF_data_pipeline.md). Does not
    # itself certify any fact about the place — see hours_verified for that.
    description_source: DescriptionSource
    category: Category
    # Hand-assigned in the seed, never derived from lat/lng at runtime
    # (SPEC section 6).
    region: Region
    # Whether opening_hours describes a real gate (BRIEF_data_pipeline.md).
    # A "open" place has no gate to close and is scheduled by daylight, not
    # by opening_hours, which is why that field is always null for it.
    access: AccessType
    # None means "not yet resolved" — an OSM name search that found zero or
    # multiple matches, pending manual entry (BRIEF_data_pipeline.md v2). A
    # place with null coordinates can never be scheduled or matrix-built;
    # nothing here invents a coordinate to paper over that.
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lng: Optional[float] = Field(default=None, ge=-180, le=180)
    # Realistic visit length, not the minimum possible.
    duration_min: int = Field(gt=0)
    # ``None`` for a weekday means closed that day; otherwise [open, close].
    opening_hours: dict[Weekday, Optional[tuple[str, str]]]
    # True only when a human read these hours from an official source
    # (SPEC section 6). An unverified gated place is unschedulable — see the
    # scheduler consequence noted in BRIEF_data_pipeline.md.
    hours_verified: bool
    closed_on_shabbat: bool
    kid_friendly: bool
    accessible: bool
    # English, lowercase; drawn from the vocabulary in places.template.json.
    tags: list[str] = Field(default_factory=list)
    # Water parks and some springs only make sense April-October
    # (BRIEF_data_acquisition.md). A dataset scraped in August is confidently
    # wrong in November without this — the scheduler consequence (exclude
    # summer_only outside Apr-Oct) is tracked as a TODO in domain/schedule.py,
    # not implemented here.
    season: Literal["year_round", "summer_only"] = "year_round"

    @field_validator("opening_hours")
    @classmethod
    def _all_weekdays_present(
        cls, hours: dict[Weekday, Optional[tuple[str, str]]]
    ) -> dict[Weekday, Optional[tuple[str, str]]]:
        """
        Require an explicit entry for all seven weekdays.

        A missing key is ambiguous — it could mean "closed" or "we forgot to
        check" — and rule 11 depends on that distinction, so we reject the
        place at load time instead of guessing.
        """
        missing = [day.value for day in Weekday if day not in hours]
        if missing:
            raise ValueError(f"opening_hours is missing weekdays: {', '.join(missing)}")
        return hours

    @field_validator("opening_hours")
    @classmethod
    def _hours_are_well_formed(
        cls, hours: dict[Weekday, Optional[tuple[str, str]]]
    ) -> dict[Weekday, Optional[tuple[str, str]]]:
        """Reject malformed or reversed time windows before the planner ever sees them."""
        import re

        for day, window in hours.items():
            if window is None:
                continue
            opens, closes = window
            if not re.match(TIME_PATTERN, opens) or not re.match(TIME_PATTERN, closes):
                raise ValueError(f"{day.value}: times must be 'HH:MM', got {window!r}")
            if opens >= closes:
                # Lexicographic comparison is safe for zero-padded "HH:MM".
                raise ValueError(f"{day.value}: opens at or after it closes ({window!r})")
        return hours


class Stop(BaseModel):
    """
    One visit inside a day, with its arrival time and the leg that led to it.

    ``place`` is embedded in full so the client can render a card without a
    second lookup; ``place_id`` stays as the stable handle for mutations.
    """

    place_id: str
    place: Place
    arrive_at: str = Field(pattern=TIME_PATTERN)
    duration_min: int = Field(gt=0)
    # Travel from the previous stop. Always 0 for the first stop: there is no
    # origin and no leg before it (rule 8).
    travel_min_from_prev: int = Field(ge=0)


class Day(BaseModel):
    """A single planned day. MVP emits exactly one of these, or none on fallback step 2."""

    stops: list[Stop]
    starts_at: str = Field(pattern=TIME_PATTERN)
    # Always computed server-side from the stops; never trusted from the client.
    ends_at: str = Field(pattern=TIME_PATTERN)


class Itinerary(BaseModel):
    """
    The full response body. Every mutating endpoint returns one of these,
    recomputed from scratch, so the client stays a pure renderer.
    """

    id: str
    region: Region
    max_leg_min: int
    weekday: Weekday
    # MVP: length 1, or 0 when fallback step 2 fired.
    days: list[Day] = Field(default_factory=list)
    # Populated only on fallback step 2, as unscheduled cards.
    places: list[Place] = Field(default_factory=list)
    # Set when fallback step 1 relaxed the leg cap, so the client can toast.
    relaxed_to: Optional[int] = None
    # The RNG seed the planner used to choose among valid options (never to
    # relax a rule). Always present: the planner mints one when the request
    # doesn't supply it. Reusing this value reproduces the exact same day —
    # the point is to let a specific bad day be reported and replayed.
    seed: int


class CreateItineraryRequest(BaseModel):
    """Body of ``POST /api/itinerary``."""

    session_id: str
    region: Region
    max_leg_min: MaxLegMin = DEFAULT_MAX_LEG_MIN
    weekday: Weekday
    # Calendar month (1-12), for the season amendment (SPEC section 11): a
    # summer_only place is excluded outside April-October. There is no
    # request date, only a weekday, so this is the narrowest addition that
    # lets the caller pin a month; omitted or null defaults to the current
    # calendar month.
    month: Optional[int] = Field(default=None, ge=1, le=12)
    # Reserved for the LLM planner (planner/llm.py). The rule-based MVP
    # planner accepts and ignores them, so the contract need not change later.
    prompt_he: Optional[str] = None
    chip: Optional[str] = None
    # Omit to get a random day; repeat a specific value to reproduce one
    # exactly (e.g. to report a bug against a particular itinerary).
    seed: Optional[int] = None


class PlaceRefRequest(BaseModel):
    """Body of the ``/remove`` and ``/swap`` endpoints: which stop to act on."""

    place_id: str
