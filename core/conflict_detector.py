"""
core/conflict_detector.py — Detect conflicting data in enriched records.

Checks:
  1. Brand field mismatches (E1_Brand vs Unilog_Brand vs Part_Manuf)
  2. Invalid unit/value combinations (electrical units on plumbing product)
  3. Impossible attribute ranges
  4. Description inconsistencies
"""
from __future__ import annotations

import re
from typing import List, Dict


def detect_conflicts(raw_row: dict, enriched: dict) -> List[dict]:
    """
    Run all conflict checks on a record.
    Returns list of conflict dicts: {type, severity, message, fields}
    """
    conflicts: List[dict] = []
    conflicts.extend(_check_brand_mismatch(raw_row, enriched))
    conflicts.extend(_check_unit_mismatch(enriched))
    conflicts.extend(_check_description_consistency(enriched))
    conflicts.extend(_check_attribute_ranges(enriched))
    return conflicts


def _check_brand_mismatch(raw: dict, enriched: dict) -> List[dict]:
    """Flag when raw brand fields disagree with each other."""
    conflicts = []
    brand_fields = {}
    for field in ("E1_Brand", "Unilog_Brand", "DIB_Brand", "Part_Manuf"):
        val = raw.get(field, "")
        if val and not _is_placeholder(val):
            brand_fields[field] = val

    if len(brand_fields) >= 2:
        values = list(brand_fields.values())
        # Normalize for comparison
        normalized = [v.lower().strip().rstrip("®™").strip() for v in values]
        unique = set(normalized)
        if len(unique) > 1:
            conflicts.append({
                "type": "brand_mismatch",
                "severity": "warning",
                "message": f"Brand fields disagree: {brand_fields}",
                "fields": list(brand_fields.keys()),
            })

    # Check resolved brand vs raw
    resolved = enriched.get("brand_name", "")
    if resolved and enriched.get("brand_match_type") == "fallback":
        conflicts.append({
            "type": "brand_unresolved",
            "severity": "info",
            "message": f"Brand '{enriched.get('raw_brand', '')}' not in master list — using as-is",
            "fields": ["brand_name"],
        })

    return conflicts


def _check_unit_mismatch(enriched: dict) -> List[dict]:
    """Flag electrical units on plumbing products or vice versa."""
    conflicts = []
    classpath = (enriched.get("classpath", "") or "").lower()
    attrs = enriched.get("attributes", {})

    is_plumbing = any(k in classpath for k in ("plumbing", "fitting", "pipe", "valve"))
    is_electrical = any(k in classpath for k in ("electrical", "wiring", "circuit", "motor"))

    for attr_name, attr_val in attrs.items():
        if not isinstance(attr_val, str):
            continue
        val_lower = attr_val.lower()

        # Electrical units on plumbing
        if is_plumbing and re.search(r"\b\d+\s*(v|volt|amp|a|watt|w)\b", val_lower):
            if attr_name.lower() not in ("voltage", "amperage", "wattage"):
                conflicts.append({
                    "type": "unit_mismatch",
                    "severity": "warning",
                    "message": f"Electrical unit in plumbing product: {attr_name}={attr_val}",
                    "fields": [attr_name],
                })

        # Plumbing units on electrical
        if is_electrical and re.search(r"\b\d+\s*(psi|gpm|gallon)\b", val_lower):
            conflicts.append({
                "type": "unit_mismatch",
                "severity": "warning",
                "message": f"Plumbing unit in electrical product: {attr_name}={attr_val}",
                "fields": [attr_name],
            })

    return conflicts


def _check_description_consistency(enriched: dict) -> List[dict]:
    """Check that descriptions are consistent with attributes."""
    conflicts = []
    brand = enriched.get("brand_name", "")
    invoice = enriched.get("invoice_desc", "")
    mobile = enriched.get("mobile_desc", "")

    # Brand should appear in mobile desc
    if brand and mobile and brand.lower().rstrip("®™").strip() not in mobile.lower():
        conflicts.append({
            "type": "desc_brand_missing",
            "severity": "info",
            "message": f"Brand '{brand}' not found in mobile description",
            "fields": ["mobile_desc"],
        })

    return conflicts


def _check_attribute_ranges(enriched: dict) -> List[dict]:
    """Flag impossible attribute values."""
    conflicts = []
    attrs = enriched.get("attributes", {})

    for attr_name, attr_val in attrs.items():
        if not isinstance(attr_val, str):
            continue
        # Extract numeric value
        num_match = re.search(r"(\d+(?:\.\d+)?)", str(attr_val))
        if not num_match:
            continue
        num = float(num_match.group(1))

        # Pressure > 10000 PSI is suspicious
        if "pressure" in attr_name.lower() and num > 10000:
            conflicts.append({
                "type": "range_suspect",
                "severity": "warning",
                "message": f"Unusually high pressure: {attr_name}={attr_val}",
                "fields": [attr_name],
            })

        # Temperature > 2000°F is suspicious
        if "temp" in attr_name.lower() and num > 2000:
            conflicts.append({
                "type": "range_suspect",
                "severity": "warning",
                "message": f"Unusually high temperature: {attr_name}={attr_val}",
                "fields": [attr_name],
            })

    return conflicts


def _is_placeholder(val: str) -> bool:
    """Quick placeholder check."""
    if not val:
        return True
    lower = val.strip().lower()
    return lower.startswith("--") or lower in ("", "n/a", "unknown", "none")
