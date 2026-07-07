"""Session management routes."""

import json

from fastapi import APIRouter, Body, HTTPException, Query, Request

from src.web.session_manager import session_manager

router = APIRouter()


@router.post("/session/init")
async def init_session(session_id: str = Body(..., embed=True)):
    """初始化或恢复 session"""
    session = session_manager.get_or_create(session_id)
    return {
        "session_id": session.session_id,
        "created_at": session.created_at.isoformat(),
        "files_count": len(session.uploaded_files),
        "jobs_count": len(session.job_ids),
    }


@router.post("/session/cleanup")
async def cleanup_session(request: Request):
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

    result = session_manager.cleanup_session(session_id)
    return {
        "status": "success",
        "cleaned_files": result["cleaned_files"],
        "cleaned_jobs": result["cleaned_jobs"],
        "errors": result["errors"],
    }


@router.get("/session/{session_id}")
def get_session_status(session_id: str, detail: bool = Query(False)):
    """获取 session 状态信息。设置 detail=true 可查看跟踪的文件列表"""
    info = session_manager.get_session_info(session_id, include_files=detail)
    if not info:
        raise HTTPException(status_code=404, detail="Session 不存在")
    return info


@router.get("/session-stats")
def get_session_stats():
    """获取 Session 管理器统计信息（管理员接口）"""
    return session_manager.get_stats()


@router.post("/session/schedule-cleanup")
async def schedule_session_cleanup(request: Request):
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

    scheduled = session_manager.schedule_cleanup(session_id)
    return {"status": "scheduled" if scheduled else "not_found", "session_id": session_id}


@router.post("/session/cancel-cleanup")
async def cancel_session_cleanup(session_id: str = Body(..., embed=True)):
    """
    取消待执行的延迟清理。用于页面刷新时。
    """
    cancelled = session_manager.cancel_cleanup(session_id)
    return {"status": "cancelled" if cancelled else "not_pending", "session_id": session_id}
