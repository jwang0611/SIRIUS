"""Unit tests for LLM provider overrides in the recommendations background task."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mappings_file(tmp_path, monkeypatch):
    """Create data/processed/fixture.json under a temp cwd."""
    processed_dir = tmp_path / "data" / "processed"
    processed_dir.mkdir(parents=True)
    mappings = [{"metadata_table": "AE", "annotation_variable": "AETERM"}]
    (processed_dir / "fixture.json").write_text(json.dumps(mappings), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return "fixture.json"


@pytest.fixture
def task_mocks():
    with (
        patch("src.web.tasks.load_dotenv"),
        patch("src.web.tasks.OpenRouterClient") as mock_client_cls,
        patch("src.web.tasks.SDTMProcessor") as mock_processor_cls,
        patch("src.web.tasks.job_manager") as mock_jm,
    ):
        mock_client = MagicMock()
        mock_client.initialize.return_value = True
        mock_client.get_sanitized_model_name.return_value = "test_model"
        mock_client_cls.return_value = mock_client
        mock_jm.is_cancelled.return_value = False
        yield mock_client_cls, mock_processor_cls, mock_jm


def test_overrides_take_precedence_over_env(task_mocks, mappings_file, monkeypatch):
    """请求级 base_url / api_key 覆盖优先于环境变量。"""
    from src.web.tasks import _run_recommendations_job

    mock_client_cls, _, mock_jm = task_mocks
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://env.example.com/v1")

    _run_recommendations_job(
        job_id="job1",
        json_file=mappings_file,
        model_name_override="deepseek-chat",
        base_url_override="https://api.deepseek.com/v1",
        api_key_override="user-key",
    )

    mock_client_cls.assert_called_once_with(api_key="user-key", base_url="https://api.deepseek.com/v1")
    mock_client_cls.return_value.set_model.assert_called_once_with("deepseek-chat")
    # 任务应正常完成而非失败
    states = [c.kwargs.get("state") for c in mock_jm.update_job.call_args_list if "state" in c.kwargs]
    assert "failed" not in states
    assert "completed" in states


def test_env_fallback_when_no_overrides(task_mocks, mappings_file, monkeypatch):
    """未提供覆盖值时回退到环境变量（原有行为不变）。"""
    from src.web.tasks import _run_recommendations_job

    mock_client_cls, _, _ = task_mocks
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://env.example.com/v1")

    _run_recommendations_job(job_id="job2", json_file=mappings_file)

    mock_client_cls.assert_called_once_with(api_key="env-key", base_url="https://env.example.com/v1")


def test_missing_api_key_fails_without_leaking_secrets(task_mocks, mappings_file, monkeypatch):
    """无覆盖值且无环境变量时任务失败，且失败消息不含密钥。"""
    from src.web.tasks import _run_recommendations_job

    mock_client_cls, _, mock_jm = task_mocks
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://env.example.com/v1")
    mock_jm.get_job.return_value = MagicMock(state="running")

    _run_recommendations_job(job_id="job3", json_file=mappings_file)

    mock_client_cls.assert_not_called()
    failed_calls = [c for c in mock_jm.update_job.call_args_list if c.kwargs.get("state") == "failed"]
    assert failed_calls
    message = failed_calls[-1].kwargs.get("message", "")
    assert "API Key" in message or "OPENROUTER_API_KEY" in message
    assert "env-key" not in message and "user-key" not in message
