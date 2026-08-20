"""
Schema loader — reads product type ontologies from schemas/
"""
import json
from pathlib import Path

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"

_cache: dict = {}

PRODUCT_TYPES = ["bearing", "valve", "sensor", "coupling", "fastener", "pump"]


def load_schema(product_type: str) -> dict:
    """Load and cache a product type schema."""
    if product_type not in _cache:
        path = SCHEMAS_DIR / f"{product_type}.json"
        if not path.exists():
            raise ValueError(f"No schema found for product type: {product_type}")
        with open(path) as f:
            _cache[product_type] = json.load(f)
    return _cache[product_type]


def get_attribute_names(product_type: str) -> list[str]:
    schema = load_schema(product_type)
    return list(schema["attributes"].keys())


def get_required_fields(product_type: str) -> list[str]:
    schema = load_schema(product_type)
    return schema.get("required_fields", [])


def get_validation_ranges(product_type: str) -> dict:
    """Return {field: (min, max)} for fields with range constraints."""
    schema = load_schema(product_type)
    ranges = {}
    for field, spec in schema["attributes"].items():
        if "range" in spec:
            ranges[field] = tuple(spec["range"])
    return ranges


def schema_for_prompt(product_type: str) -> str:
    """Return a condensed schema description for inclusion in extraction prompts."""
    schema = load_schema(product_type)
    lines = [f"Product type: {schema['display_name']}", "Attributes to extract:"]
    for field, spec in schema["attributes"].items():
        unit = f" [{spec['unit']}]" if spec.get("unit") else ""
        desc = f" — {spec['description']}" if spec.get("description") else ""
        examples = ""
        if spec.get("examples"):
            ex = ", ".join(spec["examples"][:3])
            examples = f" (e.g. {ex})"
        lines.append(f"  - {field}{unit}{desc}{examples}")
    return "\n".join(lines)


def load_all_schemas() -> dict:
    return {pt: load_schema(pt) for pt in PRODUCT_TYPES}
