"""Spec Mapper and ALS2SDTM conversion routes."""

import json
import logging
import shutil
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.processors.project_ingest import ingest_project_kb
from src.utils.atomic_file import atomic_snapshot_file
from src.web.dependencies import session_operation, session_writer_operation
from src.web.job_manager import job_manager
from src.web.security import (
    PYTHON_BIN,
    RATE_LIMIT_AI_JOB,
    RATE_LIMIT_GENERAL,
    RATE_LIMIT_READ,
    InvalidWorkbookError,
    limiter,
    run_command,
    sanitize_filename,
)
from src.web.session_manager import SessionClosingError, session_manager
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
    x_session_id: str = Depends(session_writer_operation, scope="request"),
):
    safe_als_file = sanitize_filename(body.als_file)
    safe_template_file = sanitize_filename(body.template_file)
    safe_output_name = sanitize_filename(body.output_name.replace(".xlsx", ""))

    als_path = session_manager.get_session_als_dir(x_session_id) / safe_als_file
    template_path = Path("data/knowledge_base/template_spec") / safe_template_file

    if not als_path.exists():
        raise HTTPException(status_code=404, detail=f"ALS 文件不存在: {safe_als_file}")
    if not template_path.exists():
        raise HTTPException(status_code=404, detail=f"模板文件不存在: {safe_template_file}")

    job_id = uuid.uuid4().hex
    job_dir = session_manager.get_session_spec_job_dir(x_session_id, job_id)
    tracked_snapshots: list[Path] = []
    job_created = False
    ingest_attempted = False
    project_shard = session_manager.get_session_kb_dir(x_session_id) / f"project_{body.project_name}.parquet"
    rollback_shard = job_dir / ".rollback" / project_shard.name
    had_project_shard = project_shard.is_file()

    def snapshot_and_track(source: Path, target: Path) -> Path:
        snapshot = atomic_snapshot_file(source, target)
        if not session_manager.add_file(x_session_id, str(snapshot)):
            snapshot.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail="Session 正在清理，请重试")
        tracked_snapshots.append(snapshot)
        return snapshot

    try:
        if had_project_shard:
            atomic_snapshot_file(project_shard, rollback_shard)
        als_snapshot = snapshot_and_track(als_path, job_dir / "input" / safe_als_file)
        template_snapshot = snapshot_and_track(
            template_path,
            job_dir / "input" / safe_template_file,
        )

        ingest_attempted = True
        try:
            ingest_project_kb(
                session_id=x_session_id,
                als_file_path=als_snapshot,
                project_name=body.project_name,
                sheet_name=body.als_sheet,
                _writer_locked=True,
            )
            logger.info("Project KB ingested before spec-mapper job")
        except ValueError as exc:
            logger.info("Project KB ingestion rejected (%s)", type(exc).__name__)
            raise HTTPException(
                status_code=422,
                detail="项目 KB 回注失败：请检查 ALS sheet 与必需列。",
            ) from exc
        except SessionClosingError as exc:
            raise HTTPException(status_code=409, detail="Session 正在清理，请重试") from exc
        except Exception as exc:
            logger.error("Project KB ingestion failed (%s)", type(exc).__name__)
            raise HTTPException(status_code=500, detail="项目 KB 回注失败，请查看服务端日志。") from exc

        job_manager.create_job(job_id, owner_session_id=x_session_id)
        job_created = True
        if not session_manager.add_job(x_session_id, job_id):
            raise HTTPException(status_code=409, detail="Session 正在清理，请重试")

        started = start_spec_mapper_job(
            job_id=job_id,
            als_file=str(als_snapshot.resolve()),
            template_file=str(template_snapshot.resolve()),
            output_name=safe_output_name,
            als_sheet=body.als_sheet,
            highlight=body.highlight,
            create_test_sheets=body.create_test_sheets,
            session_id=x_session_id,
        )
        if started is False:
            raise HTTPException(status_code=409, detail="任务未能启动，请重试")
        rollback_shard.unlink(missing_ok=True)
    except Exception as exc:
        if job_created:
            job_manager.remove_job(job_id)
            session_manager.discard_job(x_session_id, job_id)
        if ingest_attempted:
            if had_project_shard and rollback_shard.is_file():
                atomic_snapshot_file(rollback_shard, project_shard)
            elif not had_project_shard:
                project_shard.unlink(missing_ok=True)
                session_manager.discard_file(x_session_id, project_shard)
        for snapshot in tracked_snapshots:
            session_manager.discard_file(x_session_id, snapshot)
        with suppress(OSError):
            shutil.rmtree(job_dir)
        if isinstance(exc, HTTPException):
            raise
        logger.error("Spec job initialization failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Spec 任务初始化失败，请重试") from exc

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
    x_session_id: str = Depends(session_writer_operation, scope="request"),
):
    """手动触发 ALS2SDTM 转换，支持指定 sheet。输出到 session 专属目录。"""
    safe_filename = Path(file_path).name
    session_dir = session_manager.get_session_kb_dir(x_session_id)
    input_path = session_dir / safe_filename

    if not input_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {safe_filename}")

    output_dir = session_manager.get_session_kb_dir(x_session_id)
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
        logger.error("ALS2SDTM conversion failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="转换失败，请查看服务端日志。") from exc

    kb_files: list[str] = []
    for ext in ["*.parquet", "*.json"]:
        for kb_file in output_dir.glob(f"{input_path.stem}{ext}"):
            if not session_manager.add_kb_file(x_session_id, str(kb_file)):
                kb_file.unlink(missing_ok=True)
                raise HTTPException(status_code=409, detail="Session 正在清理，请重试")
            kb_files.append(str(kb_file))
    print(f"[Convert] 已生成 {len(kb_files)} 个 session KB 文件")

    return {
        "message": "转换完成",
        "output_dir": "session",
        "kb_files": [Path(path).name for path in kb_files],
        "log": "completed" if result else "",
    }


@router.post("/list-sheets")
@limiter.limit(RATE_LIMIT_READ)
def list_sheets(
    request: Request,
    file_path: str = Body(...),
    x_session_id: str = Depends(session_operation, scope="request"),
) -> dict[str, Any]:
    """列出 Excel 中的可用 sheet（优先非空）。"""
    safe_filename = Path(file_path).name
    session_dir = session_manager.get_session_kb_dir(x_session_id)
    input_path = session_dir / safe_filename

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
        logger.error("Workbook sheet listing failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="列出 sheet 失败，请查看服务端日志。") from exc

    return {"sheets": sheets}
