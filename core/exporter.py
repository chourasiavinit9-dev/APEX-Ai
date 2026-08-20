"""
core/exporter.py — Output format converters.

Fix 3 (HIGH): JSON-LD now includes _apex.relationships from knowledge graph.
All constants from core/constants.py.
"""
from __future__ import annotations
import csv
import json
import uuid
from io import StringIO
from pathlib import Path

from .constants import SCHEMA_ORG_CONTEXT, JSONLD_PRODUCT_TYPE


def to_jsonld(product: dict) -> dict:
    """Convert APEX product record to schema.org/Product JSON-LD."""
    props = _build_additional_properties(product)
    result = _assemble_jsonld(product, props)
    result["_apex"]["relationships"] = _build_kg_relationships(product)
    return result


def _build_additional_properties(product: dict) -> list:
    """Build schema.org additionalProperty list from attributes."""
    prov = product.get("provenance", {})
    props = []
    for key, val in product.get("attributes", {}).items():
        if val is None:
            continue
        props.append({
            "@type": "PropertyValue",
            "name": key,
            "value": val,
            "valueReference": {
                "@type": "StructuredValue",
                "source": prov.get("field_sources", {}).get(key, "extracted"),
                "confidence": round(
                    prov.get("field_confidences", {}).get(key, prov.get("confidence", 1.0)), 3
                ),
                "evidence": prov.get("evidence", {}).get(key),
            },
        })
    return props


def _assemble_jsonld(product: dict, props: list) -> dict:
    """Assemble the base JSON-LD object."""
    prov = product.get("provenance", {})
    result = {
        "@context": SCHEMA_ORG_CONTEXT,
        "@type": JSONLD_PRODUCT_TYPE,
        "identifier": product.get("product_id") or str(uuid.uuid4()),
        "name": product.get("name") or product.get("product_type", "").title(),
        "sku": product.get("part_number"),
        "category": product.get("product_type", "").title(),
        "additionalProperty": props,
        "_apex": {
            "source_document": prov.get("source_document"),
            "extraction_date": prov.get("extraction_date"),
            "model_used": prov.get("model_used"),
            "confidence": prov.get("confidence"),
            "web_enriched_fields": prov.get("web_enriched_fields", []),
            "web_sources": prov.get("web_sources", []),
            "validation": product.get("validation", {}),
        },
    }
    if product.get("manufacturer"):
        result["manufacturer"] = {"@type": "Organization", "name": product["manufacturer"]}
    return {k: v for k, v in result.items() if v is not None}


def _build_kg_relationships(product: dict) -> dict:
    """Fix 3: Pull compatibility, aliases, replacements from knowledge graph."""
    try:
        from .knowledge_graph import (
            load_graph, get_compatible_products,
            get_aliases, get_replacement,
        )
        graph = load_graph()
        pid = str(product.get("product_id") or product.get("part_number") or "")
        if not pid or not graph.has_node(pid):
            return {}
        return {
            "compatible_with": [c["product_id"] for c in get_compatible_products(graph, pid)],
            "aliases": get_aliases(graph, pid),
            "replaced_by": get_replacement(graph, pid),
        }
    except Exception:
        return {}


def to_csv_row(product: dict) -> dict:
    """Flatten a product record into a CSV-compatible dict."""
    prov = product.get("provenance", {})
    val = product.get("validation", {})
    row = _base_csv_fields(product, prov, val)
    _add_attribute_fields(row, product.get("attributes", {}))
    _add_source_fields(row, prov.get("field_sources", {}))
    return row


def _base_csv_fields(product: dict, prov: dict, val: dict) -> dict:
    return {
        "product_id": product.get("product_id", ""),
        "product_type": product.get("product_type", ""),
        "name": product.get("name", ""),
        "manufacturer": product.get("manufacturer", ""),
        "part_number": product.get("part_number", ""),
        "confidence": prov.get("confidence", ""),
        "source_document": prov.get("source_document", ""),
        "extraction_date": prov.get("extraction_date", ""),
        "passed_validation": val.get("passed_rules", ""),
        "needs_review": val.get("needs_human_review", ""),
        "validation_issues": "; ".join(val.get("issues", [])),
        "web_enriched_fields": ", ".join(prov.get("web_enriched_fields", [])),
    }


def _add_attribute_fields(row: dict, attrs: dict) -> None:
    for key, val in attrs.items():
        row[f"attr_{key}"] = (
            ", ".join(str(v) for v in val) if isinstance(val, list)
            else ("" if val is None else val)
        )


def _add_source_fields(row: dict, field_sources: dict) -> None:
    for key, source in field_sources.items():
        row[f"source_{key}"] = source


def export_batch_json(products: list[dict], output_path: str | Path) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)


def export_batch_jsonld(products: list[dict], output_path: str | Path) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump([to_jsonld(p) for p in products], f, indent=2, ensure_ascii=False)


def export_batch_csv(products: list[dict], output_path: str | Path) -> None:
    if not products:
        return
    rows = [to_csv_row(p) for p in products]
    all_keys = list({k: None for row in rows for k in row})
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def products_to_csv_string(products: list[dict]) -> str:
    if not products:
        return ""
    rows = [to_csv_row(p) for p in products]
    all_keys = list({k: None for row in rows for k in row})
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=all_keys, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()
