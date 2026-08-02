"""Shared FastAPI dependencies for session lifecycle safety."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Header, HTTPException, Request

from src.web.session_manager import (
    SessionCapacityError,
    SessionClosingError,
    SessionRetiredError,
    is_valid_session_id,
    session_manager,
)


def _session_closing_conflict(exc: SessionClosingError) -> HTTPException:
    if isinstance(exc, SessionRetiredError):
        return HTTPException(status_code=410, detail={"code": "session_retired", "message": "Session 已结束"})
    return HTTPException(status_code=409, detail="Session 正在安全清理，请稍后重试")


def _session_capacity_error(exc: SessionCapacityError) -> HTTPException:
    return HTTPException(status_code=503, detail="Session 容量已满，请稍后重试")


def _validated_session_id(session_id: str) -> str:
    if not is_valid_session_id(session_id):
        raise HTTPException(status_code=422, detail="X-Session-ID 格式无效")
    return session_id


def session_operation(request: Request, x_session_id: str = Header(...)) -> Iterator[str]:
    """Lease a session for the complete lifetime of one API request."""
    x_session_id = _validated_session_id(x_session_id)
    if getattr(request.state, "session_operation_id", None) == x_session_id:
        yield x_session_id
        return
    try:
        with session_manager.operation(x_session_id):
            yield x_session_id
    except SessionClosingError as exc:
        raise _session_closing_conflict(exc) from exc
    except SessionCapacityError as exc:
        raise _session_capacity_error(exc) from exc


def existing_session_operation(request: Request, x_session_id: str = Header(...)) -> Iterator[str]:
    """Lease an existing session without creating one for an unknown caller."""
    x_session_id = _validated_session_id(x_session_id)
    if getattr(request.state, "session_operation_id", None) == x_session_id:
        yield x_session_id
        return
    try:
        with session_manager.operation(x_session_id, create=False):
            yield x_session_id
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc
    except SessionClosingError as exc:
        raise _session_closing_conflict(exc) from exc


def session_writer_operation(request: Request, x_session_id: str = Header(...)) -> Iterator[str]:
    """Lease and serialize one session-scoped write workflow."""
    x_session_id = _validated_session_id(x_session_id)
    try:
        if getattr(request.state, "session_operation_id", None) == x_session_id:
            with session_manager.writer_operation(x_session_id):
                yield x_session_id
            return
        with session_manager.operation(x_session_id), session_manager.writer_operation(x_session_id):
            yield x_session_id
    except SessionClosingError as exc:
        raise _session_closing_conflict(exc) from exc
    except SessionCapacityError as exc:
        raise _session_capacity_error(exc) from exc
