"""Audit completeness and potential entity inconsistencies in reference data."""

from typing import Any, Dict, List, Optional

import pandas as pd


def _is_blank(value: Any) -> bool:
    """Return True for null, empty, or whitespace-only values."""
    return pd.isna(value) or not str(value).strip()


def _find_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """Find a DataFrame column using case-insensitive normalized matching."""
    normalized = {
        "".join(char for char in str(column).lower() if char.isalnum()): column
        for column in df.columns
    }

    for candidate in candidates:
        key = "".join(char for char in candidate.lower() if char.isalnum())
        if key in normalized:
            return normalized[key]

    return None


def _words(value: str) -> set:
    """Convert a manufacturer or brand value to normalized comparison words."""
    return {
        word
        for word in "".join(
            char.lower() if char.isalnum() else " " for char in value
        ).split()
        if word
    }


def detect_brand_manufacturer_mismatch(
    manufacturer: str,
    brand: str,
) -> Optional[str]:
    """Return a mismatch explanation when manufacturer and brand conflict."""
    manufacturer = (manufacturer or "").strip()
    brand = (brand or "").strip()

    if not manufacturer or not brand:
        return None

    manufacturer_lower = manufacturer.lower()
    brand_lower = brand.lower()

    if "rheem" in manufacturer_lower and "frigidaire" in brand_lower:
        return (
            "OEM/white-label relationship suspected — Rheem manufactures "
            "FRIGIDAIRE-branded appliances"
        )

    manufacturer_words = _words(manufacturer)
    brand_words = _words(brand)
    shares_words = bool(manufacturer_words.intersection(brand_words))
    contains_other = (
        manufacturer_lower in brand_lower or brand_lower in manufacturer_lower
    )

    if not shares_words and not contains_other:
        return "Possible mismatch — verify against manufacturer documentation"

    return None


def audit_ground_truth(gt_df: pd.DataFrame) -> Dict[str, Any]:
    """Audit a reference dataset for missing fields and entity mismatches."""
    unspsc_column = _find_column(gt_df, ["UNSPSC", "UNSPSC Code"])
    country_column = _find_column(
        gt_df,
        ["Country of Origin", "Country_of_Origin", "Country"],
    )
    manufacturer_column = _find_column(
        gt_df,
        ["Manufacturer", "Part_Manuf", "Manufacturer Name"],
    )
    brand_column = _find_column(
        gt_df,
        ["Brand", "Unilog_Brand", "E1_Brand", "DIB_Brand"],
    )

    blank_unspsc_rows = []
    blank_country_rows = []
    mismatches = []

    for row_index, row in gt_df.iterrows():
        if unspsc_column and _is_blank(row[unspsc_column]):
            blank_unspsc_rows.append(int(row_index))

        if country_column and _is_blank(row[country_column]):
            blank_country_rows.append(int(row_index))

        if manufacturer_column and brand_column:
            manufacturer = row[manufacturer_column]
            brand = row[brand_column]
            reason = detect_brand_manufacturer_mismatch(
                "" if _is_blank(manufacturer) else str(manufacturer),
                "" if _is_blank(brand) else str(brand),
            )

            if reason:
                mismatches.append(
                    {
                        "row": int(row_index),
                        "manufacturer": str(manufacturer),
                        "brand": str(brand),
                        "reason": reason,
                    }
                )

    total_rows = len(gt_df)
    completeness_by_field = {
        str(column): round(
            0.0
            if total_rows == 0
            else ((total_rows - gt_df[column].map(_is_blank).sum()) / total_rows)
            * 100,
            2,
        )
        for column in gt_df.columns
    }

    return {
        "blank_unspsc_rows": blank_unspsc_rows,
        "blank_country_of_origin_rows": blank_country_rows,
        "brand_manufacturer_mismatches": mismatches,
        "total_rows": total_rows,
        "completeness_by_field": completeness_by_field,
    }


def generate_audit_report(audit: Dict[str, Any]) -> str:
    """Create a formatted report for the Reports → Data Quality page."""
    blank_unspsc_rows = audit["blank_unspsc_rows"]
    blank_country_rows = audit["blank_country_of_origin_rows"]
    mismatches = audit["brand_manufacturer_mismatches"]

    unspsc_rows = ", ".join(map(str, blank_unspsc_rows)) or "None"
    lines = [
        "Ground Truth Data Quality Report",
        "─────────────────────────────────",
        f"Total rows: {audit['total_rows']}",
        (
            f"Blank UNSPSC codes: {len(blank_unspsc_rows)} rows "
            f"(rows: {unspsc_rows})"
        ),
        f"Blank country of origin: {len(blank_country_rows)} rows",
        f"Brand/manufacturer mismatches flagged: {len(mismatches)} rows",
    ]

    for mismatch in mismatches:
        lines.append(
            f"Row {mismatch['row']}: {mismatch['manufacturer']} / "
            f"{mismatch['brand']} — {mismatch['reason']}"
        )

    lines.extend(
        [
            "",
            "Note: These gaps exist in the provided reference data,",
            "not in APEX output. They are noted for transparency.",
        ]
    )

    return "\n".join(lines)
