"""
core/web_enricher.py — Web search enrichment for sparse product records.

Single responsibility: Claude web_search tool call + result parsing.
Only called when extraction confidence < CONFIDENCE_WEB_SEARCH_THRESHOLD.
All model names from core/constants.py.
"""
import json
import re

import anthropic

from .constants import EXTRACTION_MODEL
from .pydantic_schemas import WebEnrichmentResultSchema

WEB_SYSTEM = """\
You are an industrial product data researcher. Use web_search to find
missing specifications. Return ONLY JSON (no markdown):
{"fields_found": {"field": value}, "sources": ["url"], "confidence": 0.0, "success": true/false}
Rules: extract only values you find on pages. Prefer manufacturer sites.
"""


def web_enrich(query: str, target_fields: list[str], product: dict,
               client: anthropic.Anthropic) -> dict:
    """Search the web for missing product attributes."""
    enhanced = _build_query(query, product, target_fields)
    try:
        response = client.messages.create(
            model=EXTRACTION_MODEL, max_tokens=1500,
            system=WEB_SYSTEM,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": _user_message(enhanced, target_fields, product)}],
        )
        text = _extract_text(response)
        parsed = _parse(text)
        validated = WebEnrichmentResultSchema(query_used=enhanced, **parsed)
        return validated.model_dump()
    except Exception:
        return _empty_result(enhanced)


def apply_web_enrichment_to_product(product: dict, enrichment: dict) -> dict:
    """Merge web enrichment results into a product record."""
    if not enrichment.get("success"):
        return product
    attrs = product.setdefault("attributes", {})
    prov = product.setdefault("provenance", {})
    sources = prov.setdefault("web_sources", [])
    web_fields = prov.setdefault("web_enriched_fields", [])
    for field, val in enrichment.get("fields_found", {}).items():
        if attrs.get(field) is None and val is not None:
            attrs[field] = val
            prov.setdefault("field_sources", {})[field] = "web_enriched"
            web_fields.append(field)
    for src in enrichment.get("sources", []):
        if src not in sources:
            sources.append(src)
    _boost_confidence(prov, len(enrichment.get("fields_found", {})))
    return product


def _build_query(query: str, product: dict, target_fields: list[str]) -> str:
    parts = [query]
    for key in ("part_number", "manufacturer", "product_type"):
        val = product.get(key)
        if val and str(val) not in query:
            parts.append(str(val))
    if not any(w in query.lower() for w in ("spec", "datasheet")):
        parts.append("specifications datasheet")
    return " ".join(parts)


def _user_message(query: str, target_fields: list[str], product: dict) -> str:
    known = {k: v for k, v in product.get("attributes", {}).items() if v is not None}
    known_str = "\n".join(f"  {k}: {v}" for k, v in list(known.items())[:8])
    fields_str = ", ".join(target_fields) if target_fields else "all null fields"
    return f"Query: {query}\nMissing: {fields_str}\nKnown:\n{known_str}"


def _extract_text(response) -> str:
    return "\n".join(b.text for b in response.content if hasattr(b, "type") and b.type == "text")


def _parse(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        data = json.loads(text)
        return {
            "fields_found": data.get("fields_found", {}),
            "sources": data.get("sources", []),
            "confidence": float(data.get("confidence", 0.5)),
            "success": bool(data.get("success", False)),
        }
    except (json.JSONDecodeError, ValueError):
        return _empty_result("")


def _empty_result(query: str) -> dict:
    return {"query_used": query, "fields_found": {}, "sources": [], "confidence": 0.0, "success": False}


def _boost_confidence(prov: dict, field_count: int) -> None:
    if field_count > 0:
        current = prov.get("confidence", 0.5)
        prov["confidence"] = min(1.0, current + 0.05 * field_count)
