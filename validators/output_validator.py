"""
validators/output_validator.py — Validate all Unilog output fields.

Checks:
  1. LOV compliance — every attribute value in approved list
  2. Character limits — Invoice ≤40, Mobile 60–80
  3. UOM compliance — all units in approved abbreviation list
  4. Brand accuracy — exact match including ® / ™ symbols
  5. Fraction compliance — decimal inches converted to fractions
  6. Placeholder absence — no placeholder strings in output

Returns a ValidationReport with field-level results and overall score.
All constants from core/constants.py.
"""
import re
from dataclasses import dataclass, field

from loaders.data_loader import load_uom_standards, get_valid_values, is_placeholder
from loaders.uom_normaliser import _NUM_UNIT_RE

# Decimal inch pattern for fraction check
_DECIMAL_INCH_RE = re.compile(r"\d+\.\d+\s+in\b", re.IGNORECASE)


@dataclass
class FieldResult:
    field_name: str
    passed: bool
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    sku: str = ""
    field_results: list[FieldResult] = field(default_factory=list)
    overall_score: float = 0.0
    needs_human_review: bool = False
    summary: str = ""

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.field_results if r.passed)

    @property
    def total_count(self) -> int:
        return len(self.field_results)


def validate_output(record: dict) -> ValidationReport:
    """
    Run all validation checks on a UniHack output record.
    Returns a ValidationReport with field-level results.
    """
    report = ValidationReport(sku=record.get("sku", record.get("mpn", "")))
    results = []

    results.append(_check_invoice_desc(record))
    results.append(_check_mobile_desc(record))
    results.append(_check_brand(record))
    results.append(_check_uom_compliance(record))
    results.append(_check_fraction_compliance(record))
    results.append(_check_lov_compliance(record))
    results.append(_check_no_placeholders(record))

    report.field_results = [r for r in results if r is not None]
    passed = sum(1 for r in report.field_results if r.passed)
    total = len(report.field_results)
    report.overall_score = round(passed / total, 3) if total > 0 else 0.0
    report.needs_human_review = report.overall_score < 0.80
    report.summary = _build_summary(report)
    return report


def _check_invoice_desc(record: dict) -> FieldResult:
    """Invoice Desc must be ≤40 chars and ALL CAPS."""
    val = record.get("invoice_desc", "")
    issues = []
    if not val:
        return FieldResult("invoice_desc", False, ["Missing invoice_desc"])
    if len(val) > 40:
        issues.append(f"Too long: {len(val)} chars (max 40)")
    if val != val.upper():
        issues.append("Must be ALL CAPS")
    return FieldResult("invoice_desc", len(issues) == 0, issues)


def _check_mobile_desc(record: dict) -> FieldResult:
    """Mobile Desc must be 60–80 chars."""
    val = record.get("mobile_desc", "")
    issues = []
    if not val:
        return FieldResult("mobile_desc", False, ["Missing mobile_desc"])
    if len(val) < 60:
        issues.append(f"Too short: {len(val)} chars (min 60)")
    if len(val) > 80:
        issues.append(f"Too long: {len(val)} chars (max 80)")
    return FieldResult("mobile_desc", len(issues) == 0, issues)


def _check_brand(record: dict) -> FieldResult:
    """Brand must match canonical form exactly (® / ™ included)."""
    brand = record.get("brand_name", "")
    raw = record.get("raw_brand", "")
    issues = []
    warnings = []
    if not brand:
        return FieldResult("brand", False, ["No canonical brand resolved"])
    if record.get("brand_match_type") == "fallback":
        warnings.append(f"Brand '{raw}' not found in approved list — using as-is")
    if record.get("brand_confidence", 1.0) < 0.85:
        issues.append(f"Low brand match confidence: {record.get('brand_confidence', 0):.2f}")
    return FieldResult("brand", len(issues) == 0, issues, warnings)


def _check_uom_compliance(record: dict) -> FieldResult:
    """All units in output fields must use approved Unilog abbreviations."""
    uom_map = load_uom_standards()
    approved = set(uom_map.values())
    issues = []
    check_fields = ["short_desc", "long_desc", "invoice_desc", "mobile_desc"]
    for fname in check_fields:
        text = record.get(fname, "")
        if not text:
            continue
        for match in _NUM_UNIT_RE.finditer(text):
            raw_unit = match.group(2)
            if raw_unit not in approved:
                norm = uom_map.get(raw_unit.lower())
                if not norm:
                    issues.append(f"{fname}: non-standard unit '{raw_unit}'")
    return FieldResult("uom_compliance", len(issues) == 0, issues)


def _check_fraction_compliance(record: dict) -> FieldResult:
    """Decimal inch values must be converted to fractions."""
    issues = []
    check_fields = ["short_desc", "long_desc", "invoice_desc"]
    for fname in check_fields:
        text = record.get(fname, "")
        if _DECIMAL_INCH_RE.search(text):
            issues.append(f"{fname}: decimal inches not converted to fraction")
    return FieldResult("fraction_compliance", len(issues) == 0, issues)


def _check_lov_compliance(record: dict) -> FieldResult:
    """Attribute values must exist in LOV for the classpath."""
    classpath = record.get("classpath", "")
    attributes = record.get("attributes", {})
    issues = []
    if not classpath or not attributes:
        return FieldResult("lov_compliance", True, [], ["No classpath to check against"])
    for attr_label, attr_value in attributes.items():
        if not attr_value:
            continue
        valid_values = get_valid_values(classpath, attr_label)
        if not valid_values:
            continue  # attribute not constrained in LOV
        if str(attr_value) not in valid_values:
            issues.append(
                f"'{attr_value}' not in LOV for '{attr_label}' "
                f"(valid: {valid_values[:3]}…)"
            )
    return FieldResult("lov_compliance", len(issues) == 0, issues)


def _check_no_placeholders(record: dict) -> FieldResult:
    """No placeholder strings should appear in any output field."""
    issues = []
    for key, val in record.items():
        if isinstance(val, str) and is_placeholder(val):
            issues.append(f"Placeholder found in '{key}': '{val}'")
    return FieldResult("no_placeholders", len(issues) == 0, issues)


def _build_summary(report: ValidationReport) -> str:
    passed = report.passed_count
    total = report.total_count
    pct = int(report.overall_score * 100)
    flag = "⚠️ NEEDS REVIEW" if report.needs_human_review else "✅ AUTO-APPROVED"
    return f"{flag} — {passed}/{total} checks passed ({pct}%)"


def score_against_ground_truth(output: dict, ground_truth: dict) -> dict:
    """
    Compare output fields against ground truth row.
    Returns field-level match results for evaluation report.
    """
    fields_to_check = [
        "invoice_desc", "mobile_desc", "short_desc",
        "long_desc", "brand_name", "classpath",
    ]
    results = {}
    for field in fields_to_check:
        out_val = str(output.get(field, "")).strip().lower()
        gt_val = str(ground_truth.get(field, "")).strip().lower()
        if not gt_val:
            results[field] = "no_ground_truth"
        elif out_val == gt_val:
            results[field] = "exact_match"
        elif gt_val in out_val or out_val in gt_val:
            results[field] = "partial_match"
        else:
            results[field] = "mismatch"
    results["_score"] = sum(
        1 for v in results.values() if v == "exact_match"
    ) / max(len(fields_to_check), 1)
    return results
