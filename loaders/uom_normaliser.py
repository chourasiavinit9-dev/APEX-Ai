"""
loaders/uom_normaliser.py — Unit of measure normalisation.

Rules from UniHack guide:
  1. Every unit must use the approved Unilog abbreviation
  2. ALWAYS a space between number and unit: "24 in" not "24in"
  3. Decimal inches → fraction: 0.5 → 1/2, 50.25 → 50-1/4
  4. Fractions in compound dimensions use hyphen: "50-1/4 in"

Sources:
  - Unilog_Master_UOM_Standards_Abbreviations_and_Terms.xlsx
  - Decimal_Fraction.xlsx
"""
from __future__ import annotations
import re
from functools import lru_cache

from loaders.data_loader import load_uom_standards, load_fraction_lookup

# Regex: matches "24in", "24IN", "24 inches", "24-inch", "3.5in"
_NUM_UNIT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(inches?|in\.?|foot|feet|ft\.?|lbs?\.?|pounds?|oz\.?|ounces?|"
    r"gal\.?|gallons?|volts?|v|amps?|a|watts?|w|rpm|r\.p\.m\.|"
    r"psi|p\.s\.i\.|gpm|g\.p\.m\.|btu|b\.t\.u\.|"
    r"°[fFcC]|deg\s*[fFcC]|dba?|db|%|pct|"
    r"sq\.?\s*ft|sq\.?\s*in|cu\.?\s*ft|cu\.?\s*in)",
    re.IGNORECASE,
)


def normalise_uom(text: str) -> str:
    """
    Normalise all unit expressions in a text string.
    Applies approved abbreviation and ensures number-space-unit format.
    """
    if not text:
        return text
    result = _NUM_UNIT_RE.sub(_replace_unit, text)
    return result


def normalise_single_value(value: str, unit_hint: str = "") -> str:
    """
    Normalise a single attribute value like "24in", "3.5 inches", "1/2".
    If unit_hint given (e.g. "in"), apply it to bare numbers.
    """
    stripped = str(value).strip()
    # Try direct UOM map lookup first
    uom_map = load_uom_standards()
    if stripped.lower() in uom_map:
        return uom_map[stripped.lower()]
    # Apply regex normalisation
    normalised = normalise_uom(stripped)
    # Apply fraction conversion to any decimal inches
    return _convert_decimal_inches(normalised)


def decimal_to_fraction(decimal: float) -> str | None:
    """
    Convert a decimal inch value to fraction string.
    Returns None if no match in lookup table.
    e.g. 0.5 → "1/2", 0.25 → "1/4"
    """
    fractions = load_fraction_lookup()
    # Try exact match
    if decimal in fractions:
        return fractions[decimal]
    # Try rounded to 6 decimal places
    rounded = round(decimal, 6)
    return fractions.get(rounded)


def format_compound_dimension(whole: int, decimal_part: float,
                               unit: str = "in") -> str:
    """
    Format a compound dimension like 50.25 in → "50-1/4 in".
    e.g. format_compound_dimension(50, 0.25, "in") → "50-1/4 in"
    """
    if decimal_part == 0:
        return f"{whole} {unit}"
    frac = decimal_to_fraction(decimal_part)
    if frac:
        return f"{whole}-{frac} {unit}"
    return f"{whole}.{str(decimal_part).split('.')[-1]} {unit}"


def convert_inch_value(value: str) -> str:
    """
    Full inch value conversion pipeline:
    "50.25" → "50-1/4 in", "0.5" → "1/2 in", "24" → "24 in"
    """
    stripped = str(value).strip().rstrip("in").strip()
    try:
        num = float(stripped)
        whole = int(num)
        decimal_part = round(num - whole, 6)
        if decimal_part == 0:
            return f"{whole} in"
        frac = decimal_to_fraction(decimal_part)
        if frac:
            return f"{whole}-{frac} in" if whole > 0 else f"{frac} in"
        return f"{num} in"
    except (ValueError, TypeError):
        return value


def _replace_unit(match: re.Match) -> str:
    """Regex replacement function — normalise matched number+unit."""
    number = match.group(1)
    raw_unit = match.group(2)
    uom_map = load_uom_standards()
    approved = uom_map.get(raw_unit.lower(), raw_unit)
    # Ensure space between number and unit
    return f"{number} {approved}"


def _convert_decimal_inches(text: str) -> str:
    """
    Find decimal inch values in text and convert to fractions.
    e.g. "50.25 in" → "50-1/4 in"
    """
    pattern = re.compile(r"(\d+\.\d+)\s+in\b", re.IGNORECASE)

    def replace_decimal(m: re.Match) -> str:
        return convert_inch_value(m.group(1)) 

    return pattern.sub(replace_decimal, text)


def normalise_uom_dict(attrs: dict) -> dict:
    """Apply UOM normalisation to all string values in an attribute dict."""
    result = {}
    for key, val in attrs.items():
        if isinstance(val, str):
            result[key] = normalise_single_value(val)
        else:
            result[key] = val
    return result
