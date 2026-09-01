"""
Tests for fetch_commons_images.py, entirely against local fixtures — never the network.

fixtures/commons_imageinfo_sample.json mirrors the real Commons
``prop=imageinfo&iiprop=url|extmetadata|mime`` response shape (one file's
imageinfo, keyed by title here for lookup convenience): a valid CC BY-SA
photo, an SVG (non-photo mime), a photo missing Artist, a CC BY-NC photo,
and a CC0 photo.
"""

from __future__ import annotations

import json
from pathlib import Path

from api.scripts import fetch_commons_images as fci

FIXTURES = Path(__file__).parent / "fixtures"


def _imageinfo_map() -> dict:
    return json.loads((FIXTURES / "commons_imageinfo_sample.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# License gate
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


# --------------------------------------------------------------------------
# Candidate combination
# --------------------------------------------------------------------------


def test_combine_prefers_geosearch_order():
    combined = fci.combine_candidate_titles(["A", "B"], ["C", "D"], limit=3)
    assert combined == ["A", "B", "C"]


def test_combine_dedupes_across_both_lists():
    combined = fci.combine_candidate_titles(["A", "B"], ["B", "C"], limit=5)
    assert combined == ["A", "B", "C"]


def test_combine_caps_at_limit():
    combined = fci.combine_candidate_titles(["A", "B", "C", "D"], [], limit=2)
    assert combined == ["A", "B"]


# --------------------------------------------------------------------------
# extract_image_candidate
# --------------------------------------------------------------------------


def test_extract_valid_candidate_strips_html_and_builds_credit():
    imageinfo = _imageinfo_map()["File:Ein Hemed pools.jpg"]
    candidate = fci.extract_image_candidate("File:Ein Hemed pools.jpg", imageinfo)
    assert candidate is not None
    assert candidate.credit == "Someone, CC BY-SA 4.0"
    assert candidate.file_page_url == "https://commons.wikimedia.org/wiki/File:Ein_Hemed_pools.jpg"
    assert candidate.direct_url == imageinfo["url"]


def test_extract_returns_none_when_artist_missing():
    imageinfo = _imageinfo_map()["File:No credit photo.jpg"]
    assert fci.extract_image_candidate("File:No credit photo.jpg", imageinfo) is None


def test_extract_returns_none_for_disallowed_license():
    imageinfo = _imageinfo_map()["File:NC licensed photo.jpg"]
    assert fci.extract_image_candidate("File:NC licensed photo.jpg", imageinfo) is None


def test_extract_accepts_cc0():
    imageinfo = _imageinfo_map()["File:CC0 photo.jpg"]
    candidate = fci.extract_image_candidate("File:CC0 photo.jpg", imageinfo)
    assert candidate is not None
    assert candidate.credit == "Jane Doe, CC0 1.0"


# --------------------------------------------------------------------------
# select_and_validate_images — the pure end-to-end core
# --------------------------------------------------------------------------


def test_select_and_validate_images_matches_only_the_valid_jpeg():
    imageinfo_by_title = _imageinfo_map()
    geo_titles = ["File:Ein Hemed pools.jpg", "File:Ein Hemed map.svg"]
    name_titles = ["File:No credit photo.jpg"]

    matched, considered, skipped = fci.select_and_validate_images(
        geo_titles, name_titles, imageinfo_by_title, candidate_limit=3
    )

    assert considered == [
        "File:Ein Hemed pools.jpg", "File:Ein Hemed map.svg", "File:No credit photo.jpg",
    ]
    assert [c.title for c in matched] == ["File:Ein Hemed pools.jpg"]
    skipped_titles = [title for title, _reason in skipped]
    assert skipped_titles == ["File:Ein Hemed map.svg", "File:No credit photo.jpg"]


def test_select_and_validate_images_reports_svg_as_not_a_jpeg():
    imageinfo_by_title = _imageinfo_map()
    matched, considered, skipped = fci.select_and_validate_images(
        [], ["File:Ein Hemed map.svg"], imageinfo_by_title
    )
    assert matched == []
    assert skipped == [("File:Ein Hemed map.svg", "not a JPEG (mime='image/svg+xml')")]


def test_select_and_validate_images_reports_disallowed_license_reason():
    imageinfo_by_title = _imageinfo_map()
    matched, considered, skipped = fci.select_and_validate_images(
        [], ["File:NC licensed photo.jpg"], imageinfo_by_title
    )
    assert matched == []
    assert skipped == [("File:NC licensed photo.jpg", "license not allowed: 'CC BY-NC 4.0'")]


def test_select_and_validate_images_handles_missing_imageinfo():
    """A title Commons search returned but imageinfo lookup came back empty for."""
    matched, considered, skipped = fci.select_and_validate_images(
        ["File:Missing.jpg"], [], {"File:Missing.jpg": None}
    )
    assert matched == []
    assert skipped == [("File:Missing.jpg", "Commons returned no imageinfo for this title")]


def test_select_and_validate_images_respects_candidate_limit_even_when_later_ones_would_pass():
    """The 4th candidate (valid CC0) is never reached because the cap is 3 — no backfilling."""
    imageinfo_by_title = _imageinfo_map()
    geo_titles = [
        "File:Ein Hemed map.svg", "File:No credit photo.jpg",
        "File:NC licensed photo.jpg", "File:CC0 photo.jpg",
    ]
    matched, considered, _skipped = fci.select_and_validate_images(
        geo_titles, [], imageinfo_by_title, candidate_limit=3
    )
    assert matched == []
    assert "File:CC0 photo.jpg" not in considered


# --------------------------------------------------------------------------
# needs_images / build_image_entries
# --------------------------------------------------------------------------


def test_needs_images_true_when_key_missing():
    assert fci.needs_images({"id": "x"}) is True


def test_needs_images_true_when_list_empty():
    assert fci.needs_images({"id": "x", "images": []}) is True


def test_needs_images_false_when_images_present():
    assert fci.needs_images({"id": "x", "images": [{"url": "/images/x-1.jpg"}]}) is False


# --------------------------------------------------------------------------
# Retry classification / failure handling / summary — the crash-safety bits
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


def _result(*, matched=None, failed=None):
    return fci.PlaceResult(
        place_id="x", name_he="x", region="central",
        matched=matched or [], considered=[], skipped=[], failed_reason=failed,
    )


def test_summarize_counts_matched_skipped_failed_separately():
    matched_candidate = fci.ImageCandidate(
        title="File:A.jpg", direct_url="u", file_page_url="p", credit="c",
    )
    results = [
        _result(matched=[matched_candidate]),
        _result(),
        _result(failed="429 after 3 retries"),
    ]
    assert fci.summarize(results) == (1, 1, 1)


def test_summarize_all_matched():
    matched_candidate = fci.ImageCandidate(title="File:A.jpg", direct_url="u", file_page_url="p", credit="c")
    results = [_result(matched=[matched_candidate]), _result(matched=[matched_candidate])]
    assert fci.summarize(results) == (2, 0, 0)


def test_build_image_entries_shapes_url_credit_source():
    matched = [
        fci.ImageCandidate(
            title="File:A.jpg", direct_url="https://upload.wikimedia.org/.../A.jpg",
            file_page_url="https://commons.wikimedia.org/wiki/File:A.jpg", credit="Someone, CC BY 4.0",
        ),
        fci.ImageCandidate(
            title="File:B.jpg", direct_url="https://upload.wikimedia.org/.../B.jpg",
            file_page_url="https://commons.wikimedia.org/wiki/File:B.jpg", credit="Other, CC0 1.0",
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
