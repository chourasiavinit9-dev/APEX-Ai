# Changelog

All notable changes to LEAP are documented here.

## [1.0.0] — 2026-08-20 — UniHack Final Submission

### Added
- **Live 8-page Streamlit dashboard** (Dashboard, Pipeline, Products, Review Queue, Analytics, Export, Cost Model, Security)
- **SQLite persistence** (`core/catalog_db.py`) — jobs, products, reviews, audit trail with WAL mode
- **Provenance tracking** (`core/provenance.py`) — every field carries source_type, resource_url, source_url, evidence, confidence
- **Conflict detector** (`core/conflict_detector.py`) — brand mismatches, unit incompatibilities, range checks
- **Duplicate finder** (`core/duplicate_finder.py`) — MPN normalization + ChromaDB vector similarity
- **Evidence drawer** — per-field provenance visualization in the Products and Review Queue pages
- **Human review workflow** — Approve & Index, Correct & Revalidate, Reject with SQLite audit trail
- **ChromaDB indexing** — approved records only, `all-MiniLM-L6-v2` CPU embeddings
- **Live pipeline progress** — real-time progress bars, cost tracking, ETA display
- **3-format export** — 252-col Unilog CSV, provenance JSON, JSON-LD
- **GitHub Actions CI/CD** — lint → test matrix (3.9/3.10/3.11) → security scan → build → Docker
- **Architecture diagrams** — pipeline, security, and CI/CD diagrams in `docs/images/`
- **MIT License**
- **Full documentation** — README, ARCHITECTURE, DATA_SOURCES, EVALUATION, COST_MODEL, CONTRIBUTING, SECURITY

### Fixed
- Python 3.9 type hint compatibility (`from __future__ import annotations` in 8 files)
- JWT auth without PyJWT (HMAC-SHA256 stdlib fallback)
- Password hashing without bcrypt (PBKDF2-SHA256 stdlib fallback)
- All 194 tests passing (rewired 13 broken tests from deleted `core/unilog_*` modules)
- Industrial abbreviation patterns in `_extract_item_type` (CPLG, VLV, RDCR, etc.)
- Lazy imports in `core/__init__.py` to prevent crash cascade on missing optional deps

### Architecture
- Local-first: SQLite + ChromaDB + NetworkX — zero cloud dependencies
- Deterministic-first: 80% of pipeline runs for free (RapidFuzz, pandas, rule engine)
- Selective LLM: Claude Haiku for taxonomy/attributes/descriptions; Sonnet only for sparse web enrichment
- Cost: ~$2/1,000 rows vs ~$14 for generic all-LLM pipelines
