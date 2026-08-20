# ARCHITECTURE.md — APEX Pipeline Architecture

## End-to-End Pipeline Data Flow

```
Raw Input Row (CSV)
        │
        ▼
┌───────────────────────────────────────────────────────────────────┐
│  Stage 1: INGEST & NORMALISE (loaders/)                           │
│                                                                   │
│  data_loader.py          → is_placeholder(), load_lov_table()     │
│  manufacturer_normaliser → normalise_from_row() via RapidFuzz     │
│  uom_normaliser.py       → normalise_uom(), decimal_to_fraction() │
│                                                                   │
│  Output: {mpn, brand_name, manufacturer_name, raw_brand,         │
│            brand_confidence, brand_match_type, attributes_partial}│
└────────────────────────────────┬──────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────┐
│  Stage 2: CLASSIFY (Claude Haiku 4.5)                             │
│                                                                   │
│  unihack_pipeline.py → _classify_taxonomy_llm()                  │
│    Input:  Part_Desc + brand + MPN                                │
│    Output: classpath, unspsc, dept, class, fine_line              │
│    Fallback: _extract_item_type() heuristic regex                 │
└────────────────────────────────┬──────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────┐
│  Stage 3: ATTRIBUTE EXTRACT (Claude Haiku 4.5 + LOV constraint)  │
│                                                                   │
│  unihack_pipeline.py → _extract_attributes_llm()                 │
│    Input:  Part_Desc + classpath + LOV reference                  │
│    Output: {attr_name: lov_value, ...}                            │
│    Fallback: _parse_desc_heuristic() — regex patterns            │
│    LOV check: validate every value against LOV table              │
└────────────────────────────────┬──────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────┐
│  Stage 4: WEB ENRICH (Claude Sonnet 4.8 — sparse records only)   │
│                                                                   │
│  core/web_enricher.py → enrich_from_web()                        │
│    Triggers only when: len(attributes) < 3                        │
│    Source rules: manufacturer.com only, no marketplaces           │
│    No-hallucination: Evidence required or blank                   │
└────────────────────────────────┬──────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────┐
│  Stage 5: DESCRIPTION BUILD (Claude Haiku 4.5)                   │
│                                                                   │
│  generators/description_builder.py → build_all_descriptions()    │
│    Generates: invoice_desc (≤40 ALL CAPS), mobile_desc (60-80),  │
│    short_desc (~120), long_desc (full), marketing_copy           │
│    Fallback: _fallback_descriptions() — template-based           │
└────────────────────────────────┬──────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────┐
│  Stage 6: VALIDATE (validators/output_validator.py)               │
│                                                                   │
│  validate_output() → ValidationReport                             │
│    • invoice_desc length + CAPS rule                              │
│    • mobile_desc length rule (60-80)                              │
│    • LOV compliance per attribute                                 │
│    • UOM/fraction format compliance                               │
│    • Placeholder detection in output fields                       │
│    → overall_score, needs_human_review                            │
└────────────────────────────────┬──────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────┐
│  Stage 7: PROVENANCE + CONFLICTS (core/)                          │
│                                                                   │
│  core/provenance.py     → build_provenance_for_enriched()         │
│  core/conflict_detector → detect_conflicts()                      │
│  core/duplicate_finder  → find_duplicates_in_db()                │
│    → _conflicts, _provenance, priority_score                      │
└────────────────────────────────┬──────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────┐
│  SQLite: data/catalog.db                                          │
│    products table: enriched_json + provenance + status            │
│    reviews table: append-only audit trail                         │
│    jobs table: batch run metadata + cost estimate                 │
└────────────────────────────────┬──────────────────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
              Human Review                Export
                    │                         │
              Approve/Reject            252-col CSV
                    │                   JSON provenance
              ChromaDB index            JSON-LD
```

---

## Component Responsibilities

### `loaders/` — Deterministic Data Processing
- **`data_loader.py`**: CSV ingestion, placeholder detection, LOV table loading
- **`manufacturer_normaliser.py`**: RapidFuzz fuzzy brand matching against master list
- **`uom_normaliser.py`**: UOM standardization, decimal-to-fraction conversion
- **`unihack_pipeline.py`**: Orchestrates the full row enrichment (calls all stages)

### `generators/` — Description Generation
- **`description_builder.py`**: Five-tier description generation with LLM and fallback

### `validators/` — Quality Gates
- **`output_validator.py`**: LOV, UOM, char-limit, placeholder, consistency checks

### `core/` — Infrastructure
- **`catalog_db.py`**: SQLite persistence (WAL mode, foreign keys)
- **`provenance.py`**: Field-level provenance tracking with source evidence
- **`conflict_detector.py`**: Brand mismatches, unit incompatibilities, range checks
- **`duplicate_finder.py`**: MPN normalization + ChromaDB vector similarity
- **`enricher.py`**: ChromaDB indexing and RAG retrieval
- **`knowledge_graph.py`**: NetworkX product relationship graph (compatibility, replacements)
- **`exporter.py`**: 252-column CSV, JSON, JSON-LD output

### `security/` — Auth Layer
- **`middleware.py`**: JWT (PyJWT or stdlib HMAC fallback), PBKDF2 passwords, rate limiting, CSRF, XSS sanitization, bot detection

### `ui/` — Dashboard
- **`unihack_app.py`**: Streamlit 8-page live dashboard

---

## ChromaDB Architecture

```
data/
├── catalog.db              ← SQLite (stdlib, no install)
├── chroma_db/              ← ChromaDB persistent storage
│   ├── chroma.sqlite3
│   └── <vector index files>
├── knowledge_graph.json    ← NetworkX persisted as JSON
└── exports/
    ├── delivery_output.csv
    ├── provenance.json
    └── jsonld_output.json
```

**ChromaDB policy:**
- Only approved records (human-approved OR confidence ≥ 0.80 + all validations passed) are indexed
- Embedding model: `all-MiniLM-L6-v2` — runs on CPU, no GPU required
- Collection name: `apex_products`
- Used for: RAG retrieval during enrichment, duplicate detection

---

## Human Review & Audit Trail

Every review action writes an immutable row to the `reviews` SQLite table:
```sql
INSERT INTO reviews (product_id, action, reviewer_email, changes_json, created_at)
VALUES (?, ?, ?, ?, datetime('now'));
```

Actions: `approved`, `rejected`, `corrected`

After approval → record is indexed in ChromaDB → eligible for export.

---

## Selective LLM Usage

The pipeline makes at most **3 LLM calls per row**, each to Claude Haiku:
1. Taxonomy classification (~60 tokens out)
2. Attribute extraction (LOV-constrained, ~200 tokens out)
3. Description generation (5 formats in 1 call, ~300 tokens out)

Claude Sonnet is used only for web enrichment on sparse records (< 3 attributes).
All LLM calls have heuristic fallbacks that run for free.

---

## Why Local-First Reduces Cost

| Component | Typical Cost | APEX Cost |
|---|---|---|
| Brand matching | Embedding API | $0 (RapidFuzz) |
| LOV validation | LLM call | $0 (Pandas lookup) |
| UOM normalization | LLM call | $0 (rule engine) |
| Vector search | Cloud vector DB subscription | $0 (local ChromaDB) |
| Embeddings | OpenAI/Cohere API | $0 (MiniLM CPU) |
| Persistence | RDS/Postgres | $0 (SQLite stdlib) |
| **Total saved** | | **~$12/1K rows** |
