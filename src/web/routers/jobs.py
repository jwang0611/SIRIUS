"""Job management routes (recommendations, status, download, cancel)."""

import logging
import os
import shutil
import uuid
from contextlib import suppress
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from src.utils.atomic_file import atomic_snapshot_file
from src.web.dependencies import existing_session_operation, session_writer_operation
from src.web.job_manager import job_manager
from src.web.security import (
    RATE_LIMIT_AI_JOB,
    RATE_LIMIT_GENERAL,
    RATE_LIMIT_READ,
    is_server_default_llm_endpoint,
    limiter,
    sanitize_filename,
    validate_llm_base_url,
)
from src.web.session_manager import session_manager
from src.web.tasks import _find_existing_output_base, start_recommendations_job

router = APIRouter()
logger = logging.getLogger(__name__)


def _owned_job_or_404(job_id: str, session_id: str):
    """Return a job only when it belongs to the requesting session.

    Ownership mismatches intentionally use the same 404 as an unknown job to
    avoid turning the endpoint into a job-enumeration oracle.
    """
    job = job_manager.get_job(job_id)
    if not job or not job_manager.is_owned_by(job_id, session_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


def _can_resume_cancelled_job(job, session_id: str) -> bool:
    """Return whether a cancelled recommendation has a complete checkpoint."""
    if job.state != "cancelled" or not job.json_file or not job.model_name or not job.checkpoint_context:
        return False
    output_dir = Path("data/output/sessions") / session_manager.session_dir_key(session_id)
    if not output_dir.is_dir():
        return False
    json_path = output_dir / Path(job.json_file).name
    return (
        _find_existing_output_base(
            json_path,
            job.model_name,
            output_dir,
            expected_context=job.checkpoint_context,
        )
        is not None
    )


class RecommendationRequest(BaseModel):
    json_file: str = Field(..., description="位于 data/processed 下的 JSON 文件名或绝对路径")
    language: str = Field("en", description="Prompt 语言 (en/cn)")
    enable_kb: bool = Field(True, description="是否启用知识库")
    model_name: str | None = Field(
        None,
        max_length=200,
        description="LLM 模型名称；为空时使用服务器 DEFAULT_MODEL",
    )
    resume: bool = Field(False, description="是否从上次进度恢复")
    base_url: str | None = Field(
        None, max_length=500, description="OpenAI 兼容 API Base URL（可选，默认使用服务器环境变量）"
    )
    api_token: str | None = Field(None, max_length=500, description="API Key（可选，仅本次任务使用，不落盘不回显）")

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        # scheme / userinfo / host allowlist 校验（防 SSRF），失败抛 ValueError → 422
        return validate_llm_base_url(v)

    @field_validator("api_token")
    @classmethod
    def _normalize_api_token(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None

    @model_validator(mode="after")
    def _require_token_for_custom_endpoint(self) -> "RecommendationRequest":
        # 非默认 endpoint 必须自带 token；否则会回退到服务器密钥并外泄到该 endpoint
        if self.base_url and not is_server_default_llm_endpoint(self.base_url) and not self.api_token:
            raise ValueError("使用非默认 Base URL 时必须提供 API Token（不会使用服务器密钥）")
        return self


@router.post("/recommendations")
@limiter.limit(RATE_LIMIT_AI_JOB)
def create_recommendation_job(
    request: Request,
    body: RecommendationRequest,
    x_session_id: str = Depends(session_writer_operation, scope="request"),
):
    safe_json_file = sanitize_filename(body.json_file)

    json_path = session_manager.get_session_processed_dir(x_session_id) / safe_json_file
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"JSON 文件不存在: {safe_json_file}")

    job_id = uuid.uuid4().hex
    snapshot_dir = session_manager.get_session_recommendation_job_dir(x_session_id, job_id)
    tracked_snapshots: list[Path] = []
    job_created = False

    def snapshot_and_track(source: Path, target: Path) -> Path:
        snapshot = atomic_snapshot_file(source, target)
        if not session_manager.add_file(x_session_id, str(snapshot)):
            snapshot.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail="Session 正在清理，请重试")
        tracked_snapshots.append(snapshot)
        return snapshot

    try:
        processed_snapshot_dir = snapshot_dir / "processed"
        raw_snapshot_dir = snapshot_dir / "raw"
        kb_snapshot_dir = snapshot_dir / "kb"
        json_snapshot = snapshot_and_track(
            json_path,
            processed_snapshot_dir / safe_json_file,
        )

        processed_excel = json_path.with_suffix(".xlsx")
        if processed_excel.is_file():
            snapshot_and_track(
                processed_excel,
                processed_snapshot_dir / processed_excel.name,
            )

        raw_excel = session_manager.get_session_raw_dir(x_session_id) / f"{json_path.stem}.xlsx"
        if raw_excel.is_file():
            snapshot_and_track(raw_excel, raw_snapshot_dir / raw_excel.name)

        kb_snapshots: list[str] = []
        for kb_file in session_manager.get_kb_files(x_session_id):
            source = Path(kb_file)
            if not source.is_file():
                raise HTTPException(status_code=409, detail="项目知识库已变化，请重新上传后重试")
            snapshot = snapshot_and_track(source, kb_snapshot_dir / source.name)
            kb_snapshots.append(str(snapshot.resolve()))

        job_manager.create_job(job_id, owner_session_id=x_session_id)
        job_created = True
        if not session_manager.add_job(x_session_id, job_id):
            raise HTTPException(status_code=409, detail="Session 正在清理，请重试")

        started = start_recommendations_job(
            job_id=job_id,
            json_file=str(json_snapshot.resolve()),
            language=body.language,
            enable_kb=body.enable_kb,
            model_name_override=body.model_name,
            resume=body.resume,
            session_id=x_session_id,
            kb_files_snapshot=kb_snapshots,
            base_url_override=body.base_url,
            api_key_override=body.api_token,
        )
        if started is False:
            raise HTTPException(status_code=409, detail="任务未能启动，请重试")
    except Exception as exc:
        if job_created:
            job_manager.remove_job(job_id)
            session_manager.discard_job(x_session_id, job_id)
        for snapshot in tracked_snapshots:
            session_manager.discard_file(x_session_id, snapshot)
        with suppress(OSError):
            shutil.rmtree(snapshot_dir)
        if isinstance(exc, HTTPException):
            raise
        logger.error("Recommendation job initialization failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="任务输入快照失败，请重试") from exc
    return {"job_id": job_id, "resume": body.resume}


@router.get("/jobs/{job_id}")
@limiter.limit(RATE_LIMIT_READ)
def get_job_status(request: Request, job_id: str, x_session_id: str = Header(...)):
    job = _owned_job_or_404(job_id, x_session_id)
    payload = job.to_dict()
    payload["can_resume"] = _can_resume_cancelled_job(job, x_session_id)
    return payload


@router.get("/jobs/{job_id}/download")
@limiter.limit(RATE_LIMIT_READ)
def download_result(
    request: Request,
    job_id: str,
    format: str = Query("excel", pattern="^(excel|json)$"),
    x_session_id: str = Depends(existing_session_operation, scope="request"),
):
    job = _owned_job_or_404(job_id, x_session_id)
    if job.state not in {"completed", "completed_with_errors"}:
        raise HTTPException(status_code=404, detail="任务未完成或不存在")

    target = job.output_excel if format == "excel" else job.output_json
    if not target or not Path(target).exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    filename = os.path.basename(target)
    return FileResponse(path=target, filename=filename)


@router.get("/jobs/{job_id}/download-issues")
@limiter.limit(RATE_LIMIT_READ)
def download_issues(
    request: Request,
    job_id: str,
    x_session_id: str = Depends(existing_session_operation, scope="request"),
):
    """下载任务的完整结构化写入问题清单 (JSON)。

    UI 的问题明细可能被截断；此端点提供完整、脱敏的问题列表（仅包含
    code/stage/operation/sheet/row/column/detail，绝不含路径、临床值或堆栈），
    以确保任何被跳过/失败的写入项都可被查看，而不是被静默丢弃。
    """
    job = _owned_job_or_404(job_id, x_session_id)

    if not job.output_issues:
        raise HTTPException(status_code=404, detail="该任务没有问题明细文件")

    issues_path = Path(job.output_issues)
    if not issues_path.exists():
        raise HTTPException(status_code=404, detail="问题明细文件不存在")

    filename = os.path.basename(job.output_issues)
    return FileResponse(path=job.output_issues, filename=filename, media_type="application/json")


@router.get("/jobs/{job_id}/download-log")
@limiter.limit(RATE_LIMIT_READ)
def download_log(
    request: Request,
    job_id: str,
    x_session_id: str = Depends(existing_session_operation, scope="request"),
):
    """下载任务的日志文件"""
    job = _owned_job_or_404(job_id, x_session_id)

    if not job.output_log:
        raise HTTPException(status_code=404, detail="该任务没有日志文件")

    log_path = Path(job.output_log)
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="日志文件不存在")

    filename = os.path.basename(job.output_log)
    return FileResponse(path=job.output_log, filename=filename, media_type="text/plain")


@router.post("/jobs/{job_id}/cancel")
@limiter.limit(RATE_LIMIT_GENERAL)
def cancel_job(request: Request, job_id: str, x_session_id: str = Header(...)):
    job = _owned_job_or_404(job_id, x_session_id)

    job_manager.cancel_job(job_id)
    updated = job_manager.get_job(job_id) or job
    can_resume = _can_resume_cancelled_job(updated, x_session_id)

    return {
        "status": updated.state,
        "job_id": job_id,
        "message": updated.message,
        "can_resume": can_resume,
        "json_file": updated.json_file,
    }
