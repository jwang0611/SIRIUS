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


def test_custom_endpoint_without_token_does_not_leak_env_key(task_mocks, mappings_file, monkeypatch):
    """P0 回归：非默认 endpoint + 无请求 token → 失败，绝不用服务器 env 密钥构造客户端。"""
    from src.web.tasks import _run_recommendations_job

    mock_client_cls, _, mock_jm = task_mocks
    monkeypatch.setenv("OPENROUTER_API_KEY", "server-secret-key")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)  # 默认 host = openrouter.ai
    mock_jm.get_job.return_value = MagicMock(state="running")

    _run_recommendations_job(
        job_id="jobp0",
        json_file=mappings_file,
        base_url_override="https://api.deepseek.com/v1",  # 非默认 host
        api_key_override=None,  # 省略 token
    )

    # 客户端未被构造 → 服务器密钥从未发往攻击者 endpoint
    mock_client_cls.assert_not_called()
    failed_calls = [c for c in mock_jm.update_job.call_args_list if c.kwargs.get("state") == "failed"]
    assert failed_calls
    message = failed_calls[-1].kwargs.get("message", "")
    assert "API Token" in message
    assert "server-secret-key" not in message


def test_same_host_http_downgrade_does_not_leak_env_key(task_mocks, mappings_file, monkeypatch):
    """P1 回归：同 host 的 http 降级 + 无 token → 失败，服务器密钥绝不经明文 HTTP 外送。"""
    from src.web.tasks import _run_recommendations_job

    mock_client_cls, _, mock_jm = task_mocks
    monkeypatch.setenv("OPENROUTER_API_KEY", "server-secret-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")  # 默认走 https
    mock_jm.get_job.return_value = MagicMock(state="running")

    _run_recommendations_job(
        job_id="jobdowngrade",
        json_file=mappings_file,
        base_url_override="http://openrouter.ai/api/v1",  # 同 host、明文降级
        api_key_override=None,
    )

    mock_client_cls.assert_not_called()
    failed_calls = [c for c in mock_jm.update_job.call_args_list if c.kwargs.get("state") == "failed"]
    assert failed_calls
    assert "server-secret-key" not in failed_calls[-1].kwargs.get("message", "")


def test_override_equals_server_default_uses_env_key(task_mocks, mappings_file, monkeypatch):
    """base_url 覆盖恰为服务器默认 host 时，空 token 仍可使用 env 密钥（合理保留）。"""
    from src.web.tasks import _run_recommendations_job

    mock_client_cls, _, _ = task_mocks
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    _run_recommendations_job(
        job_id="jobdef",
        json_file=mappings_file,
        base_url_override="https://openrouter.ai/api/v1",
        api_key_override=None,
    )

    mock_client_cls.assert_called_once_with(api_key="env-key", base_url="https://openrouter.ai/api/v1")


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


def test_fallback_and_critic_errors_complete_with_visible_error_state(task_mocks, mappings_file, monkeypatch):
    """PENDING placeholders must not make a task look fully successful."""
    from src.web.tasks import _run_recommendations_job

    _, mock_processor_cls, mock_jm = task_mocks
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    mock_processor_cls.return_value.process_mappings.return_value = [
        {
            "table_name": "AE",
            "domain_recommendations": [
                {
                    "variable_name": "BROKEN",
                    "domain": "AE",
                    "sdtm_variable": "AE_BROKEN_PENDING",
                    "source": "FALLBACK",
                }
            ],
            "consistency_issues": [
                {"severity": "error", "check": "variable_name_validity", "description": "invalid"}
            ],
        }
    ]
    mock_jm.get_job.return_value = MagicMock(state="running", total=1)

    _run_recommendations_job(
        job_id="job-review",
        json_file=mappings_file,
        model_name_override="test/model",
    )

    completed = [
        call for call in mock_jm.update_job.call_args_list if call.kwargs.get("state") == "completed_with_errors"
    ]
    assert completed
    assert completed[-1].kwargs["failed_variables"] == 1
    assert completed[-1].kwargs["consistency_errors"] == 1
    assert "人工复核" in completed[-1].kwargs["message"]
