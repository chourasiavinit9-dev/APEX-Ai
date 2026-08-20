"""
run_unilog.py — CLI entry point for the Unilog enrichment pipeline.

Usage:
    python3 run_unilog.py --input data.csv --output delivery.csv
    python3 run_unilog.py --input data.csv --output delivery.csv --evaluate gt.csv
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path


def main() -> None:
    """Parse CLI arguments and run pipeline."""
    parser = argparse.ArgumentParser(description="APEX Unilog Enrichment Pipeline")
    parser.add_argument("--input", required=True, help="Input CSV path")
    parser.add_argument("--output", default="results/delivery_output.csv", help="Output delivery CSV")
    parser.add_argument("--evaluate", default=None, help="Ground truth CSV for evaluation")
    args = parser.parse_args()

    Path("results").mkdir(parents=True, exist_ok=True)

    from core.unilog_pipeline import run_unilog_batch
    print(f"Processing {args.input}...")
    records = run_unilog_batch(args.input, args.output)
    print(f"Done! {len(records)} records exported to {args.output}")

    if args.evaluate:
        from evaluate_unilog import run_evaluation
        print(f"\nEvaluating against {args.evaluate}...")
        run_evaluation(args.output, args.evaluate)


if __name__ == "__main__":
    main()
