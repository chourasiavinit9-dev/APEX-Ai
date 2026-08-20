"""
core/digital_assets.py — Asset classification and source coverage engine.

Classifies verified URLs as product pages, datasheets, or images.
Computes source coverage score and flags low-coverage records for review.

Coverage scoring:
  1.0 = verified product page + datasheet + at least one image
  0.7 = verified product page + datasheet (no image)
  0.4 = at least one verified source (page OR datasheet)
  0.0 = no verified source found

needs_human_review = True when coverage < 0.7
"""
from __future__ import annotations

from typing import List, Optional, Set, Tuple

from schemas.asset import AssetType, DigitalAsset, ProductSources, SourceStatus
from core.source_verifier import verify_source_url

# ── File extension classifiers ────────────────────────────────────────────────

_DATASHEET_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}

_MAX_IMAGES = 3  # Keep at most 3 verified images per product


def _classify_url(url: str) -> str:
    """
    Classify a URL into asset_type based on path extension.

    Returns: "datasheet" | "image" | "product_page"
    """
    # Normalize: strip query string and fragment for extension check
    path = url.split("?")[0].split("#")[0].lower().rstrip("/")
    for ext in _DATASHEET_EXTENSIONS:
        if path.endswith(ext):
            return AssetType.DATASHEET.value
    for ext in _IMAGE_EXTENSIONS:
        if path.endswith(ext):
            return AssetType.IMAGE.value
    return AssetType.PRODUCT_PAGE.value


def _not_found_asset(asset_type: str) -> DigitalAsset:
    """Return a NOT_FOUND placeholder asset for a given type."""
    return DigitalAsset(
        asset_type=asset_type,
        url=None,
        official_domain=None,
        status=SourceStatus.NOT_FOUND,
        rejection_reason="No verified official URL found",
    )


def _calculate_coverage(
    page: DigitalAsset,
    datasheet: DigitalAsset,
    images: List[DigitalAsset],
) -> Tuple[float, bool]:
    """
    Compute source_coverage_score and needs_human_review flag.

    Scoring:
      1.0 = page(verified) + datasheet(verified) + image(verified)
      0.7 = page(verified) + datasheet(verified)
      0.4 = any single verified source
      0.0 = no verified source

    Returns:
        (score, needs_human_review)
    """
    has_page = page.status == SourceStatus.VERIFIED
    has_datasheet = datasheet.status == SourceStatus.VERIFIED
    has_image = any(i.status == SourceStatus.VERIFIED for i in images)

    if has_page and has_datasheet and has_image:
        score = 1.0
    elif has_page and has_datasheet:
        score = 0.7
    elif has_page or has_datasheet:
        score = 0.4
    else:
        score = 0.0

    return score, score < 0.7


def collect_product_assets(
    candidate_urls: List[str],
    approved_manufacturer_domains: Set[str],
    resource_url: Optional[str] = None,
) -> ProductSources:
    """
    Classify and verify candidate official URLs as product pages, datasheets, or images.

    Algorithm:
    1. Deduplicate candidate URLs.
    2. Verify each URL against approved manufacturer domains.
    3. Classify verified URLs by extension.
    4. Collect: first verified product_page, first verified datasheet,
       up to MAX_IMAGES verified images.
    5. Rejected/malformed URLs are NOT included in the output assets.
    6. Compute coverage score and review flag.

    Args:
        candidate_urls: Raw candidate URLs from pipeline enrichment fields.
        approved_manufacturer_domains: Set of approved root domains.
        resource_url: Optional local resource label to attach to verified assets.

    Returns:
        ProductSources with verified/not-found assets and coverage metadata.
    """
    # ── Deduplication ─────────────────────────────────────────────────────────
    seen: Set[str] = set()
    unique_urls: List[str] = []
    for url in candidate_urls:
        if url and url.strip() and url.strip() not in seen:
            seen.add(url.strip())
            unique_urls.append(url.strip())

    # ── Verify and classify ───────────────────────────────────────────────────
    product_page: Optional[DigitalAsset] = None
    datasheet: Optional[DigitalAsset] = None
    images: List[DigitalAsset] = []

    for url in unique_urls:
        asset = verify_source_url(url, approved_manufacturer_domains)
        if asset.status != SourceStatus.VERIFIED:
            continue  # Skip non-verified; they won't appear in output

        asset_type = _classify_url(url)
        asset = asset.model_copy(update={
            "asset_type": asset_type,
            "resource_url": resource_url,
        })

        if asset_type == AssetType.PRODUCT_PAGE.value and product_page is None:
            product_page = asset
        elif asset_type == AssetType.DATASHEET.value and datasheet is None:
            datasheet = asset
        elif asset_type == AssetType.IMAGE.value and len(images) < _MAX_IMAGES:
            images.append(asset)

    # ── Fill NOT_FOUND placeholders ───────────────────────────────────────────
    final_page = product_page or _not_found_asset(AssetType.PRODUCT_PAGE.value)
    final_datasheet = datasheet or _not_found_asset(AssetType.DATASHEET.value)

    # ── Coverage scoring ──────────────────────────────────────────────────────
    score, needs_review = _calculate_coverage(final_page, final_datasheet, images)

    return ProductSources(
        product_page=final_page,
        datasheet=final_datasheet,
        images=images,
        source_coverage_score=round(score, 2),
        needs_human_review=needs_review,
    )
