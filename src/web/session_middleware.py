"""ASGI session leases that begin before request-body parsing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from contextlib import ExitStack
from typing import Any

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from src.web.session_manager import SessionClosingError, session_manager

_CREATE_PATHS = frozenset(
    {
        "/api/processed-files",
        "/api/als-files",
        "/api/recommendations",
        "/api/spec-mapper/run",
        "/api/convert-als2sdtm",
        "/api/list-sheets",
        "/api/corrections",
    }
)
_EXISTING_DOWNLOAD_SUFFIXES = ("/download", "/download-log", "/download-issues")


def _session_lease_mode(path: str, method: str) -> str | None:
    """Return ``create``/``existing`` for routes that touch session resources."""
    if path.startswith("/api/upload/") or path in _CREATE_PATHS:
        return "create"
    if path.startswith("/api/jobs/") and path.endswith(_EXISTING_DOWNLOAD_SUFFIXES):
        return "existing"
    if method == "GET" and path.startswith("/api/session/"):
        return "existing"
    return None


class SessionOperationMiddleware:
    """Hold a session lease from ASGI entry through the final response chunk.

    FastAPI resolves multipart/form bodies before route dependencies. Acquiring
    the lease here closes that gap and also protects streamed ``FileResponse``
    bodies until their last chunk has been sent.
    """

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        mode = _session_lease_mode(str(scope.get("path", "")), str(scope.get("method", "")))
        session_id = Headers(scope=scope).get("x-session-id")
        if mode is None or not session_id:
            # Let FastAPI preserve its normal 422 contract for a missing header.
            await self.app(scope, receive, send)
            return

        lease_stack = ExitStack()
        try:
            lease_stack.enter_context(session_manager.operation(session_id, create=mode == "create"))
        except KeyError:
            response = JSONResponse({"detail": "Session 不存在"}, status_code=404)
            await response(scope, receive, send)
            return
        except SessionClosingError:
            response = JSONResponse({"detail": "Session 正在安全清理，请稍后重试"}, status_code=409)
            await response(scope, receive, send)
            return

        # Downstream exceptions deliberately sit outside the lifecycle-error
        # handlers above. A route bug must propagate through FastAPI's normal
        # exception stack, never be rewritten as a session 404 after a response
        # may already have started.
        with lease_stack:
            state = scope.setdefault("state", {})
            state["session_operation_id"] = session_id
            await self.app(scope, receive, send)
