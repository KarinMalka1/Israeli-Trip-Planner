"""
Region constants and membership (SPEC.md section 2).

A region is a hard boundary, not a hint: every stop in an itinerary belongs
to the selected region, and cross-region itineraries do not exist. The region
lives on the place as a stored field, hand-assigned in the seed. Nothing here
derives a region from coordinates — the latitude bands below exist only to
smoke-test the seed, never to classify at runtime.
"""

from __future__ import annotations

from typing import Iterable

from api.models import Place, Region

# Ordered for stable UI rendering: the chips appear north -> central -> south.
ALL_REGIONS: tuple[Region, ...] = (Region.NORTH, Region.CENTRAL, Region.SOUTH)

# The only user-facing strings in this module (rule 13).
REGION_LABELS_HE: dict[Region, str] = {
    Region.NORTH: "צפון",
    Region.CENTRAL: "מרכז",
    Region.SOUTH: "דרום",
}

# What each region covers, for copy and for reviewing seed assignments by hand.
REGION_COVERS_HE: dict[Region, str] = {
    Region.NORTH: "גליל, גולן, כרמל, עמקים",
    Region.CENTRAL: "שרון, גוש דן, שפלה, ירושלים והרי יהודה",
    Region.SOUTH: "נגב, ים המלח, ערבה, אילת",
}

# Rough latitude envelopes, deliberately overlapping at the seams. Used by
# `latitude_looks_wrong` to catch a typo'd or copy-pasted region in the seed —
# not to assign one. A place inside the overlap is never flagged.
REGION_LATITUDE_BANDS: dict[Region, tuple[float, float]] = {
    Region.NORTH: (32.4, 33.4),
    Region.CENTRAL: (31.5, 32.8),
    Region.SOUTH: (29.4, 31.8),
}


def in_region(place: Place, region: Region) -> bool:
    """Rule 5: exact membership. No 'just over the line' — the stored field decides."""
    return place.region == region


def filter_by_region(places: Iterable[Place], region: Region) -> list[Place]:
    """Narrow a place collection to one region, preserving the seed's order."""
    return [place for place in places if in_region(place, region)]


def latitude_looks_wrong(place: Place) -> bool:
    """
    Seed smoke test: does this place's latitude fall outside its region's band?

    Returns ``True`` only for a clear mismatch, which almost always means the
    ``region`` field was mistyped or copied from the row above. The bands
    overlap on purpose so honest edge cases (Jerusalem, the northern Negev)
    never trip it. A place with no coordinate yet (pending manual entry,
    BRIEF_data_pipeline.md v2) has nothing to check and is never flagged.
    """
    if place.lat is None:
        return False
    low, high = REGION_LATITUDE_BANDS[place.region]
    return not (low <= place.lat <= high)
