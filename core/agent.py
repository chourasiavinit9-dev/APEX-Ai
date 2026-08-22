"""
core/agent.py — Claude tool-use agent loop.

Autonomously decides: extract → web search → catalog query → human flag.
All model names and limits from core/constants.py.
All schemas validated through core/pydantic_schemas.py.
"""
import json

import anthropic

from .constants import EXTRACTION_MODEL, MAX_AGENT_ITERATIONS, SOURCE_INFERRED, SOURCE_WEB_ENRICHED
from .ingest import IngestedDocument
from .pydantic_schemas import AgentToolCallSchema

AGENT_SYSTEM = """\
You are an industrial product data extraction agent. Produce a complete,
accurate, structured product record using the available tools.

Strategy:
1. Always call extract_attributes first.
2. If confidence < 0.5 or 3+ required fields are null → call search_web.
3. If after web search confidence still < 0.7 → call query_catalog.
4. If still < 0.7 on required fields → call request_human_input.
5. Stop when record is complete or tools exhausted.
Never fabricate values. Every attribute must come from a tool result.
"""

AGENT_TOOLS = [
    {
        "name": "extract_attributes",
        "description": "Extract structured product attributes from the document.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_type": {"type": "string",
                                 "enum": ["bearing", "valve", "sensor", "coupling", "fastener", "pump"]},
                "focus_fields": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["product_type"],
        },
    },
    {
        "name": "search_web",
        "description": "Search the web for missing product specifications.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "target_fields": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_catalog",
        "description": "Find similar products in the catalog to fill missing attributes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_description": {"type": "string"},
                "product_type": {"type": "string"},
            },
            "required": ["product_description", "product_type"],
        },
    },
    {
        "name": "request_human_input",
        "description": "Flag record for human review when confidence cannot reach 0.7.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "missing_fields": {"type": "array", "items": {"type": "string"}},
                "current_confidence": {"type": "number"},
            },
            "required": ["reason", "missing_fields", "current_confidence"],
        },
    },
]


def run_agent(doc: IngestedDocument, product_type: str, client: anthropic.Anthropic,
              extractor_fn, web_enrich_fn, catalog_query_fn) -> dict:
    """Run the tool-use agent loop. Returns a finalized product record."""
    messages = [{"role": "user", "content": _initial_message(doc, product_type)}]
    product: dict = {}
    human_review = False
    for _ in range(MAX_AGENT_ITERATIONS):
        response = client.messages.create(
            model=EXTRACTION_MODEL, max_tokens=2000,
            system=AGENT_SYSTEM, tools=AGENT_TOOLS, messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        tool_results, product, human_review = _process_tool_calls(
            response.content, doc, product_type, product,
            extractor_fn, web_enrich_fn, catalog_query_fn,
        )
        messages.append({"role": "user", "content": tool_results})
    if human_review:
        product.setdefault("validation", {})["needs_human_review"] = True
    product["agent_iterations"] = len(messages) // 2
    return product


def _initial_message(doc: IngestedDocument, product_type: str) -> str:
    return (
        f"Product type: {product_type}\nSource: {doc.source_path}\n\n"
        f"Document content:\n{(doc.text or doc.excerpt or '')[:2000]}\n\n"
        "Extract a complete product record using the available tools."
    )


def _process_tool_calls(content, doc, product_type, product,
                        extractor_fn, web_enrich_fn, catalog_query_fn):
    """Process all tool_use blocks in one assistant turn."""
    tool_results = []
    human_review = False
    for block in content:
        if block.type != "tool_use":
            continue
        result, product, flag = _dispatch(
            block.name, block.input, doc, product_type, product,
            extractor_fn, web_enrich_fn, catalog_query_fn,
        )
        if flag:
            human_review = True
        tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                             "content": json.dumps(result)})
    return tool_results, product, human_review


def _dispatch(name, inputs, doc, product_type, product,
              extractor_fn, web_enrich_fn, catalog_query_fn):
    """Route a single tool call. Returns (result, updated_product, human_flag)."""
    try:
        AgentToolCallSchema(tool_name=name, parameters=inputs)
    except Exception as e:
        return {"error": str(e)}, product, False
    if name == "extract_attributes":
        result = extractor_fn(doc, inputs.get("product_type", product_type))
        return result, result, False
    if name == "search_web":
        result = web_enrich_fn(inputs.get("query", ""), inputs.get("target_fields", []), product)
        return result, _apply_web(product, result), False
    if name == "query_catalog":
        result = catalog_query_fn(inputs.get("product_description", ""), inputs.get("product_type", product_type))
        return result, _apply_catalog(product, result), False
    if name == "request_human_input":
        return {"status": "flagged", **inputs}, product, True
    return {"error": f"Unknown tool: {name}"}, product, False


def _apply_web(product: dict, result: dict) -> dict:
    if not result.get("success"):
        return product
    attrs = product.setdefault("attributes", {})
    prov = product.setdefault("provenance", {})
    for field, val in result.get("fields_found", {}).items():
        if attrs.get(field) is None and val is not None:
            attrs[field] = val
            prov.setdefault("field_sources", {})[field] = SOURCE_WEB_ENRICHED
            prov.setdefault("web_enriched_fields", []).append(field)
    return product


def _apply_catalog(product: dict, result: dict) -> dict:
    attrs = product.setdefault("attributes", {})
    prov = product.setdefault("provenance", {})
    for neighbor in result.get("neighbors", []):
        for field, val in neighbor.get("attributes", {}).items():
            if attrs.get(field) is None and val is not None:
                attrs[field] = val
                prov.setdefault("field_sources", {})[field] = SOURCE_INFERRED
    return product
