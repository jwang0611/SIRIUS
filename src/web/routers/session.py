"""Session management routes."""

import json

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from src.web.dependencies import existing_session_operation
from src.web.security import RATE_LIMIT_GENERAL, RATE_LIMIT_READ, limiter
from src.web.session_manager import is_valid_session_id, session_manager

router = APIRouter()


def _require_session_owner(session_id: str, x_session_id: str | None) -> None:
    """Treat the session ID as a bearer capability and require an exact match."""
    if x_session_id is None:
        raise HTTPException(status_code=404, detail="Session 不存在")
    if not is_valid_session_id(session_id) or not is_valid_session_id(x_session_id):
        raise HTTPException(status_code=422, detail="X-Session-ID 格式无效")
    if x_session_id != session_id:
        raise HTTPException(status_code=404, detail="Session 不存在")


@router.post("/session/init")
@limiter.limit(RATE_LIMIT_GENERAL)
async def init_session(
    request: Request,
    session_id: str = Body(..., embed=True),
    x_session_id: str | None = Header(None),
):
    """初始化或恢复 session"""
    _require_session_owner(session_id, x_session_id)
    with session_manager.operation(session_id):
        info = session_manager.get_session_info(session_id)
        if info is None:  # pragma: no cover - protected by the request lease
            raise HTTPException(status_code=404, detail="Session 不存在")
        return {
            "session_id": info["session_id"],
            "created_at": info["created_at"],
            "files_count": info["files_count"],
            "jobs_count": info["jobs_count"],
        }


@router.post("/session/cleanup")
@limiter.limit(RATE_LIMIT_GENERAL)
async def cleanup_session(request: Request, x_session_id: str | None = Header(None)):
    """
    清理指定 session 的所有资源。
    支持 JSON body 和 sendBeacon 的 text/plain 格式。
    """
    content_type = request.headers.get("content-type", "")
    session_id = None

    try:
        if "application/json" in content_type:
            data = await request.json()
            session_id = data.get("session_id")
        else:
            body = await request.body()
            if body:
                try:
                    data = json.loads(body.decode("utf-8"))
                    session_id = data.get("session_id")
                except json.JSONDecodeError:
                    session_id = body.decode("utf-8").strip()
    except Exception:
        pass

    if not session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id")
    _require_session_owner(session_id, x_session_id)

    result = await run_in_threadpool(session_manager.cleanup_session, session_id)
    deferred_jobs = int(result.get("deferred_jobs", 0))
    cleanup_pending = bool(result.get("cleanup_pending", False))
    if deferred_jobs:
        status = "draining"
    elif cleanup_pending:
        status = "retrying"
    else:
        status = "success"
    return {
        "status": status,
        "cleaned_files": result["cleaned_files"],
        "cleaned_jobs": result["cleaned_jobs"],
        "deferred_jobs": deferred_jobs,
        "cleanup_pending": cleanup_pending,
        "errors": result["errors"],
    }


@router.get("/session/status")
@limiter.limit(RATE_LIMIT_READ)
def get_session_status(
    request: Request,
    detail: bool = Query(False),
    x_session_id: str = Depends(existing_session_operation, scope="request"),
):
    """获取 session 状态信息。设置 detail=true 可查看跟踪的文件列表"""
    info = session_manager.get_session_info(x_session_id, include_files=detail)
    if not info:
        raise HTTPException(status_code=404, detail="Session 不存在")
    return info


@router.get("/session-stats")
def get_session_stats():
    """获取 Session 管理器统计信息（管理员接口）"""
    return session_manager.get_stats()


@router.post("/session/schedule-cleanup")
@limiter.limit(RATE_LIMIT_GENERAL)
async def schedule_session_cleanup(request: Request, x_session_id: str | None = Header(None)):
    """
    安排延迟清理 session。用于页面关闭时。
    服务器会等待几秒再执行清理，给刷新操作留出取消窗口。
    """
    content_type = request.headers.get("content-type", "")
    session_id = None

    try:
        if "application/json" in content_type:
            data = await request.json()
            session_id = data.get("session_id")
        else:
            body = await request.body()
            if body:
                try:
                    data = json.loads(body.decode("utf-8"))
                    session_id = data.get("session_id")
                except json.JSONDecodeError:
                    session_id = body.decode("utf-8").strip()
    except Exception:
        pass

    if not session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id")
    _require_session_owner(session_id, x_session_id)

    scheduled = session_manager.schedule_cleanup(session_id)
    return {"status": "scheduled" if scheduled else "not_found", "session_id": session_id}


@router.post("/session/cancel-cleanup")
@limiter.limit(RATE_LIMIT_GENERAL)
async def cancel_session_cleanup(
    request: Request,
    session_id: str = Body(..., embed=True),
    x_session_id: str | None = Header(None),
):
    """
    取消待执行的延迟清理。用于页面刷新时。
    """
    _require_session_owner(session_id, x_session_id)
    cancelled = session_manager.cancel_cleanup(session_id)
    return {"status": "cancelled" if cancelled else "not_pending", "session_id": session_id}
