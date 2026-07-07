"""File management routes (processed files, ALS CRUD, template files)."""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from src.web.security import (
    RATE_LIMIT_GENERAL,
    RATE_LIMIT_READ,
    limiter,
    safe_path,
)

router = APIRouter()


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
def list_processed_files(request: Request):
    processed_dir = Path("data/processed")
    processed_dir.mkdir(parents=True, exist_ok=True)
    files = sorted([str(path.relative_to(processed_dir)) for path in processed_dir.glob("*.json")])
    return {"files": files}


@router.get("/als-files")
@limiter.limit(RATE_LIMIT_READ)
def list_als_files(request: Request):
    als_dir = Path("data/output")
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


@router.delete("/als-files/{file_id}")
@limiter.limit(RATE_LIMIT_GENERAL)
def delete_als_file(request: Request, file_id: str):
    """Delete an ALS2SDTM file from data/output directory."""
    als_file = safe_path(Path("data/output"), file_id)
    if not als_file.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    try:
        als_file.unlink()
        return {"status": "success", "message": f"文件 {als_file.name} 已删除"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"删除失败: {exc}") from exc
