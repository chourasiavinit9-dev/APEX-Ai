"""
evaluate_unilog.py — Ground truth benchmarking script.

Compares pipeline output against the 200-row expected delivery format.
Computes field-level exact-match accuracy, compliance metrics, and reports gaps.
"""
from __future__ import annotations
import csv
import sys
from pathlib import Path
from collections import defaultdict


EVAL_FIELDS = [
    "MANUFACTURER_NAME", "BRAND_NAME", "Classpath", "Dept", "Class", "Fine",
    "INVOICE_DESC", "MOBILE_DESC", "SHORT_DESC", "LONG_DESC1", "RETAIL_DESC",
    "UNSPSC", "ATTRIBUTE_LABEL 1", "ATTRIBUTE_VALUE 1",
]


def load_csv(path: str) -> dict[str, dict]:
    """Load CSV keyed by Mfg_Part_Num."""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return {r["Mfg_Part_Num"]: r for r in csv.DictReader(f)}


def compute_field_accuracy(pred: dict, gt: dict) -> dict[str, float]:
    """Compute exact-match accuracy per field across matched records."""
    matched_mpns = set(pred.keys()) & set(gt.keys())
    if not matched_mpns:
        return {"error": "No matching MPNs found"}
    scores: dict[str, list] = defaultdict(list)
    for mpn in matched_mpns:
        for field in EVAL_FIELDS:
            p_val = (pred[mpn].get(field) or "").strip().lower()
            g_val = (gt[mpn].get(field) or "").strip().lower()
            scores[field].append(1 if p_val == g_val else 0)
    return {f: round(sum(v) / len(v), 4) for f, v in scores.items()}


def compute_compliance(pred: dict) -> dict[str, float]:
    """Compute format compliance metrics across all predicted records."""
    total = len(pred)
    if total == 0:
        return {}
    inv_ok = sum(1 for r in pred.values() if len(r.get("INVOICE_DESC", "")) <= 40)
    inv_caps = sum(1 for r in pred.values() if r.get("INVOICE_DESC", "").isupper())
    mob_ok = sum(1 for r in pred.values() if 60 <= len(r.get("MOBILE_DESC", "")) <= 80)
    return {
        "invoice_length_ok": round(inv_ok / total, 4),
        "invoice_allcaps": round(inv_caps / total, 4),
        "mobile_bounds_ok": round(mob_ok / total, 4),
    }


def run_evaluation(pred_path: str, gt_path: str) -> None:
    """Run full evaluation and print results."""
    pred = load_csv(pred_path)
    gt = load_csv(gt_path)
    matched = set(pred.keys()) & set(gt.keys())
    print(f"Predicted records: {len(pred)}")
    print(f"Ground truth records: {len(gt)}")
    print(f"Matched MPNs: {len(matched)}")

    if matched:
        print("\n=== Field-Level Exact Match Accuracy ===")
        acc = compute_field_accuracy(pred, gt)
        for field, score in acc.items():
            bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
            print(f"  {field:25s} {bar} {score:.1%}")

    print("\n=== Format Compliance (All Predicted) ===")
    comp = compute_compliance(pred)
    for metric, val in comp.items():
        print(f"  {metric:25s} {val:.1%}")

    # Show side-by-side for matched records
    if matched:
        print("\n=== Side-by-Side Comparison ===")
        for mpn in list(matched)[:2]:
            print(f"\nMPN: {mpn}")
            for f in ["MANUFACTURER_NAME", "BRAND_NAME", "INVOICE_DESC"]:
                g = (gt[mpn].get(f) or "")[:60]
                p = (pred[mpn].get(f) or "")[:60]
                m = "✅" if g.strip().lower() == p.strip().lower() else "❌"
                print(f"  {m} {f}:")
                print(f"     Expected: {g}")
                print(f"     Got:      {p}")


if __name__ == "__main__":
    pred_file = sys.argv[1] if len(sys.argv) > 1 else "results/unilog_delivery_output_1000.csv"
    gt_file = sys.argv[2] if len(sys.argv) > 2 else "Unihack_ Expected Output - Delivery Format (1).csv"
    run_evaluation(pred_file, gt_file)
