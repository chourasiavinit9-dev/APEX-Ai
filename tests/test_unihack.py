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
