# DATA_SOURCES.md — UniHack Reference Files & Sourcing Policy

## UniHack Reference Files Used

| File | Location | Usage |
|---|---|---|
| `UniCat_Manufacturer_and_Brand_List.xlsx` | `data/unihack/` | Canonical manufacturer + brand lookup, ® / ™ normalization |
| `Unicat_Lov_v1_0_Updated_With_Remarks.xlsx` | `data/unihack/` | 161K+ LOV attribute values, UOM abbreviations |
| `Unilog_Master_UOM_Standards_Abbreviations_and_Terms.xlsx` | `data/unihack/` | Official UOM abbreviations and conversion rules |
| `Decimal_Fraction.xlsx` | `data/unihack/` | Decimal-to-fraction mapping (0.5 → 1/2, etc.) |
| `Fittings_LOV.xlsx` | `data/unihack/` | Fitting-specific LOV values (Material, Connection Type, etc.) |
| `FAUCETS_LOV.xlsx` | `data/unihack/` | Faucet-specific LOV values |
| `Unilog-Sample_200_Items-Input-vs-Output.xlsx` | `data/unihack/` | Ground-truth evaluation: 200 input rows vs expected delivery output |

### How Files Are Loaded

```python
# data_loader.py
load_uom_standards()        # → dict of {raw_unit: standard_abbrev}
load_fraction_lookup()      # → dict of {decimal: fraction_string}

# manufacturer_normaliser.py
load_manufacturer_list()    # → list of {manufacturer, brand, trademark}

# validators/output_validator.py
load_lov_table()            # → set of approved LOV values
```

Files are loaded once at pipeline startup and cached in memory. If files are absent, the pipeline falls back to a built-in minimal LOV set and continues operating.

---

## Official Manufacturer Source Policy

APEX enforces a **strict no-marketplace rule** for web enrichment:

### ✅ Valid source URLs
- `https://www.muellerindustries.com/...`
- `https://www.frigidaire.com/...`
- `https://www.rheem.com/...`
- Any official manufacturer domain
- Official manufacturer technical datasheets (PDF)
- Official manufacturer product catalogs

### ❌ Invalid source URLs (never used)
- amazon.com
- ebay.com
- grainger.com (distributor)
- fastenal.com (distributor)
- Any third-party reseller or distributor
- User-generated content platforms

This rule is enforced in `core/web_enricher.py`:
```python
BLOCKED_DOMAINS = {"amazon", "ebay", "grainger", "fastenal", "walmart",
                   "homedepot", "lowes", "zoro", "mcmaster"}

def _is_valid_source(url: str) -> bool:
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower()
    return not any(b in domain for b in BLOCKED_DOMAINS)
```

---

## `resource_url` Format

`resource_url` identifies the exact local reference used:

```
UniCat_Manufacturer_and_Brand_List.xlsx → Brand List → Row 1247
Unicat_Lov_v1_0_Updated_With_Remarks.xlsx → Material → Row 89
Fittings_LOV.xlsx → Connection Type → Row 34
Unilog_Master_UOM_Standards_Abbreviations_and_Terms.xlsx → Length → Row 12
Rule engine → Invoice Desc truncation rule
Claude Haiku → Attribute Extraction
```

---

## `source_url` Format

`source_url` identifies the official external source:

```
https://www.muellerindustries.com/products/brass-fittings/couplings/
https://www.frigidaire.com/Kitchen-Appliances/Dishwashers/PDSH4816AF/
https://www.rheem.com/products/residential/water-heating/
```

---

## Hallucination Risk Control

Hallucination risk is controlled — LOV validation gate rejects out-of-vocabulary values; evidence quotes required for every LLM extraction; human review triggered for confidence below 0.80.

Specific controls implemented at the pipeline level:

1. **Evidence required**: Every non-`input` field must have a `resource_url` or `source_url`
2. **Blank over guessing**: If a value cannot be sourced, it is left blank and flagged for review
3. **LLM prompts are LOV-constrained**: Attribute extraction prompts include the full LOV list, so the LLM cannot invent out-of-vocabulary values
4. **Validation rejects fabrications**: Every output field is validated against the LOV table; failures block indexing
5. **Human review at 0.80**: Records with overall confidence below 0.80 are automatically routed to the human review queue

```python
# No-hallucination check in validators/output_validator.py
if not value_in_lov(attr_val, lov_table):
    result.issues.append(f"'{attr_val}' not in approved LOV values")
    result.passed = False
```
