# SE × DEV Team Prompt — UniHack Edition
## AI-Powered Product Intelligence for Industrial Commerce
## Unilog Enrichment Pipeline

> **Roles**
> - 🏗️ **SE** — Software Engineer: systems design, cost control, architecture
> - 💻 **DEV** — Developer: implementation, tooling, code decisions
> - ✅ **CONSENSUS** — Final agreed team decision

---

## Round 1 — Re-reading the Problem (After UniHack Guide)

**🏗️ SE:** The guide completely reframes what we are building. This is NOT
generic product intelligence. This is Unilog's specific enrichment pipeline
with exact output requirements:

1. Input: 6 raw columns — `Mfg_Part_Num`, `Part_Desc`, `E1_Brand`,
   `Unilog_Brand`, `DIB_Brand`, `Part_Manuf`
2. Output: 252 specific columns written to exact rules from the content guidelines
3. Every attribute value must exist in the LOV (~161,000 rows)
4. Every manufacturer/brand must match the 27,000-row approved list exactly
5. Every unit must use the ~500 approved UOM abbreviations
6. "FRIGIDAIRE" ≠ "FRIGIDAIRE®" — the ® symbol matters

**💻 DEV:** And the output is not one description — it's the SAME product
rewritten FIVE ways at five different lengths and casings:

| Format | Length | Casing | Purpose |
|---|---|---|---|
| Invoice Desc | ≤40 chars | ALL CAPS | Till receipt |
| Mobile Desc | 60–80 chars | Title Case | Mobile app |
| Short Desc (Product Title) | ~120 chars | Mixed | Search results |
| Long Description | ~300 chars | Sentence | Product page |
| Marketing Copy | Free | Creative | Promotions |

**✅ CONSENSUS:** We are building a constrained content pipeline, not a
creative AI. Every output must be validated against lookup tables.
Invented values score ZERO even if factually correct.

---

## Round 2 — What We DON'T Build (Cost Control)

**🏗️ SE:** The guide says explicitly:
> "Picking two or three steps and doing them convincingly, with evidence,
> beats a shallow attempt at everything."

Given cost constraints, we pick the three highest-scoring steps:

### ✅ We BUILD (high scoring, measurable):
1. **Manufacturer normalisation** — exact match + fuzzy against 27K brand list
2. **UOM normalisation + fraction conversion** — deterministic, zero LLM cost
3. **Description generation** — 5 formats, formula-driven, validated against LOV
4. **LOV validation** — every attribute value checked against 161K row table
5. **Placeholder filtering** — free, zero cost, required for any accuracy

### ❌ We SKIP (scope too wide or not in datasets):
- Digital asset / image pipeline (no image data provided)
- Full 252-column output (we do the 15 most measurable fields first)
- UNSPSC code assignment (blank in ground truth anyway)
- Country of origin (blank in ground truth anyway)

**💻 DEV:** This means we can measure ourselves against the 200-item
ground truth on exactly the fields we generate. Clean score.

**✅ CONSENSUS:** 15 key fields, done correctly and validated.
Better than 252 fields done badly.

---

## Round 3 — Cost-Effective Architecture

**🏗️ SE:** Cost breakdown per 1,000 rows:

| Step | Tool | Cost | Notes |
|---|---|---|---|
| Placeholder filter | Python | $0 | Pure string check |
| Manufacturer normalise | RapidFuzz (local) | $0 | No API needed |
| UOM normalise | Pandas lookup | $0 | 500-row table |
| Decimal↔fraction | Python dict | $0 | 63-row table |
| LOV validation | Pandas lookup | $0 | 161K-row index |
| Taxonomy classify | Claude Haiku | ~$0.10 | 1 cheap call/item |
| Attribute extraction | Claude Haiku | ~$0.50 | Short descriptions |
| Description building | Claude Haiku | ~$0.40 | 5 formats/item |
| Web enrichment | Claude Sonnet | ~$1.00 | Only if confidence < 0.5 |
| **TOTAL** | | **~$2/1K rows** | vs $14 generic |

**💻 DEV:** Key insight: 80% of the work is deterministic (lookups, rules,
string operations). LLM is only needed for:
- Taxonomy classification (which classpath does this belong to?)
- Attribute extraction from abbreviated descriptions
- Description generation following the exact formula

Everything else is lookup tables. This is what the guide means by
"constrained, not creative."

**🏗️ SE:** And we use Haiku everywhere except web enrichment. Haiku costs
~8× less than Sonnet for equivalent constrained tasks. For formula-following
and LOV-constrained generation, Haiku is sufficient.

**✅ CONSENSUS:** Haiku for all generation. Sonnet only for web enrichment
on low-confidence records. All lookups run locally with zero API cost.

---

## Round 4 — The Pipeline Architecture

```
INPUT ROW (Mfg_Part_Num, Part_Desc, E1_Brand, Unilog_Brand, DIB_Brand, Part_Manuf)
    │
    ▼ loaders/placeholder_filter.py  [$0]
PLACEHOLDER FILTER
  "-- Unbranded --" → None
  "-- No Unilog Brand --" → None
  "-- No DIB Brand --" → None
    │
    ▼ loaders/manufacturer_normaliser.py  [$0 — RapidFuzz]
MANUFACTURER NORMALISATION
  "FRIGIDAIRE" → "FRIGIDAIRE®" (exact match from 27K list)
  "Rheem Mfg" → "Rheem Manufacturing" (fuzzy match, threshold 85)
  Returns: canonical_manufacturer, manufacturer_code, canonical_brand, brand_code
    │
    ▼ loaders/taxonomy_classifier.py  [$0.10/1K — Claude Haiku]
TAXONOMY CLASSIFICATION
  Input: Part_Desc + canonical brand + dept/class/fine hints
  Output: Classpath (e.g. "Plumbing > Pipe Fittings > Brass Couplings")
  Validated: must exist in LOV classpaths
    │
    ▼ loaders/attribute_extractor.py  [$0.50/1K — Claude Haiku]
ATTRIBUTE EXTRACTION
  Input: Part_Desc (abbreviated, cryptic)
  Prompt: "Extract attributes. Values MUST come from this LOV list: [subset]"
  UOM normalisation applied to all numeric outputs
  Fraction conversion: 0.5 → 1/2, 50.25 → 50-1/4
  Returns: attribute dict with LOV-validated values
    │
    ▼ loaders/web_enricher.py  [$1.00/1K sparse — Claude Sonnet]
WEB ENRICHMENT (only when non_null_attrs < 3)
  Source hierarchy: manufacturer site ONLY
  Excluded: Amazon, eBay, distributor sites (sourcing rule)
  Fills gaps in attribute dict with manufacturer-sourced values
    │
    ▼ generators/description_builder.py  [$0.40/1K — Claude Haiku]
DESCRIPTION BUILDER (5 formats)
  Invoice Desc:    formula → ALL CAPS → truncate to ≤40 chars
  Mobile Desc:     formula → 60–80 chars → validate length
  Short Desc:      Brand + Series + MPN + Item Type + key attrs
  Long Desc:       comma-separated attribute sentence
  Marketing Copy:  narrative, benefit-led
    │
    ▼ validators/output_validator.py  [$0]
VALIDATION ENGINE
  LOV check: every attribute value in approved list?
  Character limits: Invoice ≤40? Mobile 60–80?
  UOM check: all units in approved abbreviation list?
  Brand check: exact match including ® / ™?
  Fraction check: decimals converted to fractions?
  Confidence score: % fields validated successfully
  Flags: needs_human_review if confidence < 0.80
    │
    ▼ core/exporter.py  [$0]
OUTPUT
  Unilog Delivery Format (15 key fields → extendable to 252)
  CSV matching ground truth column names
  JSON with full provenance
  Evaluation report vs 200-item ground truth
```

---

## Round 5 — Implementation Sequence (Hackathon Timeline)

### Hour 1–2: Data Loading + Lookup Tables
```
loaders/data_loader.py
  - Load UniCat_Manufacturer_and_Brand_List.xlsx (27K rows)
  - Load Unicat_Lov_v1_0.xlsx (161K rows) → index by classpath
  - Load Unilog_Master_UOM_Standards.xlsx → build UOM map
  - Load Decimal_Fraction.xlsx → build fraction lookup dict
  - Handle: merged cells, multi-row headers, side-by-side blocks
```

### Hour 3–4: Deterministic Steps (Zero LLM Cost)
```
loaders/placeholder_filter.py — string filter
loaders/manufacturer_normaliser.py — RapidFuzz
loaders/uom_normaliser.py — Pandas lookup + regex
loaders/fraction_converter.py — 63-entry dict lookup
```

### Hour 5–6: LLM Steps (Haiku)
```
loaders/taxonomy_classifier.py — classpath assignment
loaders/attribute_extractor.py — LOV-constrained extraction
generators/description_builder.py — 5-format generation
```

### Hour 7: Validation + Evaluation
```
validators/output_validator.py — all checks
evaluate.py — score against 200-item ground truth
  Field-level accuracy, char-limit compliance, LOV hit rate
```

### Hour 8: UI + Demo Polish
```
ui/app.py — updated Streamlit with UniHack output schema
  Show: Input row → 5 description formats side by side
  Show: LOV validation badges per attribute
  Show: Confidence score + review flags
```

---

## Round 6 — Evaluation Strategy (Show Your Metrics)

**🏗️ SE:** The guide specifically says "Judges will look for" these metrics.
We build `evaluate.py` that measures automatically:

| Metric | How measured | Target |
|---|---|---|
| Field-level accuracy | Compare output vs 200 ground truth rows | >80% |
| Character limit compliance | Invoice ≤40, Mobile 60–80 | 100% |
| LOV hit rate | % attribute values found in LOV | >90% |
| Brand match accuracy | Exact match including symbols | >95% |
| UOM compliance | % units in approved list | 100% |
| Fraction conversion | Decimals converted correctly | 100% |
| Human review rate | % flagged (confidence < 0.80) | <25% |

**💻 DEV:** We run this on the 200-item ground truth file and print a
report card. Judges can see exactly where we are strong and weak.
This is more impressive than a demo that just "looks good."

**✅ CONSENSUS: Build UNILOG-APEX. Measure everything. Show the scorecard.**

---

## Resource Links (All Free, No Login Required)

| Resource | URL | What it gives you |
|---|---|---|
| Anthropic API docs | https://docs.anthropic.com | Claude Haiku pricing, models |
| Claude Haiku pricing | https://www.anthropic.com/pricing | ~$0.25/M input tokens |
| RapidFuzz (fuzzy matching) | https://github.com/rapidfuzz/RapidFuzz | Fast Levenshtein, free |
| ChromaDB docs | https://docs.trychroma.com | Local vector store, free |
| sentence-transformers | https://www.sbert.net | MiniLM embeddings, free |
| pandas docs | https://pandas.pydata.org | Excel/CSV loading |
| openpyxl | https://openpyxl.readthedocs.io | Merged cell handling |
| UNSPSC browser | https://www.unspsc.org | Product classification codes |
| schema.org/Product | https://schema.org/Product | JSON-LD output standard |

---

## Final Architecture Summary

```
UNILOG-APEX Stack (Cost-Effective Edition)
==========================================
Language:     Python 3.12
LLM:          Claude Haiku 4.5 (extraction + generation)
              Claude Sonnet 4.8 (web enrichment only)
Fuzzy match:  RapidFuzz (local, free)
Lookups:      Pandas DataFrames (local, free)
Vector store: ChromaDB + MiniLM (local, free)
UI:           Streamlit (free)
Security:     JWT + RLS + rate limiting (existing)
Cost:         ~$2 per 1,000 rows (vs $14 generic)
Output:       15 Unilog fields → extendable to 252
Evaluation:   Automated scorecard vs 200-item ground truth
```
