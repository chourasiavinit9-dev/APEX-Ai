"""APEX Core package — lazy imports to avoid crash cascades on Python 3.9."""
from __future__ import annotations


def __getattr__(name):
    """Lazy import to prevent crash when optional dependencies are missing."""
    _imports = {
        "run_single": ".pipeline",
        "run_batch": ".pipeline",
        "ingest_file": ".ingest",
        "ingest_text": ".ingest",
        "ingest_url": ".ingest",
        "extract": ".extractor",
        "classify_product_type": ".extractor",
        "enrich": ".enricher",
        "index_product": ".enricher",
        "validate": ".validator",
        "to_jsonld": ".exporter",
        "to_csv_row": ".exporter",
        "products_to_csv_string": ".exporter",
    }
    if name in _imports:
        import importlib
        module = importlib.import_module(_imports[name], package=__name__)
        return getattr(module, name)
    raise AttributeError(f"module 'core' has no attribute {name!r}")
