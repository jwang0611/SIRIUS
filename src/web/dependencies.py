"""Shared FastAPI dependencies for session lifecycle safety."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Header, HTTPException, Request

from src.web.session_manager import SessionClosingError, session_manager


def _session_closing_conflict(exc: SessionClosingError) -> HTTPException:
    return HTTPException(status_code=409, detail="Session 正在安全清理，请稍后重试")


def session_operation(request: Request, x_session_id: str = Header(...)) -> Iterator[str]:
    """Lease a session for the complete lifetime of one API request."""
    if getattr(request.state, "session_operation_id", None) == x_session_id:
        yield x_session_id
        return
    try:
        with session_manager.operation(x_session_id):
            yield x_session_id
    except SessionClosingError as exc:
        raise _session_closing_conflict(exc) from exc


def existing_session_operation(request: Request, x_session_id: str = Header(...)) -> Iterator[str]:
    """Lease an existing session without creating one for an unknown caller."""
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
    try:
        if getattr(request.state, "session_operation_id", None) == x_session_id:
            with session_manager.writer_operation(x_session_id):
                yield x_session_id
            return
        with session_manager.operation(x_session_id), session_manager.writer_operation(x_session_id):
            yield x_session_id
    except SessionClosingError as exc:
        raise _session_closing_conflict(exc) from exc
