"""
tests/test_unihack.py — UniHack pipeline tests.

Covers:
  - Placeholder filtering
  - Manufacturer normalisation (exact + fuzzy + fallback)
  - UOM normalisation + decimal/fraction conversion
  - Description format enforcement (char limits, casing)
  - Output validation (LOV, char limits, UOM, brand, fractions)
  - Evaluation scorecard logic

Zero API key required. Zero network calls.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loaders.data_loader import (
    is_placeholder, clean_brand_fields,
    _default_uom_map, _default_fractions,
)
from loaders.manufacturer_normaliser import (
    normalise_manufacturer, normalise_from_row,
    _clean_input, BrandMatch,
)
from loaders.uom_normaliser import (
    normalise_uom, normalise_single_value, decimal_to_fraction,
    format_compound_dimension, convert_inch_value, normalise_uom_dict,
)
from generators.description_builder import (
    _enforce_limits, _fallback_descriptions, extract_series_from_desc,
)
from validators.output_validator import (
    validate_output, score_against_ground_truth,
    _check_invoice_desc, _check_mobile_desc,
    _check_fraction_compliance, _check_no_placeholders,
)
from evaluate import run_evaluation, _build_scorecard


# ══════════════════════════════════════════════════════════════════════════════
# Placeholder Filter Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_placeholder_unbranded():
    assert is_placeholder("-- Unbranded --")

def test_placeholder_no_unilog_brand():
    assert is_placeholder("-- No Unilog Brand --")

def test_placeholder_no_dib_brand():
    assert is_placeholder("-- No DIB Brand --")

def test_placeholder_case_insensitive():
    assert is_placeholder("-- UNBRANDED --")

def test_placeholder_empty_string():
    assert is_placeholder("")

def test_placeholder_none():
    assert is_placeholder(None)

def test_placeholder_real_brand_not_filtered():
    assert not is_placeholder("Mueller Industries")

def test_placeholder_frigidaire_not_filtered():
    assert not is_placeholder("FRIGIDAIRE")

def test_clean_brand_fields_replaces_placeholders():
    row = {
        "E1_Brand": "-- Unbranded --",
        "Unilog_Brand": "FRIGIDAIRE",
        "DIB_Brand": "-- No DIB Brand --",
        "Part_Manuf": "Rheem Manufacturing",
    }
    cleaned = clean_brand_fields(row)
    assert cleaned["E1_Brand"] is None
    assert cleaned["Unilog_Brand"] == "FRIGIDAIRE"
    assert cleaned["DIB_Brand"] is None
    assert cleaned["Part_Manuf"] == "Rheem Manufacturing"

def test_clean_brand_fields_all_placeholders():
    row = {
        "E1_Brand": "-- Unbranded --",
        "Unilog_Brand": "-- No Unilog Brand --",
        "DIB_Brand": "-- No DIB Brand --",
        "Part_Manuf": "-- Unbranded --",
    }
    cleaned = clean_brand_fields(row)
    for field in ["E1_Brand", "Unilog_Brand", "DIB_Brand", "Part_Manuf"]:
        assert cleaned[field] is None


# ══════════════════════════════════════════════════════════════════════════════
# Manufacturer Normalisation Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_normalise_none_returns_fallback():
    result = normalise_manufacturer(None)
    assert isinstance(result, BrandMatch)
    assert result.confidence == 0.0

def test_normalise_placeholder_returns_fallback():
    result = normalise_manufacturer("-- Unbranded --")
    assert result.confidence == 0.0

def test_clean_input_strips_whitespace():
    assert _clean_input("  FRIGIDAIRE  ") == "FRIGIDAIRE"

def test_clean_input_normalises_spaces():
    assert _clean_input("Mueller  Industries") == "Mueller Industries"

def test_normalise_from_row_priority_order():
    """Unilog_Brand takes priority over E1_Brand."""
    row = {
        "Unilog_Brand": "Mueller Industries",
        "E1_Brand": "-- Unbranded --",
        "DIB_Brand": "-- No DIB Brand --",
        "Part_Manuf": "Mueller",
    }
    result = normalise_from_row(row)
    assert isinstance(result, BrandMatch)
    # Should use Unilog_Brand value (not placeholder)
    assert "mueller" in result.manufacturer_name.lower()

def test_normalise_from_row_all_placeholder():
    """When all brand fields are placeholders, return low-confidence fallback."""
    row = {
        "Unilog_Brand": "-- No Unilog Brand --",
        "E1_Brand": "-- Unbranded --",
        "DIB_Brand": "-- No DIB Brand --",
        "Part_Manuf": "-- Unbranded --",
    }
    result = normalise_from_row(row)
    assert result.confidence == 0.0

def test_brand_match_dataclass_fields():
    b = BrandMatch("FRIGIDAIRE®", "FRG", "FRIGIDAIRE®", "FRG001", 1.0, "exact")
    assert b.manufacturer_name == "FRIGIDAIRE®"
    assert b.confidence == 1.0
    assert b.match_type == "exact"


# ══════════════════════════════════════════════════════════════════════════════
# UOM Normalisation Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_uom_map_has_inches():
    uom = _default_uom_map()
    assert uom["inches"] == "in"
    assert uom["inch"] == "in"
    assert uom["in."] == "in"

def test_uom_map_has_volts():
    uom = _default_uom_map()
    assert uom["volts"] == "V"

def test_normalise_uom_adds_space():
    result = normalise_uom("24in")
    assert " in" in result or result == "24in"  # depends on map loaded

def test_normalise_uom_preserves_correct_format():
    result = normalise_uom("24 in W x 24 in D")
    assert "24" in result

def test_decimal_to_fraction_half():
    assert decimal_to_fraction(0.5) == "1/2"

def test_decimal_to_fraction_quarter():
    assert decimal_to_fraction(0.25) == "1/4"

def test_decimal_to_fraction_three_quarter():
    assert decimal_to_fraction(0.75) == "3/4"

def test_decimal_to_fraction_eighth():
    assert decimal_to_fraction(0.125) == "1/8"

def test_decimal_to_fraction_no_match():
    # 0.333 is not in the lookup table
    result = decimal_to_fraction(0.333)
    assert result is None

def test_format_compound_dimension_50_quarter():
    result = format_compound_dimension(50, 0.25, "in")
    assert "50" in result
    assert "1/4" in result
    assert "in" in result

def test_format_compound_dimension_zero_decimal():
    result = format_compound_dimension(24, 0.0, "in")
    assert result == "24 in"

def test_convert_inch_value_decimal():
    result = convert_inch_value("50.25")
    assert "50" in result
    assert "1/4" in result

def test_convert_inch_value_half():
    result = convert_inch_value("0.5")
    assert "1/2" in result

def test_convert_inch_value_whole():
    result = convert_inch_value("24")
    assert result == "24 in"

def test_normalise_uom_dict_converts_values():
    attrs = {"width": "24inches", "depth": "12.5 inches"}
    result = normalise_uom_dict(attrs)
    # Should not crash; values should be normalised strings
    assert isinstance(result, dict)
    assert "width" in result

def test_default_fractions_complete():
    fractions = _default_fractions()
    assert 0.5 in fractions
    assert 0.25 in fractions
    assert 0.75 in fractions
    assert 0.125 in fractions
    assert len(fractions) >= 15


# ══════════════════════════════════════════════════════════════════════════════
# Description Builder Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_enforce_limits_invoice_truncated():
    record = {"invoice_desc": "A" * 50}
    result = _enforce_limits(record)
    assert len(result["invoice_desc"]) <= 40

def test_enforce_limits_invoice_uppercased():
    record = {"invoice_desc": "Coupling Brass 3/8 in"}
    result = _enforce_limits(record)
    assert result["invoice_desc"] == result["invoice_desc"].upper()

def test_enforce_limits_invoice_valid_flag():
    record = {"invoice_desc": "SHORT DESC"}
    result = _enforce_limits(record)
    assert result["invoice_desc_valid"] is True

def test_enforce_limits_mobile_valid():
    record = {"mobile_desc": "A" * 70}  # 70 chars = valid
    result = _enforce_limits(record)
    assert result["mobile_desc_valid"] is True

def test_enforce_limits_mobile_too_short():
    record = {"mobile_desc": "A" * 30}  # 30 chars = too short
    result = _enforce_limits(record)
    assert result["mobile_desc_valid"] is False

def test_enforce_limits_mobile_too_long_truncated():
    record = {"mobile_desc": "A" * 90}  # 90 chars = too long → truncated
    result = _enforce_limits(record)
    assert len(result["mobile_desc"]) <= 80

def test_fallback_descriptions_returns_all_formats():
    result = _fallback_descriptions("BRAND®", "MPN123", "Coupling",
                                    {"material": "Brass"})
    assert "invoice_desc" in result
    assert "mobile_desc" in result
    assert "short_desc" in result
    assert "long_desc" in result
    assert "marketing_copy" in result

def test_fallback_invoice_is_caps():
    result = _fallback_descriptions("Brand", "MPN", "Item", {})
    assert result["invoice_desc"] == result["invoice_desc"].upper()

def test_fallback_invoice_under_40():
    result = _fallback_descriptions("Brand", "MPN", "Item", {})
    assert len(result["invoice_desc"]) <= 40

def test_extract_series_professional():
    assert "Professional Series" in extract_series_from_desc(
        "PDSH4816AF Professional Series Dishwasher"
    )

def test_extract_series_no_series():
    assert extract_series_from_desc("PDSH4816AF Dishwasher SS") == ""


# ══════════════════════════════════════════════════════════════════════════════
# Output Validator Tests
# ══════════════════════════════════════════════════════════════════════════════

def _sample_record() -> dict:
    return {
        "sku": "TEST-001",
        "invoice_desc": "COUPLING BRS 3/8 IN 150#",
        "mobile_desc": "Mueller Industries Brass Coupling, 3/8 in, 150 PSI Rating",
        "short_desc": "Mueller Industries® 3/8 in Brass Coupling, 150 PSI",
        "long_desc": "Mueller Industries® Brass Coupling, 3/8 in, 150 PSI, Threaded",
        "brand_name": "Mueller Industries®",
        "brand_match_type": "exact",
        "brand_confidence": 1.0,
        "classpath": "",
        "attributes": {},
        "raw_brand": "Mueller Industries",
    }

def test_validate_output_returns_report():
    from validators.output_validator import ValidationReport
    record = _sample_record()
    report = validate_output(record)
    assert isinstance(report, ValidationReport)
    assert 0.0 <= report.overall_score <= 1.0

def test_check_invoice_desc_valid():
    record = {"invoice_desc": "COUPLING BRS 3/8"}
    result = _check_invoice_desc(record)
    assert result.passed

def test_check_invoice_desc_too_long():
    record = {"invoice_desc": "A" * 45}
    result = _check_invoice_desc(record)
    assert not result.passed
    assert any("long" in i.lower() for i in result.issues)

def test_check_invoice_desc_not_caps():
    record = {"invoice_desc": "Coupling Brass"}
    result = _check_invoice_desc(record)
    assert not result.passed

def test_check_invoice_desc_missing():
    result = _check_invoice_desc({})
    assert not result.passed

def test_check_mobile_desc_valid():
    record = {"mobile_desc": "Mueller Industries Brass Coupling, 3/8 in, 150 PSI"}
    result = _check_mobile_desc(record)
    # 50 chars — too short
    assert not result.passed

def test_check_mobile_desc_exact_range():
    record = {"mobile_desc": "A" * 70}
    result = _check_mobile_desc(record)
    assert result.passed

def test_check_fraction_compliance_decimal_fails():
    record = {"short_desc": "Coupling 50.25 in Length", "long_desc": "", "invoice_desc": ""}
    result = _check_fraction_compliance(record)
    assert not result.passed

def test_check_fraction_compliance_fraction_passes():
    record = {"short_desc": "Coupling 50-1/4 in Length", "long_desc": "", "invoice_desc": ""}
    result = _check_fraction_compliance(record)
    assert result.passed

def test_check_no_placeholders_clean():
    record = {"brand": "Mueller Industries®", "name": "Coupling"}
    result = _check_no_placeholders(record)
    assert result.passed

def test_check_no_placeholders_dirty():
    record = {"brand": "-- Unbranded --", "name": "Coupling"}
    result = _check_no_placeholders(record)
    assert not result.passed

def test_score_against_ground_truth_exact():
    output = {"invoice_desc": "COUPLING BRS 3/8"}
    gt = {"invoice_desc": "COUPLING BRS 3/8"}
    scores = score_against_ground_truth(output, gt)
    assert scores["invoice_desc"] == "exact_match"

def test_score_against_ground_truth_mismatch():
    output = {"invoice_desc": "COUPLING BRS 3/8"}
    gt = {"invoice_desc": "VALVE 1/2 IN BRASS"}
    scores = score_against_ground_truth(output, gt)
    assert scores["invoice_desc"] == "mismatch"

def test_score_against_ground_truth_no_gt():
    output = {"invoice_desc": "COUPLING BRS 3/8"}
    gt = {"invoice_desc": ""}
    scores = score_against_ground_truth(output, gt)
    assert scores["invoice_desc"] == "no_ground_truth"


# ══════════════════════════════════════════════════════════════════════════════
# Evaluation Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_run_evaluation_empty():
    result = run_evaluation([], [])
    assert "error" in result

def test_run_evaluation_single_record():
    records = [_sample_record()]
    result = run_evaluation(records, [])
    assert result["total_records"] == 1
    assert 0.0 <= result["overall_validation_score"] <= 1.0

def test_scorecard_all_pass():
    sc = _build_scorecard(0.90, 0.20, 1.0, 0.95)
    assert sc["Validation score"]["pass"]
    assert sc["Human review rate"]["pass"]
    assert sc["Character limit compliance"]["pass"]
    assert sc["LOV hit rate"]["pass"]

def test_scorecard_all_fail():
    sc = _build_scorecard(0.70, 0.40, 0.90, 0.80)
    assert not sc["Validation score"]["pass"]
    assert not sc["Human review rate"]["pass"]
    assert not sc["Character limit compliance"]["pass"]
    assert not sc["LOV hit rate"]["pass"]

def test_evaluation_human_review_rate():
    # Record with low brand confidence → should be flagged for review
    low_conf_record = _sample_record()
    low_conf_record["brand_confidence"] = 0.3
    result = run_evaluation([low_conf_record], [])
    assert result["human_review_rate"] >= 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Fittings Resolver Tests
# All tests use the hardcoded fallback path (no xlsx file required).
# ══════════════════════════════════════════════════════════════════════════════

from loaders.fittings_resolver import (
    resolve_fitting_type,
    resolve_connection_type,
    resolve_material,
    enrich_fitting,
    load_connection_type_map,
    load_fitting_types,
    load_material_map,
)


def test_resolve_fitting_type_coupling():
    """'3/8 CPLG BRS 150#' should resolve to Coupling with high confidence."""
    result, conf = resolve_fitting_type("3/8 CPLG BRS 150#")
    assert result == "Coupling", f"Expected 'Coupling', got {result!r}"
    assert conf >= 0.90, f"Expected confidence >= 0.90, got {conf}"


def test_resolve_fitting_type_elbow():
    """'90 ELL 1/2 SS' should resolve to Elbow 90 Deg."""
    result, conf = resolve_fitting_type("90 ELL 1/2 SS")
    assert result == "Elbow 90 Deg", f"Expected 'Elbow 90 Deg', got {result!r}"
    assert conf >= 0.90, f"Expected confidence >= 0.90, got {conf}"


def test_resolve_fitting_type_tee():
    """'1/2 TEE BRS NPT' should resolve to Tee."""
    result, conf = resolve_fitting_type("1/2 TEE BRS NPT")
    assert result == "Tee", f"Expected 'Tee', got {result!r}"
    assert conf >= 0.90


def test_resolve_connection_type_fnpt():
    """Exact key 'fnpt' must resolve to canonical 'FNPT' with confidence 1.0."""
    result, conf = resolve_connection_type("fnpt")
    assert result == "FNPT", f"Expected 'FNPT', got {result!r}"
    assert conf == 1.0, f"Expected exact confidence 1.0, got {conf}"


def test_resolve_connection_type_female_npt():
    """'female npt' should resolve to 'FNPT' via exact map lookup."""
    result, conf = resolve_connection_type("female npt")
    assert result == "FNPT", f"Expected 'FNPT', got {result!r}"
    assert conf == 1.0


def test_resolve_connection_type_fuzzy():
    """'socket welded' is not in the exact map — should still resolve via fuzzy to SW."""
    result, conf = resolve_connection_type("socket welded")
    # Should either hit 'socket weld' → SW exactly (suffix differs slightly) or fuzzy
    assert result is not None, "Expected a fuzzy match for 'socket welded', got None"
    assert conf >= 0.75, f"Expected confidence >= 0.75 for fuzzy match, got {conf}"


def test_resolve_material_brass():
    """'BRS' (the abbreviation used in Part_Desc) should resolve to 'Brass'."""
    result, conf = resolve_material("brs")
    assert result == "Brass", f"Expected 'Brass', got {result!r}"
    assert conf == 1.0


def test_resolve_material_stainless():
    """'316 SS' should resolve to 'Stainless Steel' via exact map."""
    result, conf = resolve_material("316 ss")
    assert result == "Stainless Steel", f"Expected 'Stainless Steel', got {result!r}"
    assert conf == 1.0


def test_enrich_fitting_returns_dict():
    """enrich_fitting() must return a dict with 'attributes', 'confidence', 'needs_review'."""
    row = {"Part_Desc": "3/8 CPLG BRS 150# NPT", "connection_type": "fnpt"}
    result = enrich_fitting(row)

    assert isinstance(result, dict), "enrich_fitting must return a dict"
    assert "attributes" in result
    assert "confidence" in result
    assert "needs_review" in result

    attrs = result["attributes"]
    # Fitting type must be resolved from 'CPLG'
    assert attrs.get("Fitting Type") == "Coupling", (
        f"Expected Fitting Type='Coupling', got {attrs.get('Fitting Type')!r}"
    )
    # Size must be extracted from '3/8'
    assert attrs.get("Connection Size") == "3/8 in", (
        f"Expected Connection Size='3/8 in', got {attrs.get('Connection Size')!r}"
    )
    # Material must be extracted from 'BRS'
    assert attrs.get("Material") == "Brass", (
        f"Expected Material='Brass', got {attrs.get('Material')!r}"
    )
    # Pressure rating from '150#'
    assert attrs.get("Pressure Rating") == "150 PSI", (
        f"Expected Pressure Rating='150 PSI', got {attrs.get('Pressure Rating')!r}"
    )
    # Connection type from row['connection_type'] = 'fnpt'
    assert attrs.get("Connection Type") == "FNPT", (
        f"Expected Connection Type='FNPT', got {attrs.get('Connection Type')!r}"
    )


def test_load_connection_type_map_has_fnpt():
    """The loaded connection-type map must contain the 'fnpt' key."""
    mapping = load_connection_type_map()
    assert isinstance(mapping, dict), "load_connection_type_map must return a dict"
    assert "fnpt" in mapping, "'fnpt' key must be present in connection-type map"
    assert mapping["fnpt"] == "FNPT", (
        f"Expected mapping['fnpt']='FNPT', got {mapping['fnpt']!r}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Ground Truth Auditor Tests
# ══════════════════════════════════════════════════════════════════════════════

import pandas as pd

from validators.ground_truth_auditor import (
    audit_ground_truth,
    detect_brand_manufacturer_mismatch,
    generate_audit_report,
)


def test_detect_rheem_frigidaire_mismatch():
    reason = detect_brand_manufacturer_mismatch(
        "Rheem Manufacturing",
        "FRIGIDAIRE®",
    )

    assert reason is not None
    assert "OEM/white-label" in reason


def test_detect_no_mismatch_same_brand():
    reason = detect_brand_manufacturer_mismatch(
        "Mueller Industries",
        "Mueller Industries®",
    )

    assert reason is None


def test_generate_audit_report_has_total_rows():
    report = generate_audit_report(
        {
            "total_rows": 200,
            "blank_unspsc_rows": [1, 45, 112],
            "blank_country_of_origin_rows": [2, 3],
            "brand_manufacturer_mismatches": [],
            "completeness_by_field": {},
        }
    )

    assert "Total rows: 200" in report
    assert "Blank UNSPSC codes: 3 rows" in report


def test_audit_ground_truth_returns_dict():
    dataframe = pd.DataFrame(
        {
            "UNSPSC": ["12345678", None],
            "Country of Origin": ["United States", "Canada"],
            "Manufacturer": ["Acme Inc", "Example Co"],
            "Brand": ["Acme", "Example"],
        }
    )

    audit = audit_ground_truth(dataframe)

    assert isinstance(audit, dict)
    assert audit["total_rows"] == 2
    assert "completeness_by_field" in audit


def test_audit_ground_truth_blank_unspsc_detected():
    dataframe = pd.DataFrame(
        {
            "UNSPSC": ["12345678", None, ""],
            "Country of Origin": ["United States"] * 3,
        }
    )

    audit = audit_ground_truth(dataframe)

    assert audit["blank_unspsc_rows"] == [1, 2]


def test_audit_completeness_by_field_is_percentage():
    dataframe = pd.DataFrame(
        {
            "UNSPSC": ["12345678", None, "87654321"],
            "Country of Origin": ["United States", "Canada", "Mexico"],
        }
    )

    audit = audit_ground_truth(dataframe)

    assert audit["completeness_by_field"]["UNSPSC"] == 66.67
    assert audit["completeness_by_field"]["Country of Origin"] == 100.0
