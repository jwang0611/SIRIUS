"""Spec Mapper and ALS2SDTM conversion routes."""

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Request
from pydantic import BaseModel, Field

from src.processors.project_ingest import ingest_project_kb
from src.web.job_manager import job_manager
from src.web.security import (
    PYTHON_BIN,
    RATE_LIMIT_AI_JOB,
    RATE_LIMIT_GENERAL,
    RATE_LIMIT_READ,
    InvalidWorkbookError,
    limiter,
    run_command,
    safe_path,
    sanitize_filename,
)
from src.web.session_manager import session_manager
from src.web.tasks import start_spec_mapper_job

router = APIRouter()
logger = logging.getLogger(__name__)


class SpecMapperRequest(BaseModel):
    als_file: str = Field(..., description="ALS2SDTM 文件名")
    template_file: str = Field(..., description="SDTM 模板文件名")
    output_name: str = Field(..., description="输出文件名（不含扩展名）")
    als_sheet: str = Field(default="Sheet1", description="ALS2SDTM sheet 名称")
    highlight: bool = Field(default=True, description="是否高亮显示新映射")
    create_test_sheets: bool = Field(default=True, description="是否创建 TEST sheets（如 FATEST）")
    # 默认与 Web UI 一致（Step3 未单独收集项目名时仍可通过校验并回注 KB）
    project_name: str = Field(
        default="web",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.-]+$",
        description="项目名，用于项目级 KB 自动回注",
    )


@router.post("/spec-mapper/run")
@limiter.limit(RATE_LIMIT_AI_JOB)
def run_spec_mapper(
    request: Request,
    body: SpecMapperRequest,
    x_session_id: str | None = Header(None),
):
    safe_als_file = sanitize_filename(body.als_file)
    safe_template_file = sanitize_filename(body.template_file)
    safe_output_name = sanitize_filename(body.output_name.replace(".xlsx", ""))

    als_path = Path("data/output") / safe_als_file
    template_path = Path("data/knowledge_base/template_spec") / safe_template_file

    if not als_path.exists():
        raise HTTPException(status_code=404, detail=f"ALS 文件不存在: {safe_als_file}")
    if not template_path.exists():
        raise HTTPException(status_code=404, detail=f"模板文件不存在: {safe_template_file}")

    if x_session_id:
        session_manager.get_or_create(x_session_id)
        try:
            shard_path = ingest_project_kb(
                session_id=x_session_id,
                als_file_path=als_path,
                project_name=body.project_name,
                sheet_name=body.als_sheet,
            )
            logger.info("Project KB ingested before spec-mapper job: %s", shard_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("Project KB ingestion failed: %s", exc)
            raise HTTPException(status_code=500, detail=f"项目 KB 回注失败: {exc}") from exc

    job_id = uuid.uuid4().hex
    job_manager.create_job(job_id)

    if x_session_id:
        session_manager.add_job(x_session_id, job_id)

    start_spec_mapper_job(
        job_id=job_id,
        als_file=safe_als_file,
        template_file=safe_template_file,
        output_name=safe_output_name,
        als_sheet=body.als_sheet,
        highlight=body.highlight,
        create_test_sheets=body.create_test_sheets,
        session_id=x_session_id,
    )

    return {
        "job_id": job_id,
        "message": "Spec Mapper 任务已启动",
    }


# ==================== ALS2SDTM Conversion ====================


@router.post("/convert-als2sdtm")
@limiter.limit(RATE_LIMIT_GENERAL)
def convert_als2sdtm(
    request: Request,
    file_path: str = Body(...),
    sheet_name: str | None = Body(None),
    x_session_id: str | None = Header(None),
):
    """手动触发 ALS2SDTM 转换，支持指定 sheet。输出到 session 专属目录。"""
    safe_filename = Path(file_path).name

    # 优先检查 session 目录，然后检查共享目录
    input_path = None
    if x_session_id:
        session_dir = session_manager.get_session_kb_dir(x_session_id)
        session_file = session_dir / safe_filename
        if session_file.exists():
            input_path = session_file

    if input_path is None:
        documents_dir = Path("data/knowledge_base/documents")
        input_path = safe_path(documents_dir, safe_filename)

    if not input_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {safe_filename}")

    if x_session_id:
        session_manager.get_or_create(x_session_id)
        output_dir = session_manager.get_session_kb_dir(x_session_id)
    else:
        output_dir = Path("data/knowledge_base/structured")
    output_dir.mkdir(parents=True, exist_ok=True)

    command = [
        PYTHON_BIN,
        "scripts/convert_als2sdtm.py",
        "--input",
        str(input_path),
        "--output-dir",
        str(output_dir),
    ]
    if sheet_name:
        command.extend(["--sheet", sheet_name])

    try:
        result = run_command(command)
    except InvalidWorkbookError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"转换失败: {exc}") from exc

    kb_files: list[str] = []
    if x_session_id:
        for ext in ["*.parquet", "*.json"]:
            for kb_file in output_dir.glob(f"{input_path.stem}{ext}"):
                kb_files.append(str(kb_file))
                session_manager.add_kb_file(x_session_id, str(kb_file))
        print(f"[Convert] 生成 KB 文件: {kb_files} -> session {x_session_id[:12]}...")

    return {
        "message": "转换完成",
        "output_dir": str(output_dir),
        "kb_files": kb_files,
        "log": result,
    }


@router.post("/list-sheets")
@limiter.limit(RATE_LIMIT_READ)
def list_sheets(
    request: Request,
    file_path: str = Body(...),
    x_session_id: str | None = Header(None),
) -> dict[str, Any]:
    """列出 Excel 中的可用 sheet（优先非空）。"""
    safe_filename = Path(file_path).name

    input_path = None
    if x_session_id:
        session_dir = session_manager.get_session_kb_dir(x_session_id)
        session_file = session_dir / safe_filename
        if session_file.exists():
            input_path = session_file

    if input_path is None:
        documents_dir = Path("data/knowledge_base/documents")
        input_path = safe_path(documents_dir, safe_filename)

    if not input_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {safe_filename}")

    list_cmd = [
        PYTHON_BIN,
        "scripts/convert_als2sdtm.py",
        "--input",
        str(input_path),
        "--list-sheets",
    ]
    try:
        sheets_json = run_command(list_cmd)
        sheets = json.loads(sheets_json) if sheets_json else []
    except InvalidWorkbookError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"列出 sheet 失败: {exc}") from exc

    return {"sheets": sheets}
