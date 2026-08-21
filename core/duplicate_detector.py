"""
core/duplicate_detector.py — Step 2 of the UniHack pipeline: De-duplication.

Pipeline position: Input analysis → DE-DUPLICATION → taxonomy → attribute
extraction → enrichment → cleansing → description building → digital assets

Decision logic (three tiers, all requiring at least one exact signal):
──────────────────────────────────────────────────────────────────────────
Tier 1 — HARD DUPLICATE (skip pipeline, preserve full record for audit):
    Exact normalized manufacturer + normalized MPN match.
    Semantic similarity alone is NEVER enough to hard-deduplicate.
    Rationale: two different fittings can have near-identical textual
    descriptions ("3/8 in Brass NPT Coupling" vs "1/2 in Brass NPT Coupling").
    Without at least one exact structural signal, merging would destroy data.

Tier 2 — POSSIBLE DUPLICATE (route to human review, continue processing):
    ChromaDB cosine similarity >= DEDUP_SOFT_THRESHOLD (0.85)
    AND at least one exact supporting signal:
      - Matching normalized fitting type + same nominal dimension, OR
      - Matching normalized MPN prefix (first 6 chars), OR
      - Same manufacturer + same product_type + ≥3 identical attribute values.
    Record is processed normally but flagged for human review.

Tier 3 — SIMILARITY ONLY (no action, just note it):
    Cosine similarity >= DEDUP_SOFT_THRESHOLD but NO exact supporting signal.
    The record is processed normally with NO flag.
    Similarity alone never causes deletion or flagging.

Record preservation:
──────────────────────────────────────────────────────────────────────────
Hard duplicates are NEVER silently discarded. A full DuplicateCheckResult is
returned containing duplicate_of_sku, similarity_score, match_reason,
matched_signals, and alternate_evidence preserved from the secondary record.
The pipeline writes an immutable audit-log entry to SQLite reviews table.

All thresholds from core/constants.py.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from .constants import (
    CHROMA_COLLECTION,
    CHROMA_DB_PATH,
    DEDUP_HARD_THRESHOLD,
    DEDUP_SOFT_THRESHOLD,
    K_RAG_NEIGHBORS,
    SOURCE_MERGED_DUPLICATE,
)
from .enricher import build_product_description

logger = logging.getLogger(__name__)


# ── Result type ───────────────────────────────────────────────────────────────


@dataclass
class DuplicateCheckResult:
    """
    Result of a duplicate check.  Never silently discard — always return this.

    Fields
    ------
    is_hard_duplicate : bool
        True only when exact normalized manufacturer + MPN match found.
    is_possible_duplicate : bool
        True when high cosine similarity + at least one exact supporting signal.
    duplicate_of_sku : str | None
        The product_id / part_number of the matched catalog record.
    similarity_score : float
        ChromaDB cosine similarity (0.0 if no ChromaDB match found).
    match_reason : str
        Human-readable explanation of why the decision was made.
    matched_signals : list[str]
        Which exact signals fired (e.g. ["exact_mpn_mfr", "attr_match"]).
    alternate_evidence : dict
        Source URLs, attributes, or evidence from the matched record that
        the incoming record does NOT already have — preserved for human review.
    """

    is_hard_duplicate: bool = False
    is_possible_duplicate: bool = False
    duplicate_of_sku: Optional[str] = None
    similarity_score: float = 0.0
    match_reason: str = "No duplicate detected"
    matched_signals: list = field(default_factory=list)
    alternate_evidence: dict = field(default_factory=dict)


# ── Private helpers ───────────────────────────────────────────────────────────


def _get_embedder():
    """Return the shared MiniLM embedder (same as enricher.py)."""
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer("all-MiniLM-L6-v2")
    except ImportError as exc:
        raise ImportError("pip install sentence-transformers") from exc


def _get_collection():
    """Return the shared ChromaDB collection (same as enricher.py)."""
    try:
        import chromadb

        client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        return client.get_or_create_collection(CHROMA_COLLECTION)
    except ImportError as exc:
        raise ImportError("pip install chromadb") from exc


def _cosine_similarity(a: list, b: list) -> float:
    """Cosine similarity between two embedding vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _norm_mpn(mpn: str) -> str:
    """Normalize MPN: strip spaces, hyphens, underscores, lowercase."""
    return re.sub(r"[\s\-_./]", "", (mpn or "").strip().lower())


def _norm_mfr(mfr: str) -> str:
    """Normalize manufacturer: lowercase, strip legal suffixes & punctuation."""
    s = re.sub(r"\b(inc|llc|ltd|co|corp|industries|company|group)\b\.?", "", (mfr or "").lower())
    return re.sub(r"[^a-z0-9]", "", s).strip()


def _product_id(product: dict) -> str:
    desc = build_product_description(product)
    return str(product.get("product_id") or product.get("part_number") or desc[:32])


def _embed(product: dict) -> list:
    embedder = _get_embedder()
    return embedder.encode(build_product_description(product)).tolist()


def _extract_alternate_evidence(incoming: dict, matched: dict) -> dict:
    """
    Extract fields from matched that incoming does NOT already have.
    Used to preserve alternate manufacturer evidence and source URLs.
    """
    evidence: dict = {}
    # Preserve source URLs from matched record that incoming lacks
    for key in ("source_url", "page_url", "datasheet_url", "image_url"):
        if matched.get(key) and not incoming.get(key):
            evidence[key] = matched[key]
    # Preserve attributes from matched record that incoming lacks
    matched_attrs = matched.get("attributes") or {}
    incoming_attrs = incoming.get("attributes") or {}
    alt_attrs = {k: v for k, v in matched_attrs.items() if v is not None and incoming_attrs.get(k) is None}
    if alt_attrs:
        evidence["alternate_attributes"] = alt_attrs
    # Preserve provenance evidence quotes
    matched_prov = matched.get("provenance") or {}
    if matched_prov.get("source_excerpt"):
        evidence["alternate_source_excerpt"] = matched_prov["source_excerpt"]
    if matched_prov.get("source_document"):
        evidence["alternate_source_document"] = matched_prov["source_document"]
    return evidence


def _matching_attr_count(a: dict, b: dict) -> int:
    """Count attributes with identical non-None values in both dicts."""
    attrs_a = a.get("attributes") or {}
    attrs_b = b.get("attributes") or {}
    return sum(1 for k, v in attrs_a.items() if v is not None and attrs_b.get(k) == v)


def _exact_signals(incoming: dict, matched: dict) -> list:
    """
    Return list of exact structural signals that fire between two products.
    Signals are required alongside cosine similarity to make dedup decisions.
    """
    signals: list[str] = []
    mpn_in = _norm_mpn(incoming.get("part_number") or "")
    mpn_m = _norm_mpn(matched.get("part_number") or "")
    mfr_in = _norm_mfr(incoming.get("manufacturer") or "")
    mfr_m = _norm_mfr(matched.get("manufacturer") or "")

    # Signal 1: exact normalized manufacturer + MPN
    if mpn_in and mfr_in and mpn_in == mpn_m and mfr_in == mfr_m:
        signals.append("exact_mpn_mfr")

    # Signal 2: MPN prefix match (first 6 normalized chars)
    if mpn_in and mpn_m and len(mpn_in) >= 6 and mpn_in[:6] == mpn_m[:6]:
        signals.append("mpn_prefix_match")

    # Signal 3: same product_type + same manufacturer + ≥3 matching attributes
    same_type = (incoming.get("product_type") or "") == (matched.get("product_type") or "")
    same_mfr = mfr_in and mfr_in == mfr_m
    if same_type and same_mfr and _matching_attr_count(incoming, matched) >= 3:
        signals.append("attr_fingerprint_match")

    return signals


# ── Public API ────────────────────────────────────────────────────────────────


def check_duplicate(product: dict) -> DuplicateCheckResult:
    """
    Main entry point: check whether product is a duplicate of any existing
    catalog record in ChromaDB.

    Decision tree:
      1. exact_mpn_mfr signal fires                → HARD DUPLICATE (Tier 1)
      2. sim >= SOFT_THRESHOLD + any exact signal  → POSSIBLE DUPLICATE (Tier 2)
      3. sim >= SOFT_THRESHOLD alone               → no action (Tier 3)
      4. no ChromaDB match or empty collection     → no duplicate
    """
    try:
        collection = _get_collection()
        if collection.count() == 0:
            return DuplicateCheckResult(match_reason="ChromaDB empty — skipping dedup")

        embedding = _embed(product)
        pt = product.get("product_type", "")
        where = {"product_type": pt} if pt else None

        results = collection.query(
            query_embeddings=[embedding],
            n_results=min(K_RAG_NEIGHBORS, collection.count()),
            where=where,
            include=["embeddings", "metadatas"],
        )

        metadatas = (results.get("metadatas") or [[]])[0]
        embeddings_list = (results.get("embeddings") or [[]])[0]

        if not metadatas or not embeddings_list:
            return DuplicateCheckResult(match_reason="No ChromaDB neighbors found")

        # Pick the neighbor with the highest cosine similarity
        best_sim = 0.0
        best_meta = None
        for meta, emb in zip(metadatas, embeddings_list):
            sim = _cosine_similarity(embedding, emb)
            if sim > best_sim:
                best_sim = sim
                best_meta = meta

        if best_meta is None or best_sim < DEDUP_SOFT_THRESHOLD:
            return DuplicateCheckResult(
                similarity_score=best_sim,
                match_reason=f"Similarity {best_sim:.3f} below soft threshold {DEDUP_SOFT_THRESHOLD}",
            )

        # Reconstruct matched product dict from ChromaDB metadata
        matched: dict = json.loads(best_meta.get("attributes_json", "{}"))
        matched["product_id"] = best_meta.get("product_id", "")
        matched["part_number"] = best_meta.get("product_id", "")  # stored as product_id
        matched["product_type"] = best_meta.get("product_type", "")
        matched["manufacturer"] = best_meta.get("manufacturer", "")

        signals = _exact_signals(product, matched)
        alt_evidence = _extract_alternate_evidence(product, matched)
        sku = matched.get("product_id") or matched.get("part_number") or ""

        # ── Tier 1: HARD — exact normalized manufacturer + MPN ────────────────
        if "exact_mpn_mfr" in signals:
            logger.info(
                "HARD DUPLICATE: %s → %s (sim=%.3f, signals=%s)",
                _product_id(product),
                sku,
                best_sim,
                signals,
            )
            return DuplicateCheckResult(
                is_hard_duplicate=True,
                duplicate_of_sku=sku,
                similarity_score=best_sim,
                match_reason=(f"Exact normalized manufacturer + MPN match " f"(sim={best_sim:.3f}, signals={signals})"),
                matched_signals=signals,
                alternate_evidence=alt_evidence,
            )

        # ── Tier 2: POSSIBLE — high sim + at least one exact signal ──────────
        if best_sim >= DEDUP_SOFT_THRESHOLD and signals:
            logger.info(
                "POSSIBLE DUPLICATE: %s → %s (sim=%.3f, signals=%s)",
                _product_id(product),
                sku,
                best_sim,
                signals,
            )
            return DuplicateCheckResult(
                is_possible_duplicate=True,
                duplicate_of_sku=sku,
                similarity_score=best_sim,
                match_reason=(
                    f"High similarity + exact signal(s) {signals} " f"(sim={best_sim:.3f}) — human review required"
                ),
                matched_signals=signals,
                alternate_evidence=alt_evidence,
            )

        # ── Tier 3: similarity alone — no action ──────────────────────────────
        logger.debug(
            "Similarity-only match %s → %s (sim=%.3f) — no signals, no action",
            _product_id(product),
            sku,
            best_sim,
        )
        return DuplicateCheckResult(
            similarity_score=best_sim,
            match_reason=(
                f"Similarity {best_sim:.3f} >= soft threshold but no exact "
                f"structural signals — no action taken (similarity-only never flags)"
            ),
        )

    except Exception as exc:  # noqa: BLE001
        logger.warning("check_duplicate failed: %s", exc)
        return DuplicateCheckResult(match_reason=f"check_duplicate error: {exc}")


# ── Legacy compat wrapper (used by tests + pipeline for is_duplicate calls) ───


def is_duplicate(
    product: dict,
    threshold: float = DEDUP_HARD_THRESHOLD,
) -> tuple:
    """
    Legacy wrapper around check_duplicate() for backward compatibility.
    Returns (is_dup, matched_product_or_None, similarity_score).

    NOTE: Prefer check_duplicate() directly; it returns the full DuplicateCheckResult
    with alternate_evidence and matched_signals for the audit log.
    """
    result = check_duplicate(product)
    if result.is_hard_duplicate:
        return True, {"product_id": result.duplicate_of_sku}, result.similarity_score
    return False, None, result.similarity_score


# ── Batch helpers ─────────────────────────────────────────────────────────────


def find_duplicates(
    products: list,
) -> list:
    """
    Detect duplicate pairs within a list of products (O(n²) pairwise).

    Returns list of (product_a, product_b, similarity_score) tuples where
    exact_mpn_mfr signal fires (Tier 1 only) — not similarity alone.
    """
    if len(products) < 2:
        return []

    duplicates = []
    for i in range(len(products)):
        for j in range(i + 1, len(products)):
            signals = _exact_signals(products[i], products[j])
            if "exact_mpn_mfr" in signals:
                # Compute similarity for the log only
                try:
                    emb_a = _embed(products[i])
                    emb_b = _embed(products[j])
                    score = _cosine_similarity(emb_a, emb_b)
                except Exception:
                    score = 0.0
                duplicates.append((products[i], products[j], score))
                logger.info(
                    "Batch duplicate pair: %s ↔ %s (sim=%.3f)",
                    _product_id(products[i]),
                    _product_id(products[j]),
                    score,
                )
    return duplicates


def merge_duplicate_pair(primary: dict, secondary: dict) -> dict:
    """
    Merge two duplicate product records.

    Rules:
      - Primary field wins on every conflict (non-None primary value kept).
      - Null fields in primary are filled from secondary where secondary has values.
      - Source URLs from secondary are preserved in provenance.alternate_evidence.
      - Field provenance for filled fields is set to SOURCE_MERGED_DUPLICATE.
      - provenance.merged_from = secondary product_id.

    Returns the merged product.
    """
    merged = dict(primary)

    # ── Merge top-level scalar fields ─────────────────────────────────────────
    for key in ("name", "manufacturer", "part_number", "product_type"):
        if not merged.get(key) and secondary.get(key):
            merged[key] = secondary[key]

    # ── Merge attributes ──────────────────────────────────────────────────────
    primary_attrs: dict = dict(merged.get("attributes") or {})
    secondary_attrs: dict = secondary.get("attributes") or {}
    filled_fields: list = []

    for f, sec_val in secondary_attrs.items():
        if primary_attrs.get(f) is None and sec_val is not None:
            primary_attrs[f] = sec_val
            filled_fields.append(f)

    merged["attributes"] = primary_attrs

    # ── Merge provenance ──────────────────────────────────────────────────────
    prov: dict = dict(merged.get("provenance") or {})
    field_sources: dict = dict(prov.get("field_sources") or {})

    for f in filled_fields:
        field_sources[f] = SOURCE_MERGED_DUPLICATE

    secondary_id = _product_id(secondary)
    prov["field_sources"] = field_sources
    prov["merged_from"] = secondary_id

    # Preserve alternate evidence from secondary — source URLs, evidence quotes
    alt_evidence = _extract_alternate_evidence(merged, secondary)
    if alt_evidence:
        prov["alternate_evidence"] = alt_evidence

    merged["provenance"] = prov

    logger.info(
        "Merged duplicate: primary=%s ← secondary=%s (%d fields filled)",
        _product_id(primary),
        secondary_id,
        len(filled_fields),
    )
    return merged


def deduplicate_batch(
    products: list,
) -> tuple:
    """
    Detect and merge Tier-1 duplicates across a batch before pipeline processing.

    Returns (unique_products, duplicate_pairs_log).

    Only exact_mpn_mfr matches cause merging. Similarity alone never merges.
    """
    if not products:
        return [], []

    unique: list = []
    unique_norms: list = []  # (norm_mfr, norm_mpn) for fast O(1) lookup
    dup_log: list = []

    for product in products:
        mpn = _norm_mpn(product.get("part_number") or "")
        mfr = _norm_mfr(product.get("manufacturer") or "")
        norm = (mfr, mpn)

        # Look for exact_mpn_mfr match in already-seen records
        matched_idx = None
        for idx, seen_norm in enumerate(unique_norms):
            if mpn and mfr and seen_norm == norm:
                matched_idx = idx
                break

        if matched_idx is not None:
            # Compute similarity for the log (best-effort)
            try:
                emb_a = _embed(unique[matched_idx])
                emb_b = _embed(product)
                sim = _cosine_similarity(emb_a, emb_b)
            except Exception:
                sim = 0.0

            dup_log.append(
                {
                    "primary_id": _product_id(unique[matched_idx]),
                    "duplicate_id": _product_id(product),
                    "similarity": sim,
                    "signals": ["exact_mpn_mfr"],
                    "alternate_evidence": _extract_alternate_evidence(unique[matched_idx], product),
                }
            )
            unique[matched_idx] = merge_duplicate_pair(unique[matched_idx], product)
        else:
            unique.append(product)
            unique_norms.append(norm)

    logger.info(
        "deduplicate_batch: %d input → %d unique, %d duplicates merged",
        len(products),
        len(unique),
        len(dup_log),
    )
    return unique, dup_log
