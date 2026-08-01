"""Session 管理模块 - 用于隔离不同用户的文件和任务"""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from threading import Condition, Lock, Timer
from typing import Any

from src.infrastructure.session_key import safe_session_key


class SessionClosingError(RuntimeError):
    """Raised when a request attempts to publish into a closing session."""


_CLOSED_SESSION_TTL_SECONDS = 48 * 3600
_MAX_CLOSED_SESSIONS = 10_000


@dataclass
class SessionInfo:
    """存储单个用户会话的信息"""

    session_id: str
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)
    uploaded_files: list[str] = field(default_factory=list)
    job_ids: list[str] = field(default_factory=list)
    kb_files: list[str] = field(default_factory=list)  # 用户上传的 KB 文件（.parquet）


class SessionManager:
    """线程安全的 Session 管理器，用于隔离不同用户的文件和任务"""

    def __init__(self, expire_hours: int = 24, cleanup_delay: float = 3.0):
        self._sessions: dict[str, SessionInfo] = {}
        self._lock = Lock()
        self._cleanup_condition = Condition(self._lock)
        self._cleanup_in_progress: set[str] = set()
        self._draining_sessions: set[str] = set()
        # Recently cleaned capabilities are retired long enough to outlive
        # request retries, long-running workers, and browser refreshes. The
        # bounded TTL map avoids turning arbitrary cleanup IDs into an
        # unbounded memory sink.
        self._closed_sessions: OrderedDict[str, float] = OrderedDict()
        self._active_operations: dict[str, int] = {}
        self._writer_locks: dict[str, Lock] = {}
        self._expire_hours = expire_hours
        self._cleanup_delay = cleanup_delay  # 延迟清理的秒数（给刷新操作留出取消窗口）
        self._job_manager = None  # 延迟设置，避免循环导入
        self._pending_cleanups: dict[str, Timer] = {}  # 待执行的延迟清理
        self._drain_timers: dict[str, Timer] = {}
        self._cleanup_retry_attempts: dict[str, int] = {}
        self._cleanup_retry_tokens: dict[str, str] = {}

    def set_job_manager(self, job_manager) -> None:
        """设置 job_manager 引用"""
        self._job_manager = job_manager

    def _prune_closed_sessions_locked(self) -> None:
        now = time.monotonic()
        while self._closed_sessions:
            _session_id, expires_at = next(iter(self._closed_sessions.items()))
            if expires_at > now:
                break
            self._closed_sessions.popitem(last=False)
        while len(self._closed_sessions) > _MAX_CLOSED_SESSIONS:
            self._closed_sessions.popitem(last=False)

    def _retire_session_locked(self, session_id: str) -> None:
        self._prune_closed_sessions_locked()
        self._closed_sessions.pop(session_id, None)
        self._closed_sessions[session_id] = time.monotonic() + _CLOSED_SESSION_TTL_SECONDS

    def get_or_create(self, session_id: str) -> SessionInfo:
        """获取或创建 session"""
        with self._cleanup_condition:
            self._prune_closed_sessions_locked()
            if (
                session_id in self._cleanup_in_progress
                or session_id in self._draining_sessions
                or session_id in self._closed_sessions
            ):
                raise SessionClosingError("session is draining")
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionInfo(session_id=session_id)
            else:
                self._sessions[session_id].last_active = datetime.now()
            return self._sessions[session_id]

    @contextmanager
    def operation(self, session_id: str, *, create: bool = True) -> Iterator[SessionInfo]:
        """Lease a session for the full lifetime of one API operation.

        Cleanup marks the session as closing before waiting for these leases,
        so a request that already entered may finish and publish its files,
        while later requests cannot slip into the generation being deleted.
        """
        with self._cleanup_condition:
            self._prune_closed_sessions_locked()
            if (
                session_id in self._cleanup_in_progress
                or session_id in self._draining_sessions
                or session_id in self._closed_sessions
            ):
                raise SessionClosingError("session is draining")
            session = self._sessions.get(session_id)
            if session is None:
                if not create:
                    raise KeyError(session_id)
                session = SessionInfo(session_id=session_id)
                self._sessions[session_id] = session
            else:
                session.last_active = datetime.now()
            self._active_operations[session_id] = self._active_operations.get(session_id, 0) + 1

        try:
            yield session
        finally:
            with self._cleanup_condition:
                remaining = self._active_operations.get(session_id, 1) - 1
                if remaining > 0:
                    self._active_operations[session_id] = remaining
                else:
                    self._active_operations.pop(session_id, None)
                self._cleanup_condition.notify_all()

    def active_dir_keys(self) -> set[str]:
        """Return on-disk keys that belong to in-memory active sessions."""
        with self._lock:
            active_ids = set(self._sessions) | self._cleanup_in_progress | self._draining_sessions
            return {self.session_dir_key(session_id) for session_id in active_ids}

    @contextmanager
    def writer_operation(self, session_id: str) -> Iterator[None]:
        """Serialize read-modify-write workflows inside one session."""
        with self._lock:
            if (
                session_id not in self._sessions
                or session_id in self._draining_sessions
                or (session_id in self._cleanup_in_progress and self._active_operations.get(session_id, 0) == 0)
            ):
                raise SessionClosingError("session is draining")
            writer_lock = self._writer_locks.setdefault(session_id, Lock())
        writer_lock.acquire()
        try:
            yield
        finally:
            writer_lock.release()

    def delete_orphan_dir_if_inactive(self, session_dir: Path) -> bool:
        """Delete one orphan candidate only if it is still inactive.

        The final activity check and atomic detach happen under the manager
        lock. Recursive deletion happens after the lock is released, so a new
        request may recreate the original path without being traversed by this
        cleanup.
        """
        detached = session_dir
        with self._lock:
            active_ids = set(self._sessions) | self._cleanup_in_progress | self._draining_sessions
            active_keys = {self.session_dir_key(session_id) for session_id in active_ids}
            if session_dir.name in active_keys:
                return False
            if not session_dir.exists():
                return False
            if not session_dir.name.startswith(".cleanup-"):
                detached = session_dir.with_name(f".cleanup-orphan-{session_dir.name}-{uuid.uuid4().hex}")
                try:
                    session_dir.replace(detached)
                except OSError:
                    return False

        try:
            shutil.rmtree(detached)
        except OSError:
            return False
        return True

    def _schedule_drain_retry(self, session_id: str) -> None:
        """Retry cleanup with bounded exponential backoff."""
        with self._lock:
            existing = self._drain_timers.pop(session_id, None)
            if existing:
                existing.cancel()
            attempt = self._cleanup_retry_attempts.get(session_id, 0) + 1
            self._cleanup_retry_attempts[session_id] = attempt
            retry_token = uuid.uuid4().hex
            self._cleanup_retry_tokens[session_id] = retry_token

            def retry() -> None:
                self.cleanup_session(session_id, _retry_token=retry_token)

            delay = min(0.1 * (2 ** min(attempt - 1, 6)), 5.0)
            timer = Timer(delay, retry)
            timer.daemon = True
            self._drain_timers[session_id] = timer
            timer.start()

    def add_file(self, session_id: str, file_path: str) -> bool:
        """记录 session 文件；关闭中的 session 拒绝接收新资源。"""
        with self._lock:
            if (
                session_id in self._draining_sessions
                or (session_id in self._cleanup_in_progress and self._active_operations.get(session_id, 0) == 0)
                or session_id not in self._sessions
            ):
                return False
            # 转换为绝对路径
            abs_path = str(Path(file_path).resolve())
            if abs_path not in self._sessions[session_id].uploaded_files:
                self._sessions[session_id].uploaded_files.append(abs_path)
                print("[Session] 已跟踪 session 文件")
            return True

    def add_job(self, session_id: str, job_id: str) -> bool:
        """记录 session 任务；关闭开始后不允许新 worker 越过清理边界。"""
        with self._lock:
            if (
                session_id in self._draining_sessions
                or (session_id in self._cleanup_in_progress and self._active_operations.get(session_id, 0) == 0)
                or session_id not in self._sessions
            ):
                return False
            if job_id not in self._sessions[session_id].job_ids:
                self._sessions[session_id].job_ids.append(job_id)
            return True

    def discard_file(self, session_id: str, file_path: str | Path) -> None:
        """Forget a rolled-back artifact without affecting other session data."""
        target = str(Path(file_path).resolve())
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.uploaded_files = [path for path in session.uploaded_files if path != target]
            session.kb_files = [path for path in session.kb_files if path != target]

    def discard_job(self, session_id: str, job_id: str) -> None:
        """Forget a job whose worker could not be started."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.job_ids = [existing for existing in session.job_ids if existing != job_id]

    def owns_file(self, session_id: str, file_path: str | Path) -> bool:
        """Return whether a tracked file belongs to ``session_id``."""
        target = str(Path(file_path).resolve())
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            return target in session.uploaded_files or target in session.kb_files

    def add_kb_file(self, session_id: str, kb_file_path: str) -> bool:
        """记录 session KB 文件；关闭中的 session 拒绝接收新资源。"""
        with self._lock:
            if (
                session_id in self._draining_sessions
                or (session_id in self._cleanup_in_progress and self._active_operations.get(session_id, 0) == 0)
                or session_id not in self._sessions
            ):
                return False
            abs_path = str(Path(kb_file_path).resolve())
            if abs_path not in self._sessions[session_id].kb_files:
                self._sessions[session_id].kb_files.append(abs_path)
                print("[Session] 已跟踪 session KB 文件")
            return True

    def get_kb_files(self, session_id: str) -> list[str]:
        """获取 session 的 KB 文件列表"""
        with self._lock:
            if session_id in self._sessions:
                return list(self._sessions[session_id].kb_files)
            return []

    @staticmethod
    def session_dir_key(session_id: str) -> str:
        """返回 session 的磁盘目录名（文件系统安全、无碰撞）。

        委托给 :func:`safe_session_key`。该 key 是完整 ``session_id`` 的
        SHA-256 引用，不包含 bearer capability 本身；不同 session 落在
        独立目录，且攻击者构造的 ``X-Session-ID`` 无法逃逸 sessions 根目录。

        历史实现使用 ``session_id[:12]``，而前端 ID 形如
        ``sess_<13位毫秒时间戳>_<随机>``，截断后仅保留 ``sess_`` + 7 位
        时间戳，导致同一 ~16.7 分钟窗口内的所有用户共用一个目录（KB /
        corrections / project 数据互相串档并可能被彼此清理误删）。
        """
        return safe_session_key(session_id)

    def get_session_kb_dir(self, session_id: str) -> Path:
        """获取 session 专属的 KB 输出目录，如果不存在则创建"""
        kb_session_dir = Path("data/knowledge_base/sessions") / self.session_dir_key(session_id)
        kb_session_dir.mkdir(parents=True, exist_ok=True)
        return kb_session_dir

    def get_session_als_dir(self, session_id: str) -> Path:
        """Return the session-owned ALS output directory."""
        als_session_dir = Path("data/output/sessions") / self.session_dir_key(session_id)
        als_session_dir.mkdir(parents=True, exist_ok=True)
        return als_session_dir

    def get_session_raw_dir(self, session_id: str) -> Path:
        """Return the session-owned raw upload directory."""
        raw_session_dir = Path("data/raw/sessions") / self.session_dir_key(session_id)
        raw_session_dir.mkdir(parents=True, exist_ok=True)
        return raw_session_dir

    def get_session_processed_dir(self, session_id: str) -> Path:
        """Return the session-owned processed mapping directory."""
        processed_session_dir = Path("data/processed/sessions") / self.session_dir_key(session_id)
        processed_session_dir.mkdir(parents=True, exist_ok=True)
        return processed_session_dir

    def get_session_recommendation_job_dir(self, session_id: str, job_id: str) -> Path:
        """Return a private input-snapshot directory for one recommendation job."""
        job_dir = self.get_session_processed_dir(session_id) / "jobs" / self.session_dir_key(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

    def get_session_spec_job_dir(self, session_id: str, job_id: str) -> Path:
        """Return an isolated directory for one session-owned Spec job."""
        job_dir = Path("data/spec_output/sessions") / self.session_dir_key(session_id) / self.session_dir_key(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

    def _managed_session_dirs(self, session_id: str) -> tuple[Path, ...]:
        """Return every clinical-data directory owned by one session."""
        key = self.session_dir_key(session_id)
        return (
            Path("data/knowledge_base/sessions") / key,
            Path("data/raw/sessions") / key,
            Path("data/processed/sessions") / key,
            Path("data/output/sessions") / key,
            Path("data/spec_output/sessions") / key,
            Path("data/audit_logs/sessions") / key,
        )

    def _is_managed_session_path(self, session_id: str, file_path: str | Path) -> bool:
        target = Path(file_path).resolve()
        return any(target.is_relative_to(root.resolve()) for root in self._managed_session_dirs(session_id))

    def _clean_session_output_dirs(self, session_id: str) -> bool:
        """Atomically detach and remove every directory owned by ``session_id``.

        Failed recursive deletes leave detached tombstones. A later cleanup
        retries those tombstones as well as any still-attached directory.
        """
        success = True
        for session_dir in self._managed_session_dirs(session_id):
            detached_dirs = (
                list(session_dir.parent.glob(f".cleanup-{session_dir.name}-*")) if session_dir.parent.exists() else []
            )
            if session_dir.exists():
                detached = session_dir.with_name(f".cleanup-{session_dir.name}-{uuid.uuid4().hex}")
                try:
                    session_dir.replace(detached)
                    detached_dirs.append(detached)
                except OSError:
                    success = False
            for detached in detached_dirs:
                try:
                    shutil.rmtree(detached)
                except OSError:
                    success = False
        return success

    @staticmethod
    def _noop_cleanup_result() -> dict[str, Any]:
        return {
            "cleaned_files": 0,
            "cleaned_session_dir": True,
            "cleaned_jobs": 0,
            "deferred_jobs": 0,
            "cleanup_pending": False,
            "errors": [],
        }

    def cleanup_session(self, session_id: str, *, _retry_token: str | None = None) -> dict[str, Any]:
        """
        清理指定 session 的所有文件和任务：
        - 删除已追踪的普通文件
        - 原子摘除并删除所有 session 专属数据目录
        - 取消任务并等待后台 worker 退出
        """
        with self._cleanup_condition:
            if _retry_token is not None and (
                self._cleanup_retry_tokens.get(session_id) != _retry_token or session_id not in self._draining_sessions
            ):
                return self._noop_cleanup_result()
            while session_id in self._cleanup_in_progress:
                self._cleanup_condition.wait()
                if _retry_token is not None and (
                    self._cleanup_retry_tokens.get(session_id) != _retry_token
                    or session_id not in self._draining_sessions
                ):
                    return self._noop_cleanup_result()
            pending_cleanup = self._pending_cleanups.pop(session_id, None)
            if pending_cleanup:
                pending_cleanup.cancel()
            drain_retry = self._drain_timers.pop(session_id, None)
            if drain_retry:
                drain_retry.cancel()
            if _retry_token is None:
                # Manual/concurrent cleanup supersedes any delayed retry that
                # may already be waking up.
                self._cleanup_retry_tokens.pop(session_id, None)
            # An exact header/body cleanup request retires its bearer even when
            # no in-memory generation exists yet. This closes the stale retry
            # window for a request whose body was still arriving.
            self._retire_session_locked(session_id)
            self._cleanup_in_progress.add(session_id)
            while self._active_operations.get(session_id, 0) > 0:
                self._cleanup_condition.wait()

        errors: list[str] = []
        cleaned_files = 0
        cleaned_jobs = 0
        cleaned_session_dir = True
        deferred_cleanup = False
        try:
            # Closing is visible before the job snapshot. ``add_job`` now
            # rejects late arrivals, while a job registered just before this
            # boundary is included below.
            with self._lock:
                session = self._sessions.get(session_id)
                jobs_to_clean = list(session.job_ids) if session else []

            # A session can close while a worker is inside an LLM call or
            # workbook write. Cancel first and defer deletion until every
            # registered worker exits.
            active_jobs: list[str] = []
            if self._job_manager:
                active_jobs = [job_id for job_id in jobs_to_clean if self._job_manager.has_active_worker(job_id)]
                for job_id in active_jobs:
                    self._job_manager.cancel_job(job_id)
            if active_jobs:
                with self._lock:
                    self._draining_sessions.add(session_id)
                deferred_cleanup = True
                self._schedule_drain_retry(session_id)
                return {
                    "cleaned_files": 0,
                    "cleaned_session_dir": False,
                    "cleaned_jobs": 0,
                    "deferred_jobs": len(active_jobs),
                    "cleanup_pending": True,
                    "errors": [],
                }

            with self._lock:
                session = self._sessions.get(session_id)
                files_to_clean = list(session.uploaded_files) if session else []
                jobs_to_clean = list(session.job_ids) if session else []

            # All request leases and background workers have drained. Detach
            # every managed root before recursively deleting it.
            if not self._clean_session_output_dirs(session_id):
                cleaned_session_dir = False
                errors.append("session_output_dirs_delete_failed")
                with self._lock:
                    self._draining_sessions.add(session_id)
                deferred_cleanup = True
                self._schedule_drain_retry(session_id)
                return {
                    "cleaned_files": 0,
                    "cleaned_session_dir": False,
                    "cleaned_jobs": 0,
                    "deferred_jobs": 0,
                    "cleanup_pending": True,
                    "errors": errors,
                }

            for file_path in files_to_clean:
                if self._is_managed_session_path(session_id, file_path):
                    continue
                try:
                    path = Path(file_path)
                    if path.exists():
                        path.unlink()
                        cleaned_files += 1
                        print("[Session] 已删除已跟踪文件")
                except OSError as exc:
                    errors.append(f"tracked_file_delete_failed:{type(exc).__name__}")
                    print(f"[Session] 删除已跟踪文件失败 ({type(exc).__name__})")

            if errors:
                with self._lock:
                    self._draining_sessions.add(session_id)
                deferred_cleanup = True
                self._schedule_drain_retry(session_id)
                return {
                    "cleaned_files": cleaned_files,
                    "cleaned_session_dir": cleaned_session_dir,
                    "cleaned_jobs": 0,
                    "deferred_jobs": 0,
                    "cleanup_pending": True,
                    "errors": errors,
                }

            if self._job_manager:
                for job_id in jobs_to_clean:
                    if self._job_manager.remove_job(job_id):
                        cleaned_jobs += 1
            with self._lock:
                self._sessions.pop(session_id, None)
                self._writer_locks.pop(session_id, None)
        finally:
            with self._cleanup_condition:
                self._cleanup_in_progress.discard(session_id)
                if not deferred_cleanup:
                    self._draining_sessions.discard(session_id)
                    self._cleanup_retry_attempts.pop(session_id, None)
                    self._cleanup_retry_tokens.pop(session_id, None)
                self._cleanup_condition.notify_all()

        return {
            "cleaned_files": cleaned_files,
            "cleaned_session_dir": cleaned_session_dir,
            "cleaned_jobs": cleaned_jobs,
            "deferred_jobs": 0,
            "cleanup_pending": False,
            "errors": errors,
        }

    def schedule_cleanup(self, session_id: str) -> bool:
        """
        安排延迟清理。用于页面关闭时，给刷新操作留出取消窗口。
        即使 session 不在内存中，也会尝试清理目录。
        返回 True 表示已安排清理。
        """
        with self._lock:
            # 如果已有待执行的清理，先取消它
            if session_id in self._pending_cleanups:
                self._pending_cleanups[session_id].cancel()
                del self._pending_cleanups[session_id]

            # 创建延迟执行的定时器
            def do_cleanup():
                with self._lock:
                    # 从待执行列表中移除
                    self._pending_cleanups.pop(session_id, None)
                # 执行实际清理（在锁外）
                print(f"[Session] 延迟时间到，执行清理: {self.session_dir_key(session_id)[:16]}")
                self.cleanup_session(session_id)

            timer = Timer(self._cleanup_delay, do_cleanup)
            timer.daemon = True
            self._pending_cleanups[session_id] = timer
            timer.start()

            in_memory = session_id in self._sessions
            print(
                f"[Session] 已安排延迟清理: {self.session_dir_key(session_id)[:16]} "
                f"({self._cleanup_delay}秒后执行, 内存中={'是' if in_memory else '否'})"
            )
            return True

    def cancel_cleanup(self, session_id: str) -> bool:
        """
        取消待执行的延迟清理。用于页面刷新时。
        返回 True 表示已取消，False 表示没有待执行的清理。
        """
        with self._lock:
            cancelled = False
            if session_id in self._pending_cleanups:
                self._pending_cleanups[session_id].cancel()
                del self._pending_cleanups[session_id]
                cancelled = True
            if cancelled:
                print(f"[Session] 已取消延迟清理: {self.session_dir_key(session_id)[:16]} (可能是页面刷新)")
            return cancelled

    def cleanup_expired(self) -> dict[str, int]:
        """清理所有过期的 session（用于定时任务）"""
        with self._lock:
            now = datetime.now()
            expired_ids = [
                sid
                for sid, info in self._sessions.items()
                if now - info.last_active > timedelta(hours=self._expire_hours)
            ]

        total_files = 0
        total_jobs = 0
        for sid in expired_ids:
            result = self.cleanup_session(sid)
            total_files += result["cleaned_files"]
            total_jobs += result["cleaned_jobs"]

        return {"expired_sessions": len(expired_ids), "cleaned_files": total_files, "cleaned_jobs": total_jobs}

    def get_session_info(self, session_id: str, include_files: bool = False) -> dict[str, Any] | None:
        """获取 session 信息"""
        with self._lock:
            if session_id not in self._sessions:
                return None
            session = self._sessions[session_id]
            info = {
                "session_id": session.session_id,
                "created_at": session.created_at.isoformat(),
                "last_active": session.last_active.isoformat(),
                "files_count": len(session.uploaded_files),
                "kb_files_count": len(session.kb_files),
                "jobs_count": len(session.job_ids),
            }
            if include_files:
                # API consumers only need display names. Absolute server paths
                # remain internal for cleanup and must not cross the boundary.
                info["files"] = [Path(path).name for path in session.uploaded_files]
                info["kb_files"] = [Path(path).name for path in session.kb_files]
                info["jobs"] = list(session.job_ids)
            return info

    def get_stats(self) -> dict[str, Any]:
        """获取 SessionManager 统计信息"""
        with self._lock:
            total_files = sum(len(s.uploaded_files) for s in self._sessions.values())
            total_kb_files = sum(len(s.kb_files) for s in self._sessions.values())
            total_jobs = sum(len(s.job_ids) for s in self._sessions.values())
            return {
                "active_sessions": len(self._sessions),
                "total_tracked_files": total_files,
                "total_tracked_kb_files": total_kb_files,
                "total_tracked_jobs": total_jobs,
            }


# 全局单例
session_manager = SessionManager(expire_hours=24)


def cleanup_orphaned_session_dirs(max_age_hours: float = 8.0) -> dict[str, int]:
    """
    清理过期的 session 目录（基于目录修改时间）。

    Args:
        max_age_hours: 目录最大存活时间（小时）。超过此时间未修改的目录会被清理。
                       默认 8 小时，避免服务器热重载时误删活跃用户的数据。

    Returns:
        包含清理统计信息的字典
    """
    session_bases = (
        Path("data/knowledge_base/sessions"),
        Path("data/raw/sessions"),
        Path("data/processed/sessions"),
        Path("data/output/sessions"),
        Path("data/spec_output/sessions"),
        Path("data/audit_logs/sessions"),
    )
    if not any(base.exists() for base in session_bases):
        return {"cleaned_dirs": 0, "skipped_dirs": 0}

    cleaned_dirs = 0
    skipped_dirs = 0
    now = time.time()
    max_age_seconds = max_age_hours * 3600
    active_keys = session_manager.active_dir_keys()

    for sessions_base in session_bases:
        if not sessions_base.exists():
            continue
        for session_dir in sessions_base.iterdir():
            if not session_dir.is_dir():
                continue

            try:
                if session_dir.name in active_keys:
                    skipped_dirs += 1
                    continue

                # Parent directory mtime does not change when an existing child
                # file is updated. Use the newest descendant so a recently
                # written checkpoint survives a post-restart orphan scan.
                mtime = session_dir.stat().st_mtime
                for descendant in session_dir.rglob("*"):
                    try:
                        mtime = max(mtime, descendant.stat().st_mtime)
                    except OSError:
                        continue
                age_seconds = now - mtime

                if age_seconds < max_age_seconds:
                    # 目录还很新，可能是活跃用户，跳过
                    skipped_dirs += 1
                    continue

                if session_manager.delete_orphan_dir_if_inactive(session_dir):
                    cleaned_dirs += 1
                    age_hours = age_seconds / 3600
                    print(f"[SessionCleanup] 清理 session 目录: {session_dir.name} (空闲 {age_hours:.1f}h)")
                else:
                    skipped_dirs += 1
            except OSError as exc:
                print(f"[SessionCleanup] 清理失败: {session_dir.name} ({type(exc).__name__})")

    return {"cleaned_dirs": cleaned_dirs, "skipped_dirs": skipped_dirs}


def start_cleanup_scheduler() -> None:
    """启动后台清理线程，定期清理过期 session"""
    # 启动时只清理超过 8 小时的旧目录，避免热重载时误删活跃用户数据
    result = cleanup_orphaned_session_dirs(max_age_hours=8.0)
    if result["cleaned_dirs"] > 0 or result["skipped_dirs"] > 0:
        print(f"[Startup] Session 目录清理: 删除 {result['cleaned_dirs']} 个, 保留 {result['skipped_dirs']} 个 (<8h)")

    def cleanup_loop():
        while True:
            time.sleep(3600)  # 每小时检查一次
            try:
                result = session_manager.cleanup_expired()
                if result["expired_sessions"] > 0:
                    print(
                        f"[SessionCleanup] 清理了 {result['expired_sessions']} 个过期会话, "
                        f"{result['cleaned_files']} 个文件, {result['cleaned_jobs']} 个任务"
                    )

                # 定时清理超过 8 小时的 session 目录
                orphan_result = cleanup_orphaned_session_dirs(max_age_hours=8.0)
                if orphan_result["cleaned_dirs"] > 0:
                    print(f"[SessionCleanup] 清理了 {orphan_result['cleaned_dirs']} 个过期目录")
            except Exception as exc:
                print(f"[SessionCleanup] 清理出错 ({type(exc).__name__})")

    thread = threading.Thread(target=cleanup_loop, daemon=True, name="SessionCleanupThread")
    thread.start()
