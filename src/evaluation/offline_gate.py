"""Deterministic regression gate and redacted Markdown reporting."""

from __future__ import annotations

from typing import Any

BASELINE_SCHEMA_VERSION = "sirius-eval-baseline/v1"
REPORT_SCHEMA_VERSION = "sirius-offline-eval-report/v1"
REDACTED_METRIC_FIELDS = {"label", "mismatches", "missing_gt_keys", "extra_ai_keys", "row_results"}
RATE_PATHS = (
    "coverage",
    "exact_rate",
    "domain_rate",
    "supp.precision",
    "supp.recall",
    "supp.f1",
)


def _read_path(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"Missing metric: {path}")
        current = current[part]
    return current


def evaluate_regression_gate(
    metrics: dict[str, Any],
    baseline: dict[str, Any],
    *,
    dataset_manifest_sha256: str,
) -> dict[str, Any]:
    """Compare a replay to an explicitly reviewed, hash-bound baseline."""
    errors: list[str] = []
    if baseline.get("schema_version") != BASELINE_SCHEMA_VERSION:
        errors.append(f"baseline schema_version must be {BASELINE_SCHEMA_VERSION!r}")
    if baseline.get("dataset_manifest_sha256") != dataset_manifest_sha256:
        errors.append("baseline is not bound to the evaluated dataset manifest")

    recorded = baseline.get("metrics")
    tolerances = baseline.get("max_regression")
    if not isinstance(recorded, dict):
        recorded = {}
        errors.append("baseline.metrics must be an object")
    if not isinstance(tolerances, dict):
        tolerances = {}
        errors.append("baseline.max_regression must be an object")

    gates: list[dict[str, Any]] = []
    for path in RATE_PATHS:
        try:
            before = float(_read_path(recorded, path))
            after = float(_read_path(metrics, path))
            tolerance = float(_read_path(tolerances, path))
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
            continue
        if not 0 <= before <= 1 or not 0 <= after <= 1 or tolerance < 0:
            errors.append(f"Invalid rate/tolerance contract: {path}")
            continue
        gates.append(
            {
                "metric": path,
                "baseline": before,
                "current": after,
                "max_regression": tolerance,
                "passed": after >= before - tolerance,
            }
        )

    for section in ("quality_issues", "outcome_counts"):
        baseline_counts = baseline.get(section)
        current_counts = metrics.get(section)
        allowed_increase = baseline.get("max_increase", {}).get(section)
        if not isinstance(baseline_counts, dict) or not isinstance(current_counts, dict):
            errors.append(f"baseline/current {section} must be objects")
            continue
        if not isinstance(allowed_increase, dict):
            errors.append(f"baseline.max_increase.{section} must be an object")
            continue
        if set(baseline_counts) != set(current_counts):
            errors.append(f"baseline/current {section} counter names differ")
        for name, after_value in sorted(current_counts.items()):
            if name not in baseline_counts or name not in allowed_increase:
                errors.append(f"Missing counter contract: {section}.{name}")
                continue
            try:
                before = int(baseline_counts[name])
                after = int(after_value)
                tolerance = int(allowed_increase[name])
            except (TypeError, ValueError):
                errors.append(f"Counter contract must be integer: {section}.{name}")
                continue
            if before < 0 or after < 0 or tolerance < 0:
                errors.append(f"Counter contract cannot be negative: {section}.{name}")
                continue
            gates.append(
                {
                    "metric": f"{section}.{name}",
                    "baseline": before,
                    "current": after,
                    "max_increase": tolerance,
                    "passed": after <= before + tolerance,
                }
            )

    return {
        "evaluated": True,
        "passed": not errors and all(gate["passed"] for gate in gates),
        "gates": gates,
        "errors": errors,
    }


def redact_metrics_for_report(metrics: dict[str, Any]) -> dict[str, Any]:
    """Remove row identities and artifact names from a persistent report."""
    return {key: value for key, value in metrics.items() if key not in REDACTED_METRIC_FIELDS}


def _rate(stats: dict[str, Any], numerator: str, denominator: str) -> str:
    total = int(stats.get(denominator, 0))
    count = int(stats.get(numerator, 0))
    return f"{count / total:.1%}" if total else "N/A"


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render a stable Markdown companion without raw input metadata."""
    integrity = report["dataset_integrity"]
    leakage = report["leakage"]
    lines = [
        "# SIRIUS offline held-out evaluation",
        "",
        f"- Report schema: `{report['schema_version']}`",
        f"- Dataset manifest SHA-256: `{integrity['manifest_sha256']}`",
        f"- Studies: {integrity['dataset_count']}",
        f"- Ground-truth rows: {integrity['row_count']}",
        f"- Dataset contract: {'PASS' if integrity['valid'] else 'FAIL'}",
        f"- Leakage check: {'PASS' if leakage['valid'] else 'FAIL'} ({leakage['overlap_count']} overlaps)",
        "",
    ]
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        reason = (
            "Validation-only preflight completed; no replay was scored."
            if report.get("mode") == "validation_only" and integrity["valid"] and leakage["valid"]
            else "Evaluation was not scored because the release-data contract failed."
        )
        lines.extend([reason, ""])
    else:
        supp = metrics["supp"]
        lines.extend(
            [
                "## Headline metrics",
                "",
                "| Metric | Value |",
                "| --- | ---: |",
                f"| Coverage | {metrics['coverage']:.1%} |",
                f"| Exact variable match | {metrics['exact_rate']:.1%} |",
                f"| Domain accuracy | {metrics['domain_rate']:.1%} |",
                f"| SUPP precision | {supp['precision']:.1%} |",
                f"| SUPP recall | {supp['recall']:.1%} |",
                f"| SUPP F1 | {supp['f1']:.1%} |",
                f"| FALLBACK outputs | {metrics['outcome_counts']['fallback_outputs']} |",
                f"| `*_PENDING` outputs | {metrics['outcome_counts']['pending_outputs']} |",
                f"| MappingCritic errors | {metrics['quality_issues']['mapping_critic_errors']} |",
                "",
            ]
        )
        for title, key in (("Source strata", "source_stats"), ("Domain strata", "domain_stats")):
            lines.extend(
                [
                    f"## {title}",
                    "",
                    "| Stratum | Rows | Exact | Domain |",
                    "| --- | ---: | ---: | ---: |",
                ]
            )
            for name, stats in sorted(metrics[key].items()):
                lines.append(
                    f"| {name} | {stats['total']} | {_rate(stats, 'match', 'total')} | "
                    f"{_rate(stats, 'domain_match', 'total')} |"
                )
            lines.append("")

    gate = report.get("regression_gate", {"evaluated": False})
    lines.extend(["## Regression gate", ""])
    if not gate.get("evaluated"):
        lines.extend(["Not evaluated: no reviewed baseline was supplied.", ""])
    else:
        lines.extend(
            [
                f"Decision: **{'PASS' if gate['passed'] else 'FAIL'}**",
                "",
                "| Metric | Baseline | Current | Result |",
                "| --- | ---: | ---: | --- |",
            ]
        )
        for item in gate.get("gates", []):
            lines.append(
                f"| {item['metric']} | {item['baseline']} | {item['current']} | "
                f"{'PASS' if item['passed'] else 'FAIL'} |"
            )
        lines.append("")
    errors = [*integrity.get("errors", []), *leakage.get("errors", []), *gate.get("errors", [])]
    if errors:
        lines.extend(["## Validation errors", ""])
        lines.extend(f"- {error}" for error in errors)
        lines.append("")
    return "\n".join(lines)
