"""Centralised logging configuration.

Replaces the ``print()``-heavy pattern scattered across ``processors``,
``knowledge_base``, ``spec_mapper``, ``rag``, and ``web``. Call
``configure_logging(settings)`` exactly once at process start (the
FastAPI app and any CLI entry point should both do it).
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from src.config.settings import Settings, get_settings

_CONFIGURED = False


_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def configure_logging(
    settings: Settings | None = None,
    *,
    log_dir: Path | None = None,
    force: bool = False,
) -> logging.Logger:
    """Configure the root logger based on ``Settings``.

    Idempotent — safe to call from ``app.py``, CLI scripts, and tests.
    """
    global _CONFIGURED
    if _CONFIGURED and not force:
        return logging.getLogger("sirius")

    cfg = settings or get_settings()
    level = getattr(logging, cfg.runtime.log_level, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_DEFAULT_FORMAT)

    stream = logging.StreamHandler(stream=sys.stdout)
    stream.setFormatter(formatter)
    stream.setLevel(level)
    root.addHandler(stream)

    target_dir = log_dir or (cfg.paths.output / "logs")
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            target_dir / "sirius.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root.addHandler(file_handler)
    except OSError:
        # Read-only filesystem (e.g., some CI sandboxes) – console only.
        pass

    # Quiet chatty libraries at INFO
    for noisy in ("httpx", "httpcore", "urllib3", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
    logger = logging.getLogger("sirius")
    logger.debug("Logging configured (level=%s)", cfg.runtime.log_level)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Thin wrapper so callers do not have to import ``logging`` directly."""
    return logging.getLogger(name)


__all__ = ["configure_logging", "get_logger"]
