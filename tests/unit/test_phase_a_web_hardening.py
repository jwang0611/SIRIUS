"""Focused regression tests for the first Phase A hardening slice."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from src import __version__
from src.web.security import _get_client_identifier, run_command
from src.web.session_manager import SessionManager


def test_health_and_version_endpoints():
    from app import app

    client = TestClient(app)
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/version").json() == {"name": "SIRIUS", "version": __version__}


def test_rate_limit_key_ignores_caller_controlled_session_header():
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-session-id", b"attacker-rotates-this")],
            "client": ("203.0.113.42", 4321),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )
    assert _get_client_identifier(request) == "203.0.113.42"


def test_session_detail_returns_names_not_absolute_paths(tmp_path):
    manager = SessionManager()
    manager.get_or_create("session-a")
    manager.add_file("session-a", str(tmp_path / "private" / "result.xlsx"))
    manager.add_kb_file("session-a", str(tmp_path / "secret" / "project.parquet"))

    info = manager.get_session_info("session-a", include_files=True)
    assert info is not None
    assert info["files"] == ["result.xlsx"]
    assert info["kb_files"] == ["project.parquet"]
    assert str(tmp_path) not in str(info)


def test_run_command_error_does_not_echo_command_or_stderr():
    completed = subprocess.CompletedProcess(
        args=["tool", "--token", "secret"],
        returncode=1,
        stdout="",
        stderr="clinical raw content",
    )
    with patch("src.web.security.subprocess.run", return_value=completed):
        with pytest.raises(RuntimeError) as exc_info:
            run_command(["tool", "--token", "secret"])
    message = str(exc_info.value)
    assert "secret" not in message
    assert "clinical raw content" not in message


def test_toast_uses_text_content_instead_of_html_interpolation(repo_root: Path):
    source = (repo_root / "src" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    toast_start = source.index("function showToast")
    toast_end = source.index("// ==================== Step 1 Elements", toast_start)
    toast_source = source[toast_start:toast_end]
    assert "toast.innerHTML" not in toast_source
    assert "messageEl.textContent" in toast_source
