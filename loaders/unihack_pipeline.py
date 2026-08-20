"""
loaders/unihack_pipeline.py — Main UniHack enrichment pipeline.

Pipeline order (cost-optimised):
  1. Placeholder filter        [$0]
  2. Manufacturer normalise    [$0 — RapidFuzz local]
  3. UOM normalise             [$0 — Pandas lookup]
  4. Taxonomy classify         [$0.10/1K — Claude Haiku]
  5. Attribute extraction      [$0.50/1K — Claude Haiku]
  6. Web enrichment            [$1.00/1K sparse — Sonnet, only if < 3 attrs]
  7. Description building      [$0.40/1K — Claude Haiku]
  8. LOV validation            [$0]
  9. Output validation         [$0]

Total: ~$2/1K rows (vs $14 generic APEX pipeline)

Source constraint (UniHack rule):
  Web enrichment must use manufacturer sites only.
  Amazon, eBay, distributor sites are EXCLUDED.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path

import anthropic

from core.constants import CLASSIFICATION_MODEL, EXTRACTION_MODEL
from loaders.data_loader import (
    clean_brand_fields, get_lov_for_classpath,
    get_valid_values, get_all_classpaths, is_placeholder,
)
from loaders.manufacturer_normaliser import normalise_from_row, BrandMatch
from loaders.uom_normaliser import normalise_uom_dict, normalise_uom
from generators.description_builder import (
    build_descriptions, extract_series_from_desc,
)
from validators.output_validator import validate_output, ValidationReport

# Excluded domains for web enrichment (UniHack sourcing rule)
EXCLUDED_DOMAINS = frozenset({
    "amazon.com", "ebay.com", "grainger.com", "mcmaster.com",
    "fastenal.com", "hdSupply.com", "zoro.com", "globalindustrial.com",
})


def enrich_row(
    raw_row: dict,
    client: anthropic.Anthropic | None = None,
    enrich_web: bool = True,
) -> dict:
    """
    Full UniHack enrichment pipeline for a single raw catalogue row.
    Returns a Unilog-format output record with provenance.
    """
    if client is None:
        from core.llm_client import get_client
        client = get_client()   # handles OpenRouter and Anthropic automatically


    record: dict = {"_raw": raw_row.copy(), "_pipeline_steps": []}

    # Step 1 — Placeholder filter
    row = clean_brand_fields(raw_row.copy())
    record["_pipeline_steps"].append("placeholder_filter")

    # Step 2 — Manufacturer normalisation
    brand_match = normalise_from_row(row)
    record.update(_brand_fields(brand_match, row))
    record["_pipeline_steps"].append("manufacturer_normalise")

    # Step 3 — Taxonomy classification
    classpath = _classify_taxonomy(row, brand_match, client)
    record["classpath"] = classpath
    record["_pipeline_steps"].append("taxonomy_classify")

    # Step 4 — Attribute extraction (Haiku, LOV-constrained)
    attributes = _extract_attributes(row, classpath, client)
    record["attributes"] = normalise_uom_dict(attributes)
    record["_pipeline_steps"].append("attribute_extract")

    # Step 5 — Web enrichment (Sonnet, sparse records only)
    non_null = sum(1 for v in record["attributes"].values() if v)
    if enrich_web and non_null < 3:
        record["attributes"] = _web_enrich_attributes(
            row, record["attributes"], brand_match, client
        )
        record["_pipeline_steps"].append("web_enrich")

    # Step 6 — Description building (Haiku, all 5 formats)
    series = extract_series_from_desc(row.get("Part_Desc", ""))
    lov_values = _get_lov_values_for_attrs(classpath, record["attributes"])
    descriptions = build_descriptions(
        brand=record.get("brand_name", ""),
        manufacturer=record.get("manufacturer_name", ""),
        mpn=row.get("Mfg_Part_Num", ""),
        item_type=_extract_item_type(row.get("Part_Desc", "")),
        attributes=record["attributes"],
        classpath=classpath,
        series=series,
        lov_values=lov_values,
        client=client,
    )
    record.update(descriptions)
    record["series"] = series
    record["mpn"] = row.get("Mfg_Part_Num", "")
    record["sku"] = row.get("SKU", row.get("Mfg_Part_Num", ""))
    record["_pipeline_steps"].append("description_build")

    # Step 7 — Validation
    report = validate_output(record)
    record["validation"] = _validation_to_dict(report)
    record["confidence"] = report.overall_score
    record["needs_human_review"] = report.needs_human_review
    record["_pipeline_steps"].append("validate")

    return record


def _brand_fields(match: BrandMatch, row: dict) -> dict:
    """Extract brand-related fields from BrandMatch result."""
    return {
        "manufacturer_name": match.manufacturer_name,
        "manufacturer_code": match.manufacturer_code,
        "brand_name": match.brand_name,
        "brand_code": match.brand_code,
        "brand_confidence": match.confidence,
        "brand_match_type": match.match_type,
        "raw_brand": (row.get("Unilog_Brand") or row.get("E1_Brand")
                      or row.get("Part_Manuf") or ""),
    }


def _classify_taxonomy(
    row: dict, brand: BrandMatch, client: anthropic.Anthropic
) -> str:
    """Classify product into Unilog classpath using Claude Haiku."""
    dept = row.get("Dept", "")
    class_ = row.get("Class", "")
    fine = row.get("Fine", "")
    desc = row.get("Part_Desc", "")
    hint = " > ".join(p for p in [dept, class_, fine] if p and not is_placeholder(p))

    prompt = (
        f"Industrial product description: '{desc}'\n"
        f"Brand: {brand.brand_name}\n"
        f"Category hints: {hint}\n\n"
        "Return ONLY the product classpath in format: "
        "'Category > Subcategory > Product Type'. "
        "Use standard industrial categories. No explanation."
    )
    try:
        resp = client.messages.create(
            model=CLASSIFICATION_MODEL,
            max_tokens=60,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception:
        return hint or "Uncategorized"


def _extract_attributes(
    row: dict, classpath: str, client: anthropic.Anthropic
) -> dict:
    """Extract product attributes using Claude Haiku, constrained to LOV."""
    desc = row.get("Part_Desc", "")
    lov_df = get_lov_for_classpath(classpath)
    lov_context = ""
    if not lov_df.empty and "Attribute_Label" in lov_df.columns:
        attrs = lov_df["Attribute_Label"].dropna().unique().tolist()[:20]
        lov_context = "Extract these attributes if present: " + ", ".join(attrs)

    prompt = (
        f"Product: '{desc}'\nClasspath: {classpath}\n"
        f"{lov_context}\n\n"
        "Extract all product attributes. Return ONLY JSON: "
        '{"attribute_name": "value"}. '
        "Use approved UOM abbreviations (in, ft, lb, V, A, GPM, BTU). "
        "Number space unit: '24 in' not '24in'. "
        "Convert decimals to fractions: 0.5 → 1/2. "
        "Return {} if nothing can be extracted."
    )
    try:
        resp = client.messages.create(
            model=CLASSIFICATION_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "",
                     resp.content[0].text.strip())
        return json.loads(raw)
    except Exception:
        return _parse_desc_heuristic(desc)


def _web_enrich_attributes(
    row: dict,
    attrs: dict,
    brand: BrandMatch,
    client: anthropic.Anthropic,
) -> dict:
    """
    Web enrichment from manufacturer sites ONLY (UniHack sourcing rule).
    Excluded: Amazon, eBay, distributor sites.
    """
    mpn = row.get("Mfg_Part_Num", "")
    mfr = brand.manufacturer_name or ""
    query = f"{mfr} {mpn} specifications site:*.{mfr.split()[0].lower()}.com"

    try:
        resp = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=800,
            system=(
                "You are searching for product specs on MANUFACTURER sites only. "
                "NEVER use Amazon, eBay, Grainger, McMaster, or distributor sites. "
                "Only use the manufacturer's own website. "
                "Return JSON: {attribute: value} or {} if not found."
            ),
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": f"Find specs for: {query}"}],
        )
        text = " ".join(
            b.text for b in resp.content
            if hasattr(b, "type") and b.type == "text"
        )
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        new_attrs = json.loads(raw)
        for k, v in new_attrs.items():
            if k not in attrs or not attrs[k]:
                attrs[k] = v
                attrs.setdefault("_web_sources", [])
    except Exception:
        pass
    return attrs


def _get_lov_values_for_attrs(classpath: str, attributes: dict) -> dict:
    """Get valid LOV values for each attribute in the record."""
    lov_values = {}
    for attr in attributes:
        vals = get_valid_values(classpath, attr)
        if vals:
            lov_values[attr] = vals[:10]
    return lov_values


def _extract_item_type(desc: str) -> str:
    """Heuristic item type extraction from abbreviated description."""
    item_patterns = [
        (r"\bC(?:OUPL(?:ING)?|PLG)\b", "Coupling"),
        (r"\bV(?:ALV(?:E)?|LV)\b", "Valve"),
        (r"\bELB(?:OW)?\b", "Elbow"),
        (r"\bTEE\b", "Tee"),
        (r"\bNIPP(?:LE)?\b", "Nipple"),
        (r"\bDISHWASHER\b", "Dishwasher"),
        (r"\bFAUCET\b", "Faucet"),
        (r"\bPUMP\b", "Pump"),
        (r"\bFILTER\b", "Filter"),
        (r"\bR(?:EDUCER|DCR)\b", "Reducer"),
        (r"\bBUSH(?:ING)?\b", "Bushing"),
        (r"\bADAPT(?:ER|OR)?\b", "Adapter"),
        (r"\bUNION\b", "Union"),
        (r"\bCAP\b", "Cap"),
        (r"\bPLUG\b", "Plug"),
    ]
    upper = desc.upper()
    for pattern, item_type in item_patterns:
        if re.search(pattern, upper):
            return item_type
    return desc.split()[0].title() if desc else "Product"


def _parse_desc_heuristic(desc: str) -> dict:
    """
    Zero-cost fallback: extract attributes from abbreviated descriptions.
    e.g. "3/8 CPLG BRS 150#" → {size: "3/8 in", material: "Brass", pressure: "150 PSI"}
    """
    attrs: dict = {}
    # Size patterns: 3/8, 1/2, 1-1/4, etc.
    size_m = re.search(r"(\d+(?:-\d+)?/\d+|\d+(?:\.\d+)?)\s*(?:IN|\")?", desc, re.I)
    if size_m:
        attrs["Connection Size"] = f"{size_m.group(1)} in"
    # Material abbreviations
    mat_map = {"BRS": "Brass", "STL": "Steel", "SST": "Stainless Steel",
               "BLK": "Black Iron", "PVC": "PVC", "CPVC": "CPVC"}
    for abbr, material in mat_map.items():
        if abbr in desc.upper():
            attrs["Material"] = material
            break
    # Pressure rating
    press_m = re.search(r"(\d+)\s*#", desc)
    if press_m:
        attrs["Pressure Rating"] = f"{press_m.group(1)} PSI"
    # Voltage
    volt_m = re.search(r"(\d+)\s*V\b", desc, re.I)
    if volt_m:
        attrs["Voltage"] = f"{volt_m.group(1)} V"
    # Amperage
    amp_m = re.search(r"(\d+)\s*A\b", desc, re.I)
    if amp_m:
        attrs["Amperage"] = f"{amp_m.group(1)} A"
    return attrs


def _validation_to_dict(report: ValidationReport) -> dict:
    """Convert ValidationReport to serialisable dict."""
    return {
        "overall_score": report.overall_score,
        "needs_human_review": report.needs_human_review,
        "summary": report.summary,
        "field_results": [
            {
                "field": r.field_name,
                "passed": r.passed,
                "issues": r.issues,
                "warnings": r.warnings,
            }
            for r in report.field_results
        ],
    }
