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


def _check_mpn_match(row: dict, norm_mpn: str, exclude_id: Optional[str]) -> Optional[dict]:
    """Check a single SQLite row for exact or partial MPN match."""
    if exclude_id and row["id"] == exclude_id:
        return None
    row_mpn = normalize_mpn(row["mpn"] or "")
    if not row_mpn:
        return None
    import json
    enriched = json.loads(row["enriched_json"] or "{}")
    mfr = enriched.get("manufacturer_name", "")
    if row_mpn == norm_mpn:
        return {"product_id": row["id"], "mpn": row["mpn"], "manufacturer": mfr, "similarity": 1.0, "match_type": "exact_mpn"}
    if norm_mpn in row_mpn or row_mpn in norm_mpn:
        sim = min(len(norm_mpn), len(row_mpn)) / max(len(norm_mpn), len(row_mpn))
        if sim >= 0.7:
            return {"product_id": row["id"], "mpn": row["mpn"], "manufacturer": mfr, "similarity": round(sim, 3), "match_type": "partial_mpn"}
    return None


def find_duplicates_in_db(mpn: str, manufacturer: str = "",
                          job_id: Optional[str] = None,
                          exclude_id: Optional[str] = None) -> List[dict]:
    """Find potential duplicates in SQLite by normalized MPN."""
    norm_mpn = normalize_mpn(mpn)
    if not norm_mpn:
        return []
    with get_conn() as conn:
        q = "SELECT id, mpn, enriched_json FROM products WHERE job_id=?" if job_id else "SELECT id, mpn, enriched_json FROM products"
        params = (job_id,) if job_id else ()
        rows = conn.execute(q, params).fetchall()
    candidates = [cand for row in rows if (cand := _check_mpn_match(row, norm_mpn, exclude_id))]
    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    return candidates[:10]


def _parse_chroma_results(results: dict) -> List[dict]:
    """Parse chroma query results dictionary into candidate list."""
    candidates = []
    if results and results.get("ids"):
        for i, doc_id in enumerate(results["ids"][0]):
            dist = results["distances"][0][i] if results.get("distances") else 0
            similarity = max(0, 1 - dist)
            meta = results["metadatas"][0][i] if results.get("metadatas") else {}
            candidates.append({
                "product_id": doc_id,
                "mpn": meta.get("mpn", ""),
                "manufacturer": meta.get("manufacturer", ""),
                "similarity": round(similarity, 3),
                "match_type": "vector_similarity",
            })
    return candidates


def find_duplicates_in_chroma(record: dict, top_k: int = 5) -> List[dict]:
    """Find similar products via ChromaDB vector search."""
    try:
        from core.enricher import _get_collection, _get_embedder, build_product_description
        collection, embedder = _get_collection(), _get_embedder()
        query_text = build_product_description(record)
        if not query_text:
            return []
        query_embedding = embedder.encode([query_text]).tolist()
        results = collection.query(query_embeddings=query_embedding, n_results=top_k)
        return _parse_chroma_results(results)
    except Exception:
        return []
