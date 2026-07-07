"""Unit tests for ``src.infrastructure.logging``."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import src.infrastructure.logging as logging_mod
from src.config.settings import Settings
from src.infrastructure.logging import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _reset_logging():
    """Reset module-level _CONFIGURED flag between tests."""
    logging_mod._CONFIGURED = False
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in original_handlers:
        root.addHandler(h)
    root.setLevel(original_level)
    logging_mod._CONFIGURED = False


def test_configure_logging_attaches_handlers(clean_env, tmp_path):
    settings = Settings.from_env()
    logger = configure_logging(settings, log_dir=tmp_path, force=True)
    assert isinstance(logger, logging.Logger)
    root = logging.getLogger()
    assert len(root.handlers) >= 1


def test_configure_logging_creates_log_file(clean_env, tmp_path):
    settings = Settings.from_env()
    configure_logging(settings, log_dir=tmp_path, force=True)
    assert (tmp_path / "sirius.log").exists()


def test_log_level_applied_from_settings(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    settings = Settings.from_env()
    configure_logging(settings, log_dir=tmp_path, force=True)
    assert logging.getLogger().level == logging.WARNING


def test_idempotent_without_force(clean_env, tmp_path):
    settings = Settings.from_env()
    configure_logging(settings, log_dir=tmp_path, force=True)
    handlers_before = list(logging.getLogger().handlers)
    configure_logging(settings, log_dir=tmp_path)
    handlers_after = list(logging.getLogger().handlers)
    assert len(handlers_before) == len(handlers_after)


def test_force_reconfigures(clean_env, tmp_path):
    settings = Settings.from_env()
    configure_logging(settings, log_dir=tmp_path, force=True)
    first_ids = [id(h) for h in logging.getLogger().handlers]
    configure_logging(settings, log_dir=tmp_path, force=True)
    second_ids = [id(h) for h in logging.getLogger().handlers]
    assert first_ids != second_ids


def test_noisy_loggers_quieted(clean_env, tmp_path):
    settings = Settings.from_env()
    configure_logging(settings, log_dir=tmp_path, force=True)
    for name in ("httpx", "urllib3", "openai"):
        assert logging.getLogger(name).level == logging.WARNING


def test_get_logger_returns_named_logger():
    log = get_logger("sirius.test")
    assert log.name == "sirius.test"


def test_readonly_dir_falls_back_to_console_only(clean_env):
    settings = Settings.from_env()
    logger = configure_logging(settings, log_dir=Path("/__nonexistent_readonly__/_"), force=True)
    assert isinstance(logger, logging.Logger)
    assert len(logging.getLogger().handlers) >= 1
