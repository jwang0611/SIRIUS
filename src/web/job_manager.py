from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from threading import Lock
from typing import Any


class _Unset(Enum):
    """Sentinel to distinguish 'not provided' from an explicit None."""

    TOKEN = 0


_UNSET: Any = _Unset.TOKEN


@dataclass
class JobStatus:
    job_id: str
    state: str = "pending"  # pending | running | completed | completed_with_errors | failed | cancelled
    message: str = ""
    processed: int = 0
    total: int = 0
    current_table: str | None = None
    current_variable: str | None = None
    output_excel: str | None = None
    output_json: str | None = None
    output_log: str | None = None  # Path to log file
    cancelled: bool = False  # Flag to signal task should stop
    json_file: str | None = None  # Store input file for resume
    failed_variables: int = 0
    consistency_errors: int = 0
    # Spec Mapper write observability (actual workbook write outcome).
    spec_attempted: int = 0  # planned write operations that were attempted
    spec_written: int = 0  # operations that actually mutated the workbook
    spec_skipped: int = 0  # operations safely not performed
    spec_warnings: int = 0  # recoverable, advisory issues
    spec_errors: int = 0  # recoverable per-item write failures

    def to_dict(self) -> dict[str, str | int | float | bool | None]:
        data = asdict(self)
        percent: float = 0.0
        if self.total > 0:
            percent = round((self.processed / self.total) * 100, 2)
        data["progress_percent"] = percent
        return data


class JobManager:
    """Thread-safe in-memory job registry."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobStatus] = {}
        self._lock = Lock()

    def create_job(self, job_id: str) -> JobStatus:
        with self._lock:
            status = JobStatus(job_id=job_id)
            self._jobs[job_id] = status
            return status

    def update_job(self, job_id: str, **updates) -> JobStatus:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(f"Job {job_id} not found")
            status = self._jobs[job_id]
            for key, value in updates.items():
                if hasattr(status, key) and value is not _UNSET:
                    setattr(status, key, value)
            return status

    def get_job(self, job_id: str) -> JobStatus | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        """Mark a job as cancelled. Returns True if job was found and cancelled."""
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].cancelled = True
                self._jobs[job_id].state = "cancelled"
                self._jobs[job_id].message = "任务已被用户终止"
                return True
            return False

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
