# UniHack Gap Analysis
## What the guide requires vs. what APEX currently does

### THE CORE DIFFERENCE
APEX was built for generic industrial product enrichment.
UniHack is specifically Unilog's enrichment pipeline for industrial DISTRIBUTORS.
The output is not JSON — it is 252 specific named columns written to exact rules.

---

## Gap 1 — WRONG OUTPUT SCHEMA ❌ CRITICAL
APEX outputs: generic JSON-LD (schema.org/Product)
UniHack requires: 252 specific Unilog columns including:
  - Invoice Desc (≤40 chars, ALL CAPS)
  - Mobile Desc (60–80 chars, specific format)
  - Product Title / Short Desc (Brand + Series + MPN + Item Type formula)
  - Long Description (comma-separated attribute sentence)
  - 5 different description formats for same product
  - Classpath (taxonomy path, e.g. "Appliances > Kitchen > Dishwashers")

## Gap 2 — NO LOV VALIDATION ❌ CRITICAL
APEX validates against our own schema ranges.
UniHack requires: every attribute value must come from Unicat_Lov_v1_0 (~161,000 rows).
A value not in the LOV = score zero, even if it is factually correct.

## Gap 3 — NO MANUFACTURER NORMALISATION ❌ CRITICAL
APEX does fuzzy brand matching via RAG.
UniHack requires: exact match against 27,000+ row UniCat_Manufacturer_and_Brand_List
  including exact legal casing, ® and ™ symbols, Inc/LLC/Ltd suffixes.
"FRIGIDAIRE" ≠ "FRIGIDAIRE®" — they are different.

## Gap 4 — NO UOM NORMALISATION ❌ CRITICAL
APEX normalises units via prompt instruction only.
UniHack requires: ~500 approved UOM abbreviations from Unilog_Master_UOM_Standards.
"IN" must become "in". "24in" must become "24 in" (space required).
Decimal↔fraction conversion: 0.5 → 1/2, 50.25 in → 50-1/4 in.

## Gap 5 — NO TAXONOMY CLASSIFICATION ❌ HIGH
APEX does product type classification (bearing/valve/etc).
UniHack requires: Classpath assignment (hierarchical taxonomy, e.g.
  "Plumbing > Pipe Fittings > Brass Fittings > Coupling").

## Gap 6 — NO PLACEHOLDER FILTERING ❌ HIGH
APEX: no special handling of placeholder values.
UniHack: "-- Unbranded --", "-- No Unilog Brand --", "-- No DIB Brand --"
  must be treated as null before any processing.

## Gap 7 — NO CHARACTER LIMIT ENFORCEMENT ❌ HIGH
APEX: no character limits on output fields.
UniHack: hard limits per field (Invoice Desc ≤40, Mobile 60–80, etc).
Violation = scoring deduction.

## Gap 8 — NO DECIMAL/FRACTION LOOKUP ❌ MEDIUM
APEX: no fraction conversion.
UniHack: 63-row lookup table for inch fractions (0.015625→1/64 to 0.984375→63/64).
Manufacturers use decimals; buyers search fractions.

## Gap 9 — NO CATEGORY-SPECIFIC RULES ❌ MEDIUM
APEX: generic extraction for 6 product types.
UniHack: Fittings LOV has 1,472 connection-type variants → 515 canonical values.
  Faucets LOV has fixed attribute ORDER and title word order.

## Gap 10 — WEB SOURCING CONSTRAINT NOT ENFORCED ⚠️ MEDIUM
APEX: no sourcing hierarchy constraint.
UniHack: web enrichment must come from MANUFACTURER's own site only.
  Marketplaces (Amazon, eBay) and distributor sites EXPLICITLY EXCLUDED.

---

## WHAT APEX ALREADY HAS (keep these)
✅ Agent loop (tool-use decision making)
✅ RAG from catalog (ChromaDB + MiniLM)
✅ Web enrichment with source tracking
✅ Knowledge graph (NetworkX)
✅ Human-in-the-loop review queue
✅ Field-level provenance + confidence scores
✅ HITL flags for low-confidence records
✅ Security layer (JWT, RLS, rate limiting, XSS)
✅ Async batch processing
✅ Export in multiple formats

---

## REBUILD PRIORITY ORDER
1. UniHack output schema (252 columns → start with 15 key fields)
2. LOV lookup engine (161K rows → indexed for fast lookup)
3. Manufacturer normalisation (27K rows → fuzzy + exact)
4. UOM normalisation + decimal/fraction conversion
5. Description builders (5 formats per product)
6. Taxonomy classifier (classpath assignment)
7. Placeholder filter
8. Character limit enforcer + validator
9. Category-specific rules (Fittings + Faucets first)
10. Sourcing constraint on web enrichment
