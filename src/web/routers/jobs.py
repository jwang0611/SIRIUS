"""Job management routes (recommendations, status, download, cancel)."""

import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from src.web.job_manager import job_manager
from src.web.security import (
    RATE_LIMIT_AI_JOB,
    RATE_LIMIT_GENERAL,
    RATE_LIMIT_READ,
    limiter,
    sanitize_filename,
)
from src.web.session_manager import session_manager
from src.web.tasks import start_recommendations_job

router = APIRouter()


class RecommendationRequest(BaseModel):
    json_file: str = Field(..., description="位于 data/processed 下的 JSON 文件名或绝对路径")
    language: str = Field("en", description="Prompt 语言 (en/cn)")
    enable_kb: bool = Field(True, description="是否启用知识库")
    model_name: str = Field("google/gemini-2.5-flash", description="LLM 模型名称")
    resume: bool = Field(False, description="是否从上次进度恢复")
    base_url: str | None = Field(
        None, max_length=500, description="OpenAI 兼容 API Base URL（可选，默认使用服务器环境变量）"
    )
    api_token: str | None = Field(
        None, max_length=500, description="API Key（可选，仅本次任务使用，不落盘不回显）"
    )

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        parts = urlsplit(v)
        # 拒绝带 userinfo 的 URL，避免凭据混入 URL 被日志打印
        if parts.username or parts.password:
            raise ValueError("base_url 不能包含用户名/密码")
        return v

    @field_validator("api_token")
    @classmethod
    def _normalize_api_token(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None


@router.post("/recommendations")
@limiter.limit(RATE_LIMIT_AI_JOB)
def create_recommendation_job(
    request: Request,
    body: RecommendationRequest,
    x_session_id: str | None = Header(None),
):
    safe_json_file = sanitize_filename(body.json_file)

    json_path = Path("data/processed") / safe_json_file
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"JSON 文件不存在: {safe_json_file}")

    job_id = uuid.uuid4().hex
    job_manager.create_job(job_id)

    if x_session_id:
        session_manager.get_or_create(x_session_id)
        session_manager.add_job(x_session_id, job_id)

    start_recommendations_job(
        job_id=job_id,
        json_file=safe_json_file,
        language=body.language,
        enable_kb=body.enable_kb,
        model_name_override=body.model_name,
        resume=body.resume,
        session_id=x_session_id,
        base_url_override=body.base_url,
        api_key_override=body.api_token,
    )
    return {"job_id": job_id, "resume": body.resume}


@router.get("/jobs/{job_id}")
@limiter.limit(RATE_LIMIT_READ)
def get_job_status(request: Request, job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job.to_dict()


@router.get("/jobs/{job_id}/download")
@limiter.limit(RATE_LIMIT_READ)
def download_result(request: Request, job_id: str, format: str = Query("excel", pattern="^(excel|json)$")):
    job = job_manager.get_job(job_id)
    if not job or job.state != "completed":
        raise HTTPException(status_code=404, detail="任务未完成或不存在")

    target = job.output_excel if format == "excel" else job.output_json
    if not target or not Path(target).exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    filename = os.path.basename(target)
    return FileResponse(path=target, filename=filename)


@router.get("/jobs/{job_id}/download-log")
@limiter.limit(RATE_LIMIT_READ)
def download_log(request: Request, job_id: str):
    """下载任务的日志文件"""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")

    if not job.output_log:
        raise HTTPException(status_code=404, detail="该任务没有日志文件")

    log_path = Path(job.output_log)
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="日志文件不存在")

    filename = os.path.basename(job.output_log)
    return FileResponse(path=job.output_log, filename=filename, media_type="text/plain")


@router.post("/jobs/{job_id}/cancel")
@limiter.limit(RATE_LIMIT_GENERAL)
def cancel_job(request: Request, job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")

    job_manager.cancel_job(job_id)

    return {
        "status": "cancelled",
        "job_id": job_id,
        "message": "任务已终止，进度已保存",
        "can_resume": True,
        "json_file": job.json_file,
    }
