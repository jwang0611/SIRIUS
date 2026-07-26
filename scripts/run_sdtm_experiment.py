#!/usr/bin/env python
"""Run one pinned SIRIUS evaluation revision and write an audit manifest.

The command is estimate-only by default. ``--execute`` is deliberately
required before the production generator subprocess can make external calls.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.run_manifest import (  # noqa: E402
    build_run_manifest,
    finalize_run_manifest,
    hash_file,
    write_manifest,
)
from src.rag.chunker import Chunker  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--code-root", type=Path, required=True)
    parser.add_argument("--input", dest="input_path", type=Path, required=True)
    parser.add_argument("--heldout", dest="heldout_path", type=Path, required=True)
    parser.add_argument("--kb-root", type=Path, required=True)
    parser.add_argument(
        "--rag-kb-path",
        type=Path,
        help="Parquet directory for RAG; defaults to <kb-root>/structured",
    )
    parser.add_argument("--output-base", type=Path, required=True)
    parser.add_argument("--manifest", dest="manifest_path", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Execute production generation; default only estimates")

    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--model", default="google/gemini-3-flash-preview")
    parser.add_argument(
        "--endpoint",
        default=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--top-k", type=int, default=40)
    parser.add_argument("--max-output-tokens", type=int, default=2000)
    parser.add_argument("--language", choices=("cn", "en"), default="cn")
    parser.add_argument("--rate-limit", type=int, default=120)

    parser.add_argument("--rag-top-k", type=int, default=3)
    parser.add_argument("--rag-embedding-model", default="openai/text-embedding-3-small")
    parser.add_argument("--rag-min-score", type=float, default=0.4)
    parser.add_argument("--rag-char-limit", type=int, default=1500)
    parser.add_argument("--force-rag", action="store_true")
    parser.add_argument("--rag-kb-default-file", default="ALS2SDTM_Mapping_Template_v1.0.parquet")

    parser.add_argument("--parallel", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-workers", type=int, default=5)

    parser.add_argument("--kb-min-confidence", type=float, default=0.8)
    parser.add_argument("--cascade-kb-high-conf", type=float, default=0.85)
    parser.add_argument("--cascade-rag-high-conf", type=float, default=0.7)
    parser.add_argument("--domain-override-confidence", type=float, default=0.85)
    return parser


def _rag_kb_path(args: argparse.Namespace) -> Path:
    return args.rag_kb_path or (args.kb_root / "structured")


def _rag_cache_dir(args: argparse.Namespace) -> Path:
    return args.code_root / "data" / "cache" / "rag_vectors"


def _validate_pinned_generation(args: argparse.Namespace) -> None:
    """Reject wrapper values the target generator cannot apply explicitly."""
    if args.top_p != 0.95:
        raise ValueError("--top-p must remain 0.95 for this pinned production generator")
    if args.top_k != 40:
        raise ValueError("--top-k must remain 40 for this pinned production generator")


def build_generator_command(
    args: argparse.Namespace,
    *,
    python_executable: Path,
) -> list[str]:
    """Build the exact production-generator argument list recorded by the run."""
    command = [
        str(python_executable),
        str(args.code_root / "scripts/generate_sdtm_recommendations.py"),
        "--json-file",
        str(args.input_path),
        "--output",
        str(args.output_base),
        "--provider",
        str(args.provider),
        "--model",
        str(args.model),
        "--base-url",
        str(args.endpoint),
        "--rate-limit",
        str(args.rate_limit),
        "--max-output-tokens",
        str(args.max_output_tokens),
        "--temperature",
        str(args.temperature),
        "--language",
        str(args.language),
        "--kb-path",
        str(_rag_kb_path(args)),
        "--rag-top-k",
        str(args.rag_top_k),
        "--rag-embedding-model",
        str(args.rag_embedding_model),
        "--rag-min-score",
        str(args.rag_min_score),
        "--rag-char-limit",
        str(args.rag_char_limit),
        "--max-workers",
        str(args.max_workers),
    ]
    command.append("--parallel" if args.parallel else "--no-parallel")
    if args.force_rag:
        command.append("--force-rag")
    return command


def build_pinned_environment(
    args: argparse.Namespace,
    *,
    base_environment: dict[str, str] | None = None,
) -> dict[str, str]:
    """Pin non-CLI cascade settings while preserving externally supplied credentials."""
    environment = dict(os.environ if base_environment is None else base_environment)
    rag_kb_path = _rag_kb_path(args)
    environment.update(
        {
            "DEFAULT_AI_PROVIDER": str(args.provider),
            "DEFAULT_MODEL": str(args.model),
            "OPENROUTER_BASE_URL": str(args.endpoint),
            "KNOWLEDGE_STRUCTURED_PATH": str(rag_kb_path),
            "RAG_KB_BASE_PATH": str(args.kb_root),
            "RAG_KB_PATH": str(rag_kb_path),
            "RATE_LIMIT_RPM": str(args.rate_limit),
            "RAG_ENABLED": "1",
            "RAG_TOP_K": str(args.rag_top_k),
            "RAG_EMBED_MODEL": str(args.rag_embedding_model),
            "RAG_MIN_SCORE": str(args.rag_min_score),
            "RAG_CHAR_LIMIT": str(args.rag_char_limit),
            "RAG_KB_DEFAULT_FILE": str(args.rag_kb_default_file),
            "KB_MIN_CONFIDENCE": str(args.kb_min_confidence),
            "CASCADE_KB_HIGH_CONF": str(args.cascade_kb_high_conf),
            "CASCADE_RAG_HIGH_CONF": str(args.cascade_rag_high_conf),
            "KB_DOMAIN_OVERRIDE_CONF": str(args.domain_override_confidence),
            "SDTM_ENABLE_PARALLEL": "1" if args.parallel else "0",
            "SDTM_MAX_WORKERS": str(args.max_workers),
            "SDTM_LOG_AI": "0",
            "PYTHONUTF8": "1",
        }
    )
    return environment


def estimate_external_requests(
    heldout_path: Path,
    *,
    runs: int = 1,
    rag_kb_path: Path | None = None,
    embedding_batch_size: int = 100,
) -> dict[str, int]:
    """Return conservative generation and batched-embedding upper bounds."""
    with heldout_path.open(encoding="utf-8") as handle:
        rows = json.load(handle)
    if not isinstance(rows, list):
        raise ValueError(f"Held-out data must be a JSON list: {heldout_path}")
    if embedding_batch_size <= 0:
        raise ValueError("Embedding batch size must be positive")

    ai_rows = sum(1 for row in rows if row.get("evaluation_cohort") == "AI_RECOMMENDATION")
    query_items = len(rows)
    query_requests = math.ceil(query_items / embedding_batch_size)
    kb_items = (
        len(Chunker(str(rag_kb_path)).load_all_parquet())
        if rag_kb_path is not None
        else 0
    )
    kb_requests = math.ceil(kb_items / embedding_batch_size)
    embedding_items = query_items + kb_items
    embedding_requests = query_requests + kb_requests
    return {
        "heldout_rows": len(rows),
        "ai_recommendation_rows": ai_rows,
        "generation_calls_per_run_max": ai_rows,
        "rag_query_embedding_items_per_run_max": query_items,
        "rag_query_embedding_requests_per_run_max": query_requests,
        "rag_kb_embedding_items_per_run_max": kb_items,
        "rag_kb_embedding_requests_per_run_max": kb_requests,
        "embedding_items_per_run_max": embedding_items,
        "embedding_requests_per_run_max": embedding_requests,
        "runs": runs,
        "generation_calls_total_max": ai_rows * runs,
        "embedding_items_total_max": embedding_items * runs,
        "embedding_requests_total_max": embedding_requests * runs,
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _run_git(code_root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=code_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(arguments)} failed")
    return result.stdout.strip()


def _sanitized_model_name(model: str) -> str:
    return model.replace("/", "_").replace(":", "-").replace(" ", "_")[:30]


def _expected_output_paths(output_base: Path, model: str) -> tuple[Path, Path]:
    suffix = f"_{_sanitized_model_name(model)}"
    final_base = output_base if suffix in str(output_base) else Path(f"{output_base}{suffix}")
    return Path(f"{final_base}.json"), Path(f"{final_base}.xlsx")


def _summarize_output(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError(f"Processor output must be a JSON list: {path}")

    source_counts: Counter[str] = Counter()
    cascade_counts: Counter[str] = Counter()
    row_count = 0
    for table in payload:
        for rec in table.get("domain_recommendations", []):
            row_count += 1
            source_counts[str(rec.get("source") or "UNSPECIFIED")] += 1
            level = rec.get("cascade_level")
            cascade_counts["null" if level is None else str(level)] += 1
    return {
        "processor_output_rows": row_count,
        "source_counts": dict(sorted(source_counts.items())),
        "cascade_level_counts": dict(sorted(cascade_counts.items())),
    }


def _validate_inputs(args: argparse.Namespace) -> None:
    _validate_pinned_generation(args)
    for label, path in (
        ("code root", args.code_root),
        ("input", args.input_path),
        ("held-out", args.heldout_path),
        ("KB root", args.kb_root),
        ("RAG KB", _rag_kb_path(args)),
    ):
        if not path.exists():
            raise FileNotFoundError(f"{label} not found: {path}")

    with args.input_path.open(encoding="utf-8") as handle:
        benchmark = json.load(handle)
    with args.heldout_path.open(encoding="utf-8") as handle:
        heldout = json.load(handle)
    if not isinstance(benchmark, list) or not isinstance(heldout, list):
        raise ValueError("Benchmark input and held-out data must both be JSON lists")
    if len(benchmark) != len(heldout):
        raise ValueError(f"Benchmark/held-out row mismatch: {len(benchmark)} != {len(heldout)}")
    if len(heldout) != 490:
        raise ValueError(f"Full experiment requires exactly 490 held-out rows, got {len(heldout)}")
    if args.execute:
        cache_files = sorted(_rag_cache_dir(args).glob("*.pkl"))
        if cache_files:
            raise RuntimeError(
                "Execution requires an empty isolated RAG vector cache; "
                f"found {len(cache_files)} cache file(s) in {_rag_cache_dir(args)}"
            )


def _rag_kb_identity(args: argparse.Namespace) -> str:
    rag_path = _rag_kb_path(args).resolve()
    try:
        return rag_path.relative_to(args.kb_root.resolve()).as_posix()
    except ValueError:
        return str(rag_path)


def _manifest_from_args(
    args: argparse.Namespace,
    *,
    git_sha: str,
    git_dirty: bool,
    started_at: str,
) -> dict[str, Any]:
    manifest = build_run_manifest(
        run_label=args.run_label,
        code_root=args.code_root,
        git_sha=git_sha,
        git_dirty=git_dirty,
        input_path=args.input_path,
        heldout_path=args.heldout_path,
        kb_root=args.kb_root,
        provider=args.provider,
        model=args.model,
        endpoint=args.endpoint,
        generation={
            "temperature": args.temperature,
            "top_p": args.top_p,
            "top_k": args.top_k,
            "max_output_tokens": args.max_output_tokens,
            "language": args.language,
            "rate_limit": args.rate_limit,
        },
        rag={
            "enabled": True,
            "kb_path": _rag_kb_identity(args),
            "cache_policy": "cold_isolated_worktree",
            "cache_path": "data/cache/rag_vectors",
            "top_k": args.rag_top_k,
            "embedding_model": args.rag_embedding_model,
            "embedding_batch_size": 100,
            "min_score": args.rag_min_score,
            "char_limit": args.rag_char_limit,
            "force": args.force_rag,
            "default_file": args.rag_kb_default_file,
        },
        concurrency={"parallel": args.parallel, "max_workers": args.max_workers},
        cascade={
            "kb_min_confidence": args.kb_min_confidence,
            "kb_high_confidence": args.cascade_kb_high_conf,
            "rag_high_confidence": args.cascade_rag_high_conf,
            "domain_override_confidence": args.domain_override_confidence,
        },
        started_at=started_at,
    )
    manifest["runner"] = {
        "path": "scripts/run_sdtm_experiment.py",
        "script_sha256": hash_file(Path(__file__), normalize_text=True),
    }
    return manifest


def main() -> int:
    args = build_parser().parse_args()
    _validate_inputs(args)
    estimate = estimate_external_requests(
        args.heldout_path,
        runs=1,
        rag_kb_path=_rag_kb_path(args),
    )
    print(json.dumps({"model": args.model, "temperature": args.temperature, **estimate}, indent=2))

    if not args.execute:
        print("Estimate only: no production generator or external model was called.")
        return 0

    dirty_output = _run_git(args.code_root, "status", "--porcelain", "--untracked-files=no")
    if dirty_output:
        raise RuntimeError("Execution requires a clean tracked worktree")
    git_sha = _run_git(args.code_root, "rev-parse", "HEAD")

    manifest = _manifest_from_args(
        args,
        git_sha=git_sha,
        git_dirty=False,
        started_at=_utc_now(),
    )
    write_manifest(args.manifest_path, manifest)
    output_json, output_xlsx = _expected_output_paths(args.output_base, args.model)

    try:
        result = subprocess.run(
            build_generator_command(args, python_executable=Path(sys.executable)),
            cwd=args.code_root,
            env=build_pinned_environment(args),
            check=False,
        )
        status = "succeeded" if result.returncode == 0 and output_json.exists() else "failed"
        summary = _summarize_output(output_json) if output_json.exists() else {}
        finalized = finalize_run_manifest(
            manifest,
            status=status,
            completed_at=_utc_now(),
            output_json=output_json,
            output_xlsx=output_xlsx,
            summary=summary,
        )
        write_manifest(args.manifest_path, finalized)
        if status != "succeeded":
            return result.returncode or 1
        return 0
    except Exception:
        finalized = finalize_run_manifest(
            manifest,
            status="failed",
            completed_at=_utc_now(),
        )
        write_manifest(args.manifest_path, finalized)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
