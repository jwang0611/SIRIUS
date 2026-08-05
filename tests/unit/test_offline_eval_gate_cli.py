"""End-to-end mechanism self-test for ``scripts/run_offline_eval_gate.py``.

The real release gate stays blocked until a maintainer supplies two authorized,
de-identified held-out studies and reviews the first baseline.  Everything in
this module is deliberately synthetic — opaque ``study-NNN`` files, ``EVAL-NNNN``
rows, and invented ``Opaque panel``/``X001`` metadata built in ``tmp_path`` and
never committed under ``data/``.  It is not a release baseline and must never be
used as evaluation evidence.

What it does cover is the *mechanism* the release gate depends on, exercised
through the actual command line rather than in-process calls:

- repeating a run with identical inputs produces byte-identical JSON and
  Markdown reports (no random drift between replays);
- a replay that regresses against the reviewed baseline exits non-zero and the
  reports name the metric that moved;
- an unregressed replay exits zero;
- the whole run stays offline: sockets are blocked and credentials are poisoned
  in the subprocess environment, and it still succeeds.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

from scripts.eval_prompt_accuracy import evaluate, ground_truth_from_rows
from src.evaluation.heldout import manifest_fingerprint
from tests.unit.test_offline_eval_gate import _ai, _baseline, _row, _write_release_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GATE_SCRIPT = PROJECT_ROOT / "scripts" / "run_offline_eval_gate.py"
FAKE_GIT_SHA = "1" * 40
NETWORK_SENTINEL = "SIRIUS offline gate attempted network access"

pytestmark = pytest.mark.skipif(os.name != "posix", reason="The CLI harness uses a POSIX shell stub for git")

# Replay evidence records the checkout revision and whether it is dirty.  Stub
# ``git`` so that evidence is a fixed input of the test instead of a property of
# the developer's working tree; report determinism is what is under test here.
_GIT_STUB = f"""#!/bin/sh
case "$1" in
  rev-parse) echo {FAKE_GIT_SHA} ;;
  status) : ;;
  *) exit 1 ;;
esac
"""

# Injected via PYTHONPATH so the gate subprocess cannot reach a network peer
# even if some import path tried to construct an AI client.
_SOCKET_GUARD = f'''"""Fail loudly on any outbound connection attempt."""

import socket


def _blocked(*_args, **_kwargs):
    raise RuntimeError({NETWORK_SENTINEL!r})


socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked
'''


class _Fixture(NamedTuple):
    manifest: Path
    rows: list[dict]
    knowledge_root: Path


class _GateRun(NamedTuple):
    process: subprocess.CompletedProcess[str]
    report_json: Path
    report_markdown: Path


def _synthetic_release_fixture(tmp_path: Path) -> _Fixture:
    """Build an obviously synthetic two-study release fixture in ``tmp_path``."""
    studies = [[_row(1, supp=True)], [_row(2)]]
    manifest = _write_release_manifest(tmp_path, studies)
    knowledge_root = tmp_path / "project-kb"
    knowledge_root.mkdir()
    return _Fixture(manifest, [row for study in studies for row in study], knowledge_root)


def _reviewed_predictions(rows: list[dict]) -> list[dict]:
    """Predictions matching ground truth; the accepted baseline replay."""
    return [_ai(row) for row in rows]


def _regressed_predictions(rows: list[dict]) -> list[dict]:
    """The same replay with one non-SUPP variable mapped to the wrong column."""
    predictions = _reviewed_predictions(rows)
    return [*predictions[:-1], {**predictions[-1], "ai_variable": "AGE", "sdtm_variable": "AGE"}]


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _write_reviewed_baseline(tmp_path: Path, fixture: _Fixture) -> Path:
    """Record the reviewed metrics of a perfect replay as the gate baseline."""
    metrics = evaluate(_reviewed_predictions(fixture.rows), ground_truth_from_rows(fixture.rows))
    return _write_json(tmp_path / "baseline.json", _baseline(metrics, manifest_fingerprint(fixture.manifest)))


def _offline_environment(tmp_path: Path) -> dict[str, str]:
    """Pin git evidence, block sockets, and poison provider credentials."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    git_stub = bin_dir / "git"
    git_stub.write_text(_GIT_STUB, encoding="utf-8")
    git_stub.chmod(0o755)
    guard_dir = tmp_path / "offline-guard"
    guard_dir.mkdir(exist_ok=True)
    (guard_dir / "sitecustomize.py").write_text(_SOCKET_GUARD, encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])
    env["PYTHONPATH"] = os.pathsep.join(part for part in (str(guard_dir), env.get("PYTHONPATH", "")) if part)
    # An offline replay must never consult provider settings.  If it did, these
    # values would make the run fail instead of quietly succeeding.
    env["OPENROUTER_API_KEY"] = "poisoned-key-must-not-be-used"
    env["OPENROUTER_BASE_URL"] = "http://127.0.0.1:9/must-not-be-called"
    return env


def _run_gate(
    tmp_path: Path,
    fixture: _Fixture,
    *,
    ai_output: Path,
    baseline: Path,
    stem: str,
    env: dict[str, str],
) -> _GateRun:
    report_json = tmp_path / f"{stem}.json"
    report_markdown = tmp_path / f"{stem}.md"
    process = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--dataset-manifest",
            str(fixture.manifest),
            "--ai-output",
            str(ai_output),
            "--baseline",
            str(baseline),
            "--project-knowledge-root",
            str(fixture.knowledge_root),
            "--report-json",
            str(report_json),
            "--report-markdown",
            str(report_markdown),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return _GateRun(process, report_json, report_markdown)


def test_repeated_offline_gate_runs_produce_byte_identical_reports(tmp_path):
    fixture = _synthetic_release_fixture(tmp_path)
    ai_output = _write_json(tmp_path / "replay.json", _reviewed_predictions(fixture.rows))
    baseline = _write_reviewed_baseline(tmp_path, fixture)
    env = _offline_environment(tmp_path)

    first = _run_gate(tmp_path, fixture, ai_output=ai_output, baseline=baseline, stem="first", env=env)
    second = _run_gate(tmp_path, fixture, ai_output=ai_output, baseline=baseline, stem="second", env=env)

    assert (first.process.returncode, second.process.returncode) == (0, 0), first.process.stderr
    report = json.loads(first.report_json.read_text(encoding="utf-8"))
    assert report["regression_gate"]["passed"] is True
    assert report["metrics"]["exact_rate"] == 1.0
    assert report["evidence"]["git"] == {"sha": FAKE_GIT_SHA, "dirty": False}
    assert "Decision: **PASS**" in first.report_markdown.read_text(encoding="utf-8")
    assert first.report_json.read_bytes() == second.report_json.read_bytes()
    assert first.report_markdown.read_bytes() == second.report_markdown.read_bytes()


def test_regressed_replay_fails_the_gate_and_names_the_moved_metric(tmp_path):
    fixture = _synthetic_release_fixture(tmp_path)
    baseline = _write_reviewed_baseline(tmp_path, fixture)
    ai_output = _write_json(tmp_path / "replay.json", _regressed_predictions(fixture.rows))
    env = _offline_environment(tmp_path)

    run = _run_gate(tmp_path, fixture, ai_output=ai_output, baseline=baseline, stem="report", env=env)

    assert run.process.returncode == 1
    assert "regression gate failed" in run.process.stderr
    report = json.loads(run.report_json.read_text(encoding="utf-8"))
    assert report["regression_gate"]["passed"] is False
    gates = {item["metric"]: item for item in report["regression_gate"]["gates"]}
    assert gates["exact_rate"]["baseline"] == 1.0
    assert gates["exact_rate"]["current"] == 0.5
    assert gates["exact_rate"]["passed"] is False
    # Only the metric that actually moved may be reported as a regression.
    assert [name for name, item in gates.items() if not item["passed"]] == ["exact_rate"]
    assert "| exact_rate | 1.0 | 0.5 | FAIL |" in run.report_markdown.read_text(encoding="utf-8")


def test_offline_gate_passes_with_sockets_blocked_and_credentials_poisoned(tmp_path):
    fixture = _synthetic_release_fixture(tmp_path)
    ai_output = _write_json(tmp_path / "replay.json", _reviewed_predictions(fixture.rows))
    baseline = _write_reviewed_baseline(tmp_path, fixture)
    env = _offline_environment(tmp_path)
    probe = subprocess.run(
        [sys.executable, "-c", "import socket; socket.create_connection(('127.0.0.1', 9))"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    run = _run_gate(tmp_path, fixture, ai_output=ai_output, baseline=baseline, stem="report", env=env)

    # The guard must actually bite, otherwise the offline claim below is vacuous.
    assert probe.returncode != 0
    assert NETWORK_SENTINEL in probe.stderr
    assert run.process.returncode == 0, run.process.stderr
    assert NETWORK_SENTINEL not in run.process.stderr
    assert json.loads(run.report_json.read_text(encoding="utf-8"))["regression_gate"]["passed"] is True
