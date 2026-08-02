"""Session ownership boundary for job status, artifacts, and cancellation."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from src.web.job_manager import job_manager
from src.web.session_manager import session_manager


def _client() -> TestClient:
    from app import app

    return TestClient(app)


def test_session_owned_job_rejects_other_and_missing_session(tmp_path: Path) -> None:
    job_id = uuid.uuid4().hex
    owner = "session-owner"
    other = "session-other"
    excel = tmp_path / "result.xlsx"
    result_json = tmp_path / "result.json"
    log = tmp_path / "result.log"
    issues = tmp_path / "result.issues.json"
    excel.write_bytes(b"xlsx")
    result_json.write_text("[]", encoding="utf-8")
    log.write_text("safe log", encoding="utf-8")
    issues.write_text(json.dumps([{"code": "warning"}]), encoding="utf-8")

    session_manager.get_or_create(owner)
    job_manager.create_job(job_id, owner_session_id=owner)
    job_manager.update_job(
        job_id,
        state="completed_with_errors",
        output_excel=str(excel),
        output_json=str(result_json),
        output_log=str(log),
        output_issues=str(issues),
    )

    client = _client()
    owner_headers = {"X-Session-ID": owner}
    forbidden_headers = {"X-Session-ID": other}
    reads = [
        f"/api/jobs/{job_id}",
        f"/api/jobs/{job_id}/download?format=excel",
        f"/api/jobs/{job_id}/download?format=json",
        f"/api/jobs/{job_id}/download-log",
        f"/api/jobs/{job_id}/download-issues",
    ]

    try:
        status = client.get(reads[0], headers=owner_headers)
        assert status.status_code == 200
        assert "owner_session_id" not in status.json()
        assert status.json()["output_excel"] == "result.xlsx"
        assert str(tmp_path) not in status.text
        for url in reads[1:]:
            assert client.get(url, headers=owner_headers).status_code == 200

        for url in reads:
            assert client.get(url, headers=forbidden_headers).status_code == 404
            assert client.get(url).status_code == 422

        cancel_url = f"/api/jobs/{job_id}/cancel"
        assert client.post(cancel_url, headers=forbidden_headers).status_code == 404
        assert client.post(cancel_url).status_code == 422
        assert client.post(cancel_url, headers=owner_headers).status_code == 200
        # Late cancellation is idempotent and cannot rewrite a published
        # terminal outcome.
        assert job_manager.get_job(job_id).state == "completed_with_errors"
    finally:
        job_manager.remove_job(job_id)
        session_manager.cleanup_session(owner)


def test_anonymous_internal_job_is_not_exposed_by_session_api(tmp_path: Path) -> None:
    job_id = uuid.uuid4().hex
    output = tmp_path / "legacy.xlsx"
    output.write_bytes(b"xlsx")
    job_manager.create_job(job_id)
    job_manager.update_job(job_id, state="completed", output_excel=str(output))

    client = _client()
    try:
        assert client.get(f"/api/jobs/{job_id}").status_code == 422
        assert client.get(f"/api/jobs/{job_id}/download?format=excel").status_code == 422
        assert (
            client.get(
                f"/api/jobs/{job_id}",
                headers={"X-Session-ID": "unexpected-session"},
            ).status_code
            == 404
        )
    finally:
        job_manager.remove_job(job_id)
