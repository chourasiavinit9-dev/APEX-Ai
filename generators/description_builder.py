"""
generators/description_builder.py — Build all 5 Unilog description formats.

Formats (from guide worked example):
  1. Invoice Desc    — ≤40 chars, ALL CAPS, abbreviations
  2. Mobile Desc     — 60–80 chars, "Manufacturer Brand, Item Type, Series, MPN"
  3. Short Desc      — Brand + Series + MPN + Item Type + key attrs (~120 chars)
  4. Long Desc       — Full attribute sentence, comma-separated
  5. Marketing Copy  — Narrative, benefit-led (no hard limit)

All generation uses Claude Haiku (cheap).
All values must come from LOV — no invented attributes.
All units must use approved UOM abbreviations.
All model names from core/constants.py.
"""
from __future__ import annotations
import json
import os
import re

import anthropic

from core.constants import CLASSIFICATION_MODEL
from loaders.uom_normaliser import normalise_uom


DESCRIPTION_SYSTEM = """\
You are a Unilog product content writer. Generate product descriptions
following EXACT Unilog content standards.

CRITICAL RULES:
1. ALL attribute values must come from the provided LOV list. Never invent values.
2. Units must use approved abbreviations (in, ft, lb, V, A, W, GPM, BTU, dBA).
3. Always put a space between number and unit: "24 in" not "24in".
4. Decimal inches must become fractions: 0.5→1/2, 0.25→1/4, 50.25→50-1/4.
5. Brand must include ® or ™ exactly as provided.
6. Do NOT use marketplaces or distributor sites as sources.

Return ONLY valid JSON, no markdown fences.
"""

DESCRIPTION_USER = """\
Generate 5 description formats for this product:

Brand: {brand}
Manufacturer: {manufacturer}
MPN: {mpn}
Item Type: {item_type}
Series: {series}
Classpath: {classpath}
Attributes: {attributes}
LOV-valid values only: {lov_values}

Return JSON:
{{
  "invoice_desc": "≤40 CHARS ALL CAPS ABBREVIATED",
  "mobile_desc": "60-80 chars: Manufacturer Brand, Item Type, Series, MPN",
  "short_desc": "Brand Series MPN Item Type With key attributes ~120 chars",
  "long_desc": "Brand Item Type With Feature, Series, Attr1, Attr2 ... Material",
  "marketing_copy": "Benefit-led narrative paragraph"
}}
"""


def build_descriptions(
    brand: str,
    manufacturer: str,
    mpn: str,
    item_type: str,
    attributes: dict,
    classpath: str = "",
    series: str = "",
    lov_values: dict | None = None,
    client: anthropic.Anthropic | None = None,
) -> dict:
    """
    Generate all 5 description formats using Claude Haiku.
    Returns dict with all format keys.
    """
    if client is None:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    # Normalise attribute units before passing to LLM
    norm_attrs = {k: normalise_uom(str(v)) for k, v in attributes.items() if v}

    user_msg = DESCRIPTION_USER.format(
        brand=brand,
        manufacturer=manufacturer,
        mpn=mpn,
        item_type=item_type,
        series=series or "",
        classpath=classpath,
        attributes=json.dumps(norm_attrs, indent=2),
        lov_values=json.dumps(lov_values or {}, indent=2),
    )

    try:
        response = client.messages.create(
            model=CLASSIFICATION_MODEL,
            max_tokens=1000,
            system=DESCRIPTION_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
        result = json.loads(raw)
    except Exception as e:
        result = _fallback_descriptions(brand, mpn, item_type, norm_attrs)
        result["_generation_error"] = str(e)

    # Post-process: enforce character limits and UOM normalisation
    return _enforce_limits(result)


def _enforce_limits(result: dict) -> dict:
    """Enforce character limits and UOM normalisation on all formats."""
    if "invoice_desc" in result:
        text = result["invoice_desc"].upper()[:40]
        result["invoice_desc"] = text
        result["invoice_desc_char_count"] = len(text)
        result["invoice_desc_valid"] = len(text) <= 40

    if "mobile_desc" in result:
        text = result["mobile_desc"]
        result["mobile_desc_char_count"] = len(text)
        result["mobile_desc_valid"] = 60 <= len(text) <= 80
        if len(text) > 80:
            result["mobile_desc"] = text[:77] + "..."
        
    for field in ("short_desc", "long_desc", "marketing_copy"):
        if field in result and isinstance(result[field], str):
            result[field] = normalise_uom(result[field])

    return result


def _fallback_descriptions(brand: str, mpn: str,
                            item_type: str, attrs: dict) -> dict:
    """Rule-based fallback when LLM call fails."""
    attr_str = ", ".join(f"{k}: {v}" for k, v in list(attrs.items())[:5])
    short = f"{brand} {mpn} {item_type}"[:120]
    invoice = f"{item_type.upper()[:20]} {mpn[:15]}"[:40]
    mobile = f"{brand}, {item_type}, {mpn}"
    mobile = mobile[:80].ljust(60) if len(mobile) < 60 else mobile[:80]
    long_d = f"{brand} {item_type}, {attr_str}"

    return {
        "invoice_desc": invoice,
        "mobile_desc": mobile,
        "short_desc": short,
        "long_desc": long_d,
        "marketing_copy": short,
        "_fallback": True,
    }


def extract_series_from_desc(part_desc: str) -> str:
    """
    Try to identify product series from abbreviated description.
    e.g. "PDSH4816AF Professional Series Dishwasher" → "Professional Series"
    """
    series_patterns = [
        r"(\w+\s+Series)",
        r"(Pro(?:fessional)?\s+\w+)",
        r"(Premium\s+\w+)",
        r"(Commercial\s+\w+)",
        r"(Industrial\s+\w+)",
    ]
    for pattern in series_patterns:
        match = re.search(pattern, part_desc, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""
