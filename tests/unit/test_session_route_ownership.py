"""Session lifecycle APIs require the session bearer header to match."""

from __future__ import annotations

import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.web.session_manager import session_manager


def test_session_lifecycle_rejects_cross_session_access() -> None:
    from app import app

    client = TestClient(app)
    session_id = f"session-{uuid.uuid4().hex}"
    owner = {"X-Session-ID": session_id}
    other = {"X-Session-ID": "different-session"}
    body = {"session_id": session_id}

    try:
        assert client.post("/api/session/init", json=body).status_code == 404
        assert client.post("/api/session/init", json=body, headers=other).status_code == 404
        assert client.post("/api/session/init", json=body, headers=owner).status_code == 200

        assert client.get("/api/session/status?detail=true").status_code == 422
        assert client.get("/api/session/status?detail=true", headers=other).status_code == 404
        assert client.get("/api/session/status?detail=true", headers=owner).status_code == 200
        # Session bearer capabilities must never appear in URL paths, where
        # access logs and browser history would retain them.
        assert client.get(f"/api/session/{session_id}", headers=owner).status_code == 404

        assert client.post("/api/session/schedule-cleanup", json=body, headers=other).status_code == 404
        assert client.post("/api/session/cancel-cleanup", json=body, headers=other).status_code == 404
        assert client.post("/api/session/cleanup", json=body, headers=other).status_code == 404

        assert client.post("/api/session/schedule-cleanup", json=body, headers=owner).status_code == 200
        assert client.post("/api/session/cancel-cleanup", json=body, headers=owner).status_code == 200
        assert client.post("/api/session/cleanup", json=body, headers=owner).status_code == 200
    finally:
        session_manager.cancel_cleanup(session_id)
        session_manager.cleanup_session(session_id)


def test_draining_session_returns_retryable_conflict() -> None:
    from app import app

    client = TestClient(app)
    session_id = f"draining-{uuid.uuid4().hex}"
    headers = {"X-Session-ID": session_id}
    session_manager.get_or_create(session_id)
    with session_manager._lock:
        session_manager._draining_sessions.add(session_id)

    try:
        response = client.get("/api/processed-files", headers=headers)
        assert response.status_code == 409
        assert response.json() == {"detail": "Session 正在安全清理，请稍后重试"}
    finally:
        with session_manager._lock:
            session_manager._draining_sessions.discard(session_id)
        session_manager.cleanup_session(session_id)


def test_cleanup_delete_failure_is_reported_as_retrying() -> None:
    from app import app

    client = TestClient(app)
    session_id = f"retrying-{uuid.uuid4().hex}"
    result = {
        "cleaned_files": 0,
        "cleaned_session_dir": False,
        "cleaned_jobs": 0,
        "deferred_jobs": 0,
        "cleanup_pending": True,
        "errors": ["session_output_dirs_delete_failed"],
    }
    with patch.object(session_manager, "cleanup_session", return_value=result):
        response = client.post(
            "/api/session/cleanup",
            json={"session_id": session_id},
            headers={"X-Session-ID": session_id},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "retrying"
    assert response.json()["cleanup_pending"] is True
