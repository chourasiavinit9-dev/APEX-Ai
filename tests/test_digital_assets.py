"""
tests/test_digital_assets.py — Unit tests for core/digital_assets.py.

Tests cover:
  - Product page, datasheet, and image classification
  - Maximum 3-image cap enforcement
  - Deduplication of identical URLs
  - Coverage scoring: 1.0, 0.7, 0.4, 0.0
  - needs_human_review flag behavior
  - Blocked URLs not included in output
  - Empty / no candidate URL handling
"""
from __future__ import annotations

import pytest

from core.digital_assets import collect_product_assets, _classify_url, _calculate_coverage
from schemas.asset import AssetType, DigitalAsset, SourceStatus

APPROVED = {"muellerindustries.com", "rheem.com"}

# ── Helper URLs ───────────────────────────────────────────────────────────────

OFFICIAL_PAGE = "https://www.muellerindustries.com/products/coupling/CPLG-38-BR"
OFFICIAL_PDF  = "https://www.muellerindustries.com/datasheets/CPLG-38-BR.pdf"
OFFICIAL_IMG1 = "https://www.muellerindustries.com/images/CPLG-38-BR.jpg"
OFFICIAL_IMG2 = "https://www.muellerindustries.com/images/CPLG-38-BR-2.png"
OFFICIAL_IMG3 = "https://www.muellerindustries.com/images/CPLG-38-BR-3.webp"
OFFICIAL_IMG4 = "https://www.muellerindustries.com/images/CPLG-38-BR-4.jpeg"

BLOCKED_URL  = "https://www.amazon.com/dp/CPLG-38-BR"
WRONG_DOMAIN = "https://honeywell.com/products/coupling"


# ══════════════════════════════════════════════════════════════════════════════
# _classify_url
# ══════════════════════════════════════════════════════════════════════════════

class TestClassifyUrl:

    def test_pdf_is_datasheet(self):
        assert _classify_url("https://example.com/doc.pdf") == AssetType.DATASHEET.value

    def test_docx_is_datasheet(self):
        assert _classify_url("https://example.com/manual.docx") == AssetType.DATASHEET.value

    def test_jpg_is_image(self):
        assert _classify_url("https://example.com/img.jpg") == AssetType.IMAGE.value

    def test_png_is_image(self):
        assert _classify_url("https://example.com/img.png") == AssetType.IMAGE.value

    def test_webp_is_image(self):
        assert _classify_url("https://example.com/img.webp") == AssetType.IMAGE.value

    def test_jpeg_is_image(self):
        assert _classify_url("https://example.com/img.jpeg") == AssetType.IMAGE.value

    def test_html_page_is_product_page(self):
        assert _classify_url("https://example.com/product/123") == AssetType.PRODUCT_PAGE.value

    def test_query_string_ignored_for_extension(self):
        # URL has .pdf in path but query string after it
        assert _classify_url("https://example.com/doc.pdf?v=2") == AssetType.DATASHEET.value

    def test_fragment_ignored(self):
        assert _classify_url("https://example.com/img.jpg#gallery") == AssetType.IMAGE.value

    def test_uppercase_extension_normalized(self):
        assert _classify_url("https://example.com/PHOTO.JPG") == AssetType.IMAGE.value


# ══════════════════════════════════════════════════════════════════════════════
# _calculate_coverage
# ══════════════════════════════════════════════════════════════════════════════

def _make_asset(status: SourceStatus, asset_type: str = "product_page") -> DigitalAsset:
    return DigitalAsset(asset_type=asset_type, status=status, url="https://example.com")

def _verified(asset_type: str = "product_page") -> DigitalAsset:
    return _make_asset(SourceStatus.VERIFIED, asset_type)

def _not_found(asset_type: str = "product_page") -> DigitalAsset:
    return _make_asset(SourceStatus.NOT_FOUND, asset_type)


class TestCalculateCoverage:

    def test_all_three_gives_1_0(self):
        score, review = _calculate_coverage(
            _verified("product_page"),
            _verified("datasheet"),
            [_verified("image")],
        )
        assert score == 1.0
        assert review is False

    def test_page_and_datasheet_gives_0_7(self):
        score, review = _calculate_coverage(
            _verified("product_page"),
            _verified("datasheet"),
            [],
        )
        assert score == 0.7
        assert review is False

    def test_page_only_gives_0_4(self):
        score, review = _calculate_coverage(
            _verified("product_page"),
            _not_found("datasheet"),
            [],
        )
        assert score == 0.4
        assert review is True

    def test_datasheet_only_gives_0_4(self):
        score, review = _calculate_coverage(
            _not_found("product_page"),
            _verified("datasheet"),
            [],
        )
        assert score == 0.4
        assert review is True

    def test_nothing_gives_0_0(self):
        score, review = _calculate_coverage(
            _not_found("product_page"),
            _not_found("datasheet"),
            [],
        )
        assert score == 0.0
        assert review is True

    def test_needs_review_below_0_7(self):
        score, review = _calculate_coverage(
            _verified("product_page"),
            _not_found("datasheet"),
            [],
        )
        assert review is True  # 0.4 < 0.7

    def test_no_review_at_0_7(self):
        score, review = _calculate_coverage(
            _verified("product_page"),
            _verified("datasheet"),
            [],
        )
        assert review is False  # exactly 0.7


# ══════════════════════════════════════════════════════════════════════════════
# collect_product_assets
# ══════════════════════════════════════════════════════════════════════════════

class TestCollectProductAssets:

    def test_empty_candidates_returns_not_found(self):
        result = collect_product_assets([], APPROVED)
        assert result.product_page.status == SourceStatus.NOT_FOUND
        assert result.datasheet.status == SourceStatus.NOT_FOUND
        assert result.images == []
        assert result.source_coverage_score == 0.0
        assert result.needs_human_review is True

    def test_blocked_url_excluded(self):
        result = collect_product_assets([BLOCKED_URL], APPROVED)
        assert result.product_page.status == SourceStatus.NOT_FOUND
        assert result.source_coverage_score == 0.0

    def test_wrong_domain_excluded(self):
        result = collect_product_assets([WRONG_DOMAIN], APPROVED)
        assert result.product_page.status == SourceStatus.NOT_FOUND

    def test_verified_page_detected(self):
        result = collect_product_assets([OFFICIAL_PAGE], APPROVED)
        assert result.product_page.status == SourceStatus.VERIFIED
        assert result.product_page.url == OFFICIAL_PAGE
        assert result.product_page.asset_type == AssetType.PRODUCT_PAGE.value

    def test_verified_datasheet_detected(self):
        result = collect_product_assets([OFFICIAL_PDF], APPROVED)
        assert result.datasheet.status == SourceStatus.VERIFIED
        assert result.datasheet.url == OFFICIAL_PDF
        assert result.datasheet.asset_type == AssetType.DATASHEET.value

    def test_verified_image_detected(self):
        result = collect_product_assets([OFFICIAL_IMG1], APPROVED)
        assert len(result.images) == 1
        assert result.images[0].status == SourceStatus.VERIFIED
        assert result.images[0].asset_type == AssetType.IMAGE.value

    def test_max_three_images_enforced(self):
        """Fourth image should be dropped."""
        result = collect_product_assets(
            [OFFICIAL_IMG1, OFFICIAL_IMG2, OFFICIAL_IMG3, OFFICIAL_IMG4],
            APPROVED,
        )
        assert len(result.images) == 3

    def test_full_set_coverage_1_0(self):
        result = collect_product_assets(
            [OFFICIAL_PAGE, OFFICIAL_PDF, OFFICIAL_IMG1],
            APPROVED,
        )
        assert result.source_coverage_score == 1.0
        assert result.needs_human_review is False

    def test_page_datasheet_coverage_0_7(self):
        result = collect_product_assets([OFFICIAL_PAGE, OFFICIAL_PDF], APPROVED)
        assert result.source_coverage_score == 0.7
        assert result.needs_human_review is False

    def test_page_only_coverage_0_4(self):
        result = collect_product_assets([OFFICIAL_PAGE], APPROVED)
        assert result.source_coverage_score == 0.4
        assert result.needs_human_review is True

    def test_deduplication(self):
        """Same URL twice should produce only one asset."""
        result = collect_product_assets([OFFICIAL_IMG1, OFFICIAL_IMG1], APPROVED)
        assert len(result.images) == 1

    def test_mixed_valid_and_blocked(self):
        """Blocked URLs are silently skipped; valid ones still collected."""
        result = collect_product_assets(
            [BLOCKED_URL, OFFICIAL_PAGE, WRONG_DOMAIN, OFFICIAL_PDF],
            APPROVED,
        )
        assert result.product_page.status == SourceStatus.VERIFIED
        assert result.datasheet.status == SourceStatus.VERIFIED
        assert result.source_coverage_score == 0.7

    def test_resource_url_attached(self):
        result = collect_product_assets(
            [OFFICIAL_PAGE],
            APPROVED,
            resource_url="Unicat_Manufacturer_List.xlsx → Row 42",
        )
        assert result.product_page.resource_url == "Unicat_Manufacturer_List.xlsx → Row 42"

    def test_to_export_dict_structure(self):
        result = collect_product_assets([OFFICIAL_PAGE, OFFICIAL_PDF], APPROVED)
        export = result.to_export_dict()
        assert "product_page" in export
        assert "datasheet" in export
        assert "images" in export
        assert export["product_page"]["status"] == "verified"
        assert export["source_coverage_score"] == 0.7
        assert export["needs_human_review"] is False
