"""Unit tests for the recommendations job router (LLM provider overrides)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Deferred import to avoid side effects at import time."""
    from app import app

    return TestClient(app)


@pytest.fixture
def processed_json(tmp_path, monkeypatch):
    """Create data/processed/fixture.json under a temp cwd."""
    processed_dir = tmp_path / "data" / "processed"
    processed_dir.mkdir(parents=True)
    (processed_dir / "fixture.json").write_text("[]", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
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
        mock_start, _ = patch_job_start
        response = client.post("/api/recommendations", json={"json_file": processed_json})
        assert response.status_code == 200
        assert response.json()["job_id"]
        mock_start.assert_called_once()
        assert mock_start.call_args.kwargs["base_url_override"] is None
        assert mock_start.call_args.kwargs["api_key_override"] is None
        assert mock_start.call_args.kwargs["model_name_override"] is None

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

    output = tmp_path / "review-required.xlsx"
    output.write_bytes(b"xlsx")
    job = JobStatus(
        job_id="job-review",
        state="completed_with_errors",
        output_excel=str(output),
        failed_variables=2,
        consistency_errors=1,
    )

    from app import app

    with patch("src.web.routers.jobs.job_manager") as manager:
        manager.get_job.return_value = job
        response = TestClient(app).get("/api/jobs/job-review/download?format=excel")

    assert response.status_code == 200
    assert response.content == b"xlsx"
