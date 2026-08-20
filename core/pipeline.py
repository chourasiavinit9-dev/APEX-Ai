"""
core/pipeline.py — Orchestration only. Zero business logic.

Fix 1 (CRITICAL): Async batch processing with semaphore concurrency limiter.
Fix 5: Auto web-search when fewer than 3 attributes extracted.
All thresholds from core/constants.py.
"""
from __future__ import annotations
import asyncio
import time
from pathlib import Path

import anthropic
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from .constants import (
    CONFIDENCE_WEB_SEARCH_THRESHOLD,
    SUPPORTED_EXTENSIONS,
)
from .ingest import ingest_file, IngestedDocument
from .extractor import extract, classify_product_type, build_client, normalize_units
from .enricher import enrich, index_product
from .validator import validate
from .exporter import export_batch_json, export_batch_csv, export_batch_jsonld
from .web_enricher import web_enrich, apply_web_enrichment_to_product
from .knowledge_graph import load_graph, save_graph, index_product_in_graph, graph_stats

console = Console()
ASYNC_CONCURRENCY = 10  # max parallel Claude calls


def run_single(
    doc: IngestedDocument,
    product_type: str | None = None,
    client: anthropic.Anthropic | None = None,
    enrich_enabled: bool = True,
) -> dict:
    """Full APEX pipeline for a single document (sync entry point)."""
    if client is None:
        client = build_client()
    if not product_type:
        product_type = classify_product_type(doc, client=client)
    product = extract(doc, product_type, client=client)
    product = normalize_units(product)
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
    tasks = [
        _async_process_one(f, product_type, enrich_enabled, client, semaphore)
        for f in files
    ]
    console.print(f"\n[bold]APEX[/] — async processing {len(files)} file(s) "
                  f"(concurrency={ASYNC_CONCURRENCY})\n")
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
        return await loop.run_in_executor(
            None, run_single, doc, product_type, client, enrich_enabled
        )


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
        console.print(f"\n[bold]Knowledge Graph:[/] {stats['total_nodes']} nodes, "
                      f"{stats['total_edges']} edges")
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
