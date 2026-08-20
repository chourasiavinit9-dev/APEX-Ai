"""
core/duplicate_finder.py — Detect potential duplicate products.

Combines:
  1. MPN similarity (normalized)
  2. Brand + attribute matching
  3. ChromaDB vector similarity (when available)
"""
from __future__ import annotations

import re
from typing import List, Optional

from core.catalog_db import get_conn


def normalize_mpn(mpn: str) -> str:
    """Normalize MPN for comparison: strip spaces, hyphens, lowercase."""
    if not mpn:
        return ""
    return re.sub(r"[\s\-_./]", "", mpn.strip().lower())


def find_duplicates_in_db(mpn: str, manufacturer: str = "",
                          job_id: Optional[str] = None,
                          exclude_id: Optional[str] = None) -> List[dict]:
    """
    Find potential duplicates in SQLite by normalized MPN.
    Returns list of {product_id, mpn, manufacturer, similarity}
    """
    norm_mpn = normalize_mpn(mpn)
    if not norm_mpn:
        return []

    with get_conn() as conn:
        if job_id:
            rows = conn.execute(
                "SELECT id, mpn, enriched_json FROM products WHERE job_id=?",
                (job_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, mpn, enriched_json FROM products",
            ).fetchall()

    import json
    candidates = []
    for row in rows:
        if exclude_id and row["id"] == exclude_id:
            continue
        row_mpn = normalize_mpn(row["mpn"] or "")
        if not row_mpn:
            continue

        # Exact MPN match
        if row_mpn == norm_mpn:
            enriched = json.loads(row["enriched_json"] or "{}")
            candidates.append({
                "product_id": row["id"],
                "mpn": row["mpn"],
                "manufacturer": enriched.get("manufacturer_name", ""),
                "similarity": 1.0,
                "match_type": "exact_mpn",
            })
            continue

        # Substring / prefix match
        if norm_mpn in row_mpn or row_mpn in norm_mpn:
            enriched = json.loads(row["enriched_json"] or "{}")
            sim = min(len(norm_mpn), len(row_mpn)) / max(len(norm_mpn), len(row_mpn))
            if sim >= 0.7:
                candidates.append({
                    "product_id": row["id"],
                    "mpn": row["mpn"],
                    "manufacturer": enriched.get("manufacturer_name", ""),
                    "similarity": round(sim, 3),
                    "match_type": "partial_mpn",
                })

    # Sort by similarity descending
    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    return candidates[:10]


def find_duplicates_in_chroma(record: dict, top_k: int = 5) -> List[dict]:
    """
    Find similar products via ChromaDB vector search.
    Returns list of {product_id, mpn, similarity, match_type}
    Gracefully returns [] if ChromaDB is not available.
    """
    try:
        from core.enricher import _get_collection, _get_embedder, build_product_description
    except ImportError:
        return []

    try:
        collection = _get_collection()
        embedder = _get_embedder()
    except Exception:
        return []

    # Build query text
    query_text = build_product_description(record)
    if not query_text:
        return []

    try:
        query_embedding = embedder.encode([query_text]).tolist()
        results = collection.query(
            query_embeddings=query_embedding,
            n_results=top_k,
        )

        candidates = []
        if results and results.get("ids"):
            for i, doc_id in enumerate(results["ids"][0]):
                dist = results["distances"][0][i] if results.get("distances") else 0
                similarity = max(0, 1 - dist)  # Convert distance to similarity
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                candidates.append({
                    "product_id": doc_id,
                    "mpn": meta.get("mpn", ""),
                    "manufacturer": meta.get("manufacturer", ""),
                    "similarity": round(similarity, 3),
                    "match_type": "vector_similarity",
                })

        return candidates
    except Exception:
        return []
