"""
core/enricher.py — RAG enrichment using ChromaDB + MiniLM.

Fix 2 (HIGH): Query knowledge graph during enrichment for compatible products.
All thresholds from core/constants.py.
"""
import json
import statistics
from pathlib import Path

from .constants import (
    CHROMA_DB_PATH,
    CHROMA_COLLECTION,
    K_RAG_NEIGHBORS,
    CONFIDENCE_INFER_PENALTY,
    SOURCE_INFERRED,
)


def _get_embedder():
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer("all-MiniLM-L6-v2")
    except ImportError:
        raise ImportError("pip install sentence-transformers")


def _get_collection():
    try:
        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        return client.get_or_create_collection(CHROMA_COLLECTION)
    except ImportError:
        raise ImportError("pip install chromadb")


def build_product_description(product: dict) -> str:
    """Build a searchable text string from a product dict."""
    parts = [
        product.get("name") or product.get("product_type", ""),
        product.get("manufacturer") or "",
        f"type: {product.get('product_type', '')}",
    ]
    for key, val in product.get("attributes", {}).items():
        if val is not None:
            text = ", ".join(str(v) for v in val) if isinstance(val, list) else str(val)
            parts.append(f"{key}: {text}")
    return " | ".join(p for p in parts if p)


def index_product(product: dict) -> None:
    """Add an approved product to the ChromaDB catalog."""
    collection = _get_collection()
    embedder = _get_embedder()
    description = build_product_description(product)
    embedding = embedder.encode(description).tolist()
    pid = str(product.get("product_id") or product.get("part_number") or description[:32])
    collection.upsert(
        ids=[pid],
        embeddings=[embedding],
        documents=[description],
        metadatas=[{
            "product_type": product.get("product_type", ""),
            "attributes_json": json.dumps(product.get("attributes", {})),
            "product_id": pid,
        }],
    )


def enrich(product: dict) -> dict:
    """Fill null attributes from RAG neighbors + knowledge graph compatible products."""
    null_fields = [k for k, v in product.get("attributes", {}).items() if v is None]
    if not null_fields:
        return product
    neighbors = _fetch_rag_neighbors(product)
    kg_neighbors = _fetch_kg_neighbors(product)
    all_neighbors = neighbors + kg_neighbors
    if not all_neighbors:
        return product
    return _fill_nulls(product, all_neighbors, null_fields)


def _fetch_rag_neighbors(product: dict) -> list[dict]:
    """Query ChromaDB for similar products by embedding similarity."""
    try:
        collection = _get_collection()
        embedder = _get_embedder()
        if collection.count() == 0:
            return []
        embedding = embedder.encode(build_product_description(product)).tolist()
        pt = product.get("product_type", "")
        where = {"product_type": pt} if pt else None
        results = collection.query(
            query_embeddings=[embedding],
            n_results=min(K_RAG_NEIGHBORS, collection.count()),
            where=where,
        )
        return [
            json.loads(m.get("attributes_json", "{}"))
            for m in (results.get("metadatas") or [[]])[0]
        ]
    except Exception:
        return []


def _fetch_kg_neighbors(product: dict) -> list[dict]:
    """Fix 2: Query knowledge graph for compatible products' attributes."""
    try:
        from .knowledge_graph import load_graph, get_compatible_products
        graph = load_graph()
        pid = str(product.get("product_id") or product.get("part_number") or "")
        if not pid or not graph.has_node(pid):
            return []
        compatible = get_compatible_products(graph, pid)
        attrs_list = []
        for c in compatible[:3]:
            raw = c.get("attributes_summary", {})
            if raw:
                attrs_list.append(raw)
        return attrs_list
    except Exception:
        return []


def _fill_nulls(product: dict, neighbors: list[dict], null_fields: list[str]) -> dict:
    """Fill null fields from neighbor majority values."""
    inferred = 0
    for field in null_fields:
        values = [n[field] for n in neighbors if n.get(field) is not None]
        value = _majority_value(values)
        if value is not None:
            product["attributes"][field] = value
            product["provenance"]["field_sources"][field] = SOURCE_INFERRED
            inferred += 1
    if inferred:
        penalty = CONFIDENCE_INFER_PENALTY * inferred
        conf = product["provenance"].get("confidence", 0.5)
        product["provenance"]["confidence"] = max(0.0, conf - penalty)
        product["provenance"]["enriched_fields_count"] = inferred
    return product


def _majority_value(values: list):
    """Return median for numerics, most-common for strings."""
    if not values:
        return None
    try:
        numeric = [float(v) for v in values if v is not None]
        if len(numeric) >= 2:
            return round(statistics.median(numeric), 4)
        if len(numeric) == 1:
            return numeric[0]
    except (TypeError, ValueError):
        pass
    from collections import Counter
    hashable = [tuple(v) if isinstance(v, list) else v for v in values
                if isinstance(v, (str, int, float, bool, list))]
    if not hashable:
        return None
    most_common, _ = Counter(hashable).most_common(1)[0]
    return list(most_common) if isinstance(most_common, tuple) else most_common
