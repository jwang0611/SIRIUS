"""File management routes (processed files, ALS CRUD, template files)."""

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from src.web.dependencies import session_operation, session_writer_operation
from src.web.security import (
    RATE_LIMIT_GENERAL,
    RATE_LIMIT_READ,
    limiter,
    safe_path,
)
from src.web.session_manager import session_manager

router = APIRouter()
logger = logging.getLogger(__name__)


def _serialize_file(path: Path) -> dict:
    stat = path.stat()
    return {
        "file_id": path.name,
        "file_name": path.name,
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


# ==================== Processed Files ====================


@router.get("/processed-files")
@limiter.limit(RATE_LIMIT_READ)
def list_processed_files(request: Request, x_session_id: str = Depends(session_operation, scope="request")):
    processed_dir = session_manager.get_session_processed_dir(x_session_id)
    processed_dir.mkdir(parents=True, exist_ok=True)
    files = sorted([str(path.relative_to(processed_dir)) for path in processed_dir.glob("*.json")])
    return {"files": files}


@router.get("/als-files")
@limiter.limit(RATE_LIMIT_READ)
def list_als_files(request: Request, x_session_id: str = Depends(session_operation, scope="request")):
    als_dir = session_manager.get_session_als_dir(x_session_id)
    als_dir.mkdir(parents=True, exist_ok=True)

    files = [_serialize_file(path) for path in als_dir.glob("*.xlsx")]
    files.sort(key=lambda x: x["modified"], reverse=True)
    return {"files": files}


@router.get("/template-files")
@limiter.limit(RATE_LIMIT_READ)
def list_template_files(request: Request):
    search_dirs = [
        Path("data/knowledge_base/template_spec"),
    ]
    files = []
    seen: set[str] = set()
    for template_dir in search_dirs:
        if not template_dir.is_dir():
            continue
        for path in template_dir.glob("*.xlsx"):
            if "template" in path.stem.lower() and path.name not in seen:
                seen.add(path.name)
                files.append(_serialize_file(path))
    files.sort(key=lambda x: x["modified"], reverse=True)
    return {"files": files}


@router.delete("/als-files")
@limiter.limit(RATE_LIMIT_GENERAL)
def delete_als_file(
    request: Request,
    file_id: str = Body(..., embed=True),
    x_session_id: str = Depends(session_writer_operation, scope="request"),
):
    """Delete an ALS2SDTM file from the caller's own namespace."""
    als_dir = session_manager.get_session_als_dir(x_session_id)
    als_file = safe_path(als_dir, file_id)
    if not als_file.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    try:
        als_file.unlink()
        return {"status": "success", "message": f"文件 {als_file.name} 已删除"}
    except Exception as exc:
        logger.warning("Failed to delete ALS output file (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="删除失败，请查看服务端日志") from exc
