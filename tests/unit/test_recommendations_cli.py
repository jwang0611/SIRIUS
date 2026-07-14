"""CLI defaults and credential-free dry-run behavior."""

from __future__ import annotations

import os
import subprocess
import sys


def test_dry_run_uses_typed_default_without_api_key(tmp_path, repo_root):
    mappings = tmp_path / "mappings.json"
    mappings.write_text("[]", encoding="utf-8")
    output = tmp_path / "preview"
    env = os.environ.copy()
    env.pop("OPENROUTER_API_KEY", None)
    env["DEFAULT_MODEL"] = "test/typed-default"

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "generate_sdtm_recommendations.py"),
            "--json-file",
            str(mappings),
            "--output",
            str(output),
            "--dry-run",
            "--no-parallel",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Using OpenRouter model: test/typed-default" in result.stdout
    assert "Dry run completed successfully" in result.stdout


def test_dry_run_loads_model_and_gateway_from_dotenv(tmp_path, repo_root):
    mappings = tmp_path / "mappings.json"
    mappings.write_text("[]", encoding="utf-8")
    output = tmp_path / "preview"
    (tmp_path / ".env").write_text(
        "DEFAULT_MODEL=envfile/should-be-used\nOPENROUTER_BASE_URL=https://internal-gw.example/v1\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    for key in ("DEFAULT_MODEL", "OPENROUTER_MODEL", "OPENROUTER_BASE_URL", "OPENROUTER_API_KEY"):
        env.pop(key, None)

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "generate_sdtm_recommendations.py"),
            "--json-file",
            str(mappings),
            "--output",
            str(output),
            "--dry-run",
            "--no-parallel",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Using OpenRouter model: envfile/should-be-used at https://internal-gw.example/v1" in result.stdout
    assert "Dry run completed successfully" in result.stdout
