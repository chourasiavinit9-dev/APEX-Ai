"""
core/provenance.py — Provenance tracking for every enriched field.

Every populated field must carry:
  - source_type: where it came from
  - resource_url: local reference file/row
  - source_url: official manufacturer page
  - evidence: excerpt from the source
  - confidence: how sure we are
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Optional, Dict, List, Any


@dataclass
class FieldProvenance:
    """Provenance record for a single enriched field."""
    source_type: str  # input | master_data | manufacturer_document | inferred | human_corrected
    resource_url: Optional[str] = None   # e.g. "Fittings_LOV.xlsx → Material → Row 145"
    source_url: Optional[str] = None     # e.g. "https://mfr.com/datasheets/xyz.pdf"
    evidence: Optional[str] = None       # excerpt from source doc
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "resource_url": self.resource_url,
            "source_url": self.source_url,
            "evidence": self.evidence,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FieldProvenance":
        return cls(
            source_type=d.get("source_type", "inferred"),
            resource_url=d.get("resource_url"),
            source_url=d.get("source_url"),
            evidence=d.get("evidence"),
            confidence=d.get("confidence", 0.0),
        )


@dataclass
class RecordProvenance:
    """Provenance for all fields in a product record."""
    fields: Dict[str, FieldProvenance] = dc_field(default_factory=dict)

    def set(self, field_name: str, prov: FieldProvenance):
        self.fields[field_name] = prov

    def get(self, field_name: str) -> Optional[FieldProvenance]:
        return self.fields.get(field_name)

    def to_dict(self) -> dict:
        return {k: v.to_dict() for k, v in self.fields.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "RecordProvenance":
        return cls(fields={k: FieldProvenance.from_dict(v) for k, v in d.items()})

    @property
    def avg_confidence(self) -> float:
        if not self.fields:
            return 0.0
        return sum(p.confidence for p in self.fields.values()) / len(self.fields)

    @property
    def source_coverage(self) -> float:
        """Percentage of fields with a resource_url or source_url."""
        if not self.fields:
            return 0.0
        covered = sum(
            1 for p in self.fields.values()
            if p.resource_url or p.source_url
        )
        return covered / len(self.fields)


def build_provenance_for_enriched(raw_row: dict, enriched: dict) -> RecordProvenance:
    """
    Build provenance from an enriched record's metadata.
    Maps pipeline steps to source types.
    """
    prov = RecordProvenance()

    # Brand provenance
    match_type = enriched.get("brand_match_type", "fallback")
    brand_conf = enriched.get("brand_confidence", 0.0)
    if match_type == "exact":
        prov.set("brand_name", FieldProvenance(
            source_type="master_data",
            resource_url="UniCat_Manufacturer_and_Brand_List.xlsx → Brand List",
            confidence=brand_conf,
            evidence=f"Exact match: '{enriched.get('raw_brand', '')}' → '{enriched.get('brand_name', '')}'",
        ))
    elif match_type == "fuzzy":
        prov.set("brand_name", FieldProvenance(
            source_type="master_data",
            resource_url="UniCat_Manufacturer_and_Brand_List.xlsx → Fuzzy Match",
            confidence=brand_conf,
            evidence=f"Fuzzy match ({brand_conf:.0%}): '{enriched.get('raw_brand', '')}'",
        ))
    else:
        prov.set("brand_name", FieldProvenance(
            source_type="input",
            resource_url=None,
            confidence=max(brand_conf, 0.3),
            evidence=f"Fallback from raw input: '{enriched.get('raw_brand', '')}'",
        ))

    # Manufacturer provenance
    prov.set("manufacturer_name", FieldProvenance(
        source_type="master_data" if match_type in ("exact", "fuzzy") else "input",
        resource_url="UniCat_Manufacturer_and_Brand_List.xlsx" if match_type != "fallback" else None,
        confidence=brand_conf,
    ))

    # Classpath provenance
    classpath = enriched.get("classpath", "")
    pipeline_steps = enriched.get("_pipeline_steps", [])
    if "taxonomy_classify" in pipeline_steps:
        prov.set("classpath", FieldProvenance(
            source_type="inferred",
            resource_url="Claude Haiku → Taxonomy Classification",
            confidence=0.85,
            evidence=f"Classified from: '{raw_row.get('Part_Desc', '')}'",
        ))

    # Attribute provenance
    attrs = enriched.get("attributes", {})
    for attr_name, attr_value in attrs.items():
        if attr_name.startswith("_"):
            continue
        if not attr_value:
            continue

        # Check if web-enriched
        web_sources = attrs.get("_web_sources", [])
        if attr_name in [s.get("attr") for s in web_sources if isinstance(s, dict)]:
            prov.set(f"attr:{attr_name}", FieldProvenance(
                source_type="manufacturer_document",
                confidence=0.80,
            ))
        elif "attribute_extract" in pipeline_steps:
            prov.set(f"attr:{attr_name}", FieldProvenance(
                source_type="inferred",
                resource_url="Claude Haiku → Attribute Extraction",
                confidence=0.75,
                evidence=f"Extracted from description: '{raw_row.get('Part_Desc', '')}'",
            ))

    # Description provenance
    for desc_field in ("invoice_desc", "mobile_desc", "short_desc", "long_desc", "marketing_copy"):
        if enriched.get(desc_field):
            is_fallback = enriched.get("_fallback", False)
            prov.set(desc_field, FieldProvenance(
                source_type="inferred",
                resource_url="Rule-based fallback" if is_fallback else "Claude Haiku → Description Builder",
                confidence=0.70 if is_fallback else 0.85,
            ))

    return prov


def compute_priority_score(enriched: dict, prov: RecordProvenance) -> int:
    """
    Compute review priority score (higher = more urgent).
    - Missing required fields: +30
    - Low confidence (<0.70): +25
    - No source evidence: +10 per field
    - Conflicting sources: +20
    """
    score = 0

    # Missing required fields
    required = ["brand_name", "classpath", "invoice_desc", "mobile_desc"]
    for field in required:
        if not enriched.get(field):
            score += 30

    # Low overall confidence
    if prov.avg_confidence < 0.70:
        score += 25
    elif prov.avg_confidence < 0.80:
        score += 10

    # Source coverage penalty
    unsourced = sum(
        1 for p in prov.fields.values()
        if not p.resource_url and not p.source_url
    )
    score += unsourced * 5

    # Brand fallback
    if enriched.get("brand_match_type") == "fallback":
        score += 20

    # Validation failures
    validation = enriched.get("validation", {})
    failed = sum(
        1 for r in validation.get("field_results", [])
        if not r.get("passed", True)
    )
    score += failed * 10

    return score
