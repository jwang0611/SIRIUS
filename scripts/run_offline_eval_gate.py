#!/usr/bin/env python3
"""Replay held-out outputs offline and apply a reviewed regression baseline.

This command never constructs an AI client and never makes an external call.
Use ``run_sdtm_experiment.py --execute`` separately, with explicit approval,
only when a new real-model artifact is intentionally required.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.eval_prompt_accuracy import evaluate, ground_truth_from_rows, load_ai_output  # noqa: E402
from src.evaluation.heldout import scan_for_leakage, validate_dataset_manifest  # noqa: E402
from src.evaluation.offline_gate import (  # noqa: E402
    EVIDENCE_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    evaluate_regression_gate,
    redact_metrics_for_report,
    render_markdown_report,
)
from src.evaluation.run_manifest import hash_file, prompt_records  # noqa: E402

DEFAULT_KNOWLEDGE_ROOTS = (
    PROJECT_ROOT / "data/knowledge_base/structured",
    PROJECT_ROOT / "src/prompts/examples",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--ai-output", type=Path, help="Previously generated JSON; no model call is made")
    parser.add_argument("--validate-only", action="store_true", help="Validate data/leakage and write benchmark input")
    parser.add_argument("--benchmark-output", type=Path, help="External processor input path for --validate-only")
    parser.add_argument("--baseline", type=Path, help="Reviewed, manifest-bound regression baseline JSON")
    parser.add_argument(
        "--project-knowledge-root",
        action="append",
        type=Path,
        required=True,
        dest="project_knowledge_roots",
        help="Exported project/session KB root; repeat for every project source",
    )
    parser.add_argument(
        "--knowledge-root",
        action="append",
        type=Path,
        dest="additional_knowledge_roots",
        help="Additional rule/example roots beyond the built-in production KB and prompt examples",
    )
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-markdown", type=Path, required=True)
    gate = parser.add_mutually_exclusive_group()
    gate.add_argument(
        "--require-gate",
        action="store_true",
        dest="require_gate",
        default=True,
        help="Require a passing reviewed baseline (default)",
    )
    gate.add_argument(
        "--no-gate",
        action="store_false",
        dest="require_gate",
        help="Diagnostic replay only; do not require a baseline or passing gate",
    )
    return parser


def _load_object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load JSON {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path.name}")
    return payload


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def collect_replay_evidence(ai_output: Path) -> dict:
    """Bind replay metrics to the exact output, clean checkout, and prompts."""
    try:
        git_sha = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        git_status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("Cannot collect Git evidence for offline replay") from exc
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "ai_output": {"sha256": hash_file(ai_output)},
        "git": {"sha": git_sha, "dirty": bool(git_status.strip())},
        "prompts": prompt_records(PROJECT_ROOT),
    }


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.validate_only:
        if args.ai_output or args.baseline:
            parser.error("--validate-only cannot be combined with --ai-output or --baseline")
        if not args.benchmark_output:
            parser.error("--validate-only requires --benchmark-output")
    elif not args.ai_output:
        parser.error("replay mode requires --ai-output")
    if not args.validate_only and args.require_gate and not args.baseline:
        parser.error("replay mode requires --baseline unless --no-gate is supplied")

    try:
        integrity, rows = validate_dataset_manifest(args.dataset_manifest, require_release=True)
        leakage = scan_for_leakage(
            rows,
            knowledge_roots=[
                *DEFAULT_KNOWLEDGE_ROOTS,
                *args.project_knowledge_roots,
                *(args.additional_knowledge_roots or []),
            ],
        )
        metrics = None
        evidence = None
        gate = {"evaluated": False, "passed": False, "gates": [], "errors": []}
        if integrity["valid"] and leakage["valid"] and args.validate_only:
            benchmark = [
                {
                    field: row[field]
                    for field in ("metadata_table", "metadata_variable", "annotation_table", "annotation_variable")
                }
                for row in rows
            ]
            _write(args.benchmark_output, json.dumps(benchmark, ensure_ascii=False, indent=2) + "\n")
        elif integrity["valid"] and leakage["valid"]:
            evidence = collect_replay_evidence(args.ai_output)
            metrics = evaluate(load_ai_output(args.ai_output), ground_truth_from_rows(rows), label=args.ai_output.name)
            if args.baseline:
                gate = evaluate_regression_gate(
                    metrics,
                    _load_object(args.baseline),
                    dataset_manifest_sha256=integrity["manifest_sha256"],
                    current_evidence=evidence,
                )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "validation_only" if args.validate_only else "offline_replay",
        "dataset_integrity": integrity,
        "leakage": leakage,
        "evidence": evidence,
        "metrics": redact_metrics_for_report(metrics) if metrics is not None else None,
        "regression_gate": gate,
    }
    _write(args.report_json, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _write(args.report_markdown, render_markdown_report(report))

    if not integrity["valid"] or not leakage["valid"]:
        print("ERROR: release dataset or leakage contract failed", file=sys.stderr)
        raise SystemExit(1)
    if not args.validate_only and args.require_gate and not gate["passed"]:
        print("ERROR: regression gate failed", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
