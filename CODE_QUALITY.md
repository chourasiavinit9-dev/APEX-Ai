# Code Quality & Architecture Specification

> APEX — AI-Powered Product Intelligence for Industrial Commerce
> Hack2Skill Submission

---

## 1. Architectural Principles

### System Boundaries

```
┌─────────────────────────────────────────────────────────────────────┐
│  CLIENT SURFACE (Streamlit UI)                                      │
│  • File upload, text paste, URL input                               │
│  • Review queue with source/extracted side-by-side                  │
│  • Approve / correct / reject controls                              │
│  • Export to JSON-LD, CSV, raw JSON                                 │
└─────────────────────┬───────────────────────────────────────────────┘
                      │ IngestedDocument
┌─────────────────────▼───────────────────────────────────────────────┐
│  AGENT LAYER (core/agent.py)                                        │
│  • Claude tool-use agent with 4 tools:                              │
│    - extract_attributes   → calls extractor.py                      │
│    - search_web           → Anthropic web_search tool               │
│    - query_catalog        → queries ChromaDB                        │
│    - request_human_input  → flags for review queue                  │
│  • Autonomous decision: "do I have enough to produce a record?"     │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
        ┌─────────────┼──────────────┬───────────────┐
        ▼             ▼              ▼               ▼
┌──────────────┐ ┌─────────────┐ ┌──────────┐ ┌────────────────┐
│ EXTRACTOR    │ │ ENRICHER    │ │VALIDATOR │ │ KNOWLEDGE GRAPH│
│ Claude Sonnet│ │ ChromaDB +  │ │ Rules +  │ │ NetworkX       │
│ Schema JSON  │ │ MiniLM RAG  │ │ LLM check│ │ Compatibility  │
│ Evidence tags│ │ Web fallback│ │ Pydantic │ │ Aliases, Stds  │
└──────────────┘ └─────────────┘ └──────────┘ └────────────────┘
        │                │              │               │
        └────────────────┴──────────────┴───────────────┘
                                        │
┌───────────────────────────────────────▼───────────────────────────┐
│  EXPORT LAYER (core/exporter.py)                                   │
│  • JSON-LD (schema.org/Product)                                    │
│  • CSV (flat, catalog-ready)                                       │
│  • Raw APEX JSON (full provenance)                                 │
└────────────────────────────────────────────────────────────────────┘
```

---

## 2. Domain-Named Schema Validation

All schemas are named after the problem domain — not generic `InputSchema` / `OutputSchema`.
Every schema lives in `core/schemas.py` as the single source of truth.

```python
# core/schemas.py — ALL Pydantic schemas live here, nowhere else

class BearingAttributeSchema(BaseModel):
    """Schema for bearing product attributes."""
    material: Optional[str] = None
    bore_diameter: Optional[float] = Field(None, ge=1, le=2000)
    outer_diameter: Optional[float] = Field(None, ge=3, le=2500)
    operating_temp_max: Optional[float] = Field(None, ge=60, le=600)
    # ... all attributes typed and bounded

class ProductExtractionSchema(BaseModel):
    """Schema for the full extraction result from Claude."""
    product_id: Optional[str] = None
    product_type: ProductTypeEnum
    name: Optional[str] = None
    attributes: Dict[str, Any]
    evidence: Dict[str, str]           # field_name → verbatim quote
    field_confidences: Dict[str, float]
    extraction_confidence: float = Field(ge=0.0, le=1.0)

class ProductProvenanceSchema(BaseModel):
    """Traceable provenance for every product record."""
    source_document: str
    extraction_date: datetime
    model_used: str
    confidence: float
    field_sources: Dict[str, FieldSourceEnum]  # extracted|inferred|web_enriched|human_corrected
    evidence: Dict[str, str]

class AgentToolCallSchema(BaseModel):
    """Schema for validating agent tool call inputs."""
    tool_name: AgentToolEnum
    parameters: Dict[str, Any]
```

**Error contract** — validation failures return structured detail, not bare strings:

```python
try:
    validated = ProductExtractionSchema(**raw_llm_output)
except ValidationError as e:
    return ExtractionError(
        error="Schema validation failed",
        issues=[{"field": err["loc"], "msg": err["msg"]} for err in e.errors()]
    )
```

---

## 3. AI Call Efficiency

- **One Claude call per extraction** — system prompt contains full ontology schema, user message contains document. Single call returns all attributes + evidence + confidences.
- **Haiku for cheap ops** — product type classification and LLM sanity checks use `claude-haiku-4-5` (~10× cheaper than Sonnet).
- **Sonnet only for extraction** — `claude-sonnet-4-8` used only for the main extraction call and agent reasoning.
- **RAG-first enrichment** — missing attributes checked in ChromaDB before any API call. Zero cost for catalog hits.
- **Web search last resort** — agent only calls web search when confidence < 0.5 AND catalog has no neighbors.

---

## 4. Security & Secret Management

- All API keys via environment variables. Zero hardcoded secrets in any file.
- `.env.local` in `.gitignore`. `.env.example` committed with placeholder values.
- No secrets returned in any API response or log output.
- All LLM responses validated through Pydantic before use — AI output never trusted raw.
- Try/except on every external call with safe fallback behavior.

---

## 5. Testing Standards

```
tests/
├── test_pipeline.py      ← 30+ unit tests, zero API key required
│   ├── Schema tests      — valid + invalid + edge cases per product type
│   ├── Ingest tests      — format detection, text extraction, excerpt truncation
│   ├── Validator tests   — missing required, out-of-range, temp inversion, bore > outer
│   ├── Enricher tests    — majority value, description builder, empty catalog
│   ├── Exporter tests    — JSON-LD structure, CSV flatten, batch export, empty list
│   ├── Agent tests       — tool selection, fallback, schema validation
│   └── Integration       — full pipeline smoke test (mocked Claude)
```

All tests pass without an API key using mocked product records.

---

## 6. Accessibility (Streamlit UI)

- All dynamic AI output regions use `st.empty()` containers with ARIA-compatible rendering
- Error messages rendered with distinct visual treatment (st.error) — maps to `role="alert"`
- Status messages use `st.info` / `st.success` — maps to `role="status"`
- Loading states use `st.spinner` with descriptive text
- Color is never the sole indicator of state — icons + text used alongside color badges
- Touch targets: all Streamlit buttons are full-width on mobile viewport
- Confidence values shown as both percentage number AND color (not color alone)

---

## 7. Module Responsibilities (single responsibility principle)

| Module | Responsibility | Max function length |
|---|---|---|
| `core/ingest.py` | Format detection + text extraction only | 25 lines |
| `core/extractor.py` | Claude API call + JSON parsing only | 25 lines |
| `core/enricher.py` | ChromaDB query + majority vote only | 25 lines |
| `core/validator.py` | Rule checks + selective LLM check only | 25 lines |
| `core/agent.py` | Tool-use loop + decision logic only | 25 lines |
| `core/knowledge_graph.py` | Graph build + relationship queries only | 25 lines |
| `core/exporter.py` | Format conversion only | 25 lines |
| `core/pipeline.py` | Orchestration only — no business logic | 25 lines |
| `core/schemas.py` | All Pydantic models — zero logic | N/A |
| `core/constants.py` | All magic values — zero logic | N/A |
