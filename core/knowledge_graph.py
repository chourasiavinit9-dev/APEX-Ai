"""
core/knowledge_graph.py — Product relationship graph (Gap 2 fix).

Uses NetworkX to store and query:
  - Compatibility links: "bearing 6205 is compatible_with coupling SK45"
  - Manufacturer aliases: "FAG 6205 same_as SKF 6205-2Z"
  - Standard equivalences: "product meets_standard ISO 15:2017"
  - Replacement chains: "product_A replaces product_B (discontinued)"

Persists to data/knowledge_graph.json between runs.
"""
import json
from pathlib import Path
from typing import Optional

from .constants import (
    KNOWLEDGE_GRAPH_PATH,
    EDGE_COMPATIBLE, EDGE_REPLACES, EDGE_SAME_AS, EDGE_STANDARD,
)
from .pydantic_schemas import KnowledgeGraphNodeSchema, KnowledgeGraphEdgeSchema


def _load_nx():
    try:
        import networkx as nx
        return nx
    except ImportError:
        raise ImportError("pip install networkx")


def _graph_path() -> Path:
    return Path(KNOWLEDGE_GRAPH_PATH)


def load_graph():
    """Load persisted graph or create empty one."""
    nx = _load_nx()
    path = _graph_path()
    if path.exists():
        data = json.loads(path.read_text())
        return nx.node_link_graph(data)
    return nx.DiGraph()


def save_graph(graph) -> None:
    """Persist graph to JSON."""
    nx = _load_nx()
    path = _graph_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = nx.node_link_data(graph)
    path.write_text(json.dumps(data, indent=2))


def add_product_node(graph, product: dict) -> str:
    """Add or update a product node. Returns node_id."""
    pid = (
        product.get("product_id")
        or product.get("part_number")
        or f"{product.get('product_type','?')}_{id(product)}"
    )
    node = KnowledgeGraphNodeSchema(
        node_id=pid,
        product_type=product.get("product_type", ""),
        name=product.get("name"),
        part_number=product.get("part_number"),
        manufacturer=product.get("manufacturer"),
        attributes_summary={
            k: v for k, v in product.get("attributes", {}).items()
            if v is not None
        },
    )
    graph.add_node(pid, **node.model_dump())
    return pid


def add_compatibility(graph, source_id: str, target_id: str, confidence: float = 1.0) -> None:
    """Record that source product is compatible with target."""
    edge = KnowledgeGraphEdgeSchema(
        source_id=source_id,
        target_id=target_id,
        edge_type=EDGE_COMPATIBLE,
        confidence=confidence,
    )
    graph.add_edge(source_id, target_id, **edge.model_dump())


def add_alias(graph, source_id: str, alias_id: str, manufacturer: Optional[str] = None) -> None:
    """Record that two product IDs refer to the same product."""
    meta = {"manufacturer": manufacturer} if manufacturer else {}
    edge = KnowledgeGraphEdgeSchema(
        source_id=source_id,
        target_id=alias_id,
        edge_type=EDGE_SAME_AS,
        confidence=0.95,
        metadata=meta,
    )
    graph.add_edge(source_id, alias_id, **edge.model_dump())
    graph.add_edge(alias_id, source_id, **edge.model_dump())


def add_replacement(graph, old_id: str, new_id: str) -> None:
    """Record that new_id replaces old_id (discontinued)."""
    edge = KnowledgeGraphEdgeSchema(
        source_id=new_id,
        target_id=old_id,
        edge_type=EDGE_REPLACES,
        confidence=1.0,
    )
    graph.add_edge(new_id, old_id, **edge.model_dump())


def add_standard(graph, product_id: str, standard: str) -> None:
    """Record that a product meets a standard."""
    if not graph.has_node(standard):
        graph.add_node(standard, node_type="standard", name=standard)
    edge = KnowledgeGraphEdgeSchema(
        source_id=product_id,
        target_id=standard,
        edge_type=EDGE_STANDARD,
        confidence=1.0,
    )
    graph.add_edge(product_id, standard, **edge.model_dump())


def get_compatible_products(graph, product_id: str) -> list[dict]:
    """Return all products compatible with the given product."""
    if not graph.has_node(product_id):
        return []
    compatible = []
    for _, target, data in graph.out_edges(product_id, data=True):
        if data.get("edge_type") == EDGE_COMPATIBLE:
            node_data = graph.nodes.get(target, {})
            compatible.append({
                "product_id": target,
                "confidence": data.get("confidence", 1.0),
                **node_data,
            })
    return compatible


def get_aliases(graph, product_id: str) -> list[str]:
    """Return known alternate part numbers for this product."""
    if not graph.has_node(product_id):
        return []
    return [
        target for _, target, data in graph.out_edges(product_id, data=True)
        if data.get("edge_type") == EDGE_SAME_AS
    ]


def get_replacement(graph, product_id: str) -> Optional[str]:
    """Return the replacement product ID if this product is discontinued."""
    if not graph.has_node(product_id):
        return None
    for source, _, data in graph.in_edges(product_id, data=True):
        if data.get("edge_type") == EDGE_REPLACES:
            return source
    return None


def get_products_by_standard(graph, standard: str) -> list[str]:
    """Return all product IDs that meet a given standard."""
    if not graph.has_node(standard):
        return []
    return [
        source for source, _, data in graph.in_edges(standard, data=True)
        if data.get("edge_type") == EDGE_STANDARD
    ]


def index_product_in_graph(graph, product: dict) -> str:
    """Index a product and auto-detect relationships from its attributes."""
    pid = add_product_node(graph, product)

    # Auto-add standard edges from certifications
    certs = product.get("attributes", {}).get("certifications") or []
    standards = product.get("attributes", {}).get("compatible_standards") or []
    for std in list(certs) + list(standards):
        if std:
            add_standard(graph, pid, std)

    return pid


def graph_stats(graph) -> dict:
    """Return summary statistics for the knowledge graph."""
    _load_nx()
    edge_types: dict = {}
    for _, _, data in graph.edges(data=True):
        et = data.get("edge_type", "unknown")
        edge_types[et] = edge_types.get(et, 0) + 1

    return {
        "total_nodes": graph.number_of_nodes(),
        "total_edges": graph.number_of_edges(),
        "edge_types": edge_types,
        "product_nodes": sum(
            1 for _, d in graph.nodes(data=True)
            if d.get("node_type") != "standard"
        ),
        "standard_nodes": sum(
            1 for _, d in graph.nodes(data=True)
            if d.get("node_type") == "standard"
        ),
    }
