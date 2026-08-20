# EVALUATION.md — Evaluation Methodology & Metrics

## Evaluation Dataset

**File:** `Unilog-Sample_200_Items-Input-vs-Output.xlsx`

- **200 input rows** with known-good delivery format output
- Covers product categories: Fittings, Appliances, Plumbing, HVAC, Electrical
- Ground-truth fields: brand_name, classpath, invoice_desc, mobile_desc, attributes

**Evaluation script:** `evaluate.py --demo`

---

## Exact Evaluation Methodology

```python
# evaluate.py
from evaluate import run_evaluation

report = run_evaluation(enriched_records, ground_truth_records)
```

For each enriched record, evaluation compares:

1. **Exact match**: `enriched[field].strip().lower() == gt[field].strip().lower()`
2. **Partial match**: `SequenceMatcher(None, enriched, gt).ratio() >= 0.85`
3. **LOV match**: `enriched_attr_val in approved_lov_set`

---

## Field-Level Metrics

| Field | Metric | Target |
|---|---|---|
| `brand_name` | Exact match rate | ≥ 85% |
| `manufacturer_name` | Exact match rate | ≥ 85% |
| `classpath` | Exact + partial match | ≥ 80% |
| `invoice_desc` | Exact match + format rules | ≥ 90% |
| `mobile_desc` | Partial match + length | ≥ 80% |
| `short_desc` | Partial match | ≥ 70% |
| `long_desc` | Partial match | ≥ 70% |
| `attributes.*` | LOV compliance per attribute | ≥ 90% |

---

## LOV Compliance

Every attribute value is checked against `Unicat_Lov_v1_0_Updated_With_Remarks.xlsx`:

```python
def check_lov_compliance(attr_name: str, attr_val: str, lov_table: set) -> bool:
    """True if attr_val is in the approved LOV set for attr_name."""
    return attr_val.strip() in lov_table
```

**Target:** ≥ 90% of attribute values comply with approved LOV

---

## UOM Compliance

UOM values are checked against `Unilog_Master_UOM_Standards_Abbreviations_and_Terms.xlsx`:

```python
def check_uom_compliance(value: str) -> bool:
    """True if numeric values use approved UOM abbreviations (e.g. 'in' not 'inch')."""
    ...
```

Rules:
- Fractions must use hyphenated format: `1/2 in` not `0.5 in`
- UOM must use standard abbreviation: `in` not `inch`, `V` not `Volt`
- Values must include space between number and unit: `120 V` not `120V`

**Target:** ≥ 95% UOM compliance

---

## Description Rule Compliance

| Format | Rule | Target |
|---|---|---|
| Invoice Desc | ≤40 characters, ALL CAPS | 100% |
| Mobile Desc | 60–80 characters | ≥ 90% |
| Short Desc | ≤150 characters | ≥ 95% |
| Long Desc | ≥20 characters (non-trivial) | ≥ 95% |

---

## Source Coverage

```
source_coverage = records_with_resource_url_or_source_url / total_records
```

**Target:** ≥ 70% of enriched records have at least one source citation

---

## Human Review Rate

```
human_review_rate = records_flagged_for_review / total_records
```

**Target:** ≤ 25% — records auto-approved when confidence ≥ 0.80 and all validation gates pass

**Priority scoring** determines review urgency:
- Missing required fields: +30
- Confidence < 0.70: +25
- Brand fallback (not in master list): +20
- Conflicting source fields: +15
- No source evidence: +5 per field

---

## Scorecard Summary

```
✅ LOV compliance      ≥ 90%   → approved for delivery
✅ UOM compliance      ≥ 95%   → approved for delivery
✅ Char-limit pass     100%    → required by Unilog spec
✅ Human review rate   ≤ 25%   → efficient pipeline
✅ Source coverage     ≥ 70%   → evidence-traceable
✅ Brand accuracy      ≥ 85%   → reliable entity resolution
```

Run live: `python3 evaluate.py --demo`

---

## Known Limitations

1. **Sparse attributes on new product categories**: For categories not well-covered in the LOV files, attribute extraction relies more heavily on Claude Haiku inference (lower confidence).

2. **Brand resolution without Excel files**: If `UniCat_Manufacturer_and_Brand_List.xlsx` is absent, brand matching falls back to direct string normalization (lower recall on aliased brands).

3. **Web enrichment latency**: Web enrichment via Claude Sonnet adds ~3s per row for sparse records; disabled by default in demo mode.

4. **Ground-truth alignment**: The 200-row ground truth may have alternative valid descriptions that would score as partial matches.

---

## Human Review Policy

Records are sent for human review when **any** of the following apply:
- Validation score < 0.80
- Brand match type = `fallback` (not in master list)
- Any LOV compliance failure
- Any UOM/fraction compliance failure
- Conflict detected (brand mismatch, unit mismatch)
- Priority score > 40

Human-corrected fields are re-validated before approval. All approved records are indexed in ChromaDB. Rejected records are excluded from all exports but preserved in the audit trail.
