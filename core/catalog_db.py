"""
core/catalog_db.py — SQLite persistence for products, jobs, reviews.

Local-first: no cloud DB required. File lives at data/catalog.db.
Tables:
  - jobs: pipeline batch runs
  - products: enriched records with approval status
  - reviews: audit trail of human decisions
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

DB_PATH = Path("data/catalog.db")


# ── Connection helpers ────────────────────────────────────────────────────────

def _ensure_dir():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_conn():
    """Context-managed SQLite connection with WAL mode for concurrent reads."""
    _ensure_dir()
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    dataset_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    total_items INTEGER DEFAULT 0,
    processed_items INTEGER DEFAULT 0,
    approved_items INTEGER DEFAULT 0,
    rejected_items INTEGER DEFAULT 0,
    review_items INTEGER DEFAULT 0,
    error_message TEXT,
    cost_estimate REAL DEFAULT 0.0,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    mpn TEXT,
    sku TEXT,
    raw_json TEXT NOT NULL,
    enriched_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    confidence REAL DEFAULT 0.0,
    needs_review INTEGER DEFAULT 0,
    priority_score INTEGER DEFAULT 0,
    reviewer_email TEXT,
    reviewed_at TEXT,
    indexed_in_chroma INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(id),
    action TEXT NOT NULL,
    reviewer_email TEXT NOT NULL,
    changes_json TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS asset_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    url TEXT,
    official_domain TEXT,
    status TEXT NOT NULL,
    evidence TEXT,
    resource_url TEXT,
    rejection_reason TEXT,
    source_coverage_score REAL DEFAULT 0.0,
    needs_human_review INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_products_job ON products(job_id);
CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);
CREATE INDEX IF NOT EXISTS idx_products_review ON products(needs_review, priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_reviews_product ON reviews(product_id);
CREATE INDEX IF NOT EXISTS idx_asset_sources_sku ON asset_sources(sku);
CREATE INDEX IF NOT EXISTS idx_asset_sources_status ON asset_sources(status);
CREATE INDEX IF NOT EXISTS idx_asset_sources_review ON asset_sources(needs_human_review);
"""


def init_db():
    """Create all tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript(_SCHEMA)


# ── Job operations ────────────────────────────────────────────────────────────

def create_job(dataset_name: str, total_items: int = 0) -> str:
    """Create a new pipeline job. Returns job ID."""
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO jobs (id, dataset_name, status, total_items, started_at) "
            "VALUES (?, ?, 'running', ?, ?)",
            (job_id, dataset_name, total_items, _now()),
        )
    return job_id


def update_job(job_id: str, **kwargs):
    """Update job fields. Pass any column name as keyword argument."""
    allowed = {"status", "processed_items", "approved_items", "rejected_items",
               "review_items", "error_message", "cost_estimate", "completed_at",
               "total_items"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [job_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)


def get_job(job_id: str) -> Optional[dict]:
    """Get job by ID."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def get_latest_job() -> Optional[dict]:
    """Get the most recent job."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def get_all_jobs(limit: int = 50) -> List[dict]:
    """Get recent jobs."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── Product operations ────────────────────────────────────────────────────────

def insert_product(job_id: str, raw_row: dict, enriched: dict,
                   confidence: float = 0.0, needs_review: bool = False,
                   priority_score: int = 0) -> str:
    """Insert an enriched product. Returns product ID."""
    prod_id = f"prod_{uuid.uuid4().hex[:12]}"
    mpn = enriched.get("mpn", raw_row.get("Mfg_Part_Num", ""))
    sku = enriched.get("sku", mpn)
    status = "review" if needs_review else "ready"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO products "
            "(id, job_id, mpn, sku, raw_json, enriched_json, status, "
            " confidence, needs_review, priority_score) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (prod_id, job_id, mpn, sku,
             json.dumps(raw_row), json.dumps(enriched),
             status, confidence, int(needs_review), priority_score),
        )
    return prod_id


def update_product_status(product_id: str, status: str,
                          reviewer_email: str = "",
                          enriched_json: Optional[str] = None):
    """Update product status (approved/rejected/ready/review)."""
    with get_conn() as conn:
        if enriched_json:
            conn.execute(
                "UPDATE products SET status=?, reviewer_email=?, reviewed_at=?, "
                "enriched_json=?, updated_at=? WHERE id=?",
                (status, reviewer_email, _now(), enriched_json, _now(), product_id),
            )
        else:
            conn.execute(
                "UPDATE products SET status=?, reviewer_email=?, reviewed_at=?, "
                "updated_at=? WHERE id=?",
                (status, reviewer_email, _now(), _now(), product_id),
            )


def get_product(product_id: str) -> Optional[dict]:
    """Get single product with parsed JSON fields."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["raw"] = json.loads(d.pop("raw_json", "{}"))
        d["enriched"] = json.loads(d.pop("enriched_json", "{}"))
        return d


def get_products_for_job(job_id: str, status: Optional[str] = None) -> List[dict]:
    """Get all products for a job, optionally filtered by status."""
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM products WHERE job_id=? AND status=? ORDER BY priority_score DESC",
                (job_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM products WHERE job_id=? ORDER BY priority_score DESC",
                (job_id,),
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["raw"] = json.loads(d.pop("raw_json", "{}"))
            d["enriched"] = json.loads(d.pop("enriched_json", "{}"))
            result.append(d)
        return result


def get_review_queue(job_id: Optional[str] = None, limit: int = 100) -> List[dict]:
    """Get products needing review, sorted by priority."""
    with get_conn() as conn:
        if job_id:
            rows = conn.execute(
                "SELECT * FROM products WHERE job_id=? AND status='review' "
                "ORDER BY priority_score DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM products WHERE status='review' "
                "ORDER BY priority_score DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["raw"] = json.loads(d.pop("raw_json", "{}"))
            d["enriched"] = json.loads(d.pop("enriched_json", "{}"))
            result.append(d)
        return result


def get_approved_products(job_id: Optional[str] = None) -> List[dict]:
    """Get approved products for export."""
    with get_conn() as conn:
        if job_id:
            rows = conn.execute(
                "SELECT * FROM products WHERE job_id=? AND status='approved' ORDER BY created_at",
                (job_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM products WHERE status='approved' ORDER BY created_at",
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["raw"] = json.loads(d.pop("raw_json", "{}"))
            d["enriched"] = json.loads(d.pop("enriched_json", "{}"))
            result.append(d)
        return result


def mark_indexed(product_id: str):
    """Mark a product as indexed in ChromaDB."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE products SET indexed_in_chroma=1, updated_at=? WHERE id=?",
            (_now(), product_id),
        )


# ── Review operations ─────────────────────────────────────────────────────────

def record_review(product_id: str, action: str, reviewer_email: str,
                  changes: Optional[dict] = None, notes: str = "") -> str:
    """Record a review decision in the audit trail."""
    review_id = f"rev_{uuid.uuid4().hex[:12]}"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO reviews (id, product_id, action, reviewer_email, changes_json, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (review_id, product_id, action, reviewer_email,
             json.dumps(changes) if changes else None, notes),
        )
    return review_id


def get_audit_trail(product_id: str) -> List[dict]:
    """Get review history for a product."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM reviews WHERE product_id=? ORDER BY created_at DESC",
            (product_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Metrics ───────────────────────────────────────────────────────────────────

def compute_job_metrics(job_id: str) -> dict:
    """Compute live metrics for a job."""
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM products WHERE job_id=?", (job_id,)
        ).fetchone()[0]
        approved = conn.execute(
            "SELECT COUNT(*) FROM products WHERE job_id=? AND status='approved'", (job_id,)
        ).fetchone()[0]
        rejected = conn.execute(
            "SELECT COUNT(*) FROM products WHERE job_id=? AND status='rejected'", (job_id,)
        ).fetchone()[0]
        review = conn.execute(
            "SELECT COUNT(*) FROM products WHERE job_id=? AND status='review'", (job_id,)
        ).fetchone()[0]
        ready = conn.execute(
            "SELECT COUNT(*) FROM products WHERE job_id=? AND status='ready'", (job_id,)
        ).fetchone()[0]
        indexed = conn.execute(
            "SELECT COUNT(*) FROM products WHERE job_id=? AND indexed_in_chroma=1", (job_id,)
        ).fetchone()[0]

        # Average confidence
        avg_conf_row = conn.execute(
            "SELECT AVG(confidence) FROM products WHERE job_id=?", (job_id,)
        ).fetchone()
        avg_confidence = avg_conf_row[0] or 0.0

    return {
        "total": total,
        "approved": approved,
        "rejected": rejected,
        "review": review,
        "ready": ready,
        "indexed": indexed,
        "avg_confidence": round(avg_confidence, 3),
    }


def compute_global_metrics() -> dict:
    """Compute metrics across all jobs."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        approved = conn.execute(
            "SELECT COUNT(*) FROM products WHERE status='approved'"
        ).fetchone()[0]
        review = conn.execute(
            "SELECT COUNT(*) FROM products WHERE status='review'"
        ).fetchone()[0]
        jobs_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        indexed = conn.execute(
            "SELECT COUNT(*) FROM products WHERE indexed_in_chroma=1"
        ).fetchone()[0]
    return {
        "total_products": total,
        "approved": approved,
        "pending_review": review,
        "total_jobs": jobs_count,
        "indexed_in_chroma": indexed,
    }


# ── Helpers ────────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Asset Sources ─────────────────────────────────────────────────────────────────────

def upsert_product_sources(sku: str, product_sources) -> None:
    """
    Persist a ProductSources object to the asset_sources table.
    Replaces existing records for the same SKU.

    Args:
        sku: The product MPN/SKU identifier.
        product_sources: schemas.asset.ProductSources instance.
    """
    now = _now()
    coverage = product_sources.source_coverage_score
    review = 1 if product_sources.needs_human_review else 0

    assets = [
        product_sources.product_page,
        product_sources.datasheet,
        *product_sources.images,
    ]

    with get_conn() as conn:
        # Remove existing entries for this SKU before re-inserting
        conn.execute("DELETE FROM asset_sources WHERE sku = ?", (sku,))
        for asset in assets:
            conn.execute(
                """
                INSERT INTO asset_sources
                    (sku, asset_type, url, official_domain, status, evidence,
                     resource_url, rejection_reason, source_coverage_score,
                     needs_human_review, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sku,
                    asset.asset_type,
                    asset.url,
                    asset.official_domain,
                    asset.status.value,
                    asset.evidence,
                    asset.resource_url,
                    asset.rejection_reason,
                    coverage,
                    review,
                    now,
                ),
            )


def get_product_sources(sku: str) -> List[Dict[str, Any]]:
    """
    Retrieve all asset_sources rows for a given SKU.

    Returns:
        List of dicts with asset metadata.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM asset_sources WHERE sku = ? ORDER BY id",
            (sku,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_asset_coverage_stats() -> Dict[str, Any]:
    """
    Aggregate source coverage stats for Analytics page.

    Returns:
        Dict with verified counts, missing assets, and avg coverage.
    """
    with get_conn() as conn:
        total_skus = conn.execute(
            "SELECT COUNT(DISTINCT sku) FROM asset_sources"
        ).fetchone()[0]
        verified_pages = conn.execute(
            "SELECT COUNT(*) FROM asset_sources WHERE asset_type='product_page' AND status='verified'"
        ).fetchone()[0]
        verified_datasheets = conn.execute(
            "SELECT COUNT(*) FROM asset_sources WHERE asset_type='datasheet' AND status='verified'"
        ).fetchone()[0]
        missing_review = conn.execute(
            "SELECT COUNT(DISTINCT sku) FROM asset_sources WHERE needs_human_review=1"
        ).fetchone()[0]
        avg_coverage = conn.execute(
            "SELECT AVG(source_coverage_score) FROM asset_sources"
        ).fetchone()[0] or 0.0
    return {
        "total_skus_with_assets": total_skus,
        "verified_product_pages": verified_pages,
        "verified_datasheets": verified_datasheets,
        "missing_needs_review": missing_review,
        "avg_source_coverage": round(float(avg_coverage), 3),
    }
