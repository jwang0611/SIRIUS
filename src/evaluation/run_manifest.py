"""Reproducible, secret-free run manifests for SDTM experiments."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

SCHEMA_VERSION = "1.0.0"
_KB_SUFFIXES = {".json", ".parquet"}
_PROMPT_PATHS = {
    "template": Path("src/prompts/templates/variable_mapping.yaml"),
    "rules": Path("src/prompts/rules/sdtm_rules.yaml"),
    "examples": Path("src/prompts/examples/pattern_examples.yaml"),
}


def hash_file(path: Path, *, normalize_text: bool = False) -> str:
    """Return a SHA-256 hash, optionally normalizing CRLF text to LF."""
    payload = path.read_bytes()
    if normalize_text:
        payload = payload.replace(b"\r\n", b"\n")
    return hashlib.sha256(payload).hexdigest()


def sanitize_endpoint(url: str) -> dict[str, str | int | None]:
    """Keep only the endpoint origin fields needed for an audit."""
    parsed = urlsplit(url)
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname or "",
        "port": parsed.port,
    }


def _json_row_count(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError(f"Expected JSON list for row-counted input: {path}")
    return len(payload)


def _input_record(path: Path) -> dict[str, Any]:
    return {
        # Manifests are shareable audit artifacts. Absolute workstation paths
        # can contain user, study, or customer identifiers and are unnecessary
        # once the content hash and logical input slot are recorded.
        "path": path.name,
        "sha256": hash_file(path, normalize_text=True),
        "row_count": _json_row_count(path),
    }


def _knowledge_base_records(kb_root: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for path in sorted(
        (candidate for candidate in kb_root.rglob("*") if candidate.is_file() and candidate.suffix in _KB_SUFFIXES),
        key=lambda candidate: candidate.relative_to(kb_root).as_posix(),
    ):
        records.append(
            {
                "path": path.relative_to(kb_root).as_posix(),
                "sha256": hash_file(path, normalize_text=path.suffix == ".json"),
            }
        )
    return records


def prompt_records(code_root: Path) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    for component, relative_path in _PROMPT_PATHS.items():
        path = code_root / relative_path
        with path.open(encoding="utf-8") as handle:
            payload = yaml.safe_load(handle)
        if not isinstance(payload, dict) or not payload.get("version"):
            raise ValueError(f"Prompt component has no version: {path}")
        records[component] = {
            "path": relative_path.as_posix(),
            "version": str(payload["version"]),
            "sha256": hash_file(path, normalize_text=True),
        }
    return records


def build_run_manifest(
    *,
    run_label: str,
    code_root: Path,
    git_sha: str,
    git_dirty: bool,
    input_path: Path,
    heldout_path: Path,
    kb_root: Path,
    provider: str,
    model: str,
    endpoint: str,
    generation: dict[str, Any],
    rag: dict[str, Any],
    concurrency: dict[str, Any],
    cascade: dict[str, Any],
    started_at: str,
) -> dict[str, Any]:
    """Build a running manifest entirely from explicit experiment inputs."""
    return {
        "schema_version": SCHEMA_VERSION,
        "run_label": run_label,
        "status": "running",
        "started_at": started_at,
        "completed_at": None,
        "git": {"sha": git_sha, "dirty": bool(git_dirty)},
        "inputs": {
            "benchmark": _input_record(input_path),
            "heldout": _input_record(heldout_path),
        },
        "knowledge_base": _knowledge_base_records(kb_root),
        "prompts": prompt_records(code_root),
        "configuration": {
            "provider": provider,
            "model": model,
            "endpoint": sanitize_endpoint(endpoint),
            "generation": copy.deepcopy(generation),
            "rag": copy.deepcopy(rag),
            "concurrency": copy.deepcopy(concurrency),
            "cascade": copy.deepcopy(cascade),
        },
        "outputs": {},
        "summary": {},
    }


def _shared_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    inputs = manifest.get("inputs", {})
    return {
        "configuration": manifest.get("configuration", {}),
        "inputs": {
            name: {
                "sha256": record.get("sha256"),
                "row_count": record.get("row_count"),
            }
            for name, record in inputs.items()
        },
        "knowledge_base": manifest.get("knowledge_base", []),
    }


def _difference_paths(left: Any, right: Any, prefix: str = "") -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        differences: list[str] = []
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in left or key not in right:
                differences.append(path)
            else:
                differences.extend(_difference_paths(left[key], right[key], path))
        return differences
    if left != right:
        return [prefix]
    return []


def compare_shared_configuration(
    baseline: dict[str, Any],
    improved: dict[str, Any],
) -> dict[str, Any]:
    """Compare frozen run inputs and report runner drift as audit-only evidence."""
    differences = _difference_paths(_shared_identity(baseline), _shared_identity(improved))
    audit_differences = _difference_paths(
        {"runner": baseline.get("runner", {})},
        {"runner": improved.get("runner", {})},
    )
    comparison = {
        "equal": not differences,
        "differences": differences,
    }
    if audit_differences:
        comparison["audit_differences"] = audit_differences
    return comparison


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Write a manifest atomically enough for local experiment auditing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def finalize_run_manifest(
    manifest: dict[str, Any],
    *,
    status: str,
    completed_at: str,
    output_json: Path | None = None,
    output_xlsx: Path | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a finalized copy with output hashes and aggregate-only summary."""
    finalized = copy.deepcopy(manifest)
    finalized["status"] = status
    finalized["completed_at"] = completed_at
    outputs: dict[str, dict[str, str]] = {}
    for name, path in (("json", output_json), ("xlsx", output_xlsx)):
        if path is not None and path.exists():
            outputs[name] = {
                "path": path.name,
                "sha256": hash_file(path, normalize_text=path.suffix == ".json"),
            }
    finalized["outputs"] = outputs
    finalized["summary"] = copy.deepcopy(summary or {})
    return finalized
