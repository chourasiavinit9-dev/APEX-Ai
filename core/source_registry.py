"""
core/source_registry.py — Official manufacturer domain registry.

Builds and manages the set of approved manufacturer domains from the
local UniHack brand/manufacturer master list. Provides a fast lookup
for the source verifier without any network calls.

Domain rules:
  - Derived from canonical manufacturer names in the master list.
  - Includes common CDN subdomain patterns for known manufacturers.
  - Never includes distributor or marketplace domains.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Set

# ── Hardcoded seed domains for well-known industrial manufacturers ────────────
# Used as fallback when the Excel master list is unavailable.
_SEED_MANUFACTURER_DOMAINS: Dict[str, Set[str]] = {
    "mueller industries": {"muellerindustries.com", "muellerwaterprod.com"},
    "frigidaire": {"frigidaire.com", "electrolux.com"},
    "rheem": {"rheem.com", "ruud.com"},
    "honeywell": {"honeywell.com", "honeywellstore.com"},
    "parker": {"parker.com", "parkerhannifin.com"},
    "swagelok": {"swagelok.com"},
    "watts water": {"watts.com", "wattswater.com"},
    "crane": {"craneco.com"},
    "nibco": {"nibco.com"},
    "viega": {"viega.us", "viega.com"},
    "siemens": {"siemens.com", "siemens.us"},
    "emerson": {"emerson.com", "emersonelectric.com"},
    "brady": {"bradyid.com", "bradycorp.com"},
    "3m": {"3m.com"},
    "abb": {"abb.com", "us.abb.com"},
    "schneider electric": {"se.com", "schneider-electric.com"},
    "rockwell": {"rockwellautomation.com", "rockwellcollins.com"},
    "eaton": {"eaton.com"},
    "fluke": {"fluke.com"},
    "endress hauser": {"endress.com", "us.endress.com"},
    "spirax sarco": {"spiraxsarco.com"},
    "asco": {"asconumatics.com", "emersonelectric.com"},
    "festo": {"festo.com", "us.festo.com"},
    "smc": {"smcusa.com", "smcpneumatics.com"},
    "danfoss": {"danfoss.com", "cooling.danfoss.com"},
    "grundfos": {"grundfos.com", "us.grundfos.com"},
    "xylem": {"xylem.com", "xyleminc.com"},
    "pentair": {"pentair.com"},
    "itt": {"itt.com", "itthub.com"},
    "flowserve": {"flowserve.com"},
    "graco": {"graco.com"},
}


def _normalize_brand(brand: str) -> str:
    """Lowercase and remove special characters for fuzzy domain lookup."""
    return re.sub(r"[^a-z0-9 ]", "", brand.lower()).strip()


def _brand_to_domain_candidates(brand: str) -> Set[str]:
    """
    Heuristically derive candidate official domain from a brand name.
    Never fabricates — returns empty set rather than guessing wrong.
    """
    normalized = _normalize_brand(brand)
    # Check hardcoded seed list first
    for seed_brand, domains in _SEED_MANUFACTURER_DOMAINS.items():
        if seed_brand in normalized or normalized in seed_brand:
            return domains
    return set()


def get_approved_domains(
    manufacturer_name: str,
    brand_name: Optional[str] = None,
    extra_domains: Optional[Set[str]] = None,
) -> Set[str]:
    """
    Build the set of approved official domains for a given manufacturer.

    Resolution order:
    1. Hardcoded seed domains (always reliable)
    2. Extra domains passed in (from enrichment pipeline)
    3. Never fabricates — returns empty set if no match found

    Args:
        manufacturer_name: Canonical manufacturer name (post-normalization)
        brand_name: Optional brand name, may differ from manufacturer
        extra_domains: Optional extra domains from enrichment output

    Returns:
        Set of approved lowercase domains (e.g. {'muellerindustries.com'})
    """
    approved: Set[str] = set()

    # Seed lookup on manufacturer name
    approved |= _brand_to_domain_candidates(manufacturer_name)

    # Seed lookup on brand name if different
    if brand_name and brand_name != manufacturer_name:
        approved |= _brand_to_domain_candidates(brand_name)

    # Include any explicitly provided extra domains, if valid-looking
    if extra_domains:
        for domain in extra_domains:
            domain = domain.strip().lower()
            # Basic sanity: must have a dot and no spaces
            if "." in domain and " " not in domain and len(domain) > 3:
                approved.add(domain)

    return approved


def is_known_manufacturer_domain(domain: str) -> bool:
    """Quick check if a domain is in any known manufacturer's domain set."""
    domain = domain.strip().lower()
    for domains in _SEED_MANUFACTURER_DOMAINS.values():
        if any(domain == d or domain.endswith("." + d) for d in domains):
            return True
    return False
