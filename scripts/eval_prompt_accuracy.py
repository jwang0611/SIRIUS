#!/usr/bin/env python
"""
Evaluate SDTM mapping accuracy against ground truth.

Usage:

  # Step 1: Generate benchmark input from ground truth (sample 80 vars)
  python scripts/eval_prompt_accuracy.py --gen-benchmark --sample 80

  # Step 2: Run baseline (old prompt) and improved (new prompt) on the benchmark
  python scripts/generate_sdtm_recommendations.py \
      --input data/processed/benchmark_input.json \
      --output data/output/baseline
  python scripts/generate_sdtm_recommendations.py \
      --input data/processed/benchmark_input.json \
      --output data/output/improved

  # Step 3: Compare results
  python scripts/eval_prompt_accuracy.py \
      --baseline data/output/baseline_*.json \
      --improved data/output/improved_*.json

  # Or evaluate a single output:
  python scripts/eval_prompt_accuracy.py \
      --ai-output data/output/result.json

Ground truth is loaded from data/knowledge_base/structured/ALS2SDTM_TEST.json
by default. Each entry should have: annotation_table, annotation_variable,
metadata_variable, SDTM_Domain, SDTM_Variable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.processors.sdtm_processor import compute_diff_status  # noqa: E402

DEFAULT_GT_PATH = PROJECT_ROOT / "data" / "knowledge_base" / "structured" / "ALS2SDTM_TEST.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WHEN_RE = re.compile(r"\s+when\s+", re.IGNORECASE)


def _normalize_variable(raw: str) -> str:
    """Extract the base variable name, stripping 'when' clauses."""
    if not raw:
        return ""
    return _WHEN_RE.split(raw)[0].strip().upper()


def _normalize_domain(raw: str) -> str:
    """Normalize domain, handling multi-domain patterns like 'TU|TR'."""
    if not raw:
        return ""
    parts = re.split(r"[|/]", raw)
    return parts[0].strip().upper()


def load_ground_truth(path: Path) -> dict[tuple[str, str], dict]:
    """Load ground truth keyed by (annotation_table, metadata_variable)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    gt: dict[tuple[str, str], dict] = {}
    for entry in data:
        key = (
            str(entry.get("annotation_table", "")).strip(),
            str(entry.get("metadata_variable", "")).strip(),
        )
        if key[0] and key[1]:
            gt[key] = entry
    return gt


def load_ai_output(path: Path) -> list[dict]:
    """Load AI output JSON and flatten to per-variable rows."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    rows: list[dict] = []

    if isinstance(data, list) and data:
        first = data[0]
        if "table_recommendations" in first or "domain_recommendations" in first:
            for table_rec in data:
                table_name = table_rec.get("table_name", "")
                orig_mappings = table_rec.get("original_mappings", [])
                ann_table = ""
                if orig_mappings:
                    ann_table = orig_mappings[0].get("annotation_table", "")

                for drec in table_rec.get("domain_recommendations", []):
                    var_name = drec.get("variable_name", "")
                    rows.append(
                        {
                            "metadata_table": table_name,
                            "annotation_table": ann_table or table_name,
                            "metadata_variable": var_name,
                            "ai_domain": _normalize_domain(drec.get("domain", "")),
                            "ai_variable": _normalize_variable(drec.get("sdtm_variable", "")),
                            "score": drec.get("score", 0),
                            "source": drec.get("source", ""),
                            "sdtm_variable_type": drec.get("sdtm_variable_type", ""),
                        }
                    )
        elif "annotation_table" in first:
            for entry in data:
                rows.append(
                    {
                        "metadata_table": entry.get("metadata_table", ""),
                        "annotation_table": entry.get("annotation_table", ""),
                        "metadata_variable": entry.get("metadata_variable", ""),
                        "ai_domain": _normalize_domain(entry.get("SDTM_Domain", "")),
                        "ai_variable": _normalize_variable(entry.get("SDTM_Variable", "")),
                        "score": entry.get("Score", 0),
                        "source": entry.get("Source", ""),
                    }
                )

    return rows


def _dedup_rows(rows: list[dict]) -> list[dict]:
    """Keep only the highest-scored row per (annotation_table, metadata_variable)."""
    best: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["annotation_table"], row["metadata_variable"])
        existing = best.get(key)
        if existing is None or float(row.get("score", 0)) > float(existing.get("score", 0)):
            best[key] = row
    return list(best.values())


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate(
    ai_rows: list[dict],
    gt: dict[tuple[str, str], dict],
    label: str = "AI",
) -> dict:
    """Compare AI rows against ground truth. Return metrics dict."""
    ai_rows = _dedup_rows(ai_rows)

    total = 0
    matched = 0
    domain_matched = 0
    statuses: Counter = Counter()
    domain_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "match": 0, "domain_match": 0})
    source_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "match": 0, "domain_match": 0})
    mismatches: list[dict] = []

    for row in ai_rows:
        key = (row["annotation_table"], row["metadata_variable"])
        ref = gt.get(key)
        if ref is None:
            continue

        ref_domain = _normalize_domain(ref.get("SDTM_Domain", ""))
        ref_variable = _normalize_variable(ref.get("SDTM_Variable", ""))
        ai_domain = row["ai_domain"]
        ai_variable = row["ai_variable"]

        status = compute_diff_status(ai_domain, ai_variable, ref_domain, ref_variable)
        statuses[status] += 1
        total += 1

        source = row.get("source", "LLM") or "LLM"
        domain_key = ref_domain or "UNKNOWN"

        domain_stats[domain_key]["total"] += 1
        source_stats[source]["total"] += 1

        if status == "match":
            matched += 1
            domain_matched += 1
            domain_stats[domain_key]["match"] += 1
            domain_stats[domain_key]["domain_match"] += 1
            source_stats[source]["match"] += 1
            source_stats[source]["domain_match"] += 1
        elif status == "var_diff":
            domain_matched += 1
            domain_stats[domain_key]["domain_match"] += 1
            source_stats[source]["domain_match"] += 1
            mismatches.append(
                {
                    "table": row["annotation_table"],
                    "variable": row["metadata_variable"],
                    "status": status,
                    "ai": f"{ai_domain}.{ai_variable}",
                    "ref": f"{ref_domain}.{ref_variable}",
                }
            )
        else:
            mismatches.append(
                {
                    "table": row["annotation_table"],
                    "variable": row["metadata_variable"],
                    "status": status,
                    "ai": f"{ai_domain}.{ai_variable}",
                    "ref": f"{ref_domain}.{ref_variable}",
                }
            )

    return {
        "label": label,
        "total_evaluated": total,
        "total_ai_rows": len(ai_rows),
        "gt_coverage": total,
        "exact_match": matched,
        "domain_match": domain_matched,
        "exact_rate": matched / total if total else 0,
        "domain_rate": domain_matched / total if total else 0,
        "statuses": dict(statuses),
        "domain_stats": dict(domain_stats),
        "source_stats": dict(source_stats),
        "mismatches": mismatches,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _pct(n: int, total: int) -> str:
    return f"{n / total * 100:.1f}%" if total else "N/A"


def print_report(metrics: dict, verbose: bool = False) -> None:
    label = metrics["label"]
    total = metrics["total_evaluated"]

    print(f"\n{'=' * 64}")
    print(f"  {label} Evaluation Report")
    print(f"{'=' * 64}")
    print(f"  AI output rows:        {metrics['total_ai_rows']}")
    print(f"  Matched to GT:         {total} (variables found in ground truth)")
    print(f"  Exact Match:           {metrics['exact_match']}/{total} = {_pct(metrics['exact_match'], total)}")
    print(f"  Domain Match:          {metrics['domain_match']}/{total} = {_pct(metrics['domain_match'], total)}")
    print(f"  Domain + Var Diff:     {metrics['statuses'].get('var_diff', 0)}")
    print(f"  Domain Diff:           {metrics['statuses'].get('domain_diff', 0)}")
    print()

    ds = metrics["domain_stats"]
    if ds:
        print(f"  {'Domain':<10} {'Total':>6} {'Exact':>6} {'Rate':>8}  {'Dom Match':>10} {'Dom Rate':>8}")
        print(f"  {'-' * 56}")
        for domain in sorted(ds.keys(), key=lambda d: -ds[d]["total"]):
            s = ds[domain]
            print(
                f"  {domain:<10} {s['total']:>6} {s['match']:>6} "
                f"{_pct(s['match'], s['total']):>8}  "
                f"{s['domain_match']:>10} {_pct(s['domain_match'], s['total']):>8}"
            )
        print()

    ss = metrics["source_stats"]
    if ss:
        print(f"  {'Source':<10} {'Total':>6} {'Exact':>6} {'Rate':>8}")
        print(f"  {'-' * 36}")
        for source in sorted(ss.keys(), key=lambda s: -ss[s]["total"]):
            s = ss[source]
            print(f"  {source:<10} {s['total']:>6} {s['match']:>6} {_pct(s['match'], s['total']):>8}")
        print()

    if verbose and metrics["mismatches"]:
        print("  Top Mismatches (showing first 30):")
        print(f"  {'Table':<16} {'Variable':<14} {'Status':<12} {'AI':<30} {'Reference':<30}")
        print(f"  {'-' * 102}")
        for m in metrics["mismatches"][:30]:
            print(
                f"  {m['table'][:15]:<16} {m['variable'][:13]:<14} "
                f"{m['status']:<12} {m['ai'][:29]:<30} {m['ref'][:29]:<30}"
            )
        print()


def print_comparison(baseline: dict, improved: dict) -> None:
    print(f"\n{'=' * 64}")
    print("  A/B Comparison: Baseline vs Improved")
    print(f"{'=' * 64}")

    b_total = baseline["total_evaluated"]
    i_total = improved["total_evaluated"]
    max(b_total, i_total)

    b_exact = baseline["exact_rate"]
    i_exact = improved["exact_rate"]
    b_domain = baseline["domain_rate"]
    i_domain = improved["domain_rate"]

    delta_exact = (i_exact - b_exact) * 100
    delta_domain = (i_domain - b_domain) * 100

    sign_e = "+" if delta_exact >= 0 else ""
    sign_d = "+" if delta_domain >= 0 else ""

    print(f"  {'Metric':<22} {'Baseline':>12} {'Improved':>12} {'Delta':>10}")
    print(f"  {'-' * 58}")
    print(f"  {'Evaluated Vars':<22} {b_total:>12} {i_total:>12}")
    print(f"  {'Exact Match Rate':<22} {b_exact * 100:>11.1f}% {i_exact * 100:>11.1f}% {sign_e}{delta_exact:>8.1f}%")
    print(
        f"  {'Domain Match Rate':<22} {b_domain * 100:>11.1f}% {i_domain * 100:>11.1f}% {sign_d}{delta_domain:>8.1f}%"
    )
    print()

    # Per-domain comparison
    all_domains = sorted(
        set(baseline["domain_stats"].keys()) | set(improved["domain_stats"].keys()),
        key=lambda d: (
            -(baseline["domain_stats"].get(d, {}).get("total", 0) + improved["domain_stats"].get(d, {}).get("total", 0))
        ),
    )

    if all_domains:
        print(f"  {'Domain':<10} {'Base Exact':>12} {'Impr Exact':>12} {'Delta':>10}")
        print(f"  {'-' * 46}")
        for domain in all_domains[:15]:
            bs = baseline["domain_stats"].get(domain, {"total": 0, "match": 0})
            ims = improved["domain_stats"].get(domain, {"total": 0, "match": 0})
            br = bs["match"] / bs["total"] * 100 if bs["total"] else 0
            ir = ims["match"] / ims["total"] * 100 if ims["total"] else 0
            delta = ir - br
            sign = "+" if delta >= 0 else ""
            print(f"  {domain:<10} {br:>11.1f}% {ir:>11.1f}% {sign}{delta:>8.1f}%")
        print()

    # Per-source comparison (only LLM should change with prompt updates)
    all_sources = sorted(
        set(baseline["source_stats"].keys()) | set(improved["source_stats"].keys()),
    )
    if all_sources:
        print(f"  {'Source':<10} {'Base Exact':>12} {'Impr Exact':>12} {'Delta':>10}")
        print(f"  {'-' * 46}")
        for source in all_sources:
            bs = baseline["source_stats"].get(source, {"total": 0, "match": 0})
            ims = improved["source_stats"].get(source, {"total": 0, "match": 0})
            br = bs["match"] / bs["total"] * 100 if bs["total"] else 0
            ir = ims["match"] / ims["total"] * 100 if ims["total"] else 0
            delta = ir - br
            sign = "+" if delta >= 0 else ""
            note = "  ← prompt changes affect this" if source == "LLM" else ""
            print(f"  {source:<10} {br:>11.1f}% {ir:>11.1f}% {sign}{delta:>8.1f}%{note}")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def generate_benchmark_input(
    gt_path: Path,
    output_path: Path,
    sample_size: int = 80,
    seed: int = 42,
) -> None:
    """Sample variables from ground truth to create a benchmark input file.

    Stratified sampling: picks proportionally from each domain so the
    benchmark covers the full domain distribution.
    """
    import random

    with open(gt_path, encoding="utf-8") as f:
        data = json.load(f)

    # Filter out entries without domain or with multi-domain patterns
    clean = [
        e
        for e in data
        if e.get("SDTM_Domain")
        and "|" not in str(e.get("SDTM_Domain", ""))
        and e.get("annotation_table")
        and e.get("metadata_variable")
    ]

    # Group by domain for stratified sampling
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for entry in clean:
        by_domain[entry["SDTM_Domain"]].append(entry)

    rng = random.Random(seed)
    sampled: list[dict] = []
    total_clean = len(clean)
    remaining = sample_size

    # Proportional allocation, minimum 1 per domain
    domain_order = sorted(by_domain.keys(), key=lambda d: -len(by_domain[d]))
    allocations: dict[str, int] = {}

    for domain in domain_order:
        proportion = len(by_domain[domain]) / total_clean
        alloc = max(1, round(proportion * sample_size))
        alloc = min(alloc, len(by_domain[domain]), remaining)
        allocations[domain] = alloc
        remaining -= alloc
        if remaining <= 0:
            break

    for domain, n in allocations.items():
        pool = by_domain[domain]
        picked = rng.sample(pool, min(n, len(pool)))
        sampled.extend(picked)

    # Convert to processor input format
    benchmark_input = []
    for entry in sampled:
        benchmark_input.append(
            {
                "metadata_table": entry.get("metadata_table", ""),
                "metadata_variable": entry.get("metadata_variable", ""),
                "annotation_table": entry.get("annotation_table", ""),
                "annotation_variable": entry.get("annotation_variable", ""),
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(benchmark_input, f, ensure_ascii=False, indent=2)

    # Print summary
    domain_counts = Counter(e.get("SDTM_Domain") for e in sampled)
    print(f"Generated benchmark input: {output_path}")
    print(f"  Total variables: {len(benchmark_input)} (from {len(clean)} clean GT entries)")
    print(f"  Domain coverage: {len(domain_counts)} domains")
    print("  Distribution:")
    for domain, count in domain_counts.most_common():
        print(f"    {domain}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate SDTM mapping accuracy against ground truth.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--ai-output", type=Path, help="Single AI output JSON to evaluate")
    parser.add_argument("--baseline", type=Path, help="Baseline AI output JSON (A/B mode)")
    parser.add_argument("--improved", type=Path, help="Improved AI output JSON (A/B mode)")
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GT_PATH, help="Ground truth JSON path")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show individual mismatches")

    bench_group = parser.add_argument_group("benchmark generation")
    bench_group.add_argument(
        "--gen-benchmark",
        action="store_true",
        help="Generate benchmark input file from ground truth (then run AI on it)",
    )
    bench_group.add_argument("--sample", type=int, default=80, help="Number of variables to sample (default: 80)")
    bench_group.add_argument(
        "--benchmark-output",
        type=Path,
        default=PROJECT_ROOT / "data" / "processed" / "benchmark_input.json",
        help="Output path for benchmark input file",
    )
    bench_group.add_argument("--seed", type=int, default=42, help="Random seed for reproducible sampling")

    args = parser.parse_args()

    if not args.gen_benchmark and not args.ai_output and not (args.baseline and args.improved):
        parser.error("Provide --gen-benchmark, --ai-output, or --baseline + --improved")

    if not args.ground_truth.exists():
        parser.error(f"Ground truth file not found: {args.ground_truth}")

    if args.gen_benchmark:
        generate_benchmark_input(
            gt_path=args.ground_truth,
            output_path=args.benchmark_output,
            sample_size=args.sample,
            seed=args.seed,
        )
        print("\nNext steps:")
        print("  1. git stash  (save new code)")
        print("  2. Run baseline:")
        print("     python scripts/generate_sdtm_recommendations.py \\")
        print(f"         --input {args.benchmark_output} --output data/output/eval_baseline")
        print("  3. git stash pop  (restore new code)")
        print("  4. Run improved:")
        print("     python scripts/generate_sdtm_recommendations.py \\")
        print(f"         --input {args.benchmark_output} --output data/output/eval_improved")
        print("  5. Compare:")
        print("     python scripts/eval_prompt_accuracy.py \\")
        print("         --baseline data/output/eval_baseline_*.json \\")
        print("         --improved data/output/eval_improved_*.json -v")
        return

    gt = load_ground_truth(args.ground_truth)
    print(f"Loaded {len(gt)} ground truth entries from {args.ground_truth.name}")

    if args.ai_output:
        rows = load_ai_output(args.ai_output)
        metrics = evaluate(rows, gt, label=args.ai_output.name)
        print_report(metrics, verbose=args.verbose)

    if args.baseline and args.improved:
        base_rows = load_ai_output(args.baseline)
        impr_rows = load_ai_output(args.improved)

        base_metrics = evaluate(base_rows, gt, label=f"Baseline ({args.baseline.name})")
        impr_metrics = evaluate(impr_rows, gt, label=f"Improved ({args.improved.name})")

        print_report(base_metrics, verbose=args.verbose)
        print_report(impr_metrics, verbose=args.verbose)
        print_comparison(base_metrics, impr_metrics)


if __name__ == "__main__":
    main()
