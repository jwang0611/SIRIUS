#!/usr/bin/env python
"""
Evaluate SDTM mapping accuracy against ground truth.

Usage:

  # Step 1: Generate benchmark input from an explicit held-out set
  python scripts/eval_prompt_accuracy.py \
      --ground-truth data/evaluation/full_pipeline_heldout_v1.json \
      --gen-benchmark

  # Step 2: Run baseline (old prompt) and improved (new prompt) on the benchmark
  python scripts/generate_sdtm_recommendations.py \
      --input data/processed/benchmark_input.json \
      --output data/output/baseline
  python scripts/generate_sdtm_recommendations.py \
      --input data/processed/benchmark_input.json \
      --output data/output/improved

  # Step 3: Compare results
  python scripts/eval_prompt_accuracy.py \
      --ground-truth data/evaluation/full_pipeline_heldout_v1.json \
      --baseline data/output/baseline_*.json \
      --improved data/output/improved_*.json

  # Or evaluate a single output:
  python scripts/eval_prompt_accuracy.py \
      --ground-truth data/evaluation/full_pipeline_heldout_v1.json \
      --ai-output data/output/result.json

Ground truth must be provided explicitly and must not default to a production
knowledge-base file. Each entry should have: metadata_table, metadata_variable,
annotation_table, annotation_variable, SDTM_Domain, and SDTM_Variable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import TypeAlias

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.processors.sdtm_processor import compute_diff_status  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MappingKey: TypeAlias = tuple[str, str, str, str]
_KEY_FIELDS = ("annotation_table", "metadata_table", "annotation_variable", "metadata_variable")


def _normalize_variable(raw: str) -> str:
    """Normalize a complete mapping expression without discarding conditions."""
    if not raw:
        return ""
    value = re.sub(r"\s+", " ", str(raw).strip()).upper()
    return re.sub(r"\s*([=|/;])\s*", r"\1", value)


def _normalize_domain(raw: str) -> str:
    """Normalize the complete domain expression, including multi-domain mappings."""
    if not raw:
        return ""
    value = re.sub(r"\s+", "", str(raw).strip()).upper()
    return value


def _normalize_key_part(raw: object) -> str:
    """Normalize one input-key component for stable matching."""
    return re.sub(r"\s+", " ", str(raw or "").strip()).casefold()


def _mapping_key(entry: dict) -> MappingKey:
    return tuple(_normalize_key_part(entry.get(field, "")) for field in _KEY_FIELDS)  # type: ignore[return-value]


def _render_structured_variable(entry: dict) -> str:
    """Rebuild the display mapping stored in processor JSON recommendations."""
    variable = str(entry.get("sdtm_variable", "") or "").strip()
    if not variable or variable.upper() == "NOT SUBMITTED" or "|" in variable or " when " in variable.lower():
        return _normalize_variable(variable)

    domain = _normalize_domain(entry.get("domain", ""))
    variable_type = str(entry.get("sdtm_variable_type", "") or "").lower()
    supp_variable = str(entry.get("supp_variable", "") or "").strip()
    testcd = str(entry.get("testcd", "") or "").strip()

    if variable_type == "supp" and supp_variable:
        variable = f"QVAL when QNAM={supp_variable}"
    if testcd and domain:
        domain_prefix = domain.split("|", 1)[0][:2]
        variable = f"{variable} when {domain_prefix}TESTCD={testcd}"
    return _normalize_variable(variable)


def _mapping_status(ai_domain: str, ai_variable: str, ref_domain: str, ref_variable: str) -> str:
    """Compare complete mappings while treating NOT SUBMITTED as domainless."""
    ai_not_submitted = ai_variable == "NOT SUBMITTED"
    ref_not_submitted = ref_variable == "NOT SUBMITTED"
    if ai_not_submitted or ref_not_submitted:
        return "match" if ai_not_submitted and ref_not_submitted else "domain_diff"
    return compute_diff_status(ai_domain, ai_variable, ref_domain, ref_variable)


def load_ground_truth(path: Path) -> dict[MappingKey, dict]:
    """Load ground truth using the full four-field input identity."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"Ground truth must be a JSON list: {path}")

    gt: dict[MappingKey, dict] = {}
    for entry in data:
        key = _mapping_key(entry)
        if not all(key):
            evaluation_id = entry.get("evaluation_id", "<unknown>")
            raise ValueError(f"Incomplete ground-truth input key at {evaluation_id}: {key}")
        if key in gt:
            evaluation_id = entry.get("evaluation_id", "<unknown>")
            raise ValueError(f"Duplicate ground-truth input key at {evaluation_id}: {key}")
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
                original_by_variable: dict[str, list[dict]] = defaultdict(list)
                for mapping in orig_mappings:
                    original_by_variable[str(mapping.get("metadata_variable", ""))].append(mapping)

                for drec in table_rec.get("domain_recommendations", []):
                    var_name = drec.get("variable_name", "")
                    source_mapping = (original_by_variable.get(str(var_name)) or [{}])[0]
                    rows.append(
                        {
                            "metadata_table": table_name,
                            "annotation_table": source_mapping.get("annotation_table", table_name),
                            "annotation_variable": source_mapping.get("annotation_variable", ""),
                            "metadata_variable": var_name,
                            "ai_domain": _normalize_domain(drec.get("domain", "")),
                            "ai_variable": _render_structured_variable(drec),
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
                        "annotation_variable": entry.get("annotation_variable", ""),
                        "metadata_variable": entry.get("metadata_variable", ""),
                        "ai_domain": _normalize_domain(entry.get("SDTM_Domain", "")),
                        "ai_variable": _normalize_variable(entry.get("SDTM_Variable", "")),
                        "score": entry.get("Score", 0),
                        "source": entry.get("Source", ""),
                    }
                )

    return rows


def _dedup_rows(rows: list[dict]) -> list[dict]:
    """Keep only the highest-scored row per full four-field input identity."""
    best: dict[MappingKey, dict] = {}
    for row in rows:
        key = _mapping_key(row)
        existing = best.get(key)
        if existing is None or float(row.get("score", 0)) > float(existing.get("score", 0)):
            best[key] = row
    return list(best.values())


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate(
    ai_rows: list[dict],
    gt: dict[MappingKey, dict],
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
    cohort_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "match": 0, "domain_match": 0})
    mismatches: list[dict] = []

    for row in ai_rows:
        key = _mapping_key(row)
        ref = gt.get(key)
        if ref is None:
            continue

        ref_domain = _normalize_domain(ref.get("SDTM_Domain", ""))
        ref_variable = _normalize_variable(ref.get("SDTM_Variable", ""))
        ai_domain = row["ai_domain"]
        ai_variable = row["ai_variable"]

        status = _mapping_status(ai_domain, ai_variable, ref_domain, ref_variable)
        statuses[status] += 1
        total += 1

        source = row.get("source", "LLM") or "LLM"
        cohort = ref.get("evaluation_cohort") or ref.get("reference_source") or "UNSPECIFIED"
        domain_key = "NOT_SUBMITTED" if ref_variable == "NOT SUBMITTED" else (ref_domain or "UNKNOWN")

        domain_stats[domain_key]["total"] += 1
        source_stats[source]["total"] += 1
        cohort_stats[cohort]["total"] += 1

        if status == "match":
            matched += 1
            domain_matched += 1
            domain_stats[domain_key]["match"] += 1
            domain_stats[domain_key]["domain_match"] += 1
            source_stats[source]["match"] += 1
            source_stats[source]["domain_match"] += 1
            cohort_stats[cohort]["match"] += 1
            cohort_stats[cohort]["domain_match"] += 1
        elif status == "var_diff":
            domain_matched += 1
            domain_stats[domain_key]["domain_match"] += 1
            source_stats[source]["domain_match"] += 1
            cohort_stats[cohort]["domain_match"] += 1
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
        "cohort_stats": dict(cohort_stats),
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
        print("  Actual cascade source:")
        print(f"  {'Source':<18} {'Total':>6} {'Exact':>6} {'Rate':>8}")
        print(f"  {'-' * 36}")
        for source in sorted(ss.keys(), key=lambda s: -ss[s]["total"]):
            s = ss[source]
            print(f"  {source:<18} {s['total']:>6} {s['match']:>6} {_pct(s['match'], s['total']):>8}")
        print()

    cs = metrics["cohort_stats"]
    if cs:
        print("  Held-out cohort:")
        print(f"  {'Cohort':<18} {'Total':>6} {'Exact':>6} {'Rate':>8}")
        print(f"  {'-' * 44}")
        for cohort in sorted(cs.keys(), key=lambda c: -cs[c]["total"]):
            s = cs[cohort]
            print(f"  {cohort:<18} {s['total']:>6} {s['match']:>6} {_pct(s['match'], s['total']):>8}")
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

    # Per-source comparison
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
            print(f"  {source:<10} {br:>11.1f}% {ir:>11.1f}% {sign}{delta:>8.1f}%")
        print()

    all_cohorts = sorted(set(baseline["cohort_stats"]) | set(improved["cohort_stats"]))
    if all_cohorts:
        print(f"  {'Cohort':<20} {'Base Exact':>12} {'Impr Exact':>12} {'Delta':>10}")
        print(f"  {'-' * 56}")
        for cohort in all_cohorts:
            bs = baseline["cohort_stats"].get(cohort, {"total": 0, "match": 0})
            ims = improved["cohort_stats"].get(cohort, {"total": 0, "match": 0})
            br = bs["match"] / bs["total"] * 100 if bs["total"] else 0
            ir = ims["match"] / ims["total"] * 100 if ims["total"] else 0
            delta = ir - br
            sign = "+" if delta >= 0 else ""
            print(f"  {cohort:<20} {br:>11.1f}% {ir:>11.1f}% {sign}{delta:>8.1f}%")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def generate_benchmark_input(
    gt_path: Path,
    output_path: Path,
    sample_size: int = 0,
    seed: int = 42,
) -> None:
    """Create a leak-free processor input from held-out ground truth.

    A sample size of zero includes the complete held-out set. Positive sample
    sizes are selected round-robin across cohort/source/domain strata so both
    KB and AI-recommendation paths remain represented.
    """
    import random

    load_ground_truth(gt_path)  # validate completeness and uniqueness before sampling
    with open(gt_path, encoding="utf-8") as f:
        data = json.load(f)

    # A valid reference either has a domain+variable or is explicitly NOT SUBMITTED.
    clean = [
        e
        for e in data
        if all(str(e.get(field, "") or "").strip() for field in _KEY_FIELDS)
        and str(e.get("SDTM_Variable", "") or "").strip()
        and (
            str(e.get("SDTM_Domain", "") or "").strip()
            or str(e.get("SDTM_Variable", "") or "").strip().upper() == "NOT SUBMITTED"
        )
    ]

    rng = random.Random(seed)
    if sample_size <= 0 or sample_size >= len(clean):
        sampled = clean
    else:
        strata: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        for entry in clean:
            cohort = str(entry.get("evaluation_cohort") or entry.get("reference_source") or "UNSPECIFIED")
            reference_source = str(entry.get("reference_source") or "UNSPECIFIED")
            domain = str(entry.get("SDTM_Domain") or "NOT_SUBMITTED")
            strata[(cohort, reference_source, domain)].append(entry)
        for pool in strata.values():
            rng.shuffle(pool)

        sampled = []
        active = sorted(strata)
        while active and len(sampled) < sample_size:
            next_active: list[tuple[str, str, str]] = []
            for key in active:
                pool = strata[key]
                if pool and len(sampled) < sample_size:
                    sampled.append(pool.pop())
                if pool:
                    next_active.append(key)
            active = next_active

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
    domain_counts = Counter(e.get("SDTM_Domain") or "NOT_SUBMITTED" for e in sampled)
    cohort_counts = Counter(e.get("evaluation_cohort") or e.get("reference_source") or "UNSPECIFIED" for e in sampled)
    print(f"Generated benchmark input: {output_path}")
    print(f"  Total variables: {len(benchmark_input)} (from {len(clean)} valid GT entries)")
    print(f"  Domain coverage: {len(domain_counts)} domains")
    print(f"  Cohorts: {dict(cohort_counts)}")
    print("  Domain distribution:")
    for domain, count in domain_counts.most_common():
        print(f"    {domain}: {count}")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser so required evaluation inputs are testable."""
    parser = argparse.ArgumentParser(
        description="Evaluate SDTM mapping accuracy against ground truth.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--ai-output", type=Path, help="Single AI output JSON to evaluate")
    parser.add_argument("--baseline", type=Path, help="Baseline AI output JSON (A/B mode)")
    parser.add_argument("--improved", type=Path, help="Improved AI output JSON (A/B mode)")
    parser.add_argument(
        "--ground-truth",
        type=Path,
        required=True,
        help="Explicit held-out ground truth JSON path (never defaults to the production KB)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show individual mismatches")

    bench_group = parser.add_argument_group("benchmark generation")
    bench_group.add_argument(
        "--gen-benchmark",
        action="store_true",
        help="Generate benchmark input file from ground truth (then run AI on it)",
    )
    bench_group.add_argument(
        "--sample",
        type=int,
        default=0,
        help="Number of variables to sample; 0 evaluates the complete held-out set (default: 0)",
    )
    bench_group.add_argument(
        "--benchmark-output",
        type=Path,
        default=PROJECT_ROOT / "data" / "processed" / "benchmark_input.json",
        help="Output path for benchmark input file",
    )
    bench_group.add_argument("--seed", type=int, default=42, help="Random seed for reproducible sampling")
    return parser


def main() -> None:
    parser = build_parser()

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
        print(f"         --ground-truth {args.ground_truth} \\")
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
