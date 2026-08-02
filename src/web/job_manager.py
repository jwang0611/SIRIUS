from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from threading import Lock, Thread
from typing import Any


class _Unset(Enum):
    """Sentinel to distinguish 'not provided' from an explicit None."""

    TOKEN = 0


_UNSET: Any = _Unset.TOKEN


@dataclass
class JobStatus:
    job_id: str
    # Internal ownership boundary. This is intentionally removed from
    # ``to_dict`` so session identifiers never leak through job-status APIs.
    owner_session_id: str | None = None
    # pending | running | cancelling | completed | completed_with_errors | failed | cancelled
    state: str = "pending"
    message: str = ""
    processed: int = 0
    total: int = 0
    current_table: str | None = None
    current_variable: str | None = None
    output_excel: str | None = None
    output_json: str | None = None
    output_log: str | None = None  # Path to log file
    output_issues: str | None = None  # Path to full structured write-issue JSON
    cancelled: bool = False  # Flag to signal task should stop
    json_file: str | None = None  # Store input file for resume
    model_name: str | None = None  # Full identity used to validate resume checkpoints
    checkpoint_context: dict[str, Any] | None = None  # Internal content/config identity
    failed_variables: int = 0
    consistency_errors: int = 0
    # Spec Mapper write observability (actual workbook write outcome).
    spec_attempted: int = 0  # planned write operations that were attempted
    spec_written: int = 0  # operations that actually mutated the workbook
    spec_skipped: int = 0  # operations safely not performed
    spec_warnings: int = 0  # recoverable, advisory issues
    spec_errors: int = 0  # recoverable per-item write failures
    # Safe, structured, locatable issues (capped) so the UI can show *which*
    # items failed/skipped, not just counts. Each item: code/stage/operation/
    # sheet/row/column — never paths, clinical values, or tracebacks.
    spec_issues: list[dict[str, Any]] = field(default_factory=list)
    # Total number of structured issues (may exceed len(spec_issues) when the
    # in-payload list is capped). The full list is downloadable via output_issues
    # so the UI can honestly show truncation instead of dropping items silently.
    spec_issues_total: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("owner_session_id", None)
        data.pop("checkpoint_context", None)
        # Artifact access is exclusively through authorized download endpoints;
        # expose display names, never the server-side directory layout.
        for key in ("output_excel", "output_json", "output_log", "output_issues"):
            if data.get(key):
                data[key] = Path(str(data[key])).name
        percent: float = 0.0
        if self.total > 0:
            percent = round((self.processed / self.total) * 100, 2)
        data["progress_percent"] = percent
        return data


class JobManager:
    """Thread-safe in-memory job registry."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobStatus] = {}
        self._workers: dict[str, Thread] = {}
        self._lock = Lock()

    def create_job(self, job_id: str, owner_session_id: str | None = None) -> JobStatus:
        with self._lock:
            status = JobStatus(job_id=job_id, owner_session_id=owner_session_id)
            self._jobs[job_id] = status
            return status

    def is_owned_by(self, job_id: str, session_id: str | None) -> bool:
        """Return whether ``session_id`` is allowed to access ``job_id``.

        API-created jobs are session-owned and require the exact same header.
        Internal anonymous jobs therefore never match a session caller.
        Returning only a boolean lets routers use a uniform 404 response and
        avoid revealing whether a job exists.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and job.owner_session_id == session_id)

    def attach_worker(self, job_id: str, worker: Thread) -> bool:
        """Register a worker before it starts.

        Returns ``False`` when cleanup already cancelled/removed the job, so a
        pending worker cannot start after its session has been torn down.

        New production callers should prefer :meth:`start_worker`, which makes
        registration, the final cancellation check, and ``Thread.start()``
        atomic with respect to :meth:`cancel_job`.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.cancelled:
                return False
            self._workers[job_id] = worker
            return True

    def start_worker(self, job_id: str, worker: Thread) -> bool:
        """Atomically register and start ``worker`` unless cancellation won.

        Holding the manager lock through ``Thread.start`` closes the old window
        where cleanup could cancel a just-attached job and the caller would
        nevertheless start its worker immediately afterwards. The new thread
        may block briefly when it first updates its job, but ``Thread.start``
        returns once the thread bootstrap is ready, before the target needs this
        lock.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            registered = self._workers.get(job_id)
            if job is None:
                return False
            if registered is not None and registered is not worker:
                return False
            if job.cancelled:
                self._workers.pop(job_id, None)
                job.state = "cancelled"
                job.message = "任务已被用户终止"
                return False

            self._workers[job_id] = worker
            try:
                worker.start()
            except RuntimeError:
                self._workers.pop(job_id, None)
                if job.cancelled:
                    job.state = "cancelled"
                    job.message = "任务已被用户终止"
                else:
                    job.state = "failed"
                    job.message = "任务线程启动失败，请查看服务端日志"
                return False
            return True

    def finish_worker(
        self,
        job_id: str,
        worker: Thread | None = None,
        *,
        cancellation_message: str | None = None,
    ) -> None:
        """Publish the safe terminal state after a worker target has returned.

        A cancellation request remains ``cancelling`` while the target is still
        unwinding or persisting a safe checkpoint. Only this finalizer (or an
        explicit cancellation update at the target's safe return point) exposes
        ``cancelled``. An otherwise non-terminal worker exit is classified as a
        failure instead of leaving a permanently active zombie job.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            registered = self._workers.get(job_id)
            if worker is not None and registered is not None and registered is not worker:
                return
            self._workers.pop(job_id, None)
            if job is None:
                return
            if job.cancelled:
                job.state = "cancelled"
                if cancellation_message:
                    job.message = cancellation_message
                elif not job.message or job.message == "任务正在安全终止，请稍候":
                    job.message = "任务已被用户终止"
            elif job.state in {"pending", "running", "cancelling"}:
                job.state = "failed"
                job.message = "任务异常终止，请查看服务端日志"

    def has_active_worker(self, job_id: str) -> bool:
        """Return whether a job is pending/running or its thread is still live."""
        with self._lock:
            job = self._jobs.get(job_id)
            worker = self._workers.get(job_id)
            if job is None:
                return False
            if worker is not None:
                if worker.ident is None or worker.is_alive():
                    return True

                # A legacy/directly-started worker may exit without calling
                # finish_worker. Reconcile it here so cleanup cannot poll a
                # stale pending/running state forever.
                self._workers.pop(job_id, None)
                if job.cancelled:
                    job.state = "cancelled"
                    if not job.message or job.message == "任务正在安全终止，请稍候":
                        job.message = "任务已被用户终止"
                elif job.state in {"pending", "running", "cancelling"}:
                    job.state = "failed"
                    job.message = "任务异常终止，请查看服务端日志"
                return False

            if job.state == "cancelling":
                job.state = "cancelled"
                if not job.message or job.message == "任务正在安全终止，请稍候":
                    job.message = "任务已被用户终止"
                return False
            return job.state in {"pending", "running"}

    def update_job(self, job_id: str, **updates) -> JobStatus:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(f"Job {job_id} not found")
            status = self._jobs[job_id]
            requested_state = updates.get("state", _UNSET)
            if status.cancelled and requested_state not in {"cancelling", "cancelled"}:
                # Once cancellation wins, progress or success/failure updates
                # from the unwinding worker must not resurrect the job or expose
                # artifacts from a run the user explicitly stopped.
                return status
            for key, value in updates.items():
                if hasattr(status, key) and value is not _UNSET:
                    setattr(status, key, value)
            return status

    def get_job(self, job_id: str) -> JobStatus | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        """Request cancellation without claiming a live worker has exited."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.state in {"completed", "completed_with_errors", "failed", "cancelled"}:
                # A late/repeated cancel is idempotent and must not hide a
                # completed artifact or rewrite an already terminal outcome.
                return True

            job.cancelled = True
            worker = self._workers.get(job_id)
            worker_active = bool(worker and (worker.ident is None or worker.is_alive()))
            if worker_active:
                job.state = "cancelling"
                job.message = "任务正在安全终止，请稍候"
            else:
                self._workers.pop(job_id, None)
                job.state = "cancelled"
                job.message = "任务已被用户终止"
            return True

    def is_cancelled(self, job_id: str) -> bool:
        """Check if a job has been cancelled."""
        with self._lock:
            job = self._jobs.get(job_id)
            return job.cancelled if job else False

    def remove_job(self, job_id: str) -> bool:
        """移除一个 job。返回 True 表示成功移除。"""
        with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
                self._workers.pop(job_id, None)
                return True
            return False

    def get_stats(self) -> dict[str, int | dict[str, int]]:
        """获取 JobManager 统计信息"""
        with self._lock:
            states: dict[str, int] = {}
            for job in self._jobs.values():
                states[job.state] = states.get(job.state, 0) + 1
            return {
                "total_jobs": len(self._jobs),
                "by_state": states,
            }


job_manager = JobManager()
