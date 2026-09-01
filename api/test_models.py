"""
Tests for models.py's Place.images / official_url (Commons-photo pilot).

Most Commons files are CC BY or CC BY-SA, which legally require attribution,
so PlaceImage's three fields are all required and Place caps images at 3 —
both enforced here, not left to the client to respect on its own.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.models import AccessType, Category, DescriptionSource, Place, PlaceImage, Region, Weekday


def _place(**overrides) -> Place:
    base = dict(
        id="central-test-1",
        name_he="מקום בדיקה",
        description_he="",
        tip_he="",
        description_source=DescriptionSource.GENERATED,
        category=Category.NATURE,
        region=Region.CENTRAL,
        access=AccessType.GATED,
        duration_min=60,
        opening_hours={day: None for day in Weekday},
        hours_verified=False,
        closed_on_shabbat=False,
        kid_friendly=False,
        accessible=False,
    )
    base.update(overrides)
    return Place(**base)


def _image(**overrides) -> dict:
    base = dict(
        url="/images/ein-hemed-1.jpg",
        credit="Nis101, CC BY-SA 4.0",
        source_url="https://commons.wikimedia.org/wiki/File:עין_חמד.jpg",
    )
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# images: at most 3, enforced by the model
# --------------------------------------------------------------------------


def test_place_with_no_images_is_valid():
    place = _place(images=[])
    assert place.images == []


def test_place_with_up_to_three_images_is_valid():
    place = _place(images=[_image(url=f"/images/ein-hemed-{i}.jpg") for i in (1, 2, 3)])
    assert len(place.images) == 3


def test_place_with_more_than_three_images_fails_validation():
    with pytest.raises(ValidationError, match="at most 3 images"):
        _place(images=[_image(url=f"/images/ein-hemed-{i}.jpg") for i in (1, 2, 3, 4)])


# --------------------------------------------------------------------------
# PlaceImage: credit and source_url are required, not optional — an
# un-attributed image must never construct successfully.
# --------------------------------------------------------------------------


def test_image_missing_credit_fails_validation():
    with pytest.raises(ValidationError):
        PlaceImage(url="/images/x.jpg", source_url="https://commons.wikimedia.org/wiki/File:x.jpg")


def test_image_missing_source_url_fails_validation():
    with pytest.raises(ValidationError):
        PlaceImage(url="/images/x.jpg", credit="Someone, CC BY-SA 4.0")


def test_image_with_all_three_fields_is_valid():
    image = PlaceImage(**_image())
    assert image.url and image.credit and image.source_url


def test_place_with_image_missing_credit_fails_validation():
    """The same failure, reached through Place — not just PlaceImage in isolation."""
    bad_image = _image()
    del bad_image["credit"]
    with pytest.raises(ValidationError):
        _place(images=[bad_image])


# --------------------------------------------------------------------------
# official_url: optional, defaults to None
# --------------------------------------------------------------------------


def test_official_url_defaults_to_none():
    place = _place()
    assert place.official_url is None


def test_official_url_can_be_set():
    place = _place(official_url="https://www.parks.org.il/reserve-park/ein-hemed/")
    assert place.official_url == "https://www.parks.org.il/reserve-park/ein-hemed/"
