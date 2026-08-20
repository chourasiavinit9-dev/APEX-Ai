"""
loaders/manufacturer_normaliser.py — Normalise messy brand strings.

Strategy:
  1. Exact match (case-insensitive, strip whitespace)
  2. Fuzzy match via RapidFuzz (threshold 85)
  3. Fallback: return cleaned input with low confidence

Returns canonical MANUFACTURER_NAME with exact casing, ® / ™ symbols,
and correct legal suffix (Inc / LLC / Ltd).
All constants from core/constants.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import pandas as pd

from loaders.data_loader import load_manufacturer_list, is_placeholder

FUZZY_THRESHOLD = 85  # minimum RapidFuzz score (0–100)
EXACT_SCORE = 1.0
FUZZY_SCORE_SCALE = 100.0


@dataclass
class BrandMatch:
    manufacturer_name: str       # canonical, e.g. "FRIGIDAIRE®"
    manufacturer_code: str
    brand_name: str              # canonical brand, e.g. "FRIGIDAIRE®"
    brand_code: str
    confidence: float            # 0.0–1.0
    match_type: str              # "exact" | "fuzzy" | "fallback"


def normalise_manufacturer(raw: str | None) -> BrandMatch:
    """
    Normalise a raw manufacturer/brand string to canonical form.
    Returns BrandMatch with confidence score.
    """
    if is_placeholder(raw):
        return _fallback("", "unknown", 0.0)

    cleaned = _clean_input(raw)
    df = _get_indexed_list()

    if df.empty:
        return _fallback(cleaned, "no_list", 0.3)

    # Step 1: exact match (case-insensitive)
    exact = _exact_match(cleaned, df)
    if exact:
        return exact

    # Step 2: fuzzy match
    fuzzy = _fuzzy_match(cleaned, df)
    if fuzzy:
        return fuzzy

    # Step 3: fallback
    return _fallback(cleaned, "fallback", 0.2)


def normalise_from_row(row: dict) -> BrandMatch:
    """
    Try all brand fields in priority order:
    Unilog_Brand > E1_Brand > Part_Manuf > DIB_Brand
    Return first successful match above threshold.
    """
    priority = ["Unilog_Brand", "E1_Brand", "Part_Manuf", "DIB_Brand"]
    best: BrandMatch | None = None
    for field in priority:
        raw = row.get(field)
        if is_placeholder(raw):
            continue
        match = normalise_manufacturer(raw)
        if match.confidence >= 0.8:
            return match
        if best is None or match.confidence > best.confidence:
            best = match
    return best or _fallback("", "no_brand", 0.0)


def _clean_input(raw: str) -> str:
    """Strip whitespace, remove common noise, lowercase for matching."""
    cleaned = str(raw).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


@lru_cache(maxsize=1)
def _get_indexed_list() -> pd.DataFrame:
    """Load and cache manufacturer list with lowercase key column."""
    df = load_manufacturer_list()
    if df.empty:
        return df
    if "MANUFACTURER_NAME" in df.columns:
        df["_key"] = df["MANUFACTURER_NAME"].str.lower().str.strip()
    return df


def _exact_match(cleaned: str, df: pd.DataFrame) -> BrandMatch | None:
    """Case-insensitive exact match."""
    key = cleaned.lower()
    matches = df[df["_key"] == key]
    if matches.empty:
        return None
    row = matches.iloc[0]
    return BrandMatch(
        manufacturer_name=str(row.get("MANUFACTURER_NAME", cleaned)),
        manufacturer_code=str(row.get("MANUFACTURER_CODE", "")),
        brand_name=str(row.get("BRAND_NAME", "")),
        brand_code=str(row.get("BRAND_CODE", "")),
        confidence=EXACT_SCORE,
        match_type="exact",
    )


def _fuzzy_match(cleaned: str, df: pd.DataFrame) -> BrandMatch | None:
    """RapidFuzz token_sort_ratio matching against manufacturer names."""
    try:
        from rapidfuzz import process, fuzz
        choices = df["_key"].tolist()
        result = process.extractOne(
            cleaned.lower(),
            choices,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=FUZZY_THRESHOLD,
        )
        if result is None:
            return None
        _, score, idx = result
        row = df.iloc[idx]
        return BrandMatch(
            manufacturer_name=str(row.get("MANUFACTURER_NAME", cleaned)),
            manufacturer_code=str(row.get("MANUFACTURER_CODE", "")),
            brand_name=str(row.get("BRAND_NAME", "")),
            brand_code=str(row.get("BRAND_CODE", "")),
            confidence=round(score / FUZZY_SCORE_SCALE, 3),
            match_type="fuzzy",
        )
    except ImportError:
        return _simple_contains_match(cleaned, df)


def _simple_contains_match(cleaned: str, df: pd.DataFrame) -> BrandMatch | None:
    """Fallback when RapidFuzz not installed — substring match."""
    key = cleaned.lower()
    matches = df[df["_key"].str.contains(key[:6], na=False, regex=False)]
    if matches.empty:
        return None
    row = matches.iloc[0]
    return BrandMatch(
        manufacturer_name=str(row.get("MANUFACTURER_NAME", cleaned)),
        manufacturer_code=str(row.get("MANUFACTURER_CODE", "")),
        brand_name=str(row.get("BRAND_NAME", "")),
        brand_code=str(row.get("BRAND_CODE", "")),
        confidence=0.6,
        match_type="fuzzy",
    )


def _fallback(cleaned: str, reason: str, confidence: float) -> BrandMatch:
    """Return cleaned input as-is with low confidence."""
    return BrandMatch(
        manufacturer_name=cleaned,
        manufacturer_code="",
        brand_name=cleaned,
        brand_code="",
        confidence=confidence,
        match_type="fallback",
    )
