"""ASGI-level request/response lease regressions."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from src.web.session_manager import SessionManager
from src.web.session_middleware import SessionOperationMiddleware, _session_lease_mode


def _scope(session_id: str, path: str) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST" if path.startswith("/api/upload/") else "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"x-session-id", session_id.encode())],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }


@pytest.mark.parametrize(
    ("path", "method", "expected"),
    [
        ("/api/upload/raw", "POST", "create"),
        ("/api/processed-files", "GET", "create"),
        ("/api/als-files", "DELETE", "create"),
        ("/api/recommendations", "POST", "create"),
        ("/api/spec-mapper/run", "POST", "create"),
        ("/api/convert-als2sdtm", "POST", "create"),
        ("/api/list-sheets", "POST", "create"),
        ("/api/corrections", "POST", "create"),
        ("/api/jobs/job/download", "GET", "existing"),
        ("/api/jobs/job/download-log", "GET", "existing"),
        ("/api/jobs/job/download-issues", "GET", "existing"),
        ("/api/session/status", "GET", "existing"),
    ],
)
def test_all_session_artifact_routes_are_classified_for_asgi_lease(
    path: str,
    method: str,
    expected: str,
) -> None:
    assert _session_lease_mode(path, method) == expected


def test_lease_begins_before_slow_multipart_body_is_consumed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    manager = SessionManager()
    monkeypatch.setattr("src.web.session_middleware.session_manager", manager)
    session_id = "slow-upload"
    first_chunk_read = threading.Event()
    release_last_chunk = threading.Event()
    request_finished = threading.Event()
    cleanup_result: dict = {}
    written_paths: list[Path] = []
    receive_calls = 0

    async def receive() -> dict:
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
            first_chunk_read.set()
            return {"type": "http.request", "body": b"partial", "more_body": True}
        await asyncio.to_thread(release_last_chunk.wait, 2)
        return {"type": "http.request", "body": b"tail", "more_body": False}

    async def send(_message: dict) -> None:
        return None

    async def app(scope, receive_body, send_response) -> None:
        while True:
            if not (await receive_body()).get("more_body"):
                break
        target = manager.get_session_raw_dir(session_id) / "upload.xlsx"
        target.write_bytes(b"complete")
        assert manager.add_file(session_id, str(target))
        written_paths.append(target)
        await send_response({"type": "http.response.start", "status": 200, "headers": []})
        await send_response({"type": "http.response.body", "body": b"ok"})

    middleware = SessionOperationMiddleware(app)

    def run_request() -> None:
        asyncio.run(middleware(_scope(session_id, "/api/upload/raw"), receive, send))
        request_finished.set()

    request_thread = threading.Thread(target=run_request)
    request_thread.start()
    assert first_chunk_read.wait(timeout=1)

    cleanup_thread = threading.Thread(
        target=lambda: cleanup_result.update(manager.cleanup_session(session_id)),
    )
    cleanup_thread.start()
    time.sleep(0.05)
    assert cleanup_thread.is_alive(), "cleanup must wait before the final request-body chunk"

    release_last_chunk.set()
    request_thread.join(timeout=2)
    cleanup_thread.join(timeout=2)
    assert request_finished.is_set()
    assert cleanup_result["cleanup_pending"] is False
    assert written_paths and not written_paths[0].exists()
    assert not written_paths[0].parent.exists()


def test_lease_covers_file_response_until_final_body_chunk(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    manager = SessionManager()
    monkeypatch.setattr("src.web.session_middleware.session_manager", manager)
    session_id = "slow-download"
    job_id = "job"
    manager.get_or_create(session_id)
    artifact = manager.get_session_spec_job_dir(session_id, job_id) / "result.xlsx"
    artifact.write_bytes(b"artifact")
    manager.add_file(session_id, str(artifact))

    first_response_chunk = threading.Event()
    release_final_chunk = threading.Event()

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: dict) -> None:
        return None

    async def app(_scope, _receive, send_response) -> None:
        await send_response({"type": "http.response.start", "status": 200, "headers": []})
        await send_response({"type": "http.response.body", "body": b"part", "more_body": True})
        first_response_chunk.set()
        await asyncio.to_thread(release_final_chunk.wait, 2)
        await send_response({"type": "http.response.body", "body": b"", "more_body": False})

    middleware = SessionOperationMiddleware(app)
    request_thread = threading.Thread(
        target=lambda: asyncio.run(middleware(_scope(session_id, f"/api/jobs/{job_id}/download"), receive, send))
    )
    request_thread.start()
    assert first_response_chunk.wait(timeout=1)

    cleanup_thread = threading.Thread(target=manager.cleanup_session, args=(session_id,))
    cleanup_thread.start()
    time.sleep(0.05)
    assert cleanup_thread.is_alive(), "cleanup must wait until the streamed response is complete"
    assert artifact.exists()

    release_final_chunk.set()
    request_thread.join(timeout=2)
    cleanup_thread.join(timeout=2)
    assert not request_thread.is_alive()
    assert not cleanup_thread.is_alive()
    assert not artifact.exists()


def test_downstream_keyerror_is_not_rewritten_as_session_404(monkeypatch) -> None:
    manager = SessionManager()
    monkeypatch.setattr("src.web.session_middleware.session_manager", manager)

    async def app(_scope, _receive, _send) -> None:
        raise KeyError("route implementation bug")

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: dict) -> None:
        return None

    middleware = SessionOperationMiddleware(app)
    with pytest.raises(KeyError, match="route implementation bug"):
        asyncio.run(middleware(_scope("route-bug", "/api/upload/raw"), receive, send))
