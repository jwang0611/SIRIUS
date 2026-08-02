"""Job worker lifecycle and cancellation race regressions."""

from __future__ import annotations

import threading
import time
import uuid

import pytest

from src.web import tasks
from src.web.job_manager import JobManager, job_manager


def test_live_worker_stays_cancelling_until_safe_finish() -> None:
    manager = JobManager()
    job_id = "live-cancel"
    manager.create_job(job_id)
    started = threading.Event()
    release = threading.Event()

    def target() -> None:
        try:
            manager.update_job(job_id, state="running", message="working")
            started.set()
            assert release.wait(timeout=2)
            # This is the race that used to resurrect a cancelled job and expose
            # an artifact from work the caller explicitly stopped.
            manager.update_job(
                job_id,
                state="completed",
                message="must not win",
                output_excel="/tmp/must-not-be-published.xlsx",
            )
        finally:
            manager.finish_worker(job_id, threading.current_thread())

    worker = threading.Thread(target=target)
    assert manager.start_worker(job_id, worker)
    assert started.wait(timeout=1)

    assert manager.cancel_job(job_id)
    cancelling = manager.get_job(job_id)
    assert cancelling is not None
    assert cancelling.state == "cancelling"
    assert cancelling.cancelled is True
    assert manager.has_active_worker(job_id)

    release.set()
    worker.join(timeout=2)
    assert not worker.is_alive()

    finished = manager.get_job(job_id)
    assert finished is not None
    assert finished.state == "cancelled"
    assert finished.output_excel is None
    assert finished.message == "任务已被用户终止"
    assert not manager.has_active_worker(job_id)


def test_cancel_between_attach_and_start_prevents_worker_execution() -> None:
    manager = JobManager()
    job_id = "cancel-before-start"
    manager.create_job(job_id)
    executed = threading.Event()
    worker = threading.Thread(target=executed.set)

    assert manager.attach_worker(job_id, worker)
    assert manager.cancel_job(job_id)
    assert manager.get_job(job_id).state == "cancelling"

    # start_worker performs the final cancellation check under the same lock as
    # cancellation and therefore refuses to start the attached target.
    assert not manager.start_worker(job_id, worker)
    assert not executed.is_set()
    assert worker.ident is None
    assert manager.get_job(job_id).state == "cancelled"
    assert not manager.has_active_worker(job_id)


def test_dead_legacy_worker_reconciles_stale_running_job() -> None:
    manager = JobManager()
    job_id = "dead-worker"
    manager.create_job(job_id)

    def target() -> None:
        manager.update_job(job_id, state="running")

    worker = threading.Thread(target=target)
    assert manager.attach_worker(job_id, worker)
    # Simulate a legacy caller that starts the registered thread directly and
    # cannot invoke finish_worker when its target returns.
    worker.start()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert manager.get_job(job_id).state == "running"

    assert not manager.has_active_worker(job_id)
    reconciled = manager.get_job(job_id)
    assert reconciled is not None
    assert reconciled.state == "failed"
    assert "异常终止" in reconciled.message


@pytest.mark.parametrize("terminal_state", ["completed", "completed_with_errors", "failed", "cancelled"])
def test_late_cancel_is_idempotent_for_terminal_job(terminal_state: str) -> None:
    manager = JobManager()
    job_id = f"terminal-{terminal_state}"
    manager.create_job(job_id)
    manager.update_job(
        job_id,
        state=terminal_state,
        output_excel="/tmp/already-published.xlsx",
        message="terminal outcome",
    )

    assert manager.cancel_job(job_id)
    job = manager.get_job(job_id)
    assert job is not None
    assert job.state == terminal_state
    assert job.output_excel == "/tmp/already-published.xlsx"
    assert job.message == "terminal outcome"


@pytest.mark.parametrize("worker_kind", ["recommendations", "spec"])
def test_task_starters_finalize_cancel_after_target_returns(monkeypatch, worker_kind: str) -> None:
    """Both production starters use the managed worker finalizer."""
    job_id = f"managed-{worker_kind}-{uuid.uuid4().hex}"
    started = threading.Event()
    release = threading.Event()

    def fake_target(*_args, **_kwargs) -> None:
        job_manager.update_job(job_id, state="running")
        started.set()
        assert release.wait(timeout=2)
        job_manager.update_job(job_id, state="completed", message="must not win")

    job_manager.create_job(job_id)
    try:
        if worker_kind == "recommendations":
            monkeypatch.setattr(tasks, "_run_recommendations_job", fake_target)
            tasks.start_recommendations_job(job_id=job_id, json_file="unused.json")
        else:
            monkeypatch.setattr(tasks, "_run_spec_mapper_job", fake_target)
            tasks.start_spec_mapper_job(
                job_id=job_id,
                als_file="unused.xlsx",
                template_file="unused-template.xlsx",
                output_name="unused",
            )

        assert started.wait(timeout=1)
        assert job_manager.cancel_job(job_id)
        assert job_manager.get_job(job_id).state == "cancelling"

        release.set()
        deadline = time.monotonic() + 2
        while job_manager.has_active_worker(job_id) and time.monotonic() < deadline:
            time.sleep(0.01)

        job = job_manager.get_job(job_id)
        assert job is not None
        assert job.state == "cancelled"
        assert "终止" in job.message
        assert not job_manager.has_active_worker(job_id)
    finally:
        release.set()
        job_manager.remove_job(job_id)
