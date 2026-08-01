"""Concurrency and corruption tests for recommendation resume checkpoints."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.utils.artifact_names import model_artifact_slug
from src.utils.recommendation_context import build_recommendation_context
from src.web.job_manager import JobStatus
from src.web.routers.jobs import _can_resume_cancelled_job
from src.web.session_manager import session_manager
from src.web.tasks import (
    _find_existing_output_base,
    _snapshot_recommendation_checkpoint,
)


def _checkpoint(
    *,
    completed: int,
    model_name: str = "test/model",
    checkpoint_context: dict | None = None,
) -> dict:
    return {
        "recommendations": [
            {
                "table_name": "AE",
                "domain_recommendations": [{"variable_name": "AETERM"}],
            }
        ],
        "completed_pairs": completed,
        "version": "1.0",
        "model_name": model_name,
        "checkpoint_context": checkpoint_context,
    }


def test_resume_search_skips_newer_corrupt_checkpoint(tmp_path):
    suffix = model_artifact_slug("test/model")
    valid = tmp_path / f"fixture_web_older_{suffix}.tmp.json"
    corrupt = tmp_path / f"fixture_web_newer_{suffix}.tmp.json"
    valid.write_text(json.dumps(_checkpoint(completed=3)), encoding="utf-8")
    corrupt.write_text('{"recommendations": [', encoding="utf-8")
    os.utime(valid, (1, 1))
    os.utime(corrupt, (2, 2))

    result = _find_existing_output_base(tmp_path / "fixture.json", "test/model", tmp_path)

    assert result == tmp_path / "fixture_web_older"


def test_resume_search_rejects_checkpoint_with_different_model_identity(tmp_path):
    requested = "provider/model[ab]"
    suffix = model_artifact_slug(requested)
    wrong = tmp_path / f"fixture_web_wrong_{suffix}.tmp.json"
    wrong.write_text(
        json.dumps(_checkpoint(completed=9, model_name="provider/modela")),
        encoding="utf-8",
    )

    assert _find_existing_output_base(tmp_path / "fixture.json", requested, tmp_path) is None


def test_resume_rejects_changed_same_name_input_and_legacy_checkpoint(tmp_path):
    input_path = tmp_path / "processed" / "fixture.json"
    input_path.parent.mkdir()
    input_path.write_text('[{"version": 1}]', encoding="utf-8")
    context_v1 = build_recommendation_context(
        input_path,
        kb_files=[],
        language="en",
        enable_kb=True,
    )
    suffix = model_artifact_slug("test/model")
    checkpoint = tmp_path / f"fixture_web_run_{suffix}.tmp.json"
    checkpoint.write_text(
        json.dumps(_checkpoint(completed=1, checkpoint_context=context_v1)),
        encoding="utf-8",
    )
    assert (
        _find_existing_output_base(
            input_path,
            "test/model",
            tmp_path,
            expected_context=context_v1,
        )
        == tmp_path / "fixture_web_run"
    )

    input_path.write_text('[{"version": 2}]', encoding="utf-8")
    context_v2 = build_recommendation_context(
        input_path,
        kb_files=[],
        language="en",
        enable_kb=True,
    )
    assert context_v2 != context_v1
    assert (
        _find_existing_output_base(
            input_path,
            "test/model",
            tmp_path,
            expected_context=context_v2,
        )
        is None
    )

    checkpoint.write_text(json.dumps(_checkpoint(completed=1)), encoding="utf-8")
    assert (
        _find_existing_output_base(
            input_path,
            "test/model",
            tmp_path,
            expected_context=context_v2,
        )
        is None
    )


def test_resume_rejects_changed_kb_or_config(tmp_path):
    input_path = tmp_path / "processed" / "fixture.json"
    input_path.parent.mkdir()
    input_path.write_text("[]", encoding="utf-8")
    kb_path = tmp_path / "project_demo.parquet"
    kb_path.write_bytes(b"kb-v1")
    context_v1 = build_recommendation_context(
        input_path,
        kb_files=[str(kb_path)],
        language="en",
        enable_kb=True,
    )
    suffix = model_artifact_slug("test/model")
    checkpoint = tmp_path / f"fixture_web_run_{suffix}.tmp.json"
    checkpoint.write_text(
        json.dumps(_checkpoint(completed=1, checkpoint_context=context_v1)),
        encoding="utf-8",
    )

    kb_path.write_bytes(b"kb-v2")
    changed_kb = build_recommendation_context(
        input_path,
        kb_files=[str(kb_path)],
        language="en",
        enable_kb=True,
    )
    changed_config = build_recommendation_context(
        input_path,
        kb_files=[str(kb_path)],
        language="cn",
        enable_kb=False,
    )
    assert changed_kb != context_v1
    assert changed_config != context_v1
    for context in (changed_kb, changed_config):
        assert (
            _find_existing_output_base(
                input_path,
                "test/model",
                tmp_path,
                expected_context=context,
            )
            is None
        )


def test_checkpoint_context_binds_processed_and_raw_companion_workbooks(tmp_path):
    job_dir = tmp_path / "job"
    processed_dir = job_dir / "processed"
    raw_dir = job_dir / "raw"
    processed_dir.mkdir(parents=True)
    raw_dir.mkdir()
    json_path = processed_dir / "fixture.json"
    processed_excel = processed_dir / "fixture.xlsx"
    raw_excel = raw_dir / "fixture.xlsx"
    json_path.write_text("[]", encoding="utf-8")
    processed_excel.write_bytes(b"processed-v1")
    raw_excel.write_bytes(b"raw-v1")

    baseline = build_recommendation_context(
        json_path,
        kb_files=[],
        language="en",
        enable_kb=True,
    )
    assert baseline["inputs"]["processed_excel"]["sha256"]
    assert baseline["inputs"]["raw_excel"]["sha256"]

    processed_excel.write_bytes(b"processed-v2")
    changed_processed = build_recommendation_context(
        json_path,
        kb_files=[],
        language="en",
        enable_kb=True,
    )
    assert changed_processed != baseline

    processed_excel.write_bytes(b"processed-v1")
    raw_excel.write_bytes(b"raw-v2")
    changed_raw = build_recommendation_context(
        json_path,
        kb_files=[],
        language="en",
        enable_kb=True,
    )
    assert changed_raw != baseline


def test_model_artifact_slug_is_glob_safe_and_hashes_full_identity():
    common_prefix = "provider/" + ("x" * 80)
    first = model_artifact_slug(common_prefix + "[ab]")
    second = model_artifact_slug(common_prefix + "a")

    assert first != second
    assert all(char not in first for char in "[]*?")
    assert len(first) <= 65


def test_corrupt_resume_source_never_replaces_existing_target(tmp_path):
    source = tmp_path / "source.tmp.json"
    target = tmp_path / "target.tmp.json"
    source.write_text('{"recommendations": [', encoding="utf-8")
    target_payload = _checkpoint(completed=1)
    target.write_text(json.dumps(target_payload), encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        _snapshot_recommendation_checkpoint(source, target)

    assert json.loads(target.read_text(encoding="utf-8")) == target_payload
    assert not list(tmp_path.glob(".target.tmp.json.*.part"))


def test_concurrent_resumes_receive_independent_complete_snapshots(tmp_path):
    source = tmp_path / "source.tmp.json"
    source_payload = _checkpoint(completed=7)
    source.write_text(json.dumps(source_payload), encoding="utf-8")
    targets = [tmp_path / f"resume-{index}.tmp.json" for index in range(8)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        completed = list(executor.map(lambda target: _snapshot_recommendation_checkpoint(source, target), targets))

    assert completed == [7] * len(targets)
    assert all(json.loads(target.read_text(encoding="utf-8")) == source_payload for target in targets)
    assert not list(tmp_path.glob(".*.part"))


def test_cancelled_job_can_resume_only_with_matching_complete_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_id = "checkpoint-status-session"
    output_dir = session_manager.get_session_als_dir(session_id)
    context = {"schema": "recommendation-context-v1", "inputs": {"json": "hash-a"}}
    suffix = model_artifact_slug("test/model")
    checkpoint = output_dir / f"fixture_web_run_{suffix}.tmp.json"
    checkpoint.write_text(
        json.dumps(_checkpoint(completed=2, checkpoint_context=context)),
        encoding="utf-8",
    )
    job = JobStatus(
        job_id="cancelled-job",
        owner_session_id=session_id,
        state="cancelled",
        json_file="fixture.json",
        model_name="test/model",
        checkpoint_context=context,
    )

    assert _can_resume_cancelled_job(job, session_id) is True
    job.checkpoint_context = {"schema": "recommendation-context-v1", "inputs": {"json": "hash-b"}}
    assert _can_resume_cancelled_job(job, session_id) is False
    job.checkpoint_context = context
    checkpoint.write_text('{"recommendations": [', encoding="utf-8")
    assert _can_resume_cancelled_job(job, session_id) is False
