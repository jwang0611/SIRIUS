"""Content identity for safe recommendation checkpoint resume."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

CHECKPOINT_CONTEXT_SCHEMA = "recommendation-context-v1"


def file_sha256(path: str | Path) -> str:
    """Return a streaming SHA-256 digest for one immutable job input."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_artifact(path: Path, role: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return {
        "role": role,
        "sha256": file_sha256(path),
        "size": path.stat().st_size,
    }


def build_recommendation_context(
    json_path: str | Path,
    *,
    kb_files: list[str] | tuple[str, ...],
    language: str,
    enable_kb: bool,
) -> dict[str, Any]:
    """Build a secret-free manifest for every input that affects one run.

    The expected snapshot layout mirrors the normal ``data/processed`` and
    ``data/raw`` relationship: the processed workbook sits beside the JSON,
    while the raw workbook sits in a sibling ``raw`` directory.
    """
    input_path = Path(json_path)
    processed_excel = input_path.with_suffix(".xlsx")
    raw_excel = input_path.parent.parent / "raw" / f"{input_path.stem}.xlsx"

    kb_artifacts = []
    for position, kb_file in enumerate(kb_files):
        path = Path(kb_file)
        kb_artifacts.append(
            {
                "position": position,
                "source_ref": hashlib.sha256(path.name.encode("utf-8")).hexdigest(),
                "sha256": file_sha256(path),
                "size": path.stat().st_size,
            }
        )

    return {
        "schema": CHECKPOINT_CONTEXT_SCHEMA,
        "config": {
            "language": language,
            "enable_kb": bool(enable_kb),
        },
        "inputs": {
            "json": _optional_artifact(input_path, "processed_json"),
            "processed_excel": _optional_artifact(processed_excel, "processed_excel"),
            "raw_excel": _optional_artifact(raw_excel, "raw_excel"),
        },
        "knowledge_base": kb_artifacts,
    }


__all__ = [
    "CHECKPOINT_CONTEXT_SCHEMA",
    "build_recommendation_context",
    "file_sha256",
]
