"""aCRF CLI: ACRF_USE_LLM env is honoured and threaded into the config."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_CLI_PATH = Path(__file__).resolve().parents[2] / "scripts" / "extract_acrf_pdf.py"
_spec = importlib.util.spec_from_file_location("extract_acrf_pdf_cli", _CLI_PATH)
assert _spec and _spec.loader
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


def _settings(use_llm: bool = False, mdv: str = "label") -> SimpleNamespace:
    return SimpleNamespace(
        acrf=SimpleNamespace(
            use_llm=use_llm,
            llm_min_fields=1,
            min_fields=1,
            max_fields=300,
            header_footer_band=0.08,
            metadata_variable_mode=mdv,
        )
    )


def test_effective_use_llm_honours_env_without_cli_flag():
    args = SimpleNamespace(use_llm=False)
    assert cli._effective_use_llm(args, _settings(use_llm=True)) is True
    assert cli._effective_use_llm(args, _settings(use_llm=False)) is False


def test_effective_use_llm_honours_cli_flag_without_env():
    assert cli._effective_use_llm(SimpleNamespace(use_llm=True), _settings(use_llm=False)) is True


def test_resolve_config_threads_effective_flag_and_settings():
    cfg = cli._resolve_config(SimpleNamespace(mdv_mode=None), _settings(use_llm=True), use_llm=True)
    assert cfg.use_llm is True
    assert cfg.llm_min_fields == 1
    assert cfg.metadata_variable_mode == "label"


def test_resolve_config_cli_mdv_mode_overrides_settings():
    cfg = cli._resolve_config(SimpleNamespace(mdv_mode="synthetic"), _settings(mdv="label"), use_llm=False)
    assert cfg.metadata_variable_mode == "synthetic"


def test_acrf_env_hydrates_into_typed_settings(monkeypatch: pytest.MonkeyPatch):
    from src.config.settings import Settings

    monkeypatch.setenv("ACRF_USE_LLM", "true")
    monkeypatch.setenv("ACRF_LLM_MIN_FIELDS", "5")
    settings = Settings.from_env()

    assert settings.acrf.use_llm is True
    assert settings.acrf.llm_min_fields == 5
