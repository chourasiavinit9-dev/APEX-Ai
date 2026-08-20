# REQUIREMENTS.md — APEX Feature Checklist

> Hack2Skill Submission: AI-Powered Product Intelligence for Industrial Commerce
> Every item below is implemented and demonstrated in the UI.

---

## Core Problem Statement Requirements

- [x] **Generate structured product intelligence from limited inputs**
  - Accepts: part numbers, spec text, PDFs, scanned images, HTML pages, CSV/XLSX
  - Auto-detects product type (bearing/valve/sensor/coupling/fastener/pump)
  - Returns fully structured JSON with all attributes normalized to standard units

- [x] **Improve product data quality and consistency**
  - Schema-enforced product ontology — 6 product types, 100+ attributes
  - Unit normalization: all temperatures → °C, pressures → bar, lengths → mm
  - Domain-named Pydantic schemas: `BearingExtractionSchema`, `ValveExtractionSchema`, etc.
  - Consistent attribute naming across all product types

- [x] **Validate and enrich information with traceable outputs**
  - Field-level provenance tags: `extracted` | `inferred` | `web_enriched` | `human_corrected`
  - Verbatim evidence quote stored per extracted field (max 80 chars)
  - Confidence score per field (0.0–1.0) + overall record confidence
  - Rule-based validation: required fields, numeric ranges, physical sanity checks
  - Selective LLM validation only for flagged/low-confidence records

- [x] **Scale efficiently across large product catalogs**
  - Async batch CLI: processes entire directories in parallel
  - RAG cache: repeat lookups served from ChromaDB (zero API cost)
  - Tiered model use: Haiku for classification/validation, Sonnet for extraction
  - Cost ~$7–14 per 1,000 SKUs; drops significantly after catalog is seeded

---

## AI Approaches (from problem statement)

- [x] **AI Agents** — `core/agent.py`: Claude tool-use agent that autonomously decides whether to search the web, query the catalog, or request human clarification before finalizing extraction
- [x] **RAG (Retrieval-Augmented Generation)** — `core/enricher.py`: ChromaDB + MiniLM fills missing attributes from nearest-neighbor products
- [x] **Knowledge Graph** — `core/knowledge_graph.py`: NetworkX graph stores product relationships — compatibility links, manufacturer aliases, standard equivalences
- [x] **Document Intelligence** — `core/ingest.py`: Docling (PDF tables), Trafilatura (HTML), VLM vision (scanned images)
- [x] **Vision-Language Models** — Direct base64 image input to Claude Sonnet for scanned catalogs and product photos
- [x] **Human-in-the-Loop** — `ui/app.py`: Streamlit review queue — approve, correct, reject with JSON correction form; corrections feed back to RAG store

---

## Expected Outcomes

- [x] Structured product intelligence generated from a single part number or 3-line spec
- [x] Data quality score visible per record in the UI
- [x] Every output field has a traceable source tag
- [x] Batch processing tested at 500+ SKUs via CLI
- [x] Web enrichment fills gaps when source document is sparse

---

## Code Quality

- [x] All functions ≤ 25 lines (pipeline split into single-responsibility modules)
- [x] Domain-named Pydantic schemas (not generic `InputSchema`)
- [x] Zero hardcoded API keys — environment variable only
- [x] `.env.example` with placeholder values
- [x] `CODE_QUALITY.md` documents all standards
- [x] `REQUIREMENTS.md` (this file) — all items ticked

---

## Testing

- [x] 30+ unit tests in `tests/test_pipeline.py` (no API key required)
- [x] Schema tests: valid + invalid + edge cases per product type
- [x] Validator tests: missing required, out-of-range, temp inversion, bore > outer
- [x] Enricher tests: majority value logic, description builder
- [x] Exporter tests: JSON-LD structure, CSV flattening, batch export
- [x] Agent tests: tool selection logic, fallback behavior
- [x] `make test` runs all tests

---

## Deployment

- [x] `Makefile` — `make install`, `make ui`, `make test`, `make demo`
- [x] `Dockerfile` — Cloud Run ready
- [x] `docker-compose.yml` — one command local setup
- [x] `.env.example` — all required keys documented
