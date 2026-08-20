"""
tests/test_source_verifier.py — Unit tests for core/source_verifier.py.

Tests cover:
  - Valid official manufacturer URLs → VERIFIED
  - Blocked marketplace/distributor URLs → REJECTED
  - Subdomain of approved domain → VERIFIED
  - Malformed / no-scheme URLs → REJECTED
  - Missing / None URL → NOT_FOUND
  - Domain not in approved set → REJECTED
  - get_registered_domain edge cases
  - is_blocked_domain edge cases
"""
from __future__ import annotations

import pytest

from core.source_verifier import (
    get_registered_domain,
    is_blocked_domain,
    is_official_manufacturer_source,
    verify_source_url,
)
from schemas.asset import SourceStatus

# ── Approved domains for test fixtures ───────────────────────────────────────

APPROVED = {"muellerindustries.com", "rheem.com"}


# ══════════════════════════════════════════════════════════════════════════════
# get_registered_domain
# ══════════════════════════════════════════════════════════════════════════════

class TestGetRegisteredDomain:

    def test_standard_https_url(self):
        assert get_registered_domain("https://www.muellerindustries.com/products") == "muellerindustries.com"

    def test_strips_www(self):
        assert get_registered_domain("https://www.rheem.com/path") == "rheem.com"

    def test_http_scheme(self):
        assert get_registered_domain("http://rheem.com/page") == "rheem.com"

    def test_subdomain_preserved(self):
        assert get_registered_domain("https://cdn.muellerindustries.com/img.jpg") == "cdn.muellerindustries.com"

    def test_strips_port(self):
        assert get_registered_domain("https://rheem.com:8080/page") == "rheem.com"

    def test_no_scheme_returns_none(self):
        assert get_registered_domain("muellerindustries.com/product") is None

    def test_empty_string_returns_none(self):
        assert get_registered_domain("") is None

    def test_none_returns_none(self):
        assert get_registered_domain(None) is None  # type: ignore[arg-type]

    def test_ftp_scheme_returns_none(self):
        assert get_registered_domain("ftp://muellerindustries.com/file") is None

    def test_malformed_url(self):
        # No netloc after scheme
        result = get_registered_domain("https://")
        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# is_blocked_domain
# ══════════════════════════════════════════════════════════════════════════════

class TestIsBlockedDomain:

    def test_amazon_blocked(self):
        assert is_blocked_domain("https://www.amazon.com/dp/B001") is True

    def test_ebay_blocked(self):
        assert is_blocked_domain("https://www.ebay.com/itm/12345") is True

    def test_grainger_blocked(self):
        assert is_blocked_domain("https://www.grainger.com/product/MUELLER") is True

    def test_zoro_blocked(self):
        assert is_blocked_domain("https://www.zoro.com/product/xyz") is True

    def test_homedepot_blocked(self):
        assert is_blocked_domain("https://www.homedepot.com/p/product") is True

    def test_walmart_blocked(self):
        assert is_blocked_domain("https://www.walmart.com/ip/item/100") is True

    def test_alibaba_blocked(self):
        assert is_blocked_domain("https://www.alibaba.com/product/1234") is True

    def test_amazon_subdomain_blocked(self):
        assert is_blocked_domain("https://seller.amazon.com/page") is True

    def test_official_manufacturer_not_blocked(self):
        assert is_blocked_domain("https://www.muellerindustries.com/products") is False

    def test_rheem_not_blocked(self):
        assert is_blocked_domain("https://www.rheem.com/residential") is False

    def test_malformed_not_blocked(self):
        # No scheme → get_registered_domain returns None → not blocked
        assert is_blocked_domain("muellerindustries.com/products") is False


# ══════════════════════════════════════════════════════════════════════════════
# is_official_manufacturer_source
# ══════════════════════════════════════════════════════════════════════════════

class TestIsOfficialManufacturerSource:

    def test_exact_match_approved(self):
        assert is_official_manufacturer_source(
            "https://muellerindustries.com/product", APPROVED
        ) is True

    def test_www_subdomain_approved(self):
        assert is_official_manufacturer_source(
            "https://www.muellerindustries.com/product", APPROVED
        ) is True

    def test_cdn_subdomain_approved(self):
        assert is_official_manufacturer_source(
            "https://cdn.muellerindustries.com/img.jpg", APPROVED
        ) is True

    def test_different_subdomain_approved(self):
        assert is_official_manufacturer_source(
            "https://media.rheem.com/doc.pdf", APPROVED
        ) is True

    def test_non_approved_domain_rejected(self):
        assert is_official_manufacturer_source(
            "https://honeywell.com/product", APPROVED
        ) is False

    def test_empty_approved_set_rejected(self):
        assert is_official_manufacturer_source(
            "https://muellerindustries.com/product", set()
        ) is False

    def test_blocked_domain_not_approved(self):
        assert is_official_manufacturer_source(
            "https://amazon.com/product", APPROVED
        ) is False

    def test_no_scheme_rejected(self):
        assert is_official_manufacturer_source(
            "muellerindustries.com/product", APPROVED
        ) is False


# ══════════════════════════════════════════════════════════════════════════════
# verify_source_url
# ══════════════════════════════════════════════════════════════════════════════

class TestVerifySourceUrl:

    def test_none_returns_not_found(self):
        asset = verify_source_url(None, APPROVED)
        assert asset.status == SourceStatus.NOT_FOUND
        assert asset.url is None

    def test_empty_string_returns_not_found(self):
        asset = verify_source_url("", APPROVED)
        assert asset.status == SourceStatus.NOT_FOUND

    def test_whitespace_returns_not_found(self):
        asset = verify_source_url("   ", APPROVED)
        assert asset.status == SourceStatus.NOT_FOUND

    def test_no_scheme_rejected(self):
        asset = verify_source_url("muellerindustries.com/page", APPROVED)
        assert asset.status == SourceStatus.REJECTED
        assert "scheme" in (asset.rejection_reason or "").lower()

    def test_blocked_marketplace_rejected(self):
        asset = verify_source_url("https://www.amazon.com/dp/B001", APPROVED)
        assert asset.status == SourceStatus.REJECTED
        assert "blocked" in (asset.rejection_reason or "").lower()
        assert asset.official_domain == "amazon.com"

    def test_non_approved_domain_rejected(self):
        asset = verify_source_url("https://honeywell.com/product", APPROVED)
        assert asset.status == SourceStatus.REJECTED
        assert "approved" in (asset.rejection_reason or "").lower()

    def test_valid_official_url_verified(self):
        asset = verify_source_url(
            "https://www.muellerindustries.com/products/brass-couplings/",
            APPROVED,
        )
        assert asset.status == SourceStatus.VERIFIED
        assert asset.url == "https://www.muellerindustries.com/products/brass-couplings/"
        assert asset.official_domain == "muellerindustries.com"
        assert asset.rejection_reason is None

    def test_subdomain_of_approved_verified(self):
        asset = verify_source_url(
            "https://cdn.muellerindustries.com/catalog.pdf",
            APPROVED,
        )
        assert asset.status == SourceStatus.VERIFIED
        assert "muellerindustries.com" in (asset.official_domain or "")

    def test_blocked_before_approved_check(self):
        """Ensure blocked-domain check fires even if domain is somehow in approved set."""
        hacked_approved = {"amazon.com"}  # pathological case
        asset = verify_source_url("https://amazon.com/product", hacked_approved)
        # Blocked domains are always rejected regardless of approved set
        assert asset.status == SourceStatus.REJECTED

    def test_malformed_url_rejected(self):
        asset = verify_source_url("https://", APPROVED)
        assert asset.status == SourceStatus.REJECTED
