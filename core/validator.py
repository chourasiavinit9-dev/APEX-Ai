"""
core/validator.py — Two-layer validation engine.

Layer 1: Rule-based (free, instant, deterministic)
Layer 2: LLM sanity check (Haiku, only flagged records)
All model names and thresholds from core/constants.py.
"""
import json
import re

from .constants import (
    VALIDATION_MODEL,
    CONFIDENCE_REVIEW_THRESHOLD,
)
from .schemas import get_required_fields, get_validation_ranges


def validate(product: dict, client=None) -> dict:
    """Run both validation layers. Populates product['validation']."""
    issues, warnings = _run_rules(product)
    confidence = product.get("provenance", {}).get("confidence", 1.0)
    needs_review = _needs_review(issues, confidence, product)
    product["validation"] = {
        "issues": issues,
        "warnings": warnings,
        "passed_rules": len(issues) == 0,
        "needs_human_review": needs_review,
        "review_reason": _review_reason(issues, confidence, needs_review),
    }
    if needs_review and len(issues) == 0 and client is not None:
        result = _llm_check(product, client)
        product["validation"]["llm_assessment"] = result.get("assessment", "")
        if result.get("issues"):
            product["validation"]["issues"].extend(result["issues"])
            product["validation"]["passed_rules"] = False
    return product


def _run_rules(product: dict) -> tuple[list, list]:
    """Execute all rule-based checks. Returns (issues, warnings)."""
    issues: list[str] = []
    warnings: list[str] = []
    pt = product.get("product_type", "")
    attrs = product.get("attributes", {})
    _check_required(attrs, pt, issues)
    _check_ranges(attrs, pt, issues)
    _check_temp_inversion(attrs, issues)
    _check_bore_outer(attrs, issues)
    confidence = product.get("provenance", {}).get("confidence", 1.0)
    if confidence < 0.5:
        warnings.append(f"Low extraction confidence: {confidence:.2f}")
    return issues, warnings


def _check_required(attrs: dict, product_type: str, issues: list) -> None:
    for field in get_required_fields(product_type):
        if attrs.get(field) is None:
            issues.append(f"Missing required field: '{field}'")


def _check_ranges(attrs: dict, product_type: str, issues: list) -> None:
    for field, (lo, hi) in get_validation_ranges(product_type).items():
        val = attrs.get(field)
        if val is None:
            continue
        try:
            if not (lo <= float(val) <= hi):
                issues.append(f"Out-of-range: '{field}' = {val} (expected {lo}–{hi})")
        except (TypeError, ValueError):
            issues.append(f"Non-numeric value for '{field}': {val!r}")


def _check_temp_inversion(attrs: dict, issues: list) -> None:
    lo, hi = attrs.get("operating_temp_min"), attrs.get("operating_temp_max")
    if lo is not None and hi is not None:
        try:
            if float(lo) >= float(hi):
                issues.append(f"Temperature range invalid: min ({lo}) >= max ({hi})")
        except (TypeError, ValueError):
            pass


def _check_bore_outer(attrs: dict, issues: list) -> None:
    bore = attrs.get("bore_diameter") or attrs.get("bore_diameter_1")
    outer = attrs.get("outer_diameter")
    if bore is not None and outer is not None:
        try:
            if float(bore) >= float(outer):
                issues.append(f"Bore ({bore} mm) must be less than outer diameter ({outer} mm)")
        except (TypeError, ValueError):
            pass


def _needs_review(issues: list, confidence: float, product: dict) -> bool:
    if issues:
        return True
    if confidence < CONFIDENCE_REVIEW_THRESHOLD:
        return True
    if not product.get("provenance", {}).get("field_sources"):
        return True
    return False


def _review_reason(issues: list, confidence: float, needs_review: bool) -> str:
    if not needs_review:
        return "Passed all checks"
    parts = []
    if issues:
        parts.append(f"{len(issues)} validation issue(s)")
    if confidence < CONFIDENCE_REVIEW_THRESHOLD:
        parts.append(f"low confidence ({confidence:.2f})")
    return "; ".join(parts) or "Flagged for review"


def _llm_check(product: dict, client) -> dict:
    """Haiku sanity check for borderline records."""
    summary = json.dumps({
        "product_type": product.get("product_type"),
        "attributes": {k: v for k, v in product.get("attributes", {}).items() if v is not None},
    }, indent=2)
    try:
        response = client.messages.create(
            model=VALIDATION_MODEL,
            max_tokens=300,
            system=(
                "You are an industrial product validator. Check for logical "
                "inconsistencies or implausible values. Reply with JSON only: "
                '{"assessment":"...","issues":[]}'
            ),
            messages=[{"role": "user", "content": f"Validate:\n{summary}"}],
        )
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.content[0].text.strip())
        return json.loads(raw)
    except Exception as e:
        return {"assessment": f"LLM check failed: {e}", "issues": []}
