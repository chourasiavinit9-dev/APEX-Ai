"""
core/pipeline.py — Orchestration only. Zero business logic.

Fix 1 (CRITICAL): Async batch processing with semaphore concurrency limiter.
Fix 5: Auto web-search when fewer than 3 attributes extracted.
All thresholds from core/constants.py.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import anthropic
from rich.console import Console
from rich.table import Table

from .constants import (
    CONFIDENCE_WEB_SEARCH_THRESHOLD,
    SUPPORTED_EXTENSIONS,
)
from .catalog_db import log_duplicate_audit
from .duplicate_detector import check_duplicate
from .enricher import enrich
from .exporter import export_batch_csv, export_batch_json, export_batch_jsonld
from .extractor import build_client, classify_product_type, extract, normalize_units
from .ingest import IngestedDocument, ingest_file
from .knowledge_graph import graph_stats, index_product_in_graph, load_graph, save_graph
from .validator import validate
from .web_enricher import apply_web_enrichment_to_product, web_enrich

console = Console()
logger = logging.getLogger(__name__)
ASYNC_CONCURRENCY = 10  # max parallel Claude calls


def run_single(
    doc: IngestedDocument,
    product_type: str | None = None,
    client: anthropic.Anthropic | None = None,
    enrich_enabled: bool = True,
) -> dict:
    """Full APEX pipeline for a single document (sync entry point).

    Step order (UniHack guide):
      1. Ingest (done by caller via ingest_file)
      2. De-duplication  ← check_duplicate() called here
      3. Taxonomy        ← classify_product_type()
      4. Attribute extraction ← extract()
      5. Enrichment      ← enrich() + web_enrich()
      6. Cleansing / unit normalisation ← normalize_units()
      7. Description building (in extract/build_descriptions)
      8. Digital assets  (collected by collect_product_assets)
    """
    if client is None:
        client = build_client()

    # ── Step 2: De-duplication ──────────────────────────────────────────────
    # Minimal stub from ingested document for the dedup check.
    # Part number and manufacturer are extracted after taxonomy, but the
    # duplicate check runs early on raw metadata to guard before any LLM cost.
    raw_stub = {
        "name": getattr(doc, "title", "") or "",
        "part_number": getattr(doc, "part_number", "") or "",
        "manufacturer": getattr(doc, "manufacturer", "") or "",
        "product_type": product_type or "",
        "attributes": {},
    }
    dedup = check_duplicate(raw_stub)
    incoming_sku = raw_stub["part_number"] or raw_stub["name"][:32] or "unknown"

    if dedup.is_hard_duplicate:
        # Write immutable audit-log entry — never silently discard
        try:
            log_duplicate_audit(
                incoming_sku=incoming_sku,
                duplicate_of_sku=dedup.duplicate_of_sku or "",
                similarity_score=dedup.similarity_score,
                match_reason=dedup.match_reason,
                matched_signals=dedup.matched_signals,
                alternate_evidence=dedup.alternate_evidence,
                tier="hard",
            )
        except Exception as _e:
            logger.warning("audit log write failed: %s", _e)

        console.log(
            f"[yellow]⚠ Hard duplicate[/] {incoming_sku} → {dedup.duplicate_of_sku} "
            f"(signals={dedup.matched_signals}, sim={dedup.similarity_score:.3f}) "
            f"— audit log written, pipeline skipped"
        )
        # Return FULL record — not empty — preserving alternate evidence
        return {
            "name": raw_stub["name"],
            "part_number": raw_stub["part_number"],
            "manufacturer": raw_stub["manufacturer"],
            "product_type": product_type or "",
            "attributes": {},
            "provenance": {
                "confidence": 0.0,
                "field_sources": {},
                "alternate_evidence": dedup.alternate_evidence,
                "merged_from": dedup.duplicate_of_sku,
            },
            "validation": {
                "issues": [dedup.match_reason],
                "passed_rules": False,
                "needs_human_review": True,  # always route to human for review
                "duplicate_status": "hard_duplicate",
                "duplicate_of_sku": dedup.duplicate_of_sku,
                "duplicate_similarity": dedup.similarity_score,
                "duplicate_signals": dedup.matched_signals,
            },
        }

    if not product_type:
        product_type = classify_product_type(doc, client=client)
    product = extract(doc, product_type, client=client)
    product = normalize_units(product)

    # ── Possible duplicate flag: write audit log + route to human review ─────
    if dedup.is_possible_duplicate:
        try:
            log_duplicate_audit(
                incoming_sku=incoming_sku,
                duplicate_of_sku=dedup.duplicate_of_sku or "",
                similarity_score=dedup.similarity_score,
                match_reason=dedup.match_reason,
                matched_signals=dedup.matched_signals,
                alternate_evidence=dedup.alternate_evidence,
                tier="possible",
            )
        except Exception as _e:
            logger.warning("audit log write failed: %s", _e)

        val = product.setdefault("validation", {})
        val["possible_duplicate"] = True
        val["duplicate_similarity"] = dedup.similarity_score
        val["duplicate_of_sku"] = dedup.duplicate_of_sku
        val["duplicate_signals"] = dedup.matched_signals
        val["needs_human_review"] = True
        val.setdefault("issues", []).append(
            f"Possible duplicate of {dedup.duplicate_of_sku} "
            f"(sim={dedup.similarity_score:.3f}, signals={dedup.matched_signals}) "
            f"— human review required"
        )
        # Attach alternate evidence to provenance so it shows in the evidence drawer
        if dedup.alternate_evidence:
            prov = product.setdefault("provenance", {})
            prov["alternate_evidence"] = dedup.alternate_evidence

    if enrich_enabled:
        product = enrich(product)
    product = _maybe_web_enrich(product, enrich_enabled, client)
    product = validate(product, client=client)
    _index_to_graph(product)
    return product


def run_batch(
    input_path: str | Path,
    output_dir: str | Path,
    product_type: str | None = None,
    enrich_enabled: bool = True,
) -> list[dict]:
    """Process a directory or single file with async concurrency."""
    input_path, output_dir = Path(input_path), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    files = _collect_files(input_path)
    if not files:
        console.print("[yellow]No supported files found.[/]")
        return []
    results, failed = asyncio.run(_async_batch(files, product_type, enrich_enabled))
    _write_outputs(results, output_dir)
    _print_summary(results, failed)
    _print_graph_stats()
    return results


async def _async_batch(
    files: list[Path],
    product_type: str | None,
    enrich_enabled: bool,
) -> tuple[list, list]:
    """Process all files concurrently, up to ASYNC_CONCURRENCY at once."""
    semaphore = asyncio.Semaphore(ASYNC_CONCURRENCY)
    client = build_client()
    tasks = [_async_process_one(f, product_type, enrich_enabled, client, semaphore) for f in files]
    console.print(f"\n[bold]APEX[/] — async processing {len(files)} file(s) " f"(concurrency={ASYNC_CONCURRENCY})\n")
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    results, failed = [], []
    for f, outcome in zip(files, outcomes):
        if isinstance(outcome, Exception):
            failed.append({"file": str(f), "error": str(outcome)})
        elif outcome:
            results.append(outcome)
    return results, failed


async def _async_process_one(
    file: Path,
    product_type: str | None,
    enrich_enabled: bool,
    client: anthropic.Anthropic,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Process one file inside the semaphore gate."""
    async with semaphore:
        loop = asyncio.get_event_loop()
        doc = await loop.run_in_executor(None, ingest_file, file)
        return await loop.run_in_executor(None, run_single, doc, product_type, client, enrich_enabled)


def _maybe_web_enrich(product: dict, enabled: bool, client) -> dict:
    """Web enrich when confidence low OR when fewer than 3 attrs extracted (Fix 5)."""
    if not enabled:
        return product
    prov = product.get("provenance", {})
    confidence = prov.get("confidence", 1.0)
    non_null = sum(1 for v in product.get("attributes", {}).values() if v is not None)
    should_enrich = confidence < CONFIDENCE_WEB_SEARCH_THRESHOLD or non_null < 3
    if not should_enrich:
        return product
    part = product.get("part_number") or product.get("name") or ""
    mfr = product.get("manufacturer") or ""
    pt = product.get("product_type", "")
    query = f"{mfr} {part} {pt} specifications".strip()
    null_fields = [k for k, v in product.get("attributes", {}).items() if v is None]
    enrichment = web_enrich(query, null_fields, product, client)
    return apply_web_enrichment_to_product(product, enrichment)


def _collect_files(input_path: Path) -> list[Path]:
    if input_path.is_dir():
        return [f for f in input_path.iterdir() if f.suffix.lower() in SUPPORTED_EXTENSIONS]
    return [input_path]


def _index_to_graph(product: dict) -> None:
    try:
        graph = load_graph()
        index_product_in_graph(graph, product)
        save_graph(graph)
    except Exception:
        pass


def _write_outputs(results: list, output_dir: Path) -> None:
    if not results:
        return
    export_batch_json(results, output_dir / "products.json")
    export_batch_csv(results, output_dir / "products.csv")
    export_batch_jsonld(results, output_dir / "products.jsonld")
    console.print(f"\n[green]✓[/] {len(results)} products → [bold]{output_dir}/[/]")


def _print_summary(results: list, failed: list) -> None:
    table = Table(title="APEX Run Summary", header_style="bold")
    for col in ("Product", "Type", "Confidence", "Web Enriched", "Review?"):
        table.add_column(col, justify="center" if col != "Product" else "left")
    for p in results:
        prov, val = p.get("provenance", {}), p.get("validation", {})
        conf = prov.get("confidence", 0)
        cc = "green" if conf >= 0.8 else "yellow" if conf >= 0.6 else "red"
        web = len(prov.get("web_enriched_fields", []))
        table.add_row(
            (p.get("name") or p.get("part_number") or "Unknown")[:35],
            p.get("product_type", ""),
            f"[{cc}]{conf:.2f}[/]",
            f"{web} fields" if web else "—",
            "[yellow]YES[/]" if val.get("needs_human_review") else "no",
        )
    for f in failed:
        table.add_row(Path(f["file"]).name, "—", "—", "—", "[red]ERROR[/]")
    console.print()
    console.print(table)


def _print_graph_stats() -> None:
    try:
        stats = graph_stats(load_graph())
        console.print(f"\n[bold]Knowledge Graph:[/] {stats['total_nodes']} nodes, " f"{stats['total_edges']} edges")
    except Exception:
        pass


if __name__ == "__main__":
    import click

    @click.command()
    @click.option("--input", "-i", required=True)
    @click.option("--output", "-o", default="results")
    @click.option("--type", "-t", "product_type", default=None)
    @click.option("--no-enrich", is_flag=True)
    def cli(input, output, product_type, no_enrich):
        """APEX — AI-Powered Product Intelligence Pipeline"""
        run_batch(input, output, product_type, enrich_enabled=not no_enrich)

    cli()
