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
    """Safely extract the normalized root domain from a URL."""
    if not url or not isinstance(url, str) or not url.strip().startswith(("http://", "https://")):
        return None
    try:
        netloc = urlparse(url.strip()).netloc.lower().split(":")[0]
        netloc = netloc[4:] if netloc.startswith("www.") else netloc
        return netloc if netloc else None
    except Exception:
        return None


def is_blocked_domain(url: str) -> bool:
    """Return True when the URL belongs to a marketplace or distributor domain."""
    domain = get_registered_domain(url)
    if domain is None:
        return False
    return any(domain == b or domain.endswith("." + b) for b in BLOCKED_DOMAINS)


def is_official_manufacturer_source(url: str, approved_manufacturer_domains: Set[str]) -> bool:
    """Return True only when URL belongs to an approved manufacturer domain."""
    if not approved_manufacturer_domains or (domain := get_registered_domain(url)) is None:
        return False
    return any(domain == app.strip().lower() or domain.endswith("." + app.strip().lower())
               for app in approved_manufacturer_domains)


def _rejection_reason_for_domain(url: str, domain: str, approved_manufacturer_domains: Set[str]) -> Optional[str]:
    """Return rejection reason string if domain is invalid or unapproved."""
    if is_blocked_domain(url):
        return f"Domain '{domain}' is a blocked marketplace/distributor"
    if not is_official_manufacturer_source(url, approved_manufacturer_domains):
        approved_list = ", ".join(sorted(approved_manufacturer_domains)) or "none"
        return f"Domain '{domain}' is not an approved manufacturer domain. Approved: [{approved_list}]"
    return None


def verify_source_url(url: Optional[str], approved_manufacturer_domains: Set[str]) -> DigitalAsset:
    """Verify a single candidate source URL without network calls."""
    if not url or not (clean := url.strip()):
        return DigitalAsset(asset_type="unknown", url=None, official_domain=None,
                            status=SourceStatus.NOT_FOUND, rejection_reason="No URL provided")
    if not clean.startswith(("http://", "https://")):
        return DigitalAsset(asset_type="unknown", url=clean, official_domain=None,
                            status=SourceStatus.REJECTED, rejection_reason="URL missing http/https scheme")
    if (domain := get_registered_domain(clean)) is None:
        return DigitalAsset(asset_type="unknown", url=clean, official_domain=None,
                            status=SourceStatus.REJECTED, rejection_reason="Malformed URL — could not extract domain")
    if reason := _rejection_reason_for_domain(clean, domain, approved_manufacturer_domains):
        return DigitalAsset(asset_type="unknown", url=clean, official_domain=domain,
                            status=SourceStatus.REJECTED, rejection_reason=reason)
    return DigitalAsset(asset_type="unknown", url=clean, official_domain=domain, status=SourceStatus.VERIFIED)
