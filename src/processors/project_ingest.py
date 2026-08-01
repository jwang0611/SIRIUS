"""Project KB auto-ingestion — turns a QC'd ALS2SDTM file into a session KB shard."""

from __future__ import annotations

import logging
import re
import tempfile
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from src.infrastructure.audit_logger import AuditLogger
from src.processors.als_converter import convert_als2sdtm
from src.utils.atomic_file import atomic_staging_path
from src.web.session_manager import SessionClosingError, session_manager

logger = logging.getLogger(__name__)

PROJECT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
PROJECT_NAME_MIN_LEN = 1
PROJECT_NAME_MAX_LEN = 64

DEDUP_KEYS = [
    "annotation_table",
    "annotation_variable",
    "metadata_table",
    "metadata_variable",
]


def _slug(project_name: str) -> str:
    slug = re.sub(r"\s+", "_", project_name.strip())
    slug = re.sub(r"[^A-Za-z0-9_.-]", "", slug)
    return slug


def _validate_project_name(project_name: str) -> None:
    if not isinstance(project_name, str):
        raise ValueError("project_name must be a string")
    stripped = project_name.strip()
    if not (PROJECT_NAME_MIN_LEN <= len(stripped) <= PROJECT_NAME_MAX_LEN):
        raise ValueError(
            f"project_name length must be between {PROJECT_NAME_MIN_LEN} and "
            f"{PROJECT_NAME_MAX_LEN} (got {len(stripped)})"
        )
    if not PROJECT_NAME_PATTERN.match(stripped):
        raise ValueError(f"project_name must match {PROJECT_NAME_PATTERN.pattern} (got {project_name!r})")


def ingest_project_kb(
    session_id: str,
    als_file_path: Path,
    project_name: str,
    sheet_name: str = "Sheet1",
    *,
    _writer_locked: bool = False,
) -> Path:
    """Ingest an ALS2SDTM file as a session-scoped project KB shard."""
    _validate_project_name(project_name)
    slug = _slug(project_name)

    writer_context = nullcontext() if _writer_locked else session_manager.writer_operation(session_id)
    with writer_context:
        kb_dir = session_manager.get_session_kb_dir(session_id)
        kb_dir.mkdir(parents=True, exist_ok=True)
        shard_path = kb_dir / f"project_{slug}.parquet"

        with tempfile.TemporaryDirectory() as tmp:
            outputs = convert_als2sdtm(
                input_file=str(als_file_path),
                output_dir=tmp,
                output_name=f"_ingest_{slug}",
                sheet_name=sheet_name,
                output_format="parquet",
            )
            new_df = pd.read_parquet(outputs["parquet"])

        now_iso = datetime.now(UTC).isoformat()
        new_df["_kb_source"] = f"project:{project_name}"
        new_df["_ingested_at"] = now_iso
        new_df["_session_ref"] = session_manager.session_dir_key(session_id)

        if shard_path.exists():
            existing = pd.read_parquet(shard_path)
            combined = pd.concat([existing, new_df], ignore_index=True)
        else:
            combined = new_df

        combined = combined.sort_values("_ingested_at", ascending=True)
        combined = combined.drop_duplicates(subset=DEDUP_KEYS, keep="last").reset_index(drop=True)

        with atomic_staging_path(shard_path) as staged_shard:
            combined.to_parquet(staged_shard, index=False)
        if not session_manager.add_kb_file(session_id, str(shard_path)):
            raise SessionClosingError("session is closing")

    audit = AuditLogger(session_id=session_id)
    audit.log_project_ingest(
        project_name=project_name,
        record_count=len(combined),
        kb_file=shard_path.name,
    )

    logger.info(
        "Project KB ingested: rows=%d",
        len(combined),
    )
    return shard_path


def load_session_reference_kb(session_id: str) -> pd.DataFrame | None:
    """Load all project_*.parquet shards for a session and concatenate."""
    kb_dir = session_manager.get_session_kb_dir(session_id)
    if not kb_dir.exists():
        return None
    return load_reference_kb_files([str(path) for path in sorted(kb_dir.glob("project_*.parquet"))])


def load_reference_kb_files(file_paths: list[str]) -> pd.DataFrame | None:
    """Load project reference shards from an immutable, caller-owned list."""
    shards = [
        Path(path)
        for path in file_paths
        if Path(path).name.startswith("project_") and Path(path).suffix.lower() == ".parquet"
    ]
    if not shards:
        return None
    frames = [pd.read_parquet(p) for p in shards]
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)
