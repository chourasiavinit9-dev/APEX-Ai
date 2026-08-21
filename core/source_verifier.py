"""
core/source_verifier.py — URL verification engine for official manufacturer sources.

Rules enforced:
  - No marketplace or distributor URLs can be marked verified.
  - No URLs are invented or fabricated.
  - Subdomains of approved manufacturer domains are accepted.
  - Blocked-domain check runs before official-domain matching.
  - All non-verified assets carry a clear rejection reason.

Never makes network calls — pure URL parsing only.
"""

from __future__ import annotations

from typing import Optional, Set
from urllib.parse import urlparse

from schemas.asset import DigitalAsset, SourceStatus

# ── Blocked marketplace and distributor domains ───────────────────────────────

BLOCKED_DOMAINS: Set[str] = {
    "amazon.com",
    "amazon.ca",
    "amazon.co.uk",
    "amazon.de",
    "amazon.in",
    "ebay.com",
    "ebay.co.uk",
    "grainger.com",
    "zoro.com",
    "homedepot.com",
    "lowes.com",
    "walmart.com",
    "alibaba.com",
    "aliexpress.com",
    "fastenal.com",
    "mcmaster.com",
    "mcmaster-carr.com",
    "globalindustrial.com",
    "uline.com",
    "webstaurantstore.com",
    "plumbersstock.com",
    "supplyhouse.com",
    "build.com",
    "ferguson.com",
    "hdpply.com",
    "bing.com",
    "google.com",
    "yahoo.com",
    "yelp.com",
    "reddit.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "pinterest.com",
    "shopify.com",
    "etsy.com",
    "wayfair.com",
    "target.com",
    "bestbuy.com",
    "chewy.com",
    "overstock.com",
    "rakuten.com",
}

# ── URL helpers ───────────────────────────────────────────────────────────────


def get_registered_domain(url: str) -> Optional[str]:
    """
    Safely extract the normalized root domain from a URL.

    Returns None for malformed, empty, or non-HTTP URLs.

    Examples:
        "https://www.muellerindustries.com/products" -> "muellerindustries.com"
        "https://cdn.honeywell.com/image.jpg"        -> "honeywell.com"
        "not-a-url"                                  -> None
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return None
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if not netloc:
            return None
        # Strip port if present
        netloc = netloc.split(":")[0]
        # Strip leading "www."
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc if netloc else None
    except Exception:
        return None


def is_blocked_domain(url: str) -> bool:
    """
    Return True when the URL belongs to a marketplace or distributor domain.

    Checks exact match and parent-domain match
    (e.g. "seller.amazon.com" → blocked because "amazon.com" is blocked).
    """
    domain = get_registered_domain(url)
    if domain is None:
        return False
    for blocked in BLOCKED_DOMAINS:
        if domain == blocked or domain.endswith("." + blocked):
            return True
    return False


def is_official_manufacturer_source(
    url: str,
    approved_manufacturer_domains: Set[str],
) -> bool:
    """
    Return True only when URL belongs to an approved manufacturer domain
    or one of its official subdomains.

    Args:
        url: The candidate URL to evaluate.
        approved_manufacturer_domains: Set of approved root domains
            e.g. {"muellerindustries.com", "muellerwaterprod.com"}

    Returns:
        True if the URL's domain is approved; False otherwise.
    """
    if not approved_manufacturer_domains:
        return False
    domain = get_registered_domain(url)
    if domain is None:
        return False
    for approved in approved_manufacturer_domains:
        approved = approved.strip().lower()
        if domain == approved or domain.endswith("." + approved):
            return True
    return False


def verify_source_url(
    url: Optional[str],
    approved_manufacturer_domains: Set[str],
) -> DigitalAsset:
    """
    Verify a single candidate source URL.

    Verification steps:
    1. If url is None/empty → NOT_FOUND
    2. If URL is malformed (no http/https scheme) → REJECTED
    3. If domain is on the blocked list → REJECTED (with reason)
    4. If domain is not on the approved manufacturer list → REJECTED
    5. Otherwise → VERIFIED

    Never makes network calls.

    Args:
        url: The URL to verify (may be None).
        approved_manufacturer_domains: Set of approved official domains.

    Returns:
        DigitalAsset with status, official_domain, and rejection_reason set.
    """
    # ── Step 1: Missing URL ────────────────────────────────────────────────────
    if not url or not url.strip():
        return DigitalAsset(
            asset_type="unknown",
            url=None,
            official_domain=None,
            status=SourceStatus.NOT_FOUND,
            rejection_reason="No URL provided",
        )

    url = url.strip()

    # ── Step 2: Scheme validation ──────────────────────────────────────────────
    if not url.startswith(("http://", "https://")):
        return DigitalAsset(
            asset_type="unknown",
            url=url,
            official_domain=None,
            status=SourceStatus.REJECTED,
            rejection_reason="URL missing http/https scheme",
        )

    domain = get_registered_domain(url)
    if domain is None:
        return DigitalAsset(
            asset_type="unknown",
            url=url,
            official_domain=None,
            status=SourceStatus.REJECTED,
            rejection_reason="Malformed URL — could not extract domain",
        )

    # ── Step 3: Blocked domain check (runs BEFORE approved check) ─────────────
    if is_blocked_domain(url):
        return DigitalAsset(
            asset_type="unknown",
            url=url,
            official_domain=domain,
            status=SourceStatus.REJECTED,
            rejection_reason=f"Domain '{domain}' is a blocked marketplace/distributor",
        )

    # ── Step 4: Official domain check ─────────────────────────────────────────
    if not is_official_manufacturer_source(url, approved_manufacturer_domains):
        approved_list = ", ".join(sorted(approved_manufacturer_domains)) or "none"
        return DigitalAsset(
            asset_type="unknown",
            url=url,
            official_domain=domain,
            status=SourceStatus.REJECTED,
            rejection_reason=(
                f"Domain '{domain}' is not an approved manufacturer domain. " f"Approved: [{approved_list}]"
            ),
        )

    # ── Step 5: Verified ───────────────────────────────────────────────────────
    return DigitalAsset(
        asset_type="unknown",  # Classified in digital_assets.py
        url=url,
        official_domain=domain,
        status=SourceStatus.VERIFIED,
    )
