"""
Tests for fetch_commons_images.py, entirely against local fixtures/inline dicts — never the network.

fixtures/commons_imageinfo_sample.json mirrors the real Commons
``prop=imageinfo&iiprop=url|extmetadata|mime`` response shape (one file's
imageinfo, keyed by title here for lookup convenience): a valid CC BY-SA
photo, an SVG (non-photo mime), a photo missing Artist, a CC BY-NC photo,
and a CC0 photo. Wikidata claim/sitelink shapes below were hand-verified
against a live ``wbgetentities`` response for Masada (Q186312) rather than
guessed.
"""

from __future__ import annotations

import json
from pathlib import Path

from api.scripts import fetch_commons_images as fci

FIXTURES = Path(__file__).parent / "fixtures"


def _imageinfo_map() -> dict:
    return json.loads((FIXTURES / "commons_imageinfo_sample.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# License gate — shared by every source
# --------------------------------------------------------------------------


def test_license_allowed_for_cc0():
    assert fci._license_allowed("CC0 1.0") is True


def test_license_allowed_for_cc_by():
    assert fci._license_allowed("CC BY 4.0") is True


def test_license_allowed_for_cc_by_sa():
    assert fci._license_allowed("CC BY-SA 4.0") is True
    assert fci._license_allowed("CC BY-SA 2.5") is True


def test_license_allowed_for_public_domain():
    assert fci._license_allowed("Public domain") is True
    assert fci._license_allowed("PD-old") is True


def test_license_rejects_non_commercial():
    """CC BY-NC must not match a naive "starts with CC BY" substring check."""
    assert fci._license_allowed("CC BY-NC 4.0") is False


def test_license_rejects_no_derivatives():
    assert fci._license_allowed("CC BY-ND 4.0") is False


def test_license_rejects_unrecognised_license():
    assert fci._license_allowed("GFDL") is False


def test_license_rejects_empty_string():
    assert fci._license_allowed("") is False


def test_openverse_license_map_matches_commons_vocabulary():
    """Openverse's short codes map onto exactly the strings _license_allowed already accepts."""
    assert fci._openverse_license_short_name("cc0") == "CC0 1.0"
    assert fci._openverse_license_short_name("by") == "CC BY 4.0"
    assert fci._openverse_license_short_name("by-sa") == "CC BY-SA 4.0"
    assert fci._license_allowed(fci._openverse_license_short_name("by-sa"))


def test_openverse_license_map_rejects_nc_and_nd():
    assert fci._openverse_license_short_name("by-nc") is None
    assert fci._openverse_license_short_name("by-nd") is None


def test_openverse_license_map_rejects_none_and_empty():
    assert fci._openverse_license_short_name(None) is None
    assert fci._openverse_license_short_name("") is None


# --------------------------------------------------------------------------
# _looks_like_a_credit_name — rejects a sourcing note masquerading as an Artist
# --------------------------------------------------------------------------


def test_looks_like_a_credit_name_true_for_a_plain_name():
    assert fci._looks_like_a_credit_name("Someone") is True
    assert fci._looks_like_a_credit_name("Hadar Leonzini") is True


def test_looks_like_a_credit_name_false_for_multiline_text():
    assert fci._looks_like_a_credit_name("Sources:\n\nPEF Survey\nOverlay from Palestine Open Maps") is False


def test_looks_like_a_credit_name_false_when_too_long():
    assert fci._looks_like_a_credit_name("A" * (fci.MAX_ARTIST_LENGTH + 1)) is False


def test_looks_like_a_credit_name_true_at_exactly_the_length_cap():
    assert fci._looks_like_a_credit_name("A" * fci.MAX_ARTIST_LENGTH) is True


# --------------------------------------------------------------------------
# extract_image_candidate (Commons-title based — shared by 4 of the 5 sources)
# --------------------------------------------------------------------------


def test_extract_valid_candidate_strips_html_and_builds_credit():
    imageinfo = _imageinfo_map()["File:Ein Hemed pools.jpg"]
    candidate = fci.extract_image_candidate("File:Ein Hemed pools.jpg", imageinfo, source="wikidata")
    assert candidate is not None
    assert candidate.credit == "Someone, CC BY-SA 4.0"
    assert candidate.file_page_url == "https://commons.wikimedia.org/wiki/File:Ein_Hemed_pools.jpg"
    assert candidate.direct_url == imageinfo["url"]
    assert candidate.source == "wikidata"


def test_extract_returns_none_when_artist_missing():
    imageinfo = _imageinfo_map()["File:No credit photo.jpg"]
    assert fci.extract_image_candidate("File:No credit photo.jpg", imageinfo, source="wikidata") is None


def test_extract_returns_none_for_disallowed_license():
    imageinfo = _imageinfo_map()["File:NC licensed photo.jpg"]
    assert fci.extract_image_candidate("File:NC licensed photo.jpg", imageinfo, source="wikidata") is None


def test_extract_returns_none_when_artist_is_a_sourcing_note_not_a_name():
    """Real Commons bug: a composited historical map's Artist field can be a multi-paragraph note."""
    imageinfo = {
        "url": "https://upload.wikimedia.org/wikipedia/commons/x/xy/Historical_map.jpg",
        "mime": "image/jpeg",
        "extmetadata": {
            "Artist": {"value": "Sources for historical series of maps as follows:\n\nPEF Survey of Palestine\nOverlay from Palestine Open Maps"},
            "LicenseShortName": {"value": "Public domain"},
        },
    }
    assert fci.extract_image_candidate("File:Historical map.jpg", imageinfo, source="commons_geosearch") is None


def test_skip_reason_for_sourcing_note_artist():
    imageinfo = {
        "extmetadata": {
            "Artist": {"value": "Sources for historical series of maps as follows:\n\nPEF Survey of Palestine"},
            "LicenseShortName": {"value": "Public domain"},
        }
    }
    assert fci._skip_reason(imageinfo) == "Artist metadata isn't a plain name (too long or multi-line) — cannot use as a credit"


def test_extract_accepts_cc0():
    imageinfo = _imageinfo_map()["File:CC0 photo.jpg"]
    candidate = fci.extract_image_candidate("File:CC0 photo.jpg", imageinfo, source="commons_geosearch")
    assert candidate is not None
    assert candidate.credit == "Jane Doe, CC0 1.0"
    assert candidate.source == "commons_geosearch"


# --------------------------------------------------------------------------
# validate_commons_titles — the pure end-to-end core for 4 of the 5 sources
# --------------------------------------------------------------------------


def test_validate_commons_titles_matches_only_the_valid_jpeg():
    imageinfo_by_title = _imageinfo_map()
    titles = ["File:Ein Hemed pools.jpg", "File:Ein Hemed map.svg", "File:No credit photo.jpg"]

    matched, skipped = fci.validate_commons_titles(titles, imageinfo_by_title, source="commons_category")

    assert [c.title for c in matched] == ["File:Ein Hemed pools.jpg"]
    assert [c.source for c in matched] == ["commons_category"]
    skipped_titles = [title for title, _reason in skipped]
    assert skipped_titles == ["File:Ein Hemed map.svg", "File:No credit photo.jpg"]


def test_validate_commons_titles_reports_svg_as_not_a_jpeg():
    imageinfo_by_title = _imageinfo_map()
    matched, skipped = fci.validate_commons_titles(
        ["File:Ein Hemed map.svg"], imageinfo_by_title, source="wikipedia_he"
    )
    assert matched == []
    assert skipped == [("File:Ein Hemed map.svg", "not a JPEG (mime='image/svg+xml')")]


def test_validate_commons_titles_reports_disallowed_license_reason():
    imageinfo_by_title = _imageinfo_map()
    matched, skipped = fci.validate_commons_titles(
        ["File:NC licensed photo.jpg"], imageinfo_by_title, source="wikipedia_en"
    )
    assert matched == []
    assert skipped == [("File:NC licensed photo.jpg", "license not allowed: 'CC BY-NC 4.0'")]


def test_validate_commons_titles_handles_missing_imageinfo():
    """A title the caller looked up but Commons imageinfo came back empty for."""
    matched, skipped = fci.validate_commons_titles(
        ["File:Missing.jpg"], {"File:Missing.jpg": None}, source="commons_geosearch"
    )
    assert matched == []
    assert skipped == [("File:Missing.jpg", "no imageinfo returned for this title")]


def test_validate_commons_titles_respects_limit_even_when_a_later_one_would_pass():
    """The 4th candidate (valid CC0) is never reached because the cap is 3 — no backfilling."""
    imageinfo_by_title = _imageinfo_map()
    titles = [
        "File:Ein Hemed map.svg", "File:No credit photo.jpg",
        "File:NC licensed photo.jpg", "File:CC0 photo.jpg",
    ]
    matched, skipped = fci.validate_commons_titles(titles, imageinfo_by_title, source="commons_category", limit=3)
    assert matched == []
    assert "File:CC0 photo.jpg" not in [title for title, _ in skipped]


# --------------------------------------------------------------------------
# Openverse candidate extraction
# --------------------------------------------------------------------------


def _openverse_item(**overrides) -> dict:
    item = {
        "id": "abc123",
        "title": "Masada at sunrise",
        "creator": "Jane Photographer",
        "license": "by-sa",
        "url": "https://live.staticflickr.com/1234/photo_b.jpg",
        "foreign_landing_url": "https://www.flickr.com/photos/example/1234",
        "filetype": None,
    }
    item.update(overrides)
    return item


def test_extract_openverse_candidate_builds_credit_and_source_url():
    candidate = fci.extract_openverse_candidate(_openverse_item())
    assert candidate is not None
    assert candidate.credit == "Jane Photographer, CC BY-SA 4.0"
    assert candidate.file_page_url == "https://www.flickr.com/photos/example/1234"
    assert candidate.direct_url == "https://live.staticflickr.com/1234/photo_b.jpg"
    assert candidate.source == "openverse"


def test_extract_openverse_candidate_none_when_creator_missing():
    assert fci.extract_openverse_candidate(_openverse_item(creator="")) is None


def test_extract_openverse_candidate_none_when_url_missing():
    assert fci.extract_openverse_candidate(_openverse_item(url=None)) is None


def test_extract_openverse_candidate_none_for_disallowed_license():
    assert fci.extract_openverse_candidate(_openverse_item(license="by-nc")) is None


def test_extract_openverse_candidate_uses_filetype_field_when_present():
    assert fci.extract_openverse_candidate(_openverse_item(filetype="png")) is None
    assert fci.extract_openverse_candidate(_openverse_item(filetype="jpg")) is not None


def test_extract_openverse_candidate_falls_back_to_url_extension():
    item = _openverse_item(filetype=None, url="https://example.com/photo.png")
    assert fci.extract_openverse_candidate(item) is None
    item_jpeg = _openverse_item(filetype=None, url="https://example.com/photo.jpeg")
    assert fci.extract_openverse_candidate(item_jpeg) is not None


def test_validate_openverse_items_matches_and_skips():
    items = [_openverse_item(), _openverse_item(id="d2", creator="", title="No credit one")]
    matched, skipped = fci.validate_openverse_items(items)
    assert [c.title for c in matched] == ["Masada at sunrise"]
    assert skipped == [("No credit one", "missing creator or image URL — cannot attribute")]


def test_validate_openverse_items_empty_list():
    matched, skipped = fci.validate_openverse_items([])
    assert matched == []
    assert skipped == []


# --------------------------------------------------------------------------
# Wikidata claim/sitelink extraction — shapes verified against a live
# wbgetentities response for Masada (Q186312)
# --------------------------------------------------------------------------


def _masada_entity() -> dict:
    return {
        "claims": {
            "P18": [{"mainsnak": {"datavalue": {"value": "Israel-2013-Aerial_21-Masada.jpg"}}}],
            "P373": [{"mainsnak": {"datavalue": {"value": "Masada"}}}],
        },
        "sitelinks": {
            "hewiki": {"site": "hewiki", "title": "מצדה"},
            "enwiki": {"site": "enwiki", "title": "Masada"},
        },
    }


def test_extract_p18_filename():
    assert fci.extract_p18_filename(_masada_entity()) == "File:Israel-2013-Aerial_21-Masada.jpg"


def test_extract_p18_filename_none_when_claim_missing():
    assert fci.extract_p18_filename({"claims": {}}) is None
    assert fci.extract_p18_filename({}) is None


def test_extract_p373_category():
    assert fci.extract_p373_category(_masada_entity()) == "Category:Masada"


def test_extract_p373_category_none_when_claim_missing():
    assert fci.extract_p373_category({"claims": {}}) is None


def test_extract_sitelink_title():
    entity = _masada_entity()
    assert fci.extract_sitelink_title(entity, "hewiki") == "מצדה"
    assert fci.extract_sitelink_title(entity, "enwiki") == "Masada"


def test_extract_sitelink_title_none_when_site_missing():
    assert fci.extract_sitelink_title(_masada_entity(), "frwiki") is None
    assert fci.extract_sitelink_title({}, "hewiki") is None


# --------------------------------------------------------------------------
# needs_images / build_image_entries
# --------------------------------------------------------------------------


def test_needs_images_true_when_key_missing():
    assert fci.needs_images({"id": "x"}) is True


def test_needs_images_true_when_list_empty():
    assert fci.needs_images({"id": "x", "images": []}) is True


def test_needs_images_false_when_images_present():
    assert fci.needs_images({"id": "x", "images": [{"url": "/images/x-1.jpg"}]}) is False


def test_build_image_entries_shapes_url_credit_source():
    matched = [
        fci.ImageCandidate(
            title="File:A.jpg", direct_url="https://upload.wikimedia.org/.../A.jpg",
            file_page_url="https://commons.wikimedia.org/wiki/File:A.jpg", credit="Someone, CC BY 4.0",
            source="wikidata",
        ),
        fci.ImageCandidate(
            title="File:B.jpg", direct_url="https://upload.wikimedia.org/.../B.jpg",
            file_page_url="https://commons.wikimedia.org/wiki/File:B.jpg", credit="Other, CC0 1.0",
            source="openverse",
        ),
    ]
    entries = fci.build_image_entries("central-seed-abc123", matched)
    assert entries == [
        {
            "url": "/images/central-seed-abc123-1.jpg",
            "credit": "Someone, CC BY 4.0",
            "source_url": "https://commons.wikimedia.org/wiki/File:A.jpg",
        },
        {
            "url": "/images/central-seed-abc123-2.jpg",
            "credit": "Other, CC0 1.0",
            "source_url": "https://commons.wikimedia.org/wiki/File:B.jpg",
        },
    ]
    # needs_review is a report concept only — never written into the seed entry.
    assert all("needs_review" not in entry for entry in entries)


# --------------------------------------------------------------------------
# Retry classification / failure handling / summaries — the crash-safety bits
# --------------------------------------------------------------------------


def test_is_retryable_true_for_429():
    assert fci._is_retryable(429) is True


def test_is_retryable_true_for_5xx():
    assert fci._is_retryable(500) is True
    assert fci._is_retryable(503) is True


def test_is_retryable_false_for_404():
    assert fci._is_retryable(404) is False


def test_is_retryable_false_for_200():
    assert fci._is_retryable(200) is False


def test_failed_result_carries_place_identity_and_error_text():
    row = {"id": "central-x", "name_he": "מקום", "region": "central"}
    result = fci._failed_result(row, RuntimeError("429 after 3 retries"))
    assert result.place_id == "central-x"
    assert result.name_he == "מקום"
    assert result.region == "central"
    assert result.matched == []
    assert result.failed_reason == "429 after 3 retries"


def test_failed_result_tolerates_a_row_missing_fields():
    """A place that failed before its own id/name_he were even read back out."""
    result = fci._failed_result({}, RuntimeError("boom"))
    assert result.place_id == "?"
    assert result.name_he == "?"


def _candidate(source: str = "wikidata") -> fci.ImageCandidate:
    return fci.ImageCandidate(title="File:A.jpg", direct_url="u", file_page_url="p", credit="c", source=source)


def _result(*, matched=None, failed=None) -> fci.PlaceResult:
    return fci.PlaceResult(
        place_id="x", name_he="x", region="central",
        matched=matched or [], attempts=[], failed_reason=failed,
    )


def test_summarize_counts_matched_skipped_failed_separately():
    results = [
        _result(matched=[_candidate()]),
        _result(),
        _result(failed="429 after 3 retries"),
    ]
    assert fci.summarize(results) == (1, 1, 1)


def test_summarize_all_matched():
    results = [_result(matched=[_candidate()]), _result(matched=[_candidate()])]
    assert fci.summarize(results) == (2, 0, 0)


def test_place_result_source_is_the_winning_candidates_source():
    result = _result(matched=[_candidate(source="openverse")])
    assert result.source == "openverse"


def test_place_result_source_is_none_when_nothing_matched():
    assert _result().source is None


def test_needs_review_true_only_for_commons_geosearch():
    assert _result(matched=[_candidate(source="commons_geosearch")]).needs_review is True
    assert _result(matched=[_candidate(source="wikidata")]).needs_review is False
    assert _result(matched=[_candidate(source="openverse")]).needs_review is False


def test_summarize_by_source_counts_per_winning_source_in_priority_order():
    results = [
        _result(matched=[_candidate(source="openverse")]),
        _result(matched=[_candidate(source="wikidata")]),
        _result(matched=[_candidate(source="wikidata")]),
        _result(),  # no match — excluded
    ]
    assert fci.summarize_by_source(results) == {"wikidata": 2, "openverse": 1}


def test_needs_review_places_lists_only_geosearch_matches():
    results = [
        fci.PlaceResult(
            place_id="a", name_he="Place A", region="north",
            matched=[_candidate(source="commons_geosearch")], attempts=[],
        ),
        fci.PlaceResult(
            place_id="b", name_he="Place B", region="north",
            matched=[_candidate(source="wikidata")], attempts=[],
        ),
    ]
    assert fci.needs_review_places(results) == [("a", "Place A")]
