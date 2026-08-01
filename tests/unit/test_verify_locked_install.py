"""Tests for the clean lock-install verification helper."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import verify_locked_install


def test_verify_locked_installs_uses_hashed_runtime_export(monkeypatch):
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, environment: dict[str, str], pass_number: int) -> str:
        commands.append(command)
        if command[1:3] == ["pip", "freeze"]:
            return "urllib3==2.7.0\nrequests==2.34.2\n"
        return ""

    monkeypatch.setattr(verify_locked_install, "_run", fake_run)

    result = verify_locked_install.verify_locked_installs(uv="uv", python="3.11")

    sync_commands = [command for command in commands if command[1:3] == ["pip", "sync"]]
    assert len(sync_commands) == 2
    assert all("--require-hashes" in command for command in sync_commands)
    assert all(command[-1] == str(verify_locked_install.PROJECT_ROOT / "requirements.txt") for command in sync_commands)
    assert result["package_count"] == 2
    assert result["python"] == "3.11"


def test_verify_locked_installs_rejects_package_set_drift(monkeypatch):
    freeze_outputs = iter(["requests==2.34.2\n", "requests==2.34.1\n"])

    def fake_run(command: list[str], *, environment: dict[str, str], pass_number: int) -> str:
        if command[1:3] == ["pip", "freeze"]:
            return next(freeze_outputs)
        return ""

    monkeypatch.setattr(verify_locked_install, "_run", fake_run)

    with pytest.raises(RuntimeError, match="different package sets"):
        verify_locked_install.verify_locked_installs(uv="uv", python="3.11")


def test_environment_python_uses_platform_layout(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(verify_locked_install.os, "name", "posix")
    assert verify_locked_install._environment_python(tmp_path) == tmp_path / "bin" / "python"
