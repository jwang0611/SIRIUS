"""Unit tests for ``src.config.settings``."""

from __future__ import annotations

import os

import pytest

from src.config.settings import (
    CascadeSettings,
    Settings,
    get_settings,
    reload_settings,
)


def test_defaults_when_env_clean(clean_env):
    s = Settings.from_env()
    assert s.ai.default_provider == "openrouter"
    assert s.ai.default_model == "google/gemini-3-flash-preview"
    assert s.ai.max_retries == 2
    assert s.ai.timeout_seconds == pytest.approx(120)
    assert s.cascade.enabled is True
    assert s.cascade.kb_min_confidence == pytest.approx(0.8)
    assert s.cascade.kb_high_conf == pytest.approx(0.85)
    assert s.cascade.rag_high_conf == pytest.approx(0.7)
    assert s.rag.top_k_retrieval == 3
    assert s.runtime.enable_parallel is True
    assert s.runtime.max_workers == 5
    assert s.runtime.log_level == "INFO"
    assert s.runtime.log_ai is False
    assert s.runtime.save_ai_interactions is False
    assert s.security.audit_log_enabled is True
    assert s.security.data_masking_enabled is True
    assert s.security.cors_origins == ["http://127.0.0.1:8000", "http://localhost:8000"]


def test_env_vars_override_defaults(clean_env, monkeypatch):
    monkeypatch.setenv("DEFAULT_MODEL", "anthropic/claude-3-opus")
    monkeypatch.setenv("CASCADE_KB_HIGH_CONF", "0.95")
    monkeypatch.setenv("KB_MIN_CONFIDENCE", "0.8")
    monkeypatch.setenv("SDTM_MAX_WORKERS", "12")
    monkeypatch.setenv("AUDIT_LOG_ENABLED", "false")
    monkeypatch.setenv("DATA_MASKING_ENABLED", "0")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("LLM_MAX_RETRIES", "4")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("SDTM_LOG_AI", "true")
    monkeypatch.setenv("SAVE_AI_INTERACTIONS", "true")
    monkeypatch.setenv("SIRIUS_CORS_ORIGINS", "https://ui.example, https://review.example")

    s = Settings.from_env()
    assert s.ai.default_model == "anthropic/claude-3-opus"
    assert s.cascade.kb_high_conf == pytest.approx(0.95)
    assert s.runtime.max_workers == 12
    assert s.security.audit_log_enabled is False
    assert s.security.data_masking_enabled is False
    assert s.runtime.log_level == "DEBUG"
    assert s.ai.max_retries == 4
    assert s.ai.timeout_seconds == pytest.approx(90)
    assert s.runtime.log_ai is True
    assert s.runtime.save_ai_interactions is True
    assert s.security.cors_origins == ["https://ui.example", "https://review.example"]


def test_default_model_keeps_legacy_openrouter_model_alias(clean_env, monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "legacy/model")
    assert Settings.from_env().ai.default_model == "legacy/model"

    monkeypatch.setenv("DEFAULT_MODEL", "preferred/model")
    assert Settings.from_env().ai.default_model == "preferred/model"


def test_exported_alias_outranks_dotenv_canonical_value(clean_env, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DEFAULT_MODEL=dotenv/model\n", encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_MODEL", "exported/model")

    assert Settings.from_env().ai.default_model == "exported/model"


def test_dotenv_remains_visible_to_legacy_os_getenv_callers(clean_env, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "OPENROUTER_BASE_URL=https://llm.internal.example/v1\nSIRIUS_LLM_ALLOWED_HOSTS=gateway.internal.example\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("SIRIUS_LLM_ALLOWED_HOSTS", raising=False)

    settings = Settings.from_env()

    assert settings.ai.openrouter_base_url == "https://llm.internal.example/v1"
    assert os.getenv("OPENROUTER_BASE_URL") == "https://llm.internal.example/v1"
    assert os.getenv("SIRIUS_LLM_ALLOWED_HOSTS") == "gateway.internal.example"


def test_exported_legacy_alias_outranks_dotenv_default(clean_env, monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("DEFAULT_MODEL=dotenv/model\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_MODEL", "environment/model")

    assert Settings.from_env().ai.default_model == "environment/model"


def test_cascade_rejects_incoherent_thresholds(clean_env, monkeypatch):
    monkeypatch.setenv("KB_MIN_CONFIDENCE", "0.9")
    monkeypatch.setenv("CASCADE_KB_HIGH_CONF", "0.5")
    with pytest.raises(Exception):
        Settings.from_env()


def test_reload_settings_clears_cache(clean_env, monkeypatch):
    a = get_settings()
    monkeypatch.setenv("DEFAULT_MODEL", "fake/test-model")
    b = reload_settings()
    assert a is not b
    assert b.ai.default_model == "fake/test-model"


def test_dump_summary_redacts_api_key(clean_env, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "super-secret")
    s = Settings.from_env()
    dumped = s.dump_summary()
    assert dumped["ai"]["openrouter_api_key"] == "<set>"


def test_cascade_settings_direct_construction():
    c = CascadeSettings()
    assert 0.0 <= c.kb_min_confidence <= 1.0
    assert 0.0 <= c.kb_high_conf <= 1.0
    assert 0.0 <= c.rag_high_conf <= 1.0


def test_rejects_out_of_range_confidence():
    with pytest.raises(Exception):
        CascadeSettings(kb_min_confidence=1.5)
