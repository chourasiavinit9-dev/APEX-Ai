"""
core/extractor.py — LLM extraction using Claude.

Single responsibility: Claude API call + JSON parsing.
All model names and thresholds from core/constants.py.
All schemas from core/pydantic_schemas.py.
"""
from __future__ import annotations
import json
import os
import re
from datetime import datetime, timezone

import anthropic

from .constants import (
    EXTRACTION_MODEL,
    CLASSIFICATION_MODEL,
    MAX_DOCUMENT_CHARS,
    MAX_PDF_PAGES,
    PRODUCT_TYPES,
)
from .ingest import IngestedDocument, InputType
from .schemas import schema_for_prompt, get_attribute_names
from .pydantic_schemas import ProductExtractionSchema


EXTRACTION_SYSTEM = """\
You are an industrial product data specialist. Extract structured product
attributes and return ONLY valid JSON — no preamble, no markdown fences.

Rules:
1. Extract ONLY what is explicitly stated. Never fabricate.
2. For every extracted field, include verbatim source text (max 80 chars)
   in the "evidence" object under the same key.
3. If a field is absent, set it to null. Never fill in a guess.
4. Normalize units: temperatures→°C, pressures→bar, lengths→mm, weights→kg.
5. Confidence: 1.0=directly stated, 0.8=clearly implied, 0.5=inferred.
   Set extraction_confidence to mean of all extracted field confidences.
"""


def build_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))


def extract(
    doc: IngestedDocument,
    product_type: str,
    model: str = EXTRACTION_MODEL,
    client: anthropic.Anthropic | None = None,
) -> dict:
    """Extract structured product attributes from an ingested document."""
    if client is None:
        client = build_client()
    attr_names = get_attribute_names(product_type)
    attribute_nulls = ",\n    ".join(f'"{a}": null' for a in attr_names)
    user_content = _build_user_content(doc, product_type, attribute_nulls)
    response = client.messages.create(
        model=model,
        max_tokens=2500,
        system=EXTRACTION_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )
    raw = response.content[0].text.strip()
    extracted = _parse_and_validate(raw)
    extracted["provenance"] = _build_provenance(doc, model, extracted)
    extracted["validation"] = {"issues": [], "passed_rules": False, "needs_human_review": True}
    return extracted


def classify_product_type(
    doc: IngestedDocument,
    client: anthropic.Anthropic | None = None,
) -> str:
    """Cheap Haiku call to detect product type from document text."""
    if client is None:
        client = build_client()
    text = (doc.text or doc.excerpt or "")[:1000]
    valid = set(PRODUCT_TYPES)
    response = client.messages.create(
        model=CLASSIFICATION_MODEL,
        max_tokens=10,
        system=f"Reply with ONE word only from: {', '.join(valid)}. No other output.",
        messages=[{"role": "user", "content": f"Classify this product:\n{text}"}],
    )
    result = response.content[0].text.strip().lower()
    return result if result in valid else "bearing"


def _build_user_content(
    doc: IngestedDocument,
    product_type: str,
    attribute_nulls: str,
) -> list | str:
    """Build user message — multipart for images, plain text otherwise."""
    schema_desc = schema_for_prompt(product_type)
    user_text = _user_template(product_type, schema_desc, attribute_nulls)
    if doc.input_type in (InputType.IMAGE, InputType.PDF_IMAGE) and doc.image_b64:
        return _build_image_content(doc, user_text)
    doc_text = (doc.text or "")[:MAX_DOCUMENT_CHARS]
    return user_text.replace("{DOCUMENT}", doc_text)


def _build_image_content(doc: IngestedDocument, user_text: str) -> list:
    """Build multipart content list for vision inputs."""
    content = []
    for b64 in doc.image_b64[:MAX_PDF_PAGES]:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": doc.image_media_type or "image/png",
                "data": b64,
            },
        })
    content.append({"type": "text", "text": user_text.replace("{DOCUMENT}", "[See images]")})
    return content


def _user_template(product_type: str, schema_desc: str, attribute_nulls: str) -> str:
    return (
        f"Product type hint: {product_type}\n\n{schema_desc}\n\n"
        f"---\nDocument content:\n{{DOCUMENT}}\n\n---\n"
        f'Return JSON:\n{{"product_id":null,"product_type":"{product_type}",'
        f'"name":null,"manufacturer":null,"part_number":null,'
        f'"attributes":{{{attribute_nulls}}},'
        f'"evidence":{{}},"field_confidences":{{}},"extraction_confidence":0.0}}'
    )


def _parse_and_validate(raw: str) -> dict:
    """Parse JSON and validate through Pydantic schema."""
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw.strip())
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group()) if match else {}
    ProductExtractionSchema(**{k: v for k, v in data.items()
                               if k in ProductExtractionSchema.model_fields})
    return data


def _build_provenance(doc: IngestedDocument, model: str, extracted: dict) -> dict:
    """Build provenance dict from extraction result."""
    return {
        "source_document": doc.source_path,
        "source_excerpt": doc.excerpt,
        "extraction_date": datetime.now(timezone.utc).isoformat(),
        "model_used": model,
        "confidence": extracted.pop("extraction_confidence", 0.0),
        "field_sources": {
            k: "extracted"
            for k, v in extracted.get("attributes", {}).items()
            if v is not None
        },
        "field_confidences": extracted.pop("field_confidences", {}),
        "evidence": extracted.pop("evidence", {}),
        "web_enriched_fields": [],
    }




# ── Fix 4: Programmatic unit conversion fallback ──────────────────────────────

def _fahrenheit_to_celsius(val: float) -> float:
    """Convert F to C only when value > 150 (likely Fahrenheit)."""
    return round((val - 32) * 5 / 9, 2) if val > 150 else val


_UNIT_CONVERSIONS: dict = {
    "operating_temp_min": _fahrenheit_to_celsius,
    "operating_temp_max": _fahrenheit_to_celsius,
}


def normalize_units(product: dict) -> dict:
    """Fix 4: Post-extraction unit normalization — converts Fahrenheit temperatures."""
    attrs = product.get("attributes", {})
    for field, converter in _UNIT_CONVERSIONS.items():
        val = attrs.get(field)
        if val is not None:
            try:
                attrs[field] = converter(float(val))
            except (TypeError, ValueError):
                pass
    return product
