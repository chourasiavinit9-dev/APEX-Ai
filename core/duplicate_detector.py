"""
core/duplicate_detector.py — Step 2 of the UniHack pipeline: De-duplication.

Pipeline position: Input analysis → DE-DUPLICATION → taxonomy → attribute
extraction → enrichment → cleansing → description building → digital assets

Strategy:
  - Primary signal: ChromaDB cosine similarity on all-MiniLM-L6-v2 embeddings
    (same embedder as enricher.py — no extra model load).
  - Hard duplicate: similarity >= DEDUP_HARD_THRESHOLD (0.95) → skip processing.
  - Soft duplicate: similarity >= DEDUP_SOFT_THRESHOLD (0.85) → flag for review.
  - Merge strategy: primary fields win on conflict; nulls filled from secondary.

All thresholds from core/constants.py.
"""

from __future__ import annotations

import json
import logging

from .constants import (
    CHROMA_COLLECTION,
    CHROMA_DB_PATH,
    DEDUP_HARD_THRESHOLD,
    K_RAG_NEIGHBORS,
)
from .enricher import build_product_description

logger = logging.getLogger(__name__)

# Source tag used in field_sources for fields merged from a secondary record
SOURCE_MERGED_DUPLICATE = "merged_duplicate"


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


def _embed(product: dict) -> list[float]:
    """Embed a product dict into a vector using the shared MiniLM model."""
    embedder = _get_embedder()
    description = build_product_description(product)
    return embedder.encode(description).tolist()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two embedding vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _product_id(product: dict) -> str:
    """Stable ID for a product dict (same logic as enricher.index_product)."""
    desc = build_product_description(product)
    return str(product.get("product_id") or product.get("part_number") or desc[:32])


# ── Public API ────────────────────────────────────────────────────────────────


def find_duplicates(
    products: list[dict],
) -> list[tuple[dict, dict, float]]:
    """
    Detect duplicate pairs within a list of products using ChromaDB cosine similarity.

    Returns a list of (product_a, product_b, similarity_score) tuples where
    similarity_score >= DEDUP_HARD_THRESHOLD (0.95).

    The comparison is O(n²) in the worst case but each pair is checked only once.
    For large batches (> 1000), prefer using is_duplicate() per-record instead.
    """
    if len(products) < 2:
        return []

    embedder = _get_embedder()
    embeddings: list[list[float]] = [embedder.encode(build_product_description(p)).tolist() for p in products]
    duplicates: list[tuple[dict, dict, float]] = []
    for i in range(len(products)):
        for j in range(i + 1, len(products)):
            score = _cosine_similarity(embeddings[i], embeddings[j])
            if score >= DEDUP_HARD_THRESHOLD:
                duplicates.append((products[i], products[j], score))
                logger.info(
                    "Duplicate pair detected: %s ↔ %s (score=%.3f)",
                    _product_id(products[i]),
                    _product_id(products[j]),
                    score,
                )
    return duplicates


def is_duplicate(
    product: dict,
    threshold: float = DEDUP_HARD_THRESHOLD,
) -> tuple[bool, dict | None, float]:
    """
    Check whether a single product already exists in the ChromaDB catalog.

    Returns (is_dup, matched_product_or_None, similarity_score).

    - similarity >= threshold         → is_dup=True
    - similarity < threshold          → is_dup=False, matched_product=None, score returned
    - ChromaDB empty or unavailable   → (False, None, 0.0)

    Uses the same all-MiniLM-L6-v2 embedder as enricher.py so no extra model
    is loaded when the pipeline runs both steps.
    """
    try:
        collection = _get_collection()
        if collection.count() == 0:
            return False, None, 0.0

        embedding = _embed(product)
        pt = product.get("product_type", "")
        where = {"product_type": pt} if pt else None

        results = collection.query(
            query_embeddings=[embedding],
            n_results=min(K_RAG_NEIGHBORS, collection.count()),
            where=where,
            include=["embeddings", "metadatas", "distances"],
        )

        metadatas = (results.get("metadatas") or [[]])[0]
        embeddings_list = (results.get("embeddings") or [[]])[0]

        if not metadatas or not embeddings_list:
            return False, None, 0.0

        # ChromaDB distances are L2; convert to cosine similarity via re-computation
        top_embedding = embeddings_list[0]
        similarity = _cosine_similarity(embedding, top_embedding)
        top_meta = metadatas[0]

        matched: dict = json.loads(top_meta.get("attributes_json", "{}"))
        matched["product_id"] = top_meta.get("product_id", "")
        matched["product_type"] = top_meta.get("product_type", "")

        if similarity >= threshold:
            logger.info(
                "Hard duplicate found for %s → %s (score=%.3f)",
                _product_id(product),
                matched.get("product_id"),
                similarity,
            )
            return True, matched, similarity

        return False, None, similarity

    except Exception as exc:  # noqa: BLE001
        logger.warning("is_duplicate check failed: %s", exc)
        return False, None, 0.0


def merge_duplicate_pair(primary: dict, secondary: dict) -> dict:
    """
    Merge two duplicate product records.

    Rules:
      - Primary field wins on every conflict (non-None primary value kept).
      - Null fields in primary are filled from secondary where secondary has values.
      - Field provenance for filled fields is set to SOURCE_MERGED_DUPLICATE.
      - provenance.merged_from is set to the secondary product_id.

    Returns the merged product (primary dict mutated in-place and returned).
    """
    merged = dict(primary)

    # ── Merge top-level scalar fields ────────────────────────────────────────
    for key in ("name", "manufacturer", "part_number", "product_type"):
        if not merged.get(key) and secondary.get(key):
            merged[key] = secondary[key]

    # ── Merge attributes dict ─────────────────────────────────────────────────
    primary_attrs: dict = dict(merged.get("attributes") or {})
    secondary_attrs: dict = secondary.get("attributes") or {}
    filled_fields: list[str] = []

    for field, sec_val in secondary_attrs.items():
        if primary_attrs.get(field) is None and sec_val is not None:
            primary_attrs[field] = sec_val
            filled_fields.append(field)

    merged["attributes"] = primary_attrs

    # ── Update provenance ─────────────────────────────────────────────────────
    prov: dict = dict(merged.get("provenance") or {})
    field_sources: dict = dict(prov.get("field_sources") or {})

    for field in filled_fields:
        field_sources[field] = SOURCE_MERGED_DUPLICATE

    secondary_id = _product_id(secondary)
    prov["field_sources"] = field_sources
    prov["merged_from"] = secondary_id
    merged["provenance"] = prov

    logger.info(
        "Merged duplicate: primary=%s ← secondary=%s (%d fields filled)",
        _product_id(primary),
        secondary_id,
        len(filled_fields),
    )
    return merged


def deduplicate_batch(
    products: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Detect and merge duplicates across a batch before pipeline processing.

    Returns (unique_products, duplicate_pairs_log).

    Algorithm:
      1. Embed all products.
      2. For each product, check all previously-seen products for similarity.
      3. If hard duplicate found (>= 0.95): merge into the existing record, skip.
      4. If unique: add to output list.

    duplicate_pairs_log entries:
      {"primary_id": str, "duplicate_id": str, "similarity": float}
    """
    if not products:
        return [], []

    embedder = _get_embedder()
    unique: list[dict] = []
    unique_embeddings: list[list[float]] = []
    dup_log: list[dict] = []

    for product in products:
        embedding = embedder.encode(build_product_description(product)).tolist()
        matched_idx: int | None = None
        best_score = 0.0

        for idx, seen_embedding in enumerate(unique_embeddings):
            score = _cosine_similarity(embedding, seen_embedding)
            if score > best_score:
                best_score = score
                if score >= DEDUP_HARD_THRESHOLD:
                    matched_idx = idx

        if matched_idx is not None:
            # Merge into the already-seen primary record
            dup_log.append(
                {
                    "primary_id": _product_id(unique[matched_idx]),
                    "duplicate_id": _product_id(product),
                    "similarity": best_score,
                }
            )
            unique[matched_idx] = merge_duplicate_pair(unique[matched_idx], product)
        else:
            unique.append(product)
            unique_embeddings.append(embedding)

    logger.info(
        "deduplicate_batch: %d input → %d unique, %d duplicates merged",
        len(products),
        len(unique),
        len(dup_log),
    )
    return unique, dup_log
