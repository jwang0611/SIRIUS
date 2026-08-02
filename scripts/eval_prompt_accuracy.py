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
      --json-file data/processed/benchmark_input.json \
      --output data/output/baseline
  python scripts/generate_sdtm_recommendations.py \
      --json-file data/processed/benchmark_input.json \
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
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import TypeAlias

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.ab_analysis import (  # noqa: E402
    classify_scenarios,
    count_quality_issues,
    evaluate_acceptance,
    paired_bootstrap,
    paired_cohort_outcomes,
)
from src.evaluation.run_manifest import compare_shared_configuration, hash_file  # noqa: E402
from src.processors.mapping_critic import MappingCritic  # noqa: E402
from src.processors.sdtm_processor import compute_diff_status  # noqa: E402

logger = logging.getLogger(__name__)
AB_REPORT_SCHEMA_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MappingKey: TypeAlias = tuple[str, str, str, str]
_KEY_FIELDS = ("annotation_table", "metadata_table", "annotation_variable", "metadata_variable")
_VALIDATION_FLAGS = (
    "invalid_domain_corrected",
    "variable_name_corrected",
    "variable_name_truncated",
    "domain_prefix_mismatch",
    "non_standard_variable",
    "auto_corrected_to_supp",
)


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
    unmatched_original_variables: set[str] = set()

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
                    source_mappings = original_by_variable.get(str(var_name)) or []
                    if not source_mappings:
                        display_name = str(var_name or "<missing variable_name>")
                        unmatched_original_variables.add(f"{table_name}.{display_name}")
                    source_mapping = (source_mappings or [{}])[0]
                    rows.append(
                        {
                            "metadata_table": table_name,
                            "annotation_table": source_mapping.get("annotation_table", table_name),
                            "annotation_variable": source_mapping.get("annotation_variable", ""),
                            "metadata_variable": var_name,
                            "ai_domain": _normalize_domain(drec.get("domain", "")),
                            "ai_variable": _render_structured_variable(drec),
                            "domain": _normalize_domain(drec.get("domain", "")),
                            "sdtm_variable": str(drec.get("sdtm_variable", "") or "").strip(),
                            "score": drec.get("score", 0),
                            "source": drec.get("source", ""),
                            "cascade_level": drec.get("cascade_level"),
                            "sdtm_variable_type": drec.get("sdtm_variable_type", ""),
                            "supp_dataset": drec.get("supp_dataset", ""),
                            "supp_variable": drec.get("supp_variable", ""),
                            "testcd": drec.get("testcd", ""),
                            "fallback_reason": drec.get("fallback_reason", ""),
                            "validation_flags": {key: drec.get(key) for key in _VALIDATION_FLAGS if drec.get(key)},
                            "consistency_issues": table_rec.get("consistency_issues", []),
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
                        "domain": _normalize_domain(entry.get("SDTM_Domain", "")),
                        "sdtm_variable": str(entry.get("SDTM_Variable", "") or "").strip(),
                        "score": entry.get("Score", 0),
                        "source": entry.get("Source", ""),
                        "cascade_level": entry.get("cascade_level", entry.get("Cascade_Level")),
                        "sdtm_variable_type": entry.get("sdtm_variable_type", ""),
                        "supp_dataset": entry.get("supp_dataset", ""),
                        "supp_variable": entry.get("supp_variable", ""),
                        "testcd": entry.get("testcd", ""),
                        "fallback_reason": entry.get("fallback_reason", ""),
                        "validation_flags": {key: entry.get(key) for key in _VALIDATION_FLAGS if entry.get(key)},
                        "consistency_issues": entry.get("consistency_issues", []),
                    }
                )

    if unmatched_original_variables:
        logger.warning(
            "Could not match processor recommendations to original_mappings; "
            "these variables cannot be scored against the four-field ground-truth key: %s",
            ", ".join(sorted(unmatched_original_variables)),
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


def _recompute_consistency_issues(rows: list[dict]) -> list[dict]:
    """Apply the current MappingCritic equally to both A/B output arms."""
    recommendations = [
        {
            **row,
            "variable_name": row.get("metadata_variable", ""),
            "annotation_table": row.get("annotation_table", ""),
            "metadata_table": row.get("metadata_table", ""),
        }
        for row in rows
    ]
    original_mappings = [
        {
            "metadata_table": row.get("metadata_table", ""),
            "metadata_variable": row.get("metadata_variable", ""),
        }
        for row in rows
    ]
    return [
        issue.to_dict()
        for issue in MappingCritic().criticize(
            recommendations,
            original_mappings,
        )
    ]


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
    ai_by_key = {_mapping_key(row): row for row in ai_rows}
    gt_size = len(gt)
    ai_keys = set(ai_by_key)
    gt_keys = set(gt)
    missing_gt_keys = sorted(gt_keys - ai_keys)
    extra_ai_keys = sorted(ai_keys - gt_keys)

    total = 0
    matched = 0
    domain_matched = 0
    statuses: Counter = Counter()
    domain_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"gt_size": 0, "total": 0, "match": 0, "domain_match": 0}
    )
    source_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "match": 0, "domain_match": 0})
    cascade_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "match": 0, "domain_match": 0})
    cohort_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"gt_size": 0, "total": 0, "match": 0, "domain_match": 0}
    )
    scenario_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"gt_size": 0, "total": 0, "match": 0, "domain_match": 0}
    )
    mismatches: list[dict] = []
    row_results: list[dict] = []

    def update_slice(stats: dict[str, int], *, exact: bool, domain_match: bool) -> None:
        stats["total"] += 1
        if exact:
            stats["match"] += 1
        if domain_match:
            stats["domain_match"] += 1

    for key, ref in gt.items():
        ref_domain = _normalize_domain(ref.get("SDTM_Domain", ""))
        ref_variable = _normalize_variable(ref.get("SDTM_Variable", ""))
        cohort = ref.get("evaluation_cohort") or ref.get("reference_source") or "UNSPECIFIED"
        domain_key = "NOT_SUBMITTED" if ref_variable == "NOT SUBMITTED" else (ref_domain or "UNKNOWN")
        scenarios = classify_scenarios(ref)

        cohort_stats[cohort]["gt_size"] += 1
        domain_stats[domain_key]["gt_size"] += 1
        for scenario in scenarios:
            scenario_stats[scenario]["gt_size"] += 1

        row = ai_by_key.get(key)
        if row is None:
            statuses["missing"] += 1
            row_results.append(
                {
                    "evaluation_id": ref.get("evaluation_id", ""),
                    "cohort": cohort,
                    "domain": domain_key,
                    "source": None,
                    "cascade_level": None,
                    "scenarios": sorted(scenarios),
                    "status": "missing",
                    "exact_match": False,
                    "domain_match": False,
                }
            )
            continue
        ai_domain = row["ai_domain"]
        ai_variable = row["ai_variable"]

        status = _mapping_status(ai_domain, ai_variable, ref_domain, ref_variable)
        statuses[status] += 1
        total += 1
        source = row.get("source", "LLM") or "LLM"
        cascade_level = row.get("cascade_level")
        cascade_key = "UNRECORDED" if cascade_level is None else str(cascade_level)
        exact = status == "match"
        domain_match = status in {"match", "var_diff"}

        update_slice(domain_stats[domain_key], exact=exact, domain_match=domain_match)
        update_slice(source_stats[source], exact=exact, domain_match=domain_match)
        update_slice(cascade_stats[cascade_key], exact=exact, domain_match=domain_match)
        update_slice(cohort_stats[cohort], exact=exact, domain_match=domain_match)
        for scenario in scenarios:
            update_slice(scenario_stats[scenario], exact=exact, domain_match=domain_match)

        if exact:
            matched += 1
            domain_matched += 1
        elif status == "var_diff":
            domain_matched += 1
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
        row_results.append(
            {
                "evaluation_id": ref.get("evaluation_id", ""),
                "cohort": cohort,
                "domain": domain_key,
                "source": source,
                "cascade_level": cascade_level,
                "scenarios": sorted(scenarios),
                "status": status,
                "exact_match": exact,
                "domain_match": domain_match,
            }
        )

    coverage = total / gt_size if gt_size else 0
    consistency_issues = _recompute_consistency_issues(ai_rows)
    return {
        "label": label,
        "gt_size": gt_size,
        "total_evaluated": total,
        "total_ai_rows": len(ai_rows),
        "coverage": coverage,
        "missing_gt_rows": len(missing_gt_keys),
        "extra_ai_rows": len(extra_ai_keys),
        "missing_gt_keys": missing_gt_keys,
        "extra_ai_keys": extra_ai_keys,
        "exact_match": matched,
        "domain_match": domain_matched,
        "exact_rate": matched / gt_size if gt_size else 0,
        "domain_rate": domain_matched / gt_size if gt_size else 0,
        "evaluated_exact_rate": matched / total if total else 0,
        "evaluated_domain_rate": domain_matched / total if total else 0,
        "statuses": dict(statuses),
        "domain_stats": dict(domain_stats),
        "source_stats": dict(source_stats),
        "cascade_stats": dict(cascade_stats),
        "cohort_stats": dict(cohort_stats),
        "scenario_stats": dict(scenario_stats),
        "row_results": row_results,
        "quality_issues": count_quality_issues(
            ai_rows,
            consistency_issues=consistency_issues,
        ),
        "cohort_policy": {
            "KB_AGREE": {"diagnostic_only": False, "release_gate": True},
            "KB_DISAGREE": {"diagnostic_only": True, "release_gate": False},
            "AI_RECOMMENDATION": {"diagnostic_only": False, "release_gate": True},
        },
        "mismatches": mismatches,
    }


def build_ab_report(
    baseline: dict,
    improved: dict,
    *,
    baseline_manifest: dict,
    improved_manifest: dict,
    required_tests: dict,
    bootstrap_seed: int,
    bootstrap_replicates: int,
) -> dict:
    """Build the complete machine-readable A/B evidence package."""
    paired_outcomes = paired_cohort_outcomes(
        baseline,
        improved,
        cohort="AI_RECOMMENDATION",
    )
    paired_analysis = paired_bootstrap(
        paired_outcomes["baseline"],
        paired_outcomes["improved"],
        seed=bootstrap_seed,
        replicates=bootstrap_replicates,
    )
    paired_analysis["cohort"] = "AI_RECOMMENDATION"
    manifest_comparison = compare_shared_configuration(
        baseline_manifest,
        improved_manifest,
    )
    acceptance = evaluate_acceptance(
        baseline,
        improved,
        paired=paired_analysis,
        manifest_comparison=manifest_comparison,
        required_tests=required_tests,
    )
    return {
        "schema_version": AB_REPORT_SCHEMA_VERSION,
        "manifests": {
            "baseline": baseline_manifest,
            "improved": improved_manifest,
        },
        "baseline": baseline,
        "improved": improved,
        "paired_analysis": paired_analysis,
        "quality": {
            "baseline": baseline["quality_issues"],
            "improved": improved["quality_issues"],
        },
        "manifest_comparison": manifest_comparison,
        "required_tests": required_tests,
        "gates": acceptance["gates"],
        "diagnostic_only_cohorts": acceptance["diagnostic_only_cohorts"],
        "decision": acceptance["decision"],
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _pct(n: int, total: int) -> str:
    return f"{n / total * 100:.1f}%" if total else "N/A"


def print_report(metrics: dict, verbose: bool = False) -> None:
    label = metrics["label"]
    total = metrics["total_evaluated"]
    gt_size = metrics["gt_size"]

    print(f"\n{'=' * 64}")
    print(f"  {label} Evaluation Report")
    print(f"{'=' * 64}")
    print(f"  AI output rows:        {metrics['total_ai_rows']}")
    print(f"  Ground-truth rows:     {gt_size}")
    print(f"  Coverage:              {total}/{gt_size} = {_pct(total, gt_size)}")
    print(f"  Missing GT outputs:    {metrics['missing_gt_rows']}")
    print(f"  AI rows outside GT:    {metrics['extra_ai_rows']}")
    print(f"  Exact Match / GT:      {metrics['exact_match']}/{gt_size} = {_pct(metrics['exact_match'], gt_size)}")
    print(f"  Domain Match / GT:     {metrics['domain_match']}/{gt_size} = {_pct(metrics['domain_match'], gt_size)}")
    print(f"  Exact among evaluated: {metrics['exact_match']}/{total} = {_pct(metrics['exact_match'], total)}")
    print(f"  Domain + Var Diff:     {metrics['statuses'].get('var_diff', 0)}")
    print(f"  Domain Diff:           {metrics['statuses'].get('domain_diff', 0)}")
    if metrics["coverage"] < 1:
        print("  WARNING: Coverage is below 100%; missing outputs count as non-matches in headline rates.")
    print()

    ds = metrics["domain_stats"]
    if ds:
        print(
            f"  {'Domain':<14} {'GT':>5} {'Eval':>5} {'Coverage':>9} "
            f"{'Exact':>6} {'Rate':>8} {'Dom Match':>10} {'Dom Rate':>8}"
        )
        print(f"  {'-' * 76}")
        for domain in sorted(ds.keys(), key=lambda d: -ds[d]["gt_size"]):
            s = ds[domain]
            print(
                f"  {domain:<14} {s['gt_size']:>5} {s['total']:>5} "
                f"{_pct(s['total'], s['gt_size']):>9} {s['match']:>6} "
                f"{_pct(s['match'], s['gt_size']):>8} {s['domain_match']:>10} "
                f"{_pct(s['domain_match'], s['gt_size']):>8}"
            )
        print()

    ss = metrics["source_stats"]
    if ss:
        print("  Actual cascade source:")
        print(f"  {'Source':<18} {'Total':>6} {'Exact':>6} {'Rate':>8} {'Domain':>8} {'Rate':>8}")
        print(f"  {'-' * 54}")
        for source in sorted(ss.keys(), key=lambda s: -ss[s]["total"]):
            s = ss[source]
            print(
                f"  {source:<18} {s['total']:>6} {s['match']:>6} "
                f"{_pct(s['match'], s['total']):>8} {s['domain_match']:>8} "
                f"{_pct(s['domain_match'], s['total']):>8}"
            )
        print()

    cascade_stats = metrics["cascade_stats"]
    if cascade_stats:
        print("  Cascade level:")
        print(f"  {'Level':<18} {'Total':>6} {'Exact':>6} {'Rate':>8} {'Domain':>8} {'Rate':>8}")
        print(f"  {'-' * 54}")
        for level in sorted(cascade_stats):
            stats = cascade_stats[level]
            print(
                f"  {level:<18} {stats['total']:>6} {stats['match']:>6} "
                f"{_pct(stats['match'], stats['total']):>8} "
                f"{stats['domain_match']:>8} "
                f"{_pct(stats['domain_match'], stats['total']):>8}"
            )
        print()

    cs = metrics["cohort_stats"]
    if cs:
        print("  Held-out cohort:")
        print(
            f"  {'Cohort':<20} {'GT':>5} {'Eval':>5} {'Coverage':>9} {'Exact':>6} {'Rate':>8} {'Domain':>7} {'Rate':>8}"
        )
        print(f"  {'-' * 78}")
        for cohort in sorted(cs.keys(), key=lambda c: -cs[c]["gt_size"]):
            s = cs[cohort]
            print(
                f"  {cohort:<20} {s['gt_size']:>5} {s['total']:>5} "
                f"{_pct(s['total'], s['gt_size']):>9} {s['match']:>6} "
                f"{_pct(s['match'], s['gt_size']):>8} {s['domain_match']:>7} "
                f"{_pct(s['domain_match'], s['gt_size']):>8}"
            )
        print()

    scenario_stats = metrics["scenario_stats"]
    if scenario_stats:
        print("  Special scenarios:")
        print(f"  {'Scenario':<22} {'GT':>5} {'Eval':>5} {'Exact':>6} {'Rate':>8} {'Domain':>7} {'Rate':>8}")
        print(f"  {'-' * 72}")
        for scenario in sorted(scenario_stats):
            stats = scenario_stats[scenario]
            print(
                f"  {scenario:<22} {stats['gt_size']:>5} {stats['total']:>5} "
                f"{stats['match']:>6} {_pct(stats['match'], stats['gt_size']):>8} "
                f"{stats['domain_match']:>7} "
                f"{_pct(stats['domain_match'], stats['gt_size']):>8}"
            )
        print()

    print("  Deterministic quality counters:")
    for name, count in metrics["quality_issues"].items():
        print(f"    {name}: {count}")
    print()

    if verbose and metrics["missing_gt_keys"]:
        print("  Missing GT Outputs (showing first 30):")
        for key in metrics["missing_gt_keys"][:30]:
            annotation_table, metadata_table, annotation_variable, metadata_variable = key
            print(f"    {annotation_table} / {metadata_table} / {annotation_variable} / {metadata_variable}")
        print()

    if verbose and metrics["extra_ai_keys"]:
        print("  AI Outputs Outside Ground Truth (showing first 30):")
        for key in metrics["extra_ai_keys"][:30]:
            annotation_table, metadata_table, annotation_variable, metadata_variable = key
            print(f"    {annotation_table} / {metadata_table} / {annotation_variable} / {metadata_variable}")
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

    b_exact = baseline["exact_rate"]
    i_exact = improved["exact_rate"]
    b_domain = baseline["domain_rate"]
    i_domain = improved["domain_rate"]
    b_coverage = baseline["coverage"]
    i_coverage = improved["coverage"]

    delta_exact = (i_exact - b_exact) * 100
    delta_domain = (i_domain - b_domain) * 100
    delta_coverage = (i_coverage - b_coverage) * 100

    sign_e = "+" if delta_exact >= 0 else ""
    sign_d = "+" if delta_domain >= 0 else ""
    sign_c = "+" if delta_coverage >= 0 else ""

    print(f"  {'Metric':<22} {'Baseline':>12} {'Improved':>12} {'Delta':>10}")
    print(f"  {'-' * 58}")
    print(f"  {'Ground-truth Vars':<22} {baseline['gt_size']:>12} {improved['gt_size']:>12}")
    print(f"  {'Evaluated Vars':<22} {b_total:>12} {i_total:>12}")
    print(f"  {'Coverage':<22} {b_coverage * 100:>11.1f}% {i_coverage * 100:>11.1f}% {sign_c}{delta_coverage:>8.1f}%")
    print(f"  {'Exact Match / GT':<22} {b_exact * 100:>11.1f}% {i_exact * 100:>11.1f}% {sign_e}{delta_exact:>8.1f}%")
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
            bs = baseline["domain_stats"].get(domain, {"gt_size": 0, "match": 0})
            ims = improved["domain_stats"].get(domain, {"gt_size": 0, "match": 0})
            br = bs["match"] / bs["gt_size"] * 100 if bs["gt_size"] else 0
            ir = ims["match"] / ims["gt_size"] * 100 if ims["gt_size"] else 0
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
            bs = baseline["cohort_stats"].get(cohort, {"gt_size": 0, "match": 0})
            ims = improved["cohort_stats"].get(cohort, {"gt_size": 0, "match": 0})
            br = bs["match"] / bs["gt_size"] * 100 if bs["gt_size"] else 0
            ir = ims["match"] / ims["gt_size"] * 100 if ims["gt_size"] else 0
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
    parser.add_argument("--baseline-manifest", type=Path, help="Baseline run manifest JSON")
    parser.add_argument("--improved-manifest", type=Path, help="Improved run manifest JSON")
    parser.add_argument("--report-json", type=Path, help="Write the complete A/B evidence report")
    parser.add_argument(
        "--bootstrap-seed",
        type=int,
        default=20260726,
        help="Paired-bootstrap random seed (default: 20260726)",
    )
    parser.add_argument(
        "--bootstrap-replicates",
        type=int,
        default=10_000,
        help="Paired-bootstrap replicate count (default: 10000)",
    )
    parser.add_argument(
        "--required-tests-json",
        type=Path,
        help="JSON evidence for the required offline validation commands",
    )
    parser.add_argument(
        "--require-acceptance",
        action="store_true",
        help="Exit 1 unless every release gate accepts the improved run",
    )
    parser.add_argument(
        "--ground-truth",
        type=Path,
        required=True,
        help="Explicit held-out ground truth JSON path (never defaults to the production KB)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show individual mismatches")
    parser.add_argument(
        "--require-full-coverage",
        action="store_true",
        help="Exit non-zero if any evaluated output covers less than 100%% of ground truth",
    )

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


def _load_json_object(path: Path, *, label: str) -> dict:
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load {label} JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON must contain an object: {path}")
    return payload


def _validate_run_evidence(
    manifest: dict,
    *,
    label: str,
    output_path: Path,
    heldout_path: Path,
) -> dict:
    """Bind one successful clean manifest to the files being evaluated."""
    errors: list[str] = []

    if manifest.get("status") != "succeeded":
        errors.append(f"{label} manifest terminal status must be 'succeeded'")

    runner_record = manifest.get("runner", {})
    if not runner_record.get("script_sha256"):
        errors.append(f"{label} manifest is missing experiment-runner hash")

    git_record = manifest.get("git", {})
    if not git_record.get("sha") or git_record.get("dirty") is not False:
        errors.append(f"{label} manifest must identify a clean Git revision")

    prompts = manifest.get("prompts", {})
    for component in ("template", "rules", "examples"):
        record = prompts.get(component, {})
        if not record.get("version") or not record.get("sha256"):
            errors.append(f"{label} manifest is missing {component} prompt version/hash")

    configuration = manifest.get("configuration", {})
    if not configuration.get("provider") or not configuration.get("model"):
        errors.append(f"{label} manifest is missing provider/model")
    generation = configuration.get("generation", {})
    if generation.get("temperature") != 0:
        errors.append(f"{label} manifest temperature must be 0")
    for section in ("rag", "concurrency", "cascade"):
        if not isinstance(configuration.get(section), dict) or not configuration[section]:
            errors.append(f"{label} manifest is missing {section} configuration")

    inputs = manifest.get("inputs", {})
    heldout_record = inputs.get("heldout", {})
    actual_heldout_hash = hash_file(heldout_path, normalize_text=True)
    if heldout_record.get("sha256") != actual_heldout_hash:
        errors.append(f"{label} manifest held-out hash does not match --ground-truth")
    try:
        with heldout_path.open(encoding="utf-8") as handle:
            heldout_rows = json.load(handle)
        actual_heldout_rows = len(heldout_rows) if isinstance(heldout_rows, list) else -1
    except (OSError, json.JSONDecodeError):
        actual_heldout_rows = -1
    if heldout_record.get("row_count") != actual_heldout_rows:
        errors.append(f"{label} manifest held-out row count does not match --ground-truth")

    benchmark_record = inputs.get("benchmark", {})
    if not benchmark_record.get("sha256") or benchmark_record.get("row_count") != actual_heldout_rows:
        errors.append(f"{label} manifest benchmark identity is incomplete")

    knowledge_base = manifest.get("knowledge_base")
    if not isinstance(knowledge_base, list) or not knowledge_base:
        errors.append(f"{label} manifest has no knowledge-base hashes")
    elif any(not record.get("path") or not record.get("sha256") for record in knowledge_base):
        errors.append(f"{label} manifest contains an incomplete knowledge-base hash")

    output_record = manifest.get("outputs", {}).get("json", {})
    recorded_output_path = output_record.get("path")
    try:
        # Shareable manifests deliberately store only a basename. Bind it to
        # the evaluated artifact with basename + the content hash below.
        path_matches = Path(str(recorded_output_path)).name == output_path.name
    except (OSError, TypeError, ValueError):
        path_matches = False
    if not path_matches:
        errors.append(f"{label} manifest output JSON path does not match evaluated output")
    actual_output_hash = hash_file(output_path, normalize_text=True)
    if output_record.get("sha256") != actual_output_hash:
        errors.append(f"{label} manifest output JSON hash does not match evaluated output")

    return {"valid": not errors, "errors": errors}


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = build_parser()

    args = parser.parse_args()

    if not args.gen_benchmark and not args.ai_output and not (args.baseline and args.improved):
        parser.error("Provide --gen-benchmark, --ai-output, or --baseline + --improved")
    if bool(args.baseline) != bool(args.improved):
        parser.error("A/B mode requires both --baseline and --improved")
    if (args.report_json or args.require_acceptance) and not (args.baseline and args.improved):
        parser.error("--report-json and --require-acceptance are available only in A/B mode")
    if args.report_json or args.require_acceptance:
        if not args.baseline_manifest or not args.improved_manifest:
            parser.error("A/B reporting requires --baseline-manifest and --improved-manifest")
    if args.require_acceptance and not args.required_tests_json:
        parser.error("--require-acceptance requires --required-tests-json")
    if args.bootstrap_replicates <= 0:
        parser.error("--bootstrap-replicates must be positive")

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
        print("  1. Create clean baseline and improved worktrees at their pinned SHAs.")
        print("  2. Run scripts/run_sdtm_experiment.py without --execute in each worktree.")
        print("  3. Report the model, request estimate, endpoint, and cost risk for approval.")
        print("  4. Only after explicit approval, repeat both commands with --execute.")
        print("  5. Compare the two outputs with manifests and --require-acceptance.")
        print(f"  Benchmark input: {args.benchmark_output}")
        print("  See data/evaluation/README.md for the exact pinned commands.")
        return

    gt = load_ground_truth(args.ground_truth)
    print(f"Loaded {len(gt)} ground truth entries from {args.ground_truth.name}")
    evaluated_metrics: list[dict] = []

    if args.ai_output:
        rows = load_ai_output(args.ai_output)
        metrics = evaluate(rows, gt, label=args.ai_output.name)
        print_report(metrics, verbose=args.verbose)
        evaluated_metrics.append(metrics)

    if args.baseline and args.improved:
        base_rows = load_ai_output(args.baseline)
        impr_rows = load_ai_output(args.improved)

        base_metrics = evaluate(base_rows, gt, label=f"Baseline ({args.baseline.name})")
        impr_metrics = evaluate(impr_rows, gt, label=f"Improved ({args.improved.name})")

        print_report(base_metrics, verbose=args.verbose)
        print_report(impr_metrics, verbose=args.verbose)
        print_comparison(base_metrics, impr_metrics)
        evaluated_metrics.extend([base_metrics, impr_metrics])

        if args.report_json or args.require_acceptance:
            try:
                baseline_manifest = _load_json_object(
                    args.baseline_manifest,
                    label="baseline manifest",
                )
                improved_manifest = _load_json_object(
                    args.improved_manifest,
                    label="improved manifest",
                )
                required_tests = (
                    _load_json_object(
                        args.required_tests_json,
                        label="required tests",
                    )
                    if args.required_tests_json
                    else {
                        "all_passed": False,
                        "status": "NOT_PROVIDED",
                        "commands": [],
                    }
                )
                evidence_validation = {
                    "baseline": _validate_run_evidence(
                        baseline_manifest,
                        label="baseline",
                        output_path=args.baseline,
                        heldout_path=args.ground_truth,
                    ),
                    "improved": _validate_run_evidence(
                        improved_manifest,
                        label="improved",
                        output_path=args.improved,
                        heldout_path=args.ground_truth,
                    ),
                }
                evidence_errors = [error for result in evidence_validation.values() for error in result["errors"]]
                if evidence_errors:
                    raise ValueError("; ".join(evidence_errors))
                report = build_ab_report(
                    base_metrics,
                    impr_metrics,
                    baseline_manifest=baseline_manifest,
                    improved_manifest=improved_manifest,
                    required_tests=required_tests,
                    bootstrap_seed=args.bootstrap_seed,
                    bootstrap_replicates=args.bootstrap_replicates,
                )
                report["evidence_validation"] = evidence_validation
            except ValueError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                raise SystemExit(2) from exc

            if args.report_json:
                _write_report(args.report_json, report)
                print(f"Machine-readable A/B report: {args.report_json}")

            paired = report["paired_analysis"]
            print(
                "AI_RECOMMENDATION paired exact delta: "
                f"{paired['observed_delta'] * 100:+.2f} pp "
                f"(95% CI {paired['ci_95'][0] * 100:+.2f}, "
                f"{paired['ci_95'][1] * 100:+.2f})"
            )
            print(f"Release decision: {report['decision']}")

            if not report["manifest_comparison"]["equal"]:
                print(
                    "ERROR: Baseline and improved manifests do not share the "
                    "same experiment configuration: " + ", ".join(report["manifest_comparison"]["differences"]),
                    file=sys.stderr,
                )
                raise SystemExit(2)
            if args.require_acceptance and report["decision"] != "ACCEPT":
                failed = [gate["name"] for gate in report["gates"] if not gate["passed"]]
                print(
                    "ERROR: Improved run failed acceptance gates: " + ", ".join(failed),
                    file=sys.stderr,
                )
                raise SystemExit(1)

    if args.require_full_coverage:
        incomplete = [metrics for metrics in evaluated_metrics if metrics["coverage"] < 1]
        if incomplete:
            for metrics in incomplete:
                print(
                    f"ERROR: {metrics['label']} coverage is "
                    f"{metrics['total_evaluated']}/{metrics['gt_size']} "
                    f"({_pct(metrics['total_evaluated'], metrics['gt_size'])}); "
                    "full coverage is required.",
                    file=sys.stderr,
                )
            raise SystemExit(1)


if __name__ == "__main__":
    main()
