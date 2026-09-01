"""Tests for domain/regions.py — region membership and the latitude smoke test."""

from __future__ import annotations

from api.domain import regions
from api.models import AccessType, Category, DescriptionSource, Place, Region

_ALL_CLOSED = {day: None for day in ("sun", "mon", "tue", "wed", "thu", "fri", "sat")}


def _place(**overrides) -> Place:
    base = dict(
        id="test-place",
        name_he="מקום בדיקה",
        description_he="",
        tip_he="",
        description_source=DescriptionSource.GENERATED,
        category=Category.NATURE,
        region=Region.SOUTH,
        access=AccessType.OPEN,
        lat=30.0,
        lng=35.0,
        duration_min=60,
        opening_hours=_ALL_CLOSED,
        hours_verified=False,
        closed_on_shabbat=False,
        kid_friendly=False,
        accessible=False,
    )
    base.update(overrides)
    return Place.model_validate(base)


def test_latitude_inside_band_is_not_flagged():
    place = _place(region=Region.SOUTH, lat=30.6)
    assert regions.latitude_looks_wrong(place) is False


def test_latitude_outside_band_is_flagged():
    place = _place(region=Region.SOUTH, lat=33.0)
    assert regions.latitude_looks_wrong(place) is True


def test_missing_latitude_is_never_flagged():
    place = _place(region=Region.SOUTH, lat=None)
    assert regions.latitude_looks_wrong(place) is False


def test_overridden_place_is_never_flagged_even_outside_its_band(monkeypatch):
    # lat=33.0 is outside the south band, so this would be flagged without
    # the override — the override registration below is what suppresses it.
    monkeypatch.delitem(regions.REGION_OVERRIDES_HE, "מקום בדיקה", raising=False)
    place = _place(name_he="מקום בדיקה", region=Region.SOUTH, lat=33.0)
    assert regions.latitude_looks_wrong(place) is True

    monkeypatch.setitem(regions.REGION_OVERRIDES_HE, "מקום בדיקה", Region.SOUTH)
    assert regions.latitude_looks_wrong(place) is False


def test_goda_is_registered_as_a_south_override():
    assert regions.region_override("GODA גודה") == Region.SOUTH
