#!/usr/bin/env python3
"""Verify that two clean uv installs produce an identical runtime package set."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _environment_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _run(command: list[str], *, environment: dict[str, str], pass_number: int) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"locked install verification pass {pass_number} timed out") from exc
    if result.returncode != 0:
        # Index credentials may be present in the inherited environment. Do not
        # echo the command, environment, or raw installer diagnostics.
        raise RuntimeError(f"locked install verification pass {pass_number} failed")
    return result.stdout


def _normalized_freeze(uv: str, python: Path, environment: dict[str, str], pass_number: int) -> list[str]:
    output = _run([uv, "pip", "freeze", "--python", str(python)], environment=environment, pass_number=pass_number)
    return sorted(line.strip() for line in output.splitlines() if line.strip())


def verify_locked_installs(*, uv: str, python: str) -> dict[str, object]:
    lock_path = PROJECT_ROOT / "uv.lock"
    if not lock_path.is_file():
        raise FileNotFoundError("uv.lock is required")

    with tempfile.TemporaryDirectory(prefix="sirius-lock-check-") as temp_root:
        root = Path(temp_root)
        freezes: list[list[str]] = []

        for pass_number in (1, 2):
            project_environment = root / f"environment-{pass_number}"
            environment = os.environ.copy()
            # Avoid inheriting a caller's project environment: each pass must
            # install into the explicit fresh directory below.
            environment.pop("UV_PROJECT_ENVIRONMENT", None)
            _run(
                [uv, "venv", "--python", python, str(project_environment)],
                environment=environment,
                pass_number=pass_number,
            )
            _run(
                [
                    uv,
                    "pip",
                    "sync",
                    "--python",
                    str(_environment_python(project_environment)),
                    "--require-hashes",
                    str(PROJECT_ROOT / "requirements.txt"),
                ],
                environment=environment,
                pass_number=pass_number,
            )
            freezes.append(_normalized_freeze(uv, _environment_python(project_environment), environment, pass_number))

        if freezes[0] != freezes[1]:
            raise RuntimeError("clean locked installs produced different package sets")

    package_set = "\n".join(freezes[0]).encode("utf-8")
    return {
        "schema_version": 1,
        "python": python,
        "package_count": len(freezes[0]),
        "package_set_sha256": hashlib.sha256(package_set).hexdigest(),
        "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", default=shutil.which("uv") or "uv", help="uv executable")
    parser.add_argument("--python", default="3.11", help="Python interpreter or version for the clean environments")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = verify_locked_installs(uv=args.uv, python=args.python)
    except (FileNotFoundError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
