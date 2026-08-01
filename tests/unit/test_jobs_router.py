"""Unit tests for the recommendations job router (LLM provider overrides)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.web.session_manager import session_manager

TEST_SESSION_ID = "jobs-router-session"


@pytest.fixture
def client():
    """Deferred import to avoid side effects at import time."""
    from app import app

    return TestClient(app, headers={"X-Session-ID": TEST_SESSION_ID})


@pytest.fixture
def processed_json(tmp_path, monkeypatch):
    """Create data/processed/fixture.json under a temp cwd."""
    monkeypatch.chdir(tmp_path)
    session_manager.get_or_create(TEST_SESSION_ID)
    processed_dir = session_manager.get_session_processed_dir(TEST_SESSION_ID)
    (processed_dir / "fixture.json").write_text("[]", encoding="utf-8")
    session_manager.add_file(TEST_SESSION_ID, str(processed_dir / "fixture.json"))
    return "fixture.json"


class TestRecommendationLLMOverrides:
    @pytest.fixture(autouse=True)
    def clean_llm_env(self, monkeypatch):
        monkeypatch.delenv("SIRIUS_LLM_ALLOWED_HOSTS", raising=False)
        monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)

    @pytest.fixture(autouse=True)
    def patch_job_start(self):
        with (
            patch("src.web.routers.jobs.start_recommendations_job") as mock_start,
            patch("src.web.routers.jobs.job_manager") as mock_jm,
        ):
            mock_jm.create_job.return_value = None
            yield mock_start, mock_jm

    def test_without_overrides_passes_none(self, client: TestClient, patch_job_start: Any, processed_json: str):
        """未提供 base_url / api_token 时保持 None（向后兼容，服务端回退环境变量）。"""
        mock_start, mock_jm = patch_job_start
        response = client.post("/api/recommendations", json={"json_file": processed_json})
        assert response.status_code == 200
        assert response.json()["job_id"]
        mock_start.assert_called_once()
        assert mock_start.call_args.kwargs["base_url_override"] is None
        assert mock_start.call_args.kwargs["api_key_override"] is None
        assert mock_start.call_args.kwargs["model_name_override"] is None
        assert mock_start.call_args.kwargs["session_id"] == TEST_SESSION_ID
        snapshot = Path(mock_start.call_args.kwargs["json_file"])
        source = session_manager.get_session_processed_dir(TEST_SESSION_ID) / processed_json
        assert snapshot != source
        expected_job_dir = session_manager.get_session_recommendation_job_dir(
            TEST_SESSION_ID,
            response.json()["job_id"],
        )
        assert snapshot.parent == (expected_job_dir / "processed").resolve()
        assert snapshot.read_text(encoding="utf-8") == "[]"
        source.write_text('[{"changed": true}]', encoding="utf-8")
        assert snapshot.read_text(encoding="utf-8") == "[]"
        assert mock_start.call_args.kwargs["kb_files_snapshot"] == []
        assert mock_jm.create_job.call_args.kwargs["owner_session_id"] == TEST_SESSION_ID

    def test_complete_input_bundle_is_snapshotted_with_expected_layout(
        self,
        client: TestClient,
        patch_job_start: Any,
        processed_json: str,
    ):
        mock_start, _ = patch_job_start
        source_json = session_manager.get_session_processed_dir(TEST_SESSION_ID) / processed_json
        processed_excel = source_json.with_suffix(".xlsx")
        raw_excel = session_manager.get_session_raw_dir(TEST_SESSION_ID) / "fixture.xlsx"
        processed_excel.write_bytes(b"processed-workbook-marker")
        raw_excel.write_bytes(b"raw-workbook-marker")

        response = client.post("/api/recommendations", json={"json_file": processed_json})

        assert response.status_code == 200
        job_dir = session_manager.get_session_recommendation_job_dir(
            TEST_SESSION_ID,
            response.json()["job_id"],
        ).resolve()
        json_snapshot = Path(mock_start.call_args.kwargs["json_file"])
        assert json_snapshot == job_dir / "processed" / "fixture.json"
        assert (job_dir / "processed" / "fixture.xlsx").read_bytes() == b"processed-workbook-marker"
        assert (job_dir / "raw" / "fixture.xlsx").read_bytes() == b"raw-workbook-marker"

    def test_overrides_forwarded_verbatim(self, client: TestClient, patch_job_start: Any, processed_json: str):
        mock_start, _ = patch_job_start
        response = client.post(
            "/api/recommendations",
            json={
                "json_file": processed_json,
                "model_name": "deepseek-chat",
                "base_url": "https://api.deepseek.com/v1",
                "api_token": "sk-test-token",
            },
        )
        assert response.status_code == 200
        # token 不得回显在响应中
        assert "sk-test-token" not in response.text
        mock_start.assert_called_once()
        kwargs = mock_start.call_args.kwargs
        assert kwargs["model_name_override"] == "deepseek-chat"
        assert kwargs["base_url_override"] == "https://api.deepseek.com/v1"
        assert kwargs["api_key_override"] == "sk-test-token"

    def test_session_kb_is_copied_to_immutable_job_snapshot(
        self,
        client: TestClient,
        patch_job_start: Any,
        processed_json: str,
    ):
        mock_start, _ = patch_job_start
        kb_source = session_manager.get_session_kb_dir(TEST_SESSION_ID) / "project_fixture.parquet"
        kb_source.write_bytes(b"kb-before-job")
        assert session_manager.add_kb_file(TEST_SESSION_ID, str(kb_source))
        try:
            response = client.post("/api/recommendations", json={"json_file": processed_json})

            assert response.status_code == 200
            snapshots = [Path(path) for path in mock_start.call_args.kwargs["kb_files_snapshot"]]
            assert len(snapshots) == 1
            assert snapshots[0] != kb_source.resolve()
            job_dir = session_manager.get_session_recommendation_job_dir(
                TEST_SESSION_ID,
                response.json()["job_id"],
            ).resolve()
            assert snapshots[0].parent == job_dir / "kb"
            assert snapshots[0].read_bytes() == b"kb-before-job"

            kb_source.write_bytes(b"kb-after-job")
            assert snapshots[0].read_bytes() == b"kb-before-job"
        finally:
            session_manager.discard_file(TEST_SESSION_ID, kb_source)

    def test_partial_snapshot_failure_rolls_back_private_job_inputs(
        self,
        client: TestClient,
        patch_job_start: Any,
        processed_json: str,
    ):
        from src.utils.atomic_file import atomic_snapshot_file as real_snapshot

        mock_start, mock_jm = patch_job_start
        kb_source = session_manager.get_session_kb_dir(TEST_SESSION_ID) / "project_failure.parquet"
        kb_source.write_bytes(b"kb")
        assert session_manager.add_kb_file(TEST_SESSION_ID, str(kb_source))
        calls = 0

        def fail_second_snapshot(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic copy failure")
            return real_snapshot(source, target)

        try:
            with patch(
                "src.web.routers.jobs.atomic_snapshot_file",
                side_effect=fail_second_snapshot,
            ):
                response = client.post("/api/recommendations", json={"json_file": processed_json})
            assert response.status_code == 500
            assert response.json() == {"detail": "任务输入快照失败，请重试"}
            mock_start.assert_not_called()
            mock_jm.create_job.assert_not_called()
            jobs_root = session_manager.get_session_processed_dir(TEST_SESSION_ID) / "jobs"
            assert not list(jobs_root.glob("*"))
        finally:
            session_manager.discard_file(TEST_SESSION_ID, kb_source)

    def test_worker_start_failure_removes_job_and_snapshot(
        self,
        client: TestClient,
        patch_job_start: Any,
        processed_json: str,
    ):
        mock_start, mock_jm = patch_job_start
        mock_start.return_value = False

        response = client.post("/api/recommendations", json={"json_file": processed_json})

        assert response.status_code == 409
        job_id = mock_jm.create_job.call_args.args[0]
        mock_jm.remove_job.assert_called_once_with(job_id)
        job_dir = (
            session_manager.get_session_processed_dir(TEST_SESSION_ID)
            / "jobs"
            / session_manager.session_dir_key(job_id)
        )
        assert not job_dir.exists()

    @pytest.mark.parametrize("bad_url", ["ftp://evil.example.com/v1", "not-a-url"])
    def test_invalid_base_url_scheme_returns_422(
        self, client: TestClient, patch_job_start: Any, processed_json: str, bad_url: str
    ):
        mock_start, _ = patch_job_start
        response = client.post(
            "/api/recommendations",
            json={"json_file": processed_json, "base_url": bad_url, "api_token": "sk-secret"},
        )
        assert response.status_code == 422
        # 422 detail 不得泄露 token
        assert "sk-secret" not in response.text
        mock_start.assert_not_called()

    def test_base_url_with_userinfo_returns_422(self, client: TestClient, patch_job_start: Any, processed_json: str):
        mock_start, _ = patch_job_start
        response = client.post(
            "/api/recommendations",
            json={"json_file": processed_json, "base_url": "https://user:pass@host.example.com/v1"},
        )
        assert response.status_code == 422
        mock_start.assert_not_called()

    def test_blank_overrides_normalized_to_none(self, client: TestClient, patch_job_start: Any, processed_json: str):
        mock_start, _ = patch_job_start
        response = client.post(
            "/api/recommendations",
            json={"json_file": processed_json, "base_url": "  ", "api_token": "  "},
        )
        assert response.status_code == 200
        kwargs = mock_start.call_args.kwargs
        assert kwargs["base_url_override"] is None
        assert kwargs["api_key_override"] is None

    # ---- P1: SSRF — host allowlist ----
    @pytest.mark.parametrize(
        "bad_url",
        [
            "https://evil.example.com/v1",  # 非白名单
            "http://127.0.0.1:11434/v1",  # loopback
            "http://10.0.0.5/v1",  # 私网
            "http://169.254.169.254/latest/meta-data/",  # 云元数据
        ],
    )
    def test_non_allowlisted_host_returns_422(
        self, client: TestClient, patch_job_start: Any, processed_json: str, bad_url: str
    ):
        mock_start, _ = patch_job_start
        response = client.post(
            "/api/recommendations",
            json={"json_file": processed_json, "base_url": bad_url, "api_token": "sk-secret"},
        )
        assert response.status_code == 422
        assert "sk-secret" not in response.text
        mock_start.assert_not_called()

    def test_admin_allowlisted_host_with_token_ok(
        self, client: TestClient, patch_job_start: Any, processed_json: str, monkeypatch
    ):
        monkeypatch.setenv("SIRIUS_LLM_ALLOWED_HOSTS", "llm-gw.internal")
        mock_start, _ = patch_job_start
        response = client.post(
            "/api/recommendations",
            json={
                "json_file": processed_json,
                "base_url": "https://llm-gw.internal/v1",
                "api_token": "sk-gw",
            },
        )
        assert response.status_code == 200
        assert mock_start.call_args.kwargs["base_url_override"] == "https://llm-gw.internal/v1"

    # ---- P0: custom endpoint requires request token ----
    def test_custom_endpoint_without_token_returns_422(
        self, client: TestClient, patch_job_start: Any, processed_json: str
    ):
        mock_start, _ = patch_job_start
        response = client.post(
            "/api/recommendations",
            # deepseek 在白名单内，但非服务器默认 endpoint，缺 token 必须拒绝
            json={"json_file": processed_json, "base_url": "https://api.deepseek.com/v1"},
        )
        assert response.status_code == 422
        mock_start.assert_not_called()

    def test_same_host_http_downgrade_returns_422(self, client: TestClient, patch_job_start: Any, processed_json: str):
        # 同 host 的 http 降级（服务器默认 https）不得被当作默认 endpoint 使用服务器密钥
        mock_start, _ = patch_job_start
        response = client.post(
            "/api/recommendations",
            json={"json_file": processed_json, "base_url": "http://openrouter.ai/api/v1"},
        )
        assert response.status_code == 422
        mock_start.assert_not_called()


def test_completed_with_errors_result_remains_downloadable(tmp_path):
    from src.web.job_manager import JobStatus

    session_id = "download-review-session"
    output = tmp_path / "review-required.xlsx"
    output.write_bytes(b"xlsx")
    job = JobStatus(
        job_id="job-review",
        owner_session_id=session_id,
        state="completed_with_errors",
        output_excel=str(output),
        failed_variables=2,
        consistency_errors=1,
    )

    from app import app

    session_manager.get_or_create(session_id)
    try:
        with patch("src.web.routers.jobs.job_manager") as manager:
            manager.get_job.return_value = job
            manager.is_owned_by.return_value = True
            response = TestClient(app).get(
                "/api/jobs/job-review/download?format=excel",
                headers={"X-Session-ID": session_id},
            )
    finally:
        session_manager.cleanup_session(session_id)

    assert response.status_code == 200
    assert response.content == b"xlsx"
