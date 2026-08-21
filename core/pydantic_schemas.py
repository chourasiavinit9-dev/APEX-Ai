"""
core/pydantic_schemas.py — ALL Pydantic validation models live here.

Domain-named (not generic InputSchema/OutputSchema) following the
97-score pattern from Hack2Skill winning submissions.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ── Enumerations ──────────────────────────────────────────────────────────────

class ProductTypeEnum(str, Enum):
    BEARING = "bearing"
    VALVE = "valve"
    SENSOR = "sensor"
    COUPLING = "coupling"
    FASTENER = "fastener"
    PUMP = "pump"


class FieldSourceEnum(str, Enum):
    EXTRACTED = "extracted"
    INFERRED = "inferred"
    WEB_ENRICHED = "web_enriched"
    HUMAN_CORRECTED = "human_corrected"
    RULE_DEFAULT = "rule_default"


class AgentToolEnum(str, Enum):
    EXTRACT_ATTRIBUTES = "extract_attributes"
    SEARCH_WEB = "search_web"
    QUERY_CATALOG = "query_catalog"
    REQUEST_HUMAN_INPUT = "request_human_input"


# ── Attribute schemas (domain-named, per product type) ────────────────────────

class BearingAttributeSchema(BaseModel):
    """Validated attribute set for bearing products."""
    material: Optional[str] = None
    bore_diameter: Optional[float] = Field(None, ge=1, le=2000)
    outer_diameter: Optional[float] = Field(None, ge=3, le=2500)
    width: Optional[float] = Field(None, ge=1, le=500)
    dynamic_load_rating: Optional[float] = Field(None, ge=0.01, le=50000)
    static_load_rating: Optional[float] = Field(None, ge=0.01, le=50000)
    operating_temp_min: Optional[float] = Field(None, ge=-200, le=0)
    operating_temp_max: Optional[float] = Field(None, ge=60, le=600)
    speed_rating: Optional[float] = Field(None, ge=1, le=500000)
    lubrication: Optional[str] = None
    sealing: Optional[str] = None
    bearing_type: Optional[str] = None
    certifications: Optional[List[str]] = None
    compatible_standards: Optional[List[str]] = None
    weight: Optional[float] = Field(None, ge=0.001, le=10000)


class ValveAttributeSchema(BaseModel):
    """Validated attribute set for valve products."""
    valve_type: Optional[str] = None
    material: Optional[str] = None
    connection_size: Optional[float] = Field(None, ge=3, le=3000)
    connection_type: Optional[str] = None
    pressure_rating: Optional[float] = Field(None, ge=0.1, le=1000)
    operating_temp_min: Optional[float] = None
    operating_temp_max: Optional[float] = None
    actuation: Optional[str] = None
    cv_flow_coefficient: Optional[float] = None
    body_rating: Optional[str] = None
    leakage_class: Optional[str] = None
    certifications: Optional[List[str]] = None
    weight: Optional[float] = None


class SensorAttributeSchema(BaseModel):
    """Validated attribute set for sensor/transducer products."""
    sensor_type: Optional[str] = None
    measurement_range_min: Optional[float] = None
    measurement_range_max: Optional[float] = None
    measurement_unit: Optional[str] = None
    accuracy: Optional[float] = Field(None, ge=0.001, le=10)
    output_signal: Optional[str] = None
    supply_voltage_min: Optional[float] = Field(None, ge=1, le=600)
    supply_voltage_max: Optional[float] = Field(None, ge=1, le=600)
    protection_class: Optional[str] = None
    process_connection: Optional[str] = None
    operating_temp_min: Optional[float] = None
    operating_temp_max: Optional[float] = None
    response_time: Optional[float] = Field(None, ge=0)
    material_wetted: Optional[str] = None
    certifications: Optional[List[str]] = None
    weight: Optional[float] = None


# ── Core pipeline schemas ─────────────────────────────────────────────────────

class ProductExtractionSchema(BaseModel):
    """
    Schema for the full extraction result from Claude.
    Validates AI output before it enters the pipeline.
    """
    product_id: Optional[str] = None
    product_type: str
    name: Optional[str] = None
    manufacturer: Optional[str] = None
    part_number: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)
    evidence: Dict[str, str] = Field(default_factory=dict)
    field_confidences: Dict[str, float] = Field(default_factory=dict)
    extraction_confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    @field_validator("field_confidences")
    @classmethod
    def validate_confidences(cls, v: Dict[str, float]) -> Dict[str, float]:
        for field, conf in v.items():
            if not 0.0 <= conf <= 1.0:
                raise ValueError(f"Confidence for '{field}' must be 0.0–1.0, got {conf}")
        return v


class ProductProvenanceSchema(BaseModel):
    """Traceable provenance record — every field has a named source."""
    source_document: str
    source_excerpt: str = ""
    extraction_date: str  # ISO8601
    model_used: str
    confidence: float = Field(ge=0.0, le=1.0)
    field_sources: Dict[str, str] = Field(default_factory=dict)
    field_confidences: Dict[str, float] = Field(default_factory=dict)
    evidence: Dict[str, str] = Field(default_factory=dict)
    enriched_fields_count: int = 0
    web_enriched_fields: List[str] = Field(default_factory=list)
    enrichment_skipped: Optional[str] = None


class ProductValidationSchema(BaseModel):
    """Validation result attached to every product record."""
    issues: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    passed_rules: bool = False
    needs_human_review: bool = True
    review_reason: str = ""
    human_approved: bool = False
    llm_assessment: Optional[str] = None


class ProductRecordSchema(BaseModel):
    """
    Complete APEX product record — the canonical output format.
    Every record includes attributes, provenance, and validation.
    """
    product_id: Optional[str] = None
    product_type: str
    name: Optional[str] = None
    manufacturer: Optional[str] = None
    part_number: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    validation: Dict[str, Any] = Field(default_factory=dict)


class AgentToolCallSchema(BaseModel):
    """Validates agent tool invocations before execution."""
    tool_name: str
    parameters: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_name")
    @classmethod
    def validate_tool_name(cls, v: str) -> str:
        valid = {t.value for t in AgentToolEnum}
        if v not in valid:
            raise ValueError(f"Unknown tool '{v}'. Valid: {valid}")
        return v


class WebEnrichmentResultSchema(BaseModel):
    """Result from web search enrichment attempt."""
    query_used: str
    fields_found: Dict[str, Any] = Field(default_factory=dict)
    sources: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    success: bool = False


class KnowledgeGraphNodeSchema(BaseModel):
    """A product node in the knowledge graph."""
    node_id: str
    product_type: str
    name: Optional[str] = None
    part_number: Optional[str] = None
    manufacturer: Optional[str] = None
    attributes_summary: Dict[str, Any] = Field(default_factory=dict)


class KnowledgeGraphEdgeSchema(BaseModel):
    """A relationship edge between two product nodes."""
    source_id: str
    target_id: str
    edge_type: str  # compatible_with | replaces | same_as | meets_standard
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExtractionErrorSchema(BaseModel):
    """Structured error response — never exposes internals."""
    error: str
    issues: List[Dict[str, Any]] = Field(default_factory=list)
    source_document: Optional[str] = None


class UOMConversionResultSchema(BaseModel):
    """Schema for unit of measure conversion and ambiguity handling."""
    raw_value: str
    normalised_value: Optional[str] = None
    is_ambiguous: bool = False
    ambiguity_reason: Optional[str] = None
    suggested_alternatives: List[str] = Field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: str, unit_hint: str = "") -> "UOMConversionResultSchema":
        from loaders.uom_normaliser import normalise_single_value, AmbiguousUOM
        res = normalise_single_value(raw, unit_hint=unit_hint)
        if isinstance(res, AmbiguousUOM):
            return cls(
                raw_value=res.raw_value,
                normalised_value=None,
                is_ambiguous=True,
                ambiguity_reason=res.reason,
                suggested_alternatives=res.suggested_alternatives,
            )
        return cls(
            raw_value=raw,
            normalised_value=str(res),
            is_ambiguous=False,
            ambiguity_reason=None,
            suggested_alternatives=[],
        )
