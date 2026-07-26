"""Pure scenario, quality, statistical, and gate helpers for SDTM A/B runs."""

from __future__ import annotations

import json
import math
import random
import re
from collections import Counter
from typing import Any

from src.config.domain_semantic_map import is_valid_domain, strip_supp_prefix
from src.processors.deterministic_validator import SUPPQUAL_VARS, _get_domain_standard_vars

SCENARIO_NON_STANDARD_DOMAIN = "NON_STANDARD_DOMAIN"
SCENARIO_MULTI_DOMAIN = "MULTI_DOMAIN"
SCENARIO_SUPP = "SUPP"
SCENARIO_TESTCD = "TESTCD"
SCENARIO_NOT_SUBMITTED = "NOT_SUBMITTED"

QUALITY_COUNTER_NAMES = (
    "deterministic_validation_errors",
    "illegal_sdtm_variables",
    "illegal_supp_qnam",
    "parse_failures",
    "unmapped_outputs",
    "missing_cascade_provenance",
    "mapping_critic_errors",
)

_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9]{0,7}$")
_DOMAIN_SPLIT_RE = re.compile(r"[|/;]")
_VARIABLE_SPLIT_RE = re.compile(r"[|/;]")
_VALIDATION_FLAGS = (
    "invalid_domain_corrected",
    "variable_name_corrected",
    "variable_name_truncated",
    "domain_prefix_mismatch",
    "non_standard_variable",
    "auto_corrected_to_supp",
)


def classify_scenarios(reference: dict[str, Any]) -> set[str]:
    """Derive diagnostic labels from one metadata-only ground-truth row."""
    domain = str(reference.get("SDTM_Domain", "") or "").strip().upper()
    variable = str(reference.get("SDTM_Variable", "") or "").strip().upper()
    scenarios: set[str] = set()

    if variable == "NOT SUBMITTED":
        scenarios.add(SCENARIO_NOT_SUBMITTED)
        return scenarios

    domain_tokens = [token.strip() for token in _DOMAIN_SPLIT_RE.split(domain) if token.strip()]
    if len(domain_tokens) > 1:
        scenarios.add(SCENARIO_MULTI_DOMAIN)
    if any(not is_valid_domain(strip_supp_prefix(token)) for token in domain_tokens):
        scenarios.add(SCENARIO_NON_STANDARD_DOMAIN)

    if domain.startswith("SUPP") or ("QVAL" in variable and "QNAM=" in variable):
        scenarios.add(SCENARIO_SUPP)
    if "TESTCD=" in variable:
        scenarios.add(SCENARIO_TESTCD)
    return scenarios


def _raw_variable(row: dict[str, Any]) -> str:
    return str(row.get("sdtm_variable") or row.get("ai_variable") or "").strip().upper()


def _row_validation_error(row: dict[str, Any]) -> bool:
    nested = row.get("validation_flags")
    if isinstance(nested, dict) and any(bool(nested.get(flag)) for flag in _VALIDATION_FLAGS):
        return True
    return any(bool(row.get(flag)) for flag in _VALIDATION_FLAGS)


def _standard_variable_is_illegal(row: dict[str, Any]) -> bool:
    source = str(row.get("source", "") or "").upper()
    if source in {"FALLBACK", "UNMAPPED"}:
        return False

    variable = _raw_variable(row)
    if not variable or variable == "NOT SUBMITTED":
        return False
    variable_type = str(row.get("sdtm_variable_type", "") or "standard").lower()
    if variable_type == "supp" or variable == "QVAL":
        return False

    variable = re.split(r"\s+(?:WHEN|IF)\s+", variable, maxsplit=1)[0]
    tokens = [token.strip().split("=", 1)[0] for token in _VARIABLE_SPLIT_RE.split(variable) if token.strip()]
    if not tokens or any(not _TOKEN_RE.fullmatch(token) for token in tokens):
        return True

    domain = str(row.get("domain") or row.get("ai_domain") or "").strip().upper()
    domains = [token.strip() for token in _DOMAIN_SPLIT_RE.split(domain) if token.strip()]
    for index, token in enumerate(tokens):
        target_domain = domains[index] if index < len(domains) else (domains[0] if domains else "")
        standard_vars = _get_domain_standard_vars(strip_supp_prefix(target_domain))
        if standard_vars and token not in standard_vars:
            return True
    return False


def _supp_structure_is_illegal(row: dict[str, Any]) -> bool:
    variable = _raw_variable(row)
    variable_type = str(row.get("sdtm_variable_type", "") or "").lower()
    is_supp = variable_type == "supp" or variable == "QVAL"
    if not is_supp:
        return False

    dataset = str(row.get("supp_dataset", "") or "").strip().upper()
    qnam = str(row.get("supp_variable", "") or "").strip().upper()
    domain = str(row.get("domain") or row.get("ai_domain") or "").strip().upper()
    base_domain = strip_supp_prefix(domain)

    if variable != "QVAL":
        return True
    if not dataset.startswith("SUPP") or (base_domain and dataset != f"SUPP{base_domain}"):
        return True
    if not _TOKEN_RE.fullmatch(qnam) or qnam in SUPPQUAL_VARS:
        return True
    standard_vars = _get_domain_standard_vars(base_domain)
    return bool(standard_vars and qnam in standard_vars)


def count_quality_issues(
    rows: list[dict[str, Any]],
    *,
    consistency_issues: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Count deterministic output issues once per evaluated recommendation."""
    counts: Counter[str] = Counter()
    for row in rows:
        if _row_validation_error(row):
            counts["deterministic_validation_errors"] += 1
        if _standard_variable_is_illegal(row):
            counts["illegal_sdtm_variables"] += 1
        if _supp_structure_is_illegal(row):
            counts["illegal_supp_qnam"] += 1

        source = str(row.get("source", "") or "").upper()
        reason = str(row.get("fallback_reason", "") or "").lower()
        if source == "FALLBACK" and ("parse" in reason or "json" in reason):
            counts["parse_failures"] += 1
        if source == "UNMAPPED":
            counts["unmapped_outputs"] += 1
        if row.get("cascade_level") is None:
            counts["missing_cascade_provenance"] += 1

    unique_critic_errors = {
        json.dumps(issue, ensure_ascii=False, sort_keys=True)
        for issue in consistency_issues or []
        if str(issue.get("severity", "")).lower() == "error"
    }
    counts["mapping_critic_errors"] = len(unique_critic_errors)
    return {name: counts[name] for name in QUALITY_COUNTER_NAMES}


def _nearest_rank(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("Cannot calculate a percentile from no values")
    rank = max(1, math.ceil(probability * len(values)))
    return values[min(rank - 1, len(values) - 1)]


def paired_bootstrap(
    baseline: list[int],
    improved: list[int],
    *,
    seed: int,
    replicates: int,
) -> dict[str, Any]:
    """Bootstrap the paired exact-match delta using shared row indices."""
    if len(baseline) != len(improved):
        raise ValueError("Paired baseline and improved outcomes must have the same length")
    if not baseline:
        raise ValueError("Paired bootstrap requires at least one outcome")
    if replicates <= 0:
        raise ValueError("Bootstrap replicates must be positive")

    paired_deltas = [int(after) - int(before) for before, after in zip(baseline, improved, strict=True)]
    observed_delta = sum(paired_deltas) / len(paired_deltas)
    rng = random.Random(seed)
    bootstrap_deltas = []
    for _ in range(replicates):
        sample_delta = sum(paired_deltas[rng.randrange(len(paired_deltas))] for _ in paired_deltas)
        bootstrap_deltas.append(sample_delta / len(paired_deltas))
    bootstrap_deltas.sort()

    improved_rows = sum(1 for before, after in zip(baseline, improved, strict=True) if not before and after)
    worsened_rows = sum(1 for before, after in zip(baseline, improved, strict=True) if before and not after)
    return {
        "row_count": len(paired_deltas),
        "baseline_rate": sum(baseline) / len(baseline),
        "improved_rate": sum(improved) / len(improved),
        "observed_delta": observed_delta,
        "improved_rows": improved_rows,
        "worsened_rows": worsened_rows,
        "unchanged_rows": len(paired_deltas) - improved_rows - worsened_rows,
        "seed": seed,
        "replicates": replicates,
        "ci_95": [
            _nearest_rank(bootstrap_deltas, 0.025),
            _nearest_rank(bootstrap_deltas, 0.975),
        ],
    }


def paired_cohort_outcomes(
    baseline: dict[str, Any],
    improved: dict[str, Any],
    *,
    cohort: str,
) -> dict[str, list[Any]]:
    """Align binary row outcomes by evaluation ID for one cohort."""

    def index(metrics: dict[str, Any]) -> dict[str, int]:
        indexed: dict[str, int] = {}
        for row in metrics.get("row_results", []):
            if row.get("cohort") != cohort:
                continue
            evaluation_id = str(row.get("evaluation_id", ""))
            if not evaluation_id:
                raise ValueError(f"Missing evaluation_id in {cohort} row result")
            if evaluation_id in indexed:
                raise ValueError(f"Duplicate evaluation_id in {cohort}: {evaluation_id}")
            indexed[evaluation_id] = int(bool(row.get("exact_match")))
        return indexed

    baseline_index = index(baseline)
    improved_index = index(improved)
    if set(baseline_index) != set(improved_index):
        missing_from_improved = sorted(set(baseline_index) - set(improved_index))
        missing_from_baseline = sorted(set(improved_index) - set(baseline_index))
        raise ValueError(
            "Paired cohort evaluation IDs differ: "
            f"missing_from_improved={missing_from_improved}, "
            f"missing_from_baseline={missing_from_baseline}"
        )
    evaluation_ids = sorted(baseline_index)
    return {
        "evaluation_ids": evaluation_ids,
        "baseline": [baseline_index[evaluation_id] for evaluation_id in evaluation_ids],
        "improved": [improved_index[evaluation_id] for evaluation_id in evaluation_ids],
    }


def evaluate_acceptance(
    baseline: dict[str, Any],
    improved: dict[str, Any],
    *,
    paired: dict[str, Any],
    manifest_comparison: dict[str, Any],
    required_tests: dict[str, Any],
) -> dict[str, Any]:
    """Apply every release condition and return an auditable decision."""
    gates: list[dict[str, Any]] = []

    def add_gate(
        name: str,
        passed: bool,
        *,
        baseline_value: Any,
        improved_value: Any,
        detail: str,
    ) -> None:
        gates.append(
            {
                "name": name,
                "passed": bool(passed),
                "baseline": baseline_value,
                "improved": improved_value,
                "detail": detail,
            }
        )

    baseline_coverage = float(baseline.get("coverage", 0))
    improved_coverage = float(improved.get("coverage", 0))
    add_gate(
        "full_coverage",
        baseline_coverage == 1.0 and improved_coverage == 1.0,
        baseline_value=baseline_coverage,
        improved_value=improved_coverage,
        detail="Both runs must evaluate every ground-truth row.",
    )

    baseline_cohorts = baseline.get("cohort_stats", {})
    improved_cohorts = improved.get("cohort_stats", {})
    baseline_kb_exact = int(baseline_cohorts.get("KB_AGREE", {}).get("match", 0))
    improved_kb_exact = int(improved_cohorts.get("KB_AGREE", {}).get("match", 0))
    add_gate(
        "KB_AGREE_exact_no_regression",
        improved_kb_exact >= baseline_kb_exact,
        baseline_value=baseline_kb_exact,
        improved_value=improved_kb_exact,
        detail="KB_AGREE is the clean deterministic KB-path regression cohort.",
    )

    observed_delta = float(paired.get("observed_delta", 0))
    ci_95 = paired.get("ci_95", [0, 0])
    ci_lower = float(ci_95[0])
    add_gate(
        "AI_RECOMMENDATION_exact_positive",
        observed_delta > 0,
        baseline_value=paired.get("baseline_rate"),
        improved_value=paired.get("improved_rate"),
        detail=f"Observed paired exact delta is {observed_delta:.6f}.",
    )
    add_gate(
        "AI_RECOMMENDATION_exact_evidence",
        observed_delta >= 0.02 or ci_lower > 0,
        baseline_value={"required_absolute_delta": 0.02, "required_ci_lower": 0},
        improved_value={"observed_delta": observed_delta, "ci_95": ci_95},
        detail="Require at least +2 percentage points or a paired 95% CI lower bound above zero.",
    )

    baseline_domain = int(baseline.get("domain_match", 0))
    improved_domain = int(improved.get("domain_match", 0))
    add_gate(
        "overall_domain_no_regression",
        improved_domain >= baseline_domain,
        baseline_value=baseline_domain,
        improved_value=improved_domain,
        detail="Overall Domain Match count cannot decline at fixed coverage.",
    )
    baseline_ai_domain = int(
        baseline_cohorts.get("AI_RECOMMENDATION", {}).get("domain_match", 0)
    )
    improved_ai_domain = int(
        improved_cohorts.get("AI_RECOMMENDATION", {}).get("domain_match", 0)
    )
    add_gate(
        "AI_RECOMMENDATION_domain_no_regression",
        improved_ai_domain >= baseline_ai_domain,
        baseline_value=baseline_ai_domain,
        improved_value=improved_ai_domain,
        detail="AI_RECOMMENDATION Domain Match count cannot decline.",
    )

    baseline_quality = baseline.get("quality_issues", {})
    improved_quality = improved.get("quality_issues", {})
    for counter in QUALITY_COUNTER_NAMES:
        before = int(baseline_quality.get(counter, 0))
        after = int(improved_quality.get(counter, 0))
        add_gate(
            f"quality_{counter}_no_increase",
            after <= before,
            baseline_value=before,
            improved_value=after,
            detail=f"{counter} cannot increase.",
        )

    add_gate(
        "shared_run_configuration",
        bool(manifest_comparison.get("equal")),
        baseline_value="pinned",
        improved_value=manifest_comparison.get("differences", []),
        detail="Input, KB, model, generation, RAG, cascade, and concurrency must match.",
    )
    add_gate(
        "required_offline_tests",
        required_tests.get("all_passed") is True,
        baseline_value=True,
        improved_value=required_tests.get("all_passed"),
        detail="Every named Prompt/cascade/normalizer/validator/critic/evaluation test must pass.",
    )

    return {
        "decision": "ACCEPT" if all(gate["passed"] for gate in gates) else "ROLLBACK",
        "gates": gates,
        "diagnostic_only_cohorts": ["KB_DISAGREE"],
    }
