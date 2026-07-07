"""Local developer helper: lint, type-check, test, coverage, and formatting.

Usage::

    python scripts/dev.py lint        # ruff check
    python scripts/dev.py fmt         # ruff format
    python scripts/dev.py typecheck   # mypy on the strict subset
    python scripts/dev.py test        # pytest tests/unit tests/characterization
    python scripts/dev.py cov         # pytest + coverage + baseline threshold
    python scripts/dev.py all         # lint + typecheck + test
    python scripts/dev.py ci          # exactly what GitLab CI runs

Runs on Windows PowerShell, macOS, and Linux.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str]) -> int:
    print(f"$ {' '.join(cmd)}", flush=True)
    return subprocess.call(cmd, cwd=ROOT)


def lint() -> int:
    return _run([sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"])


def fmt() -> int:
    rc1 = _run([sys.executable, "-m", "ruff", "check", "--fix", "src", "tests", "scripts"])
    rc2 = _run([sys.executable, "-m", "ruff", "format", "src", "tests", "scripts"])
    return rc1 or rc2


def typecheck() -> int:
    return _run([sys.executable, "-m", "mypy", "src"])


def test() -> int:
    return _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit",
            "tests/characterization",
            "-q",
        ]
    )


def cov() -> int:
    # NOTE: Baseline is 30% (matching GitLab CI gate). The aspirational
    # target is 80% for pure-logic modules; raise this threshold as the
    # orchestrator / Excel writer classes get additional unit coverage.
    return _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit",
            "tests/characterization",
            "--cov=src",
            "--cov-report=term-missing",
            "--cov-report=xml:coverage.xml",
            "--cov-fail-under=30",
        ]
    )


def all_checks() -> int:
    for step in (lint, typecheck, test):
        rc = step()
        if rc != 0:
            return rc
    return 0


def ci() -> int:
    for step in (lint, typecheck, cov):
        rc = step()
        if rc != 0:
            return rc
    return 0


COMMANDS = {
    "lint": lint,
    "fmt": fmt,
    "typecheck": typecheck,
    "test": test,
    "cov": cov,
    "all": all_checks,
    "ci": ci,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Usage: python scripts/dev.py {{{}}}".format("|".join(COMMANDS)), file=sys.stderr)
        return 2
    return COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())
