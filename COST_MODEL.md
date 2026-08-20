# COST_MODEL.md — APEX Cost Analysis

## Total Cost: ~$2 per 1,000 SKUs

APEX achieves a 7× cost reduction over a generic all-LLM pipeline through aggressive local-first design.

---

## Component Cost Breakdown

| Pipeline Step | Tool | Cost/1K rows | Rationale |
|---|---|---|---|
| CSV ingestion | pandas (stdlib) | $0 | Pure local |
| Placeholder detection | Python string ops | $0 | 100% deterministic |
| Manufacturer normalization | RapidFuzz fuzzy match | $0 | No API; local CPU |
| Brand resolution | Excel lookup (openpyxl) | $0 | Offline master list |
| UOM standardization | Rule engine + pandas | $0 | Lookup table |
| Decimal-to-fraction | Pandas lookup | $0 | 161K rows cached |
| LOV validation | Pandas set lookup | $0 | O(1) per check |
| Taxonomy classification | Claude Haiku 4.5 | **~$0.10** | 1 short call/row |
| Attribute extraction | Claude Haiku 4.5 | **~$0.50** | LOV-constrained, ~500 tok out |
| Description generation | Claude Haiku 4.5 | **~$0.40** | 5 formats in 1 call |
| Web enrichment (sparse) | Claude Sonnet 4.8 | **~$1.00** | Only ~20% of rows, short |
| Conflict detection | Rule engine | $0 | Local heuristics |
| Duplicate detection | SQLite + ChromaDB | $0 | Local CPU |
| ChromaDB indexing | all-MiniLM-L6-v2 | $0 | CPU, no API |
| SQLite persistence | Python stdlib | $0 | Zero infrastructure |
| **TOTAL** | | **~$2.00** | |

---

## Free Components (Zero Cost)

### RapidFuzz (Entity Resolution)
- **What it replaces**: Embedding API calls for brand matching
- **How**: Levenshtein distance + token set ratio on local brand master list
- **Cost saved**: ~$0.50/1K rows

### ChromaDB (Vector Search)
- **What it replaces**: Pinecone / Weaviate / Qdrant Cloud subscriptions
- **How**: Persistent local client storing vectors on disk
- **Cost saved**: Cloud vector DBs start at ~$50/month + per-query fees
- **Reference**: `chromadb.PersistentClient(path="data/chroma_db/")`

### all-MiniLM-L6-v2 (Embeddings)
- **What it replaces**: OpenAI Embeddings API, Cohere Embed API
- **How**: sentence-transformers, runs on CPU, ~80MB model
- **Cost saved**: OpenAI charges ~$0.10/1M tokens; at 500 tokens/product that's $0.05/1K
- **Performance**: 384-dimensional embeddings, cosine similarity for deduplication

### SQLite (Persistence)
- **What it replaces**: RDS PostgreSQL, Cloud Firestore, DynamoDB
- **How**: Python stdlib sqlite3, WAL mode for concurrent reads
- **Cost saved**: RDS Postgres t3.micro = ~$15/month minimum

### pandas + openpyxl (LOV Validation)
- **What it replaces**: LLM calls to validate attribute values
- **How**: Pre-loaded set of 161,000+ LOV values; O(1) lookup per attribute
- **Cost saved**: 3-4 LLM validation calls per row at ~$0.001/call = ~$3-4/1K rows

---

## LLM Usage: Selective, Not Universal

APEX calls an LLM only when **deterministic rules cannot resolve** the input:

### When LLM is called:
1. **Taxonomy classification**: Ambiguous product type from abbreviation (e.g. `CPLG` → Coupling)
2. **Attribute extraction from unstructured text**: When regex patterns don't match
3. **Description generation**: All 5 formats require natural language production

### When LLM is NOT called:
- Brand matching (RapidFuzz handles this)
- UOM normalization (rule table)
- Fraction conversion (lookup table)
- LOV validation (set lookup)
- Conflict detection (rule engine)
- Duplicate detection (MPN normalization + ChromaDB)

### Model selection rationale:
- **Haiku**: Used for structured output (taxonomy, attributes, descriptions). 8× cheaper than Sonnet, equivalent quality for constrained tasks.
- **Sonnet**: Used only for web enrichment of truly sparse records where context understanding matters most.

---

## Cost Comparison

| Pipeline Type | Architecture | Cost/1K rows |
|---|---|---|
| **APEX** | Local-first + selective LLM | **~$2** |
| Generic LLM pipeline | Every step via GPT-4 / Claude 3.5 | ~$14 |
| Embedding-based pipeline | OpenAI Embeddings + Claude | ~$6 |
| Rules-only pipeline | No LLM | $0 (but low quality) |

---

## Infrastructure Cost: $0

| Requirement | APEX | Typical Competitor |
|---|---|---|
| Cloud vector DB | ❌ Not needed | Pinecone ~$70/month |
| GPU for embeddings | ❌ Not needed | Cloud GPU ~$2/hr |
| Cloud database | ❌ Not needed | RDS ~$15/month |
| Search API | ❌ Not needed | Serper/SerpAPI ~$50/month |
| Paid enrichment API | ❌ Not needed | Clearbit/Diffbot ~$100/month |
| **Monthly infra cost** | **$0** | **~$250+** |

---

## Scaling Projection

| Volume | APEX Cost | Generic Pipeline |
|---|---|---|
| 1,000 rows | ~$2.00 | ~$14.00 |
| 10,000 rows | ~$20.00 | ~$140.00 |
| 100,000 rows | ~$200.00 | ~$1,400.00 |
| 1,000,000 rows | ~$2,000 | ~$14,000 |

At scale, the heuristic-first approach means a growing fraction of rows (those with clean data) cost **$0** — only rows that need LLM assistance incur cost.

---

## Heuristic-Only Mode (Zero Cost)

Without an API key, APEX runs fully local:
- Placeholder detection: ✅ Free
- Brand normalization: ✅ Free
- UOM normalization: ✅ Free
- Taxonomy: regex heuristic fallback (`_extract_item_type`)
- Attributes: regex heuristic fallback (`_parse_desc_heuristic`)
- Descriptions: template fallback (`_fallback_descriptions`)
- LOV validation: ✅ Free
- ChromaDB + SQLite: ✅ Free

**Cost: $0.00/1K rows** with acceptable baseline quality.
