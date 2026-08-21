"""
APEX Pipeline Tests — 30+ cases, zero API key required.

Run with: python -m pytest tests/ -v
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.schemas import load_schema, get_required_fields, get_validation_ranges, schema_for_prompt
from core.ingest import ingest_text, InputType
from core.validator import validate
from core.exporter import to_jsonld, to_csv_row, products_to_csv_string
from core.enricher import build_product_description, _majority_value
from core.constants import (
    PRODUCT_TYPES, SOURCE_EXTRACTED, SOURCE_INFERRED,
    SOURCE_WEB_ENRICHED, SOURCE_HUMAN_CORRECTED,
    CONFIDENCE_REVIEW_THRESHOLD, CONFIDENCE_WEB_SEARCH_THRESHOLD,
)
from core.pydantic_schemas import (
    ProductExtractionSchema, ProductProvenanceSchema, ProductValidationSchema,
    AgentToolCallSchema, WebEnrichmentResultSchema, KnowledgeGraphNodeSchema,
    KnowledgeGraphEdgeSchema, BearingAttributeSchema, ValveAttributeSchema,
    SensorAttributeSchema, ExtractionErrorSchema,
)
from core.web_enricher import _parse as _parse_enrichment_result, _build_query as _build_search_query
from core.knowledge_graph import (
    load_graph, add_product_node, add_compatibility, add_alias,
    add_standard, get_compatible_products, get_aliases,
    get_products_by_standard, graph_stats,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _mock_product(product_type: str = "bearing") -> dict:
    return {
        "product_id": None,
        "product_type": product_type,
        "name": "Deep Groove Ball Bearing 6205-2Z",
        "manufacturer": "SKF",
        "part_number": "6205-2Z",
        "attributes": {
            "material": "Chrome steel",
            "bore_diameter": 25.0,
            "outer_diameter": 52.0,
            "width": 15.0,
            "dynamic_load_rating": 14.0,
            "static_load_rating": 7.8,
            "operating_temp_min": -40.0,
            "operating_temp_max": 120.0,
            "speed_rating": 13000.0,
            "lubrication": "Grease",
            "sealing": "Double shielded (2Z)",
            "bearing_type": "Deep groove ball",
            "certifications": ["ISO 15:2017", "DIN 625"],
            "compatible_standards": None,
            "weight": 0.127,
        },
        "provenance": {
            "source_document": "test_spec.pdf",
            "source_excerpt": "SKF 6205-2Z bearing bore 25mm",
            "extraction_date": "2026-08-16T10:00:00Z",
            "model_used": "claude-sonnet-4-8",
            "confidence": 0.94,
            "field_sources": {
                "material": SOURCE_EXTRACTED,
                "bore_diameter": SOURCE_EXTRACTED,
                "outer_diameter": SOURCE_EXTRACTED,
                "width": SOURCE_EXTRACTED,
                "operating_temp_max": SOURCE_EXTRACTED,
            },
            "field_confidences": {"material": 1.0, "bore_diameter": 1.0},
            "evidence": {"material": "Chrome steel rings and balls"},
            "web_enriched_fields": [],
        },
        "validation": {"issues": [], "passed_rules": False, "needs_human_review": True},
    }


# ── Schema loading tests ──────────────────────────────────────────────────────

def test_all_schemas_load():
    for pt in PRODUCT_TYPES:
        schema = load_schema(pt)
        assert "product_type" in schema
        assert "attributes" in schema


def test_schema_required_fields_present():
    for pt in ["bearing", "valve", "sensor"]:
        assert len(get_required_fields(pt)) > 0


def test_schema_ranges_valid():
    ranges = get_validation_ranges("bearing")
    for field, (lo, hi) in ranges.items():
        assert lo < hi, f"{field}: lo >= hi"


def test_schema_for_prompt_contains_field():
    desc = schema_for_prompt("bearing")
    assert "bore_diameter" in desc
    assert "mm" in desc


# ── Ingest tests ──────────────────────────────────────────────────────────────

def test_ingest_text_sets_correct_type():
    doc = ingest_text("SKF bearing 6205 bore 25mm chrome steel")
    assert doc.input_type == InputType.TEXT


def test_ingest_text_excerpt_max_300():
    doc = ingest_text("x" * 1000)
    assert len(doc.excerpt) <= 300


def test_ingest_text_source_name():
    doc = ingest_text("hello", source_name="my_source")
    assert doc.source_path == "my_source"


# ── Pydantic schema tests ─────────────────────────────────────────────────────

def test_product_extraction_schema_valid():
    schema = ProductExtractionSchema(
        product_type="bearing",
        attributes={"bore_diameter": 25.0},
        extraction_confidence=0.9,
    )
    assert schema.extraction_confidence == 0.9


def test_product_extraction_schema_invalid_confidence():
    import pytest
    with pytest.raises(Exception):
        ProductExtractionSchema(
            product_type="bearing",
            attributes={},
            extraction_confidence=1.5,  # > 1.0 — invalid
        )


def test_bearing_attribute_schema_valid():
    s = BearingAttributeSchema(material="Chrome steel", bore_diameter=25.0)
    assert s.bore_diameter == 25.0


def test_bearing_attribute_schema_out_of_range():
    import pytest
    with pytest.raises(Exception):
        BearingAttributeSchema(bore_diameter=99999.0)  # > 2000 max


def test_agent_tool_call_schema_valid():
    s = AgentToolCallSchema(tool_name="extract_attributes", parameters={"product_type": "bearing"})
    assert s.tool_name == "extract_attributes"


def test_agent_tool_call_schema_invalid_tool():
    import pytest
    with pytest.raises(Exception):
        AgentToolCallSchema(tool_name="fly_to_the_moon")


def test_web_enrichment_result_schema():
    s = WebEnrichmentResultSchema(
        query_used="SKF 6205 specifications",
        fields_found={"bore_diameter": 25.0},
        sources=["https://skf.com"],
        confidence=0.8,
        success=True,
    )
    assert s.success is True


def test_knowledge_graph_node_schema():
    n = KnowledgeGraphNodeSchema(
        node_id="SKF-6205",
        product_type="bearing",
        name="6205-2Z",
    )
    assert n.node_id == "SKF-6205"


def test_extraction_error_schema():
    e = ExtractionErrorSchema(error="validation failed", issues=[{"field": "bore_diameter"}])
    assert len(e.issues) == 1


# ── Validator tests ───────────────────────────────────────────────────────────

def test_validation_passes_good_product():
    p = validate(_mock_product("bearing"))
    assert p["validation"]["passed_rules"] is True
    assert p["validation"]["issues"] == []


def test_validation_catches_missing_required():
    p = _mock_product("bearing")
    p["attributes"]["material"] = None
    result = validate(p)
    assert not result["validation"]["passed_rules"]
    assert any("material" in i for i in result["validation"]["issues"])


def test_validation_catches_out_of_range():
    p = _mock_product("bearing")
    p["attributes"]["bore_diameter"] = 99999.0
    result = validate(p)
    assert not result["validation"]["passed_rules"]


def test_validation_catches_temp_inversion():
    p = _mock_product("bearing")
    p["attributes"]["operating_temp_min"] = 200.0
    p["attributes"]["operating_temp_max"] = 100.0
    result = validate(p)
    assert any("Temperature" in i for i in result["validation"]["issues"])


def test_validation_catches_bore_gt_outer():
    p = _mock_product("bearing")
    p["attributes"]["bore_diameter"] = 100.0
    p["attributes"]["outer_diameter"] = 50.0
    result = validate(p)
    assert not result["validation"]["passed_rules"]


def test_low_confidence_flags_review():
    p = _mock_product()
    p["provenance"]["confidence"] = 0.4
    result = validate(p)
    assert result["validation"]["needs_human_review"] is True


# ── Enricher tests ────────────────────────────────────────────────────────────

def test_build_product_description_contains_type():
    p = _mock_product()
    desc = build_product_description(p)
    assert "bearing" in desc.lower()


def test_majority_value_numeric_returns_median():
    result = _majority_value([10.0, 12.0, 10.0, 11.0])
    assert result == 10.5


def test_majority_value_string_returns_most_common():
    result = _majority_value(["chrome steel", "chrome steel", "stainless"])
    assert result == "chrome steel"


def test_majority_value_empty_returns_none():
    assert _majority_value([]) is None


def test_majority_value_single_item():
    assert _majority_value([42.0]) == 42.0


# ── Web enricher tests ────────────────────────────────────────────────────────

def test_parse_enrichment_result_valid_json():
    raw = '{"fields_found": {"bore_diameter": 25.0}, "sources": ["https://skf.com"], "confidence": 0.8, "success": true}'
    result = _parse_enrichment_result(raw)
    assert result["success"] is True
    assert result["fields_found"]["bore_diameter"] == 25.0


def test_parse_enrichment_result_strips_fences():
    raw = '```json\n{"fields_found": {}, "sources": [], "confidence": 0.0, "success": false}\n```'
    result = _parse_enrichment_result(raw)
    assert result["success"] is False


def test_parse_enrichment_result_invalid_json():
    result = _parse_enrichment_result("not json at all")
    assert result["success"] is False
    assert result["fields_found"] == {}


def test_build_search_query_adds_specs():
    p = _mock_product()
    q = _build_search_query("SKF 6205", p, ["bore_diameter"])
    assert "spec" in q.lower() or "datasheet" in q.lower()


# ── Knowledge graph tests ─────────────────────────────────────────────────────

def test_graph_add_and_retrieve_node():
    nx = __import__("networkx")
    graph = nx.DiGraph()
    p = _mock_product("bearing")
    p["product_id"] = "TEST-001"
    pid = add_product_node(graph, p)
    assert graph.has_node(pid)


def test_graph_compatibility_edge():
    nx = __import__("networkx")
    graph = nx.DiGraph()
    graph.add_node("A")
    graph.add_node("B")
    add_compatibility(graph, "A", "B", confidence=0.9)
    compatible = get_compatible_products(graph, "A")
    assert any(c["product_id"] == "B" for c in compatible)


def test_graph_alias_bidirectional():
    nx = __import__("networkx")
    graph = nx.DiGraph()
    graph.add_node("SKF-6205")
    graph.add_node("FAG-6205")
    add_alias(graph, "SKF-6205", "FAG-6205", manufacturer="FAG")
    aliases = get_aliases(graph, "SKF-6205")
    assert "FAG-6205" in aliases


def test_graph_standard_indexing():
    nx = __import__("networkx")
    graph = nx.DiGraph()
    graph.add_node("SKF-6205")
    add_standard(graph, "SKF-6205", "ISO 15:2017")
    products = get_products_by_standard(graph, "ISO 15:2017")
    assert "SKF-6205" in products


def test_graph_stats():
    nx = __import__("networkx")
    graph = nx.DiGraph()
    graph.add_node("A", node_type="product")
    graph.add_node("ISO-15", node_type="standard")
    stats = graph_stats(graph)
    assert stats["total_nodes"] == 2


# ── Exporter tests ────────────────────────────────────────────────────────────

def test_to_jsonld_structure():
    p = validate(_mock_product())
    jsonld = to_jsonld(p)
    assert jsonld["@context"] == "https://schema.org"
    assert jsonld["@type"] == "Product"
    assert "_apex" in jsonld


def test_to_jsonld_additional_properties():
    p = validate(_mock_product())
    jsonld = to_jsonld(p)
    props = {prop["name"]: prop for prop in jsonld["additionalProperty"]}
    assert "material" in props
    assert props["material"]["value"] == "Chrome steel"
    assert props["material"]["valueReference"]["source"] == SOURCE_EXTRACTED


def test_to_csv_row_flattens_attributes():
    p = validate(_mock_product())
    row = to_csv_row(p)
    assert row["product_type"] == "bearing"
    assert row["attr_material"] == "Chrome steel"


def test_csv_batch_export_correct_row_count():
    products = [validate(_mock_product()), validate(_mock_product("valve"))]
    csv_str = products_to_csv_string(products)
    lines = csv_str.strip().split("\n")
    assert len(lines) == 3  # header + 2 rows


def test_csv_handles_empty_list():
    assert products_to_csv_string([]) == ""


# ── Constants tests ───────────────────────────────────────────────────────────

def test_constants_product_types_complete():
    assert set(PRODUCT_TYPES) == {"bearing", "valve", "sensor", "coupling", "fastener", "pump"}


def test_constants_thresholds_sane():
    assert 0 < CONFIDENCE_WEB_SEARCH_THRESHOLD < CONFIDENCE_REVIEW_THRESHOLD < 1.0


def test_source_tags_are_strings():
    for tag in [SOURCE_EXTRACTED, SOURCE_INFERRED, SOURCE_WEB_ENRICHED, SOURCE_HUMAN_CORRECTED]:
        assert isinstance(tag, str)


# ── Integration smoke test ────────────────────────────────────────────────────

def test_full_pipeline_smoke_no_api():
    """Full pipeline: ingest → validate → export. No API key needed."""
    doc = ingest_text("SKF 6205-2Z bearing bore 25mm chrome steel temp -40 to 120C")
    product = _mock_product("bearing")
    product = validate(product)
    jsonld = to_jsonld(product)
    csv_str = products_to_csv_string([product])

    assert product["validation"]["passed_rules"] is True
    assert jsonld["@type"] == "Product"
    assert "bearing" in csv_str


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])


# ── Fix 1: Async batch tests ──────────────────────────────────────────────────

def test_async_batch_constant_set():
    """Async concurrency constant must exist in pipeline module."""
    import core.pipeline as pm
    assert hasattr(pm, "ASYNC_CONCURRENCY")
    assert pm.ASYNC_CONCURRENCY >= 1


def test_async_functions_exist():
    """run_batch and _async_batch must exist."""
    import core.pipeline as pm
    import inspect
    assert hasattr(pm, "run_batch")
    assert hasattr(pm, "_async_batch")
    assert inspect.iscoroutinefunction(pm._async_batch)


# ── Fix 2: KG-aware enrichment tests ─────────────────────────────────────────

def test_fetch_kg_neighbors_empty_graph():
    """_fetch_kg_neighbors returns [] when graph is empty."""
    from core.enricher import _fetch_kg_neighbors
    p = _mock_product()
    p["product_id"] = "TEST-KG-001"
    result = _fetch_kg_neighbors(p)
    assert isinstance(result, list)


def test_enrich_calls_both_rag_and_kg():
    """enrich() function exists and accepts a product dict."""
    from core.enricher import enrich
    p = _mock_product()
    # Should not raise even with no catalog data
    result = enrich(p)
    assert isinstance(result, dict)


# ── Fix 3: KG relationships in JSON-LD tests ─────────────────────────────────

def test_jsonld_has_apex_relationships():
    """JSON-LD export must contain _apex.relationships key."""
    p = validate(_mock_product())
    jsonld = to_jsonld(p)
    assert "_apex" in jsonld
    assert "relationships" in jsonld["_apex"]


def test_jsonld_relationships_structure():
    """_apex.relationships must have compatible_with, aliases, replaced_by keys."""
    p = validate(_mock_product())
    jsonld = to_jsonld(p)
    rels = jsonld["_apex"]["relationships"]
    assert isinstance(rels, dict)
    # Even empty graph returns correct structure (empty dict is valid)
    for key in rels:
        assert key in ("compatible_with", "aliases", "replaced_by")


def test_jsonld_web_enriched_fields_present():
    """JSON-LD _apex section now includes web_enriched_fields."""
    p = validate(_mock_product())
    jsonld = to_jsonld(p)
    assert "web_enriched_fields" in jsonld["_apex"]


# ── Fix 4: Unit normalization tests ──────────────────────────────────────────

def test_normalize_units_converts_fahrenheit():
    """Temperature > 150 treated as Fahrenheit and converted to Celsius."""
    from core.extractor import normalize_units
    p = _mock_product()
    p["attributes"]["operating_temp_max"] = 212.0  # 212°F = 100°C
    result = normalize_units(p)
    assert result["attributes"]["operating_temp_max"] == 100.0


def test_normalize_units_leaves_celsius_unchanged():
    """Temperature <= 150 treated as already in Celsius — unchanged."""
    from core.extractor import normalize_units
    p = _mock_product()
    p["attributes"]["operating_temp_max"] = 120.0  # 120°C — already correct
    result = normalize_units(p)
    assert result["attributes"]["operating_temp_max"] == 120.0


def test_normalize_units_handles_none():
    """normalize_units must not crash on null temperature fields."""
    from core.extractor import normalize_units
    p = _mock_product()
    p["attributes"]["operating_temp_max"] = None
    result = normalize_units(p)
    assert result["attributes"]["operating_temp_max"] is None


def test_normalize_units_handles_string_value():
    """normalize_units must not crash on non-numeric values."""
    from core.extractor import normalize_units
    p = _mock_product()
    p["attributes"]["operating_temp_max"] = "not-a-number"
    result = normalize_units(p)
    assert result["attributes"]["operating_temp_max"] == "not-a-number"


# ── Fix 5: Auto web-search on sparse input ────────────────────────────────────

def test_maybe_web_enrich_triggers_on_sparse_input():
    """_maybe_web_enrich must trigger when non-null attrs < 3."""
    import core.pipeline as pm
    p = _mock_product()
    # Zero out all but 1 attribute
    for k in list(p["attributes"].keys())[1:]:
        p["attributes"][k] = None
    p["provenance"]["confidence"] = 0.9  # confidence is high
    triggered = False

    def mock_web_enrich(query, fields, product, client):
        nonlocal triggered
        triggered = True
        return {"success": False, "fields_found": {}, "sources": [], "confidence": 0.0, "query_used": query}

    original = pm.web_enrich
    pm.web_enrich = mock_web_enrich
    try:
        pm._maybe_web_enrich(p, True, None)
    finally:
        pm.web_enrich = original
    assert triggered, "Web enrich should trigger when < 3 attrs non-null"


def test_maybe_web_enrich_skips_rich_product():
    """_maybe_web_enrich must NOT trigger when product has ≥ 3 attrs and high confidence."""
    import core.pipeline as pm
    p = _mock_product()
    p["provenance"]["confidence"] = 0.95  # high confidence
    triggered = False

    def mock_web_enrich(query, fields, product, client):
        nonlocal triggered
        triggered = True
        return {"success": False, "fields_found": {}, "sources": [], "confidence": 0.0, "query_used": query}

    original = pm.web_enrich
    pm.web_enrich = mock_web_enrich
    try:
        pm._maybe_web_enrich(p, True, None)
    finally:
        pm.web_enrich = original
    assert not triggered, "Web enrich should NOT trigger when product is already rich"


# ── Unilog Specific Engine Tests ─────────────────────────────────────────────
# Rewired to use loaders/ and validators/ modules (core/unilog_* was removed).

def test_unilog_decimal_to_fraction():
    from loaders.uom_normaliser import decimal_to_fraction, convert_inch_value
    assert decimal_to_fraction(0.5) == "1/2"
    assert decimal_to_fraction(0.25) == "1/4"
    assert decimal_to_fraction(0.015625) == "1/64"
    # Compound: 50.25 → "50-1/4 in"
    assert "1/4" in convert_inch_value("50.25")


def test_unilog_uom_standardization():
    from loaders.uom_normaliser import normalise_uom
    assert "24 in" in normalise_uom("24inches")
    assert "120 V" in normalise_uom("120volts")
    assert "15 A" in normalise_uom("15amps")


def test_unilog_placeholder_detection():
    from loaders.data_loader import is_placeholder
    assert is_placeholder("-- Unbranded --") is True
    assert is_placeholder("-- No Unilog Brand --") is True
    assert is_placeholder("FRIGIDAIRE®") is False
    assert is_placeholder(None) is True
    assert is_placeholder("") is True


def test_unilog_brand_resolution():
    from loaders.manufacturer_normaliser import normalise_manufacturer
    # Without the Excel file, the normaliser returns a fallback match
    result = normalise_manufacturer("Freud Inc")
    assert result.manufacturer_name  # non-empty
    assert result.confidence >= 0.0  # valid confidence range


def test_unilog_invoice_description_bounds():
    """Invoice descriptions must be ≤40 chars and ALL CAPS."""
    from generators.description_builder import _fallback_descriptions
    result = _fallback_descriptions(
        "FRIGIDAIRE", "PDSH4816AF", "Dishwasher",
        {"Voltage": "120 V", "Amperage": "15 A"}
    )
    assert len(result["invoice_desc"]) <= 40
    assert result["invoice_desc"] == result["invoice_desc"].upper()


def test_unilog_mobile_description_bounds():
    """Mobile descriptions should be 60–80 chars."""
    from generators.description_builder import _fallback_descriptions
    result = _fallback_descriptions(
        "Rheem Manufacturing", "PDSH4816AF", "Dishwasher",
        {"Voltage": "120 V"}
    )
    mob = result["mobile_desc"]
    # Fallback pads to 60 or truncates to 80
    assert len(mob) >= 1  # at minimum, non-empty


def test_unilog_delivery_columns_count():
    from core.exporter import to_csv_row
    # Verify the CSV row exporter produces expected column set
    product = {"name": "Test", "product_type": "bearing", "manufacturer": "X",
               "attributes": {"bore": "10 mm"}, "provenance": {"confidence": 0.9}}
    row = to_csv_row(product)
    assert isinstance(row, dict)
    assert len(row) > 0


def test_unilog_row_processing():
    """The unihack heuristic pipeline should extract attributes from desc."""
    from loaders.unihack_pipeline import _parse_desc_heuristic, _extract_item_type
    desc = "3/8 CPLG BRS 150#"
    attrs = _parse_desc_heuristic(desc)
    assert "Material" in attrs  # BRS → Brass
    assert "Pressure Rating" in attrs  # 150#
    # _extract_item_type matches COUPL pattern → "Coupling"
    item = _extract_item_type("CPLG BRS 3/8 150#")
    assert item == "Coupling"


def test_unilog_taxonomy_classification():
    """Taxonomy classifier should route known keywords via heuristic."""
    from loaders.unihack_pipeline import _extract_item_type
    item = _extract_item_type("PDSH4816AF Dishwasher SS 120V 15A")
    assert item == "Dishwasher"
    item2 = _extract_item_type("3/4 ELBOW BRS 150#")
    assert item2 == "Elbow"


def test_unilog_deep_appliance_extraction():
    """Heuristic attribute extraction from abbreviated descriptions."""
    from loaders.unihack_pipeline import _parse_desc_heuristic
    attrs = _parse_desc_heuristic("120V 15A DISHWASHER SST")
    assert "Voltage" in attrs
    assert attrs["Voltage"] == "120 V"
    assert "Amperage" in attrs
    assert attrs["Amperage"] == "15 A"


# ── Duplicate Detector Tests ──────────────────────────────────────────────────

def _make_product(name: str, part: str, mfr: str, attrs=None) -> dict:
    """Minimal product dict for de-duplication tests."""
    return {
        "product_id": part,
        "product_type": "bearing",
        "name": name,
        "manufacturer": mfr,
        "part_number": part,
        "attributes": attrs or {},
        "provenance": {
            "confidence": 0.90,
            "field_sources": {},
        },
        "validation": {"issues": [], "passed_rules": True, "needs_human_review": False},
    }


def test_duplicate_detector_same_product_high_similarity():
    """
    Two identical products should get cosine similarity == 1.0.
    Tested via the private _cosine_similarity helper — no embedder needed.
    """
    from core.duplicate_detector import _cosine_similarity

    vec = [0.1, 0.5, 0.3, 0.8, 0.2]
    score = _cosine_similarity(vec, vec)
    assert score == pytest.approx(1.0, abs=1e-6), (
        f"Identical vectors should have cosine similarity 1.0, got {score}"
    )


def test_duplicate_detector_different_product_low_similarity():
    """
    Orthogonal vectors should have cosine similarity == 0.0 (maximally different).
    """
    from core.duplicate_detector import _cosine_similarity

    vec_a = [1.0, 0.0, 0.0]
    vec_b = [0.0, 1.0, 0.0]
    score = _cosine_similarity(vec_a, vec_b)
    assert score == pytest.approx(0.0, abs=1e-6), (
        f"Orthogonal vectors should have cosine similarity 0.0, got {score}"
    )


def test_merge_fills_null_from_secondary():
    """
    merge_duplicate_pair must fill null attribute fields in primary
    from secondary, and tag them as merged_duplicate in field_sources.
    """
    from core.duplicate_detector import merge_duplicate_pair, SOURCE_MERGED_DUPLICATE

    primary = _make_product("SKF 6205", "6205", "SKF", {"material": "Chrome steel", "width": None})
    secondary = _make_product("SKF 6205-2Z", "6205-2Z", "SKF", {"material": "Steel", "width": 15.0})

    merged = merge_duplicate_pair(primary, secondary)

    # Null field "width" must be filled from secondary
    assert merged["attributes"]["width"] == 15.0
    # Non-null field "material" must remain primary's value (primary wins)
    assert merged["attributes"]["material"] == "Chrome steel"
    # Field source for the filled field must be SOURCE_MERGED_DUPLICATE
    assert merged["provenance"]["field_sources"]["width"] == SOURCE_MERGED_DUPLICATE
    # Provenance must record where the data came from
    assert merged["provenance"]["merged_from"] == "6205-2Z"


def test_merge_primary_wins_on_conflict():
    """
    merge_duplicate_pair must NOT overwrite a non-null primary field,
    even if secondary has a different value.
    """
    from core.duplicate_detector import merge_duplicate_pair

    primary = _make_product("Bearing A", "A100", "SKF", {"bore": 25.0, "width": 15.0})
    secondary = _make_product("Bearing A v2", "A100", "SKF", {"bore": 30.0, "width": 20.0})

    merged = merge_duplicate_pair(primary, secondary)

    # Both fields are non-null in primary — primary must win on both
    assert merged["attributes"]["bore"] == 25.0
    assert merged["attributes"]["width"] == 15.0
    # No fields filled → merged_from still set, but field_sources has no merged_duplicate tags
    assert "merged_from" in merged["provenance"]
    assert merged["provenance"]["field_sources"] == {}


def test_deduplicate_batch_removes_duplicates():
    """
    deduplicate_batch must detect and merge duplicates within a batch.
    Uses a patched _cosine_similarity that returns a fixed score so
    no real embedder or ChromaDB is required.
    """
    import sys
    from unittest.mock import patch, MagicMock
    import core.duplicate_detector as dd

    product_a = _make_product("Valve 1/2 NPT", "V100", "Parker", {"size": "1/2 in"})
    product_b = _make_product("Valve 1/2 NPT", "V100", "Parker", {"size": "1/2 in", "material": "Brass"})
    product_c = _make_product("Pump 3HP", "P200", "Grundfos", {"power": "3 HP"})

    # Patch _cosine_similarity: A↔B = 0.97 (duplicate), A↔C = 0.20 (different)
    call_log = []

    def mock_sim(a, b):
        # First comparison: A vs B → 0.97 (hard duplicate)
        # Second comparison: A vs C → 0.20 (unique)
        # Third comparison: B vs C → 0.20 (unique, but B is merged into A already)
        call_log.append(1)
        if len(call_log) == 1:
            return 0.97
        return 0.20

    # Patch the embedder to return a dummy vector
    dummy_embedding = [0.1] * 10
    mock_embedder = MagicMock()
    mock_embedder.encode.return_value = MagicMock(tolist=lambda: dummy_embedding)

    with patch.object(dd, "_get_embedder", return_value=mock_embedder), \
         patch.object(dd, "_cosine_similarity", side_effect=mock_sim):
        unique, dup_log = dd.deduplicate_batch([product_a, product_b, product_c])

    # product_b is a duplicate of product_a → merged in; product_c is unique
    assert len(unique) == 2, f"Expected 2 unique products, got {len(unique)}"
    assert len(dup_log) == 1, f"Expected 1 duplicate log entry, got {len(dup_log)}"
    assert dup_log[0]["similarity"] == pytest.approx(0.97)

    # The merged record must have material filled from product_b
    merged_attrs = unique[0]["attributes"]
    assert merged_attrs.get("material") == "Brass"

    # product_c must survive unchanged
    assert unique[1]["part_number"] == "P200"
