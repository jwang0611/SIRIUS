"""Focused regression tests for the first Phase A hardening slice."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from src import __version__
from src.web.security import InvalidWorkbookError, _get_client_identifier, run_command
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


def test_run_command_logs_bounded_redacted_diagnostics(caplog):
    completed = subprocess.CompletedProcess(
        args=["tool"],
        returncode=1,
        stdout="partial output",
        stderr="SSN 123-45-6789 token=server-secret " + "x" * 3000,
    )
    with patch("src.web.security.subprocess.run", return_value=completed):
        with caplog.at_level("WARNING", logger="src.web.security"):
            with pytest.raises(RuntimeError):
                run_command(["tool"])

    assert "partial output" in caplog.text
    assert "[REDACTED]" in caplog.text
    assert "server-secret" not in caplog.text
    assert "123-45-6789" not in caplog.text
    assert "[truncated]" in caplog.text


def test_run_command_classifies_invalid_workbook_for_safe_4xx_mapping():
    completed = subprocess.CompletedProcess(
        args=["tool"],
        returncode=1,
        stdout="",
        stderr="Sheet 'eCRF' missing required columns: ItemName",
    )
    with patch("src.web.security.subprocess.run", return_value=completed):
        with pytest.raises(InvalidWorkbookError) as exc_info:
            run_command(["tool"])

    assert str(exc_info.value) == "工作簿格式不符合要求，请检查必需的工作表和列"
    assert "ItemName" not in str(exc_info.value)


def test_toast_uses_text_content_instead_of_html_interpolation(repo_root: Path):
    source = (repo_root / "src" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    toast_start = source.index("function showToast")
    toast_end = source.index("// ==================== Step 1 Elements", toast_start)
    toast_source = source[toast_start:toast_end]
    assert "toast.innerHTML" not in toast_source
    assert "messageEl.textContent" in toast_source


def test_job_artifact_downloads_use_session_authenticated_fetch(repo_root: Path):
    source = (repo_root / "src" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "async function downloadWithSession" in source
    assert "fetchWithSession(url)" in source
    assert 'data-session-download="api/jobs/${jobId}/download?format=excel"' in source
    assert 'href="api/jobs/${jobId}/download?format=excel"' not in source
    assert 'href="api/jobs/${jobId}/download-issues"' not in source


def test_browser_session_ids_use_cryptographic_randomness_without_logging_capability(repo_root: Path):
    source = (repo_root / "src" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "globalThis.crypto.randomUUID()" in source
    assert "Math.random()" not in source
    assert "Initialized:', SESSION_ID" not in source
    assert "cleanup for:', SESSION_ID" not in source
    assert "suppressUnloadCleanup = true" in source
    assert "restartWithFreshSession();" in source
