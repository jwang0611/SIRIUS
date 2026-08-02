"""Corrections routes — continuous learning loop for SDTM mapping corrections.

Enables users to submit corrections to AI mapping recommendations.
Corrections are persisted as session-level KB entries (parquet) and
automatically used in subsequent mapping queries for that session.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.utils.atomic_file import atomic_staging_path
from src.web.dependencies import session_operation, session_writer_operation
from src.web.security import RATE_LIMIT_GENERAL, limiter
from src.web.session_manager import session_manager

logger = logging.getLogger(__name__)

router = APIRouter()

CORRECTION_COLUMNS = [
    "annotation_table",
    "metadata_table",
    "annotation_variable",
    "metadata_variable",
    "SDTM_Domain",
    "SDTM_Variable",
    "_kb_source",
    "_corrected_at",
]


class CorrectionsStorageError(RuntimeError):
    """Raised when an existing corrections shard cannot be read safely."""


# ── Request / Response models ──────────────────────────────────


class CorrectionItem(BaseModel):
    """A single mapping correction submitted by the user."""

    annotation_table: str = Field(..., description="Source CRF table")
    metadata_table: str = Field(..., description="Metadata table")
    annotation_variable: str = Field(..., description="Annotation variable name")
    metadata_variable: str = Field(..., description="Metadata variable name")

    # Old (AI-recommended) values
    old_domain: str = Field("", description="Previous SDTM domain")
    old_sdtm_variable: str = Field("", description="Previous SDTM variable")

    # New (user-corrected) values
    new_domain: str = Field(..., description="Corrected SDTM domain")
    new_sdtm_variable: str = Field(..., description="Corrected SDTM variable")


class CorrectionBatch(BaseModel):
    """Batch of corrections from a single review session."""

    corrections: list[CorrectionItem] = Field(..., min_length=1, description="One or more corrections")


class CorrectionResponse(BaseModel):
    saved: int = Field(..., description="Number of corrections persisted")
    total_corrections: int = Field(..., description="Total corrections in session KB after save")
    kb_file: str = Field(..., description="Session corrections KB filename")


# ── Helpers ────────────────────────────────────────────────────


def _get_corrections_file(session_id: str) -> Path:
    """Return the parquet path for session-level corrections."""
    kb_dir = session_manager.get_session_kb_dir(session_id)
    return kb_dir / f"corrections_{session_manager.session_dir_key(session_id)}.parquet"


def _load_existing_corrections(path: Path) -> pd.DataFrame:
    """Load existing correction records or return empty DataFrame."""
    if not path.exists():
        return _empty_corrections_frame()
    try:
        frame = pd.read_parquet(path)
    except Exception as exc:
        logger.warning("Failed to load corrections file (%s)", type(exc).__name__)
        raise CorrectionsStorageError("existing corrections are unreadable") from exc
    missing = [column for column in CORRECTION_COLUMNS if column not in frame.columns]
    if missing:
        logger.warning("Corrections file has an incomplete schema")
        raise CorrectionsStorageError("existing corrections have an incomplete schema")
    return frame


def _empty_corrections_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=CORRECTION_COLUMNS)


def _build_correction_records(
    items: list[CorrectionItem],
    session_id: str,
) -> pd.DataFrame:
    """Convert CorrectionItem list to a KB-compatible DataFrame."""
    rows: list[dict[str, Any]] = []
    now = datetime.now(UTC).isoformat()
    for item in items:
        rows.append(
            {
                "annotation_table": item.annotation_table,
                "metadata_table": item.metadata_table,
                "annotation_variable": item.annotation_variable,
                "metadata_variable": item.metadata_variable,
                "SDTM_Domain": item.new_domain.upper().strip(),
                "SDTM_Variable": item.new_sdtm_variable.upper().strip(),
                "_kb_source": f"correction:{session_manager.session_dir_key(session_id)}",
                "_corrected_at": now,
            }
        )
    return pd.DataFrame(rows)


def _deduplicate_corrections(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only the latest correction for each variable key."""
    key_cols = [
        "annotation_table",
        "metadata_table",
        "annotation_variable",
        "metadata_variable",
    ]
    missing = [column for column in [*key_cols, "_corrected_at"] if column not in df.columns]
    if missing:
        raise CorrectionsStorageError("corrections frame has an incomplete schema")
    ordered = df.copy()
    ordered["_write_order"] = range(len(ordered))
    return (
        ordered.sort_values(
            ["_corrected_at", "_write_order"],
            ascending=[True, True],
            kind="stable",
        )
        .drop_duplicates(subset=key_cols, keep="last")
        .drop(columns=["_write_order"])
        .reset_index(drop=True)
    )


# ── Endpoints ──────────────────────────────────────────────────


@router.post("/corrections", response_model=CorrectionResponse)
@limiter.limit(RATE_LIMIT_GENERAL)
async def submit_corrections(
    request: Request,
    batch: CorrectionBatch = Body(...),
    x_session_id: str = Depends(session_writer_operation, scope="request"),
):
    """Submit user corrections to SDTM mapping results.

    Corrections are saved as a session-level KB parquet file.  The next
    mapping run for this session will automatically pick them up via
    ``add_extra_kb_file``, giving corrected entries confidence 1.0.
    """
    session_id = x_session_id

    corrections_path = _get_corrections_file(session_id)
    try:
        existing_df = _load_existing_corrections(corrections_path)
    except CorrectionsStorageError as exc:
        raise HTTPException(
            status_code=500,
            detail="现有更正数据无法读取，未保存任何修改",
        ) from exc
    new_df = _build_correction_records(batch.corrections, session_id)
    merged = _deduplicate_corrections(pd.concat([existing_df, new_df], ignore_index=True))

    # Persist
    with atomic_staging_path(corrections_path) as staged_corrections:
        merged.to_parquet(staged_corrections, index=False)

    # Register with session manager so cleanup includes this file
    if not session_manager.add_kb_file(session_id, str(corrections_path)):
        raise HTTPException(status_code=409, detail="Session 正在清理，请重试")

    # Log corrections via audit logger (best-effort)
    try:
        from src.infrastructure.audit_logger import AuditLogger

        auditor = AuditLogger(session_id=session_id)
        for item in batch.corrections:
            auditor.log_correction(
                variable_data={
                    "metadata_table": item.metadata_table,
                    "metadata_variable": item.metadata_variable,
                    "annotation_table": item.annotation_table,
                    "annotation_variable": item.annotation_variable,
                },
                old_result={
                    "domain": item.old_domain,
                    "sdtm_variable": item.old_sdtm_variable,
                },
                new_result={
                    "domain": item.new_domain,
                    "sdtm_variable": item.new_sdtm_variable,
                },
            )
    except Exception as exc:
        logger.warning("Audit logging for corrections failed (%s)", type(exc).__name__)

    logger.info(
        "[Corrections] saved=%d total=%d",
        len(batch.corrections),
        len(merged),
    )

    return CorrectionResponse(
        saved=len(batch.corrections),
        total_corrections=len(merged),
        kb_file=corrections_path.name,
    )


@router.get("/corrections")
@limiter.limit(RATE_LIMIT_GENERAL)
async def get_corrections(
    request: Request,
    x_session_id: str = Depends(session_operation, scope="request"),
):
    """Retrieve all corrections for the current session."""
    session_id = x_session_id
    corrections_path = _get_corrections_file(session_id)
    try:
        df = _load_existing_corrections(corrections_path)
    except CorrectionsStorageError as exc:
        raise HTTPException(status_code=500, detail="现有更正数据无法读取") from exc
    records = df.to_dict(orient="records")
    return {"corrections": records, "total": len(records)}
