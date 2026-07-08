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
from fastapi import APIRouter, Body, Header, HTTPException, Request
from pydantic import BaseModel, Field

from src.web.security import RATE_LIMIT_GENERAL, limiter
from src.web.session_manager import session_manager

logger = logging.getLogger(__name__)

router = APIRouter()


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
    kb_file: str = Field(..., description="Path to session corrections KB file")


# ── Helpers ────────────────────────────────────────────────────


def _get_corrections_file(session_id: str) -> Path:
    """Return the parquet path for session-level corrections."""
    kb_dir = session_manager.get_session_kb_dir(session_id)
    return kb_dir / f"corrections_{session_manager.session_dir_key(session_id)}.parquet"


def _load_existing_corrections(path: Path) -> pd.DataFrame:
    """Load existing correction records or return empty DataFrame."""
    try:
        return pd.read_parquet(path)
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("Failed to load corrections file %s: %s", path, exc)
    return pd.DataFrame(
        columns=[
            "annotation_table",
            "metadata_table",
            "annotation_variable",
            "metadata_variable",
            "SDTM_Domain",
            "SDTM_Variable",
            "_kb_source",
            "_corrected_at",
        ]
    )


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
                "_kb_source": f"correction:{session_id[:12]}",
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
    present_keys = [c for c in key_cols if c in df.columns]
    if not present_keys or "_corrected_at" not in df.columns:
        return df
    return (
        df.sort_values("_corrected_at", ascending=False)
        .drop_duplicates(subset=present_keys, keep="first")
        .reset_index(drop=True)
    )


# ── Endpoints ──────────────────────────────────────────────────


@router.post("/corrections", response_model=CorrectionResponse)
@limiter.limit(RATE_LIMIT_GENERAL)
async def submit_corrections(
    request: Request,
    batch: CorrectionBatch = Body(...),
    x_session_id: str | None = Header(None),
):
    """Submit user corrections to SDTM mapping results.

    Corrections are saved as a session-level KB parquet file.  The next
    mapping run for this session will automatically pick them up via
    ``add_extra_kb_file``, giving corrected entries confidence 1.0.
    """
    session_id = x_session_id
    if not session_id:
        raise HTTPException(status_code=400, detail="X-Session-ID header required")

    # Ensure session exists
    session_manager.get_or_create(session_id)

    corrections_path = _get_corrections_file(session_id)
    existing_df = _load_existing_corrections(corrections_path)
    new_df = _build_correction_records(batch.corrections, session_id)
    merged = _deduplicate_corrections(pd.concat([existing_df, new_df], ignore_index=True))

    # Persist
    merged.to_parquet(corrections_path, index=False)

    # Register with session manager so cleanup includes this file
    session_manager.add_kb_file(session_id, str(corrections_path))

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
        logger.warning("Audit logging for corrections failed: %s", exc)

    logger.info(
        "[Corrections] session=%s saved=%d total=%d path=%s",
        session_id[:12],
        len(batch.corrections),
        len(merged),
        corrections_path,
    )

    return CorrectionResponse(
        saved=len(batch.corrections),
        total_corrections=len(merged),
        kb_file=str(corrections_path),
    )


@router.get("/corrections")
@limiter.limit(RATE_LIMIT_GENERAL)
async def get_corrections(
    request: Request,
    x_session_id: str | None = Header(None),
):
    """Retrieve all corrections for the current session."""
    session_id = x_session_id
    if not session_id:
        raise HTTPException(status_code=400, detail="X-Session-ID header required")

    corrections_path = _get_corrections_file(session_id)
    df = _load_existing_corrections(corrections_path)
    records = df.to_dict(orient="records")
    return {"corrections": records, "total": len(records)}
