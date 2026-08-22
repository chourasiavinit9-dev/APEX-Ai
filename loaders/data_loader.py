"""
loaders/data_loader.py — Load all UniHack reference files into memory.

Handles:
  - UniCat_Manufacturer_and_Brand_List.xlsx (27,000+ rows)
  - Unicat_Lov_v1_0_Updated_With_Remarks.xlsx (161,000+ rows)
  - Unilog_Master_UOM_Standards_Abbreviations_and_Terms.xlsx (~500 UOMs)
  - Decimal_Fraction.xlsx (63 entries, side-by-side layout)
  - FAUCETS_LOV.xlsx and Fittings_LOV.xlsx (category-specific)

All files have quirks: merged cells, multi-row headers, side-by-side blocks.
This module isolates all the parsing messiness in one place.
"""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent.parent / "data" / "unihack"

# Placeholder strings that mean "empty field"
PLACEHOLDERS = frozenset({
    "-- unbranded --", "-- no unilog brand --", "-- no dib brand --",
    "--unbranded--", "--no unilog brand--", "--no dib brand--",
    "unbranded", "no brand", "n/a", "na", "none", "",
})


# ── Placeholder filter ────────────────────────────────────────────────────────

def is_placeholder(value: str | None) -> bool:
    """Return True if value is a known placeholder meaning 'empty'."""
    if value is None:
        return True
    return str(value).strip().lower() in PLACEHOLDERS


def clean_brand_fields(row: dict) -> dict:
    """Replace all placeholder brand values with None."""
    brand_fields = ["E1_Brand", "Unilog_Brand", "DIB_Brand", "Part_Manuf"]
    for field in brand_fields:
        if field in row and is_placeholder(row[field]):
            row[field] = None
    return row


# ── Manufacturer / Brand list ─────────────────────────────────────────────────

@lru_cache(maxsize=1)
def load_manufacturer_list() -> pd.DataFrame:
    """
    Load UniCat_Manufacturer_and_Brand_List.xlsx.
    Returns DataFrame with columns:
      MANUFACTURER_NAME, MANUFACTURER_CODE, BRAND_NAME, BRAND_CODE
    """
    path = DATA_DIR / "UniCat_Manufacturer_and_Brand_List.xlsx"
    if not path.exists():
        return pd.DataFrame(columns=["MANUFACTURER_NAME", "MANUFACTURER_CODE",
                                     "BRAND_NAME", "BRAND_CODE"])
    df = pd.read_excel(path, engine="openpyxl")
    df.columns = [c.strip().upper().replace(" ", "_") for c in df.columns]
    # Normalise column names to expected
    col_map = {}
    for col in df.columns:
        if "MANUFACTURER" in col and "NAME" in col:
            col_map[col] = "MANUFACTURER_NAME"
        elif "MANUFACTURER" in col and "CODE" in col:
            col_map[col] = "MANUFACTURER_CODE"
        elif "BRAND" in col and "NAME" in col:
            col_map[col] = "BRAND_NAME"
        elif "BRAND" in col and "CODE" in col:
            col_map[col] = "BRAND_CODE"
    df = df.rename(columns=col_map)
    df = df.dropna(subset=["MANUFACTURER_NAME"])
    return df


# ── LOV (List of Values) ──────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def load_lov() -> pd.DataFrame:
    """
    Load Unicat_Lov_v1_0_Updated_With_Remarks.xlsx (~161,000 rows).
    Columns: Classpath, Leaf_Node, Filtering, Attribute_Label,
             Attribute_Values, Normalized_Label, Normalized_Values,
             Guidelines, Remarks
    """
    path = DATA_DIR / "Unicat_Lov_v1_0_Updated_With_Remarks.xlsx"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_excel(path, engine="openpyxl", dtype=str)
    df.columns = [c.strip().replace(" ", "_").replace("/", "_") for c in df.columns]
    df = df.fillna("")
    return df


def get_lov_for_classpath(classpath: str) -> pd.DataFrame:
    """Return all LOV rows for a given classpath."""
    lov = load_lov()
    if lov.empty or "Classpath" not in lov.columns:
        return pd.DataFrame()
    return lov[lov["Classpath"].str.lower() == classpath.lower()]


def get_valid_values(classpath: str, attribute_label: str) -> list[str]:
    """Return approved values for a specific attribute in a classpath."""
    rows = get_lov_for_classpath(classpath)
    if rows.empty:
        return []
    attr_col = "Attribute_Label" if "Attribute_Label" in rows.columns else ""
    val_col = "Normalized_Values" if "Normalized_Values" in rows.columns else "Attribute_Values"
    if not attr_col or attr_col not in rows.columns:
        return []
    matches = rows[rows[attr_col].str.lower() == attribute_label.lower()]
    if matches.empty:
        return []
    return [v.strip() for v in matches[val_col].tolist() if v.strip()]


def get_all_classpaths() -> list[str]:
    """Return all unique classpaths in the LOV."""
    lov = load_lov()
    if lov.empty or "Classpath" not in lov.columns:
        return []
    return lov["Classpath"].dropna().unique().tolist()


# ── UOM Standards ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def load_uom_standards() -> dict[str, str]:
    """
    Load Unilog_Master_UOM_Standards_Abbreviations_and_Terms.xlsx.
    Sheet 1: approved abbreviations. Notes in stray columns — skip them.
    Returns dict: {variant_form: approved_abbreviation}
    e.g. {"inches": "in", "IN.": "in", "INCHES": "in", "inch": "in"}
    """
    path = DATA_DIR / "Unilog_Master_UOM_Standards_Abbreviations_and_Terms.xlsx"
    if not path.exists():
        return _default_uom_map()
    try:
        df = pd.read_excel(path, sheet_name=0, engine="openpyxl",
                           header=None, dtype=str)
        uom_map: dict[str, str] = {}
        for _, row in df.iterrows():
            cells = [str(c).strip() for c in row if pd.notna(c) and str(c).strip()]
            if len(cells) >= 2:
                approved = cells[0]
                for variant in cells[1:]:
                    if len(variant) < 30:  # skip long notes
                        uom_map[variant.lower()] = approved
                        uom_map[approved.lower()] = approved
        return uom_map if uom_map else _default_uom_map()
    except Exception:
        return _default_uom_map()


def _default_uom_map() -> dict[str, str]:
    """Fallback UOM map from guide examples."""
    return {
        "inches": "in", "inch": "in", "in.": "in", "\"": "in",
        "feet": "ft", "foot": "ft", "ft.": "ft", "'": "ft",
        "pounds": "lb", "lbs": "lb", "lbs.": "lb",
        "ounces": "oz", "oz.": "oz",
        "gallons": "gal", "gal.": "gal",
        "volts": "V", "v": "V", "volt": "V",
        "amps": "A", "amp": "A", "ampere": "A",
        "watts": "W", "watt": "W",
        "degrees fahrenheit": "°F", "°f": "°F", "deg f": "°F",
        "degrees celsius": "°C", "°c": "°C", "deg c": "°C",
        "decibels": "dB", "db": "dB", "dba": "dBA",
        "rpm": "RPM", "r.p.m.": "RPM",
        "psi": "PSI", "p.s.i.": "PSI",
        "gpm": "GPM", "g.p.m.": "GPM",
        "btu": "BTU", "b.t.u.": "BTU",
        "percent": "%", "pct": "%",
        "stainless steel": "SST", "ss": "SST",
    }


# ── Decimal / Fraction lookup ─────────────────────────────────────────────────

@lru_cache(maxsize=1)
def load_fraction_lookup() -> dict[float, str]:
    """
    Load Decimal_Fraction.xlsx — 63 inch conversions.
    Layout: 4 side-by-side pairs of (Fraction | Decimal) columns.
    Returns dict: {decimal_value: fraction_string}
    e.g. {0.5: "1/2", 0.25: "1/4", 0.015625: "1/64"}
    """
    path = DATA_DIR / "Decimal_Fraction.xlsx"
    if not path.exists():
        return _default_fractions()
    try:
        df = pd.read_excel(path, engine="openpyxl", header=None, dtype=str)
        fractions: dict[float, str] = {}
        # File has 4 side-by-side pairs: cols 0-1, 2-3, 4-5, 6-7
        for pair_start in range(0, 8, 2):
            if pair_start + 1 >= len(df.columns):
                break
            for _, row in df.iterrows():
                frac = str(row.iloc[pair_start]).strip()
                dec = str(row.iloc[pair_start + 1]).strip()
                if "/" in frac and _is_numeric(dec):
                    fractions[float(dec)] = frac
        return fractions if fractions else _default_fractions()
    except Exception:
        return _default_fractions()


def _is_numeric(s: str) -> bool:
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def _default_fractions() -> dict[float, str]:
    """Core fractions from the guide examples."""
    return {
        0.5: "1/2", 0.25: "1/4", 0.75: "3/4",
        0.125: "1/8", 0.375: "3/8", 0.625: "5/8", 0.875: "7/8",
        0.0625: "1/16", 0.1875: "3/16", 0.3125: "5/16",
        0.4375: "7/16", 0.5625: "9/16", 0.6875: "11/16",
        0.8125: "13/16", 0.9375: "15/16",
        0.03125: "1/32", 0.015625: "1/64",
    }


# ── Category LOVs (Faucets + Fittings) ───────────────────────────────────────

@lru_cache(maxsize=1)
def load_fittings_connection_map() -> dict[str, str]:
    """
    Load Fittings_LOV.xlsx — 1,472 connection variants → 515 canonical.
    Returns dict: {supplier_variant_lower: canonical_value}
    """
    path = DATA_DIR / "Fittings_LOV.xlsx"
    if not path.exists():
        return {}
    try:
        df = pd.read_excel(path, engine="openpyxl", dtype=str)
        mapping: dict[str, str] = {}
        # Find columns containing variant and canonical values
        for col in df.columns:
            if "variant" in col.lower() or "manufacturer" in col.lower():
                canonical_col = next(
                    (c for c in df.columns if "canonical" in c.lower()), None
                )
                if canonical_col:
                    for _, row in df.iterrows():
                        variant = str(row[col]).strip()
                        canonical = str(row[canonical_col]).strip()
                        if variant and canonical and variant != "nan":
                            mapping[variant.lower()] = canonical
                    break
        return mapping
    except Exception:
        return {}


def get_sample_items(n: int = 200) -> pd.DataFrame:
    """
    Load Unilog-Sample_200_Items-Input-vs-Output.xlsx — Input sheet.
    Returns the raw input rows.
    """
    path = DATA_DIR / "Unilog-Sample_200_Items-Input-vs-Output.xlsx"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_excel(path, sheet_name="Input", engine="openpyxl", dtype=str)


def get_ground_truth() -> pd.DataFrame:
    """
    Load Unilog-Sample_200_Items-Input-vs-Output.xlsx — Delivery Format sheet.
    Returns the ground truth enriched rows (252 columns).
    """
    path = DATA_DIR / "Unilog-Sample_200_Items-Input-vs-Output.xlsx"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_excel(path, sheet_name="Delivery Format",
                         engine="openpyxl", dtype=str)
