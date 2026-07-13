"""Session 管理模块 - 用于隔离不同用户的文件和任务"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock, Timer
from typing import Any

from src.infrastructure.session_key import safe_session_key


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
        self._expire_hours = expire_hours
        self._cleanup_delay = cleanup_delay  # 延迟清理的秒数（给刷新操作留出取消窗口）
        self._job_manager = None  # 延迟设置，避免循环导入
        self._pending_cleanups: dict[str, Timer] = {}  # 待执行的延迟清理

    def set_job_manager(self, job_manager) -> None:
        """设置 job_manager 引用"""
        self._job_manager = job_manager

    def get_or_create(self, session_id: str) -> SessionInfo:
        """获取或创建 session"""
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionInfo(session_id=session_id)
            else:
                self._sessions[session_id].last_active = datetime.now()
            return self._sessions[session_id]

    def add_file(self, session_id: str, file_path: str) -> None:
        """记录文件归属于某个 session（存储绝对路径以确保清理时能找到文件）"""
        with self._lock:
            if session_id in self._sessions:
                # 转换为绝对路径
                abs_path = str(Path(file_path).resolve())
                if abs_path not in self._sessions[session_id].uploaded_files:
                    self._sessions[session_id].uploaded_files.append(abs_path)
                    print(f"[Session] 跟踪文件: {abs_path} -> session {session_id[:12]}...")

    def add_job(self, session_id: str, job_id: str) -> None:
        """记录任务归属于某个 session"""
        with self._lock:
            if session_id in self._sessions and job_id not in self._sessions[session_id].job_ids:
                self._sessions[session_id].job_ids.append(job_id)

    def add_kb_file(self, session_id: str, kb_file_path: str) -> None:
        """记录 KB 文件归属于某个 session（存储绝对路径）"""
        with self._lock:
            if session_id in self._sessions:
                abs_path = str(Path(kb_file_path).resolve())
                if abs_path not in self._sessions[session_id].kb_files:
                    self._sessions[session_id].kb_files.append(abs_path)
                    print(f"[Session] 跟踪 KB 文件: {abs_path} -> session {session_id[:12]}...")

    def get_kb_files(self, session_id: str) -> list[str]:
        """获取 session 的 KB 文件列表"""
        with self._lock:
            if session_id in self._sessions:
                return list(self._sessions[session_id].kb_files)
            return []

    @staticmethod
    def session_dir_key(session_id: str) -> str:
        """返回 session 的磁盘目录名（文件系统安全、无碰撞）。

        委托给 :func:`safe_session_key`。该 key 是完整 ``session_id`` 的纯
        函数（**不截断**）且始终附带原始 id 的哈希后缀，因此不同 session
        永远落在不同目录，即便清洗后的可读前缀相同（如 ``"a/b"`` 与
        ``"ab"``）；同时剥离路径分隔符与 ``..`` 穿越序列，使得攻击者构造的
        ``X-Session-ID`` 无法逃逸出 sessions 根目录。

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

    def _clean_session_files(self, session_id: str) -> bool:
        """
        删除 session 目录下的临时文件（excel/json/parquet），目录本身保留。
        返回 True 表示清理完成（目录可能仍然存在但已无目标文件）。
        """
        kb_session_dir = Path("data/knowledge_base/sessions") / self.session_dir_key(session_id)
        if not kb_session_dir.exists():
            return True

        patterns = ["*.xlsx", "*.xls", "*.json", "*.parquet"]
        success = True
        for pattern in patterns:
            for f in kb_session_dir.glob(pattern):
                try:
                    f.unlink()
                    print(f"[Session] 已删除 session 文件: {f}")
                except Exception as e:
                    success = False
                    print(f"[Session] 删除 session 文件失败: {f} - {e}")
        return success

    def cleanup_session(self, session_id: str) -> dict[str, Any]:
        """
        清理指定 session 的所有文件和任务：
        - 删除已追踪的普通文件
        - 删除 sessions/{session_dir_key} 下的 excel/json/parquet
        - 目录保留（由定时/启动清理处理）
        """
        with self._lock:
            if session_id not in self._sessions:
                print(f"[Session] 清理请求: session {session_id[:12]}... 不存在")
                cleaned_session_dir = self._clean_session_files(session_id)
                return {
                    "cleaned_files": 0,
                    "cleaned_session_dir": cleaned_session_dir,
                    "cleaned_jobs": 0,
                    "errors": [] if cleaned_session_dir else ["session_dir_files_delete_failed"],
                }

            session = self._sessions.pop(session_id)
            files_to_clean = list(session.uploaded_files)
            jobs_to_clean = list(session.job_ids)

        print(
            f"[Session] 开始清理 session {session_id[:12]}...: "
            f"{len(files_to_clean)} 个普通文件, {len(jobs_to_clean)} 个任务"
        )

        errors = []
        cleaned_files = 0

        # 清理普通上传文件（非 session 目录下的文件，如 data/processed/, data/output/）
        for file_path in files_to_clean:
            try:
                path = Path(file_path)
                if path.exists():
                    path.unlink()
                    cleaned_files += 1
                    print(f"[Session] 已删除: {file_path}")
            except Exception as e:
                errors.append(f"{file_path}: {e}")
                print(f"[Session] 删除失败: {file_path} - {e}")

        # 删除 session 目录下的 excel/json/parquet 文件（目录保留）
        cleaned_session_dir = self._clean_session_files(session_id)
        if not cleaned_session_dir:
            errors.append("session_dir_files_delete_failed")

        # 清理该 session 的 job 状态
        cleaned_jobs = 0
        if self._job_manager:
            for job_id in jobs_to_clean:
                if self._job_manager.remove_job(job_id):
                    cleaned_jobs += 1

        print(
            f"[Session] 清理完成: {cleaned_files} 个文件, session目录={'已删除' if cleaned_session_dir else '未删除'}, "
            f"{cleaned_jobs} 个任务, {len(errors)} 个错误"
        )

        return {
            "cleaned_files": cleaned_files,
            "cleaned_session_dir": cleaned_session_dir,
            "cleaned_jobs": cleaned_jobs,
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
                print(f"[Session] 延迟时间到，执行清理: {session_id[:12]}...")
                self.cleanup_session(session_id)

            timer = Timer(self._cleanup_delay, do_cleanup)
            timer.daemon = True
            self._pending_cleanups[session_id] = timer
            timer.start()

            in_memory = session_id in self._sessions
            print(
                f"[Session] 已安排延迟清理: {session_id[:12]}... ({self._cleanup_delay}秒后执行, 内存中={'是' if in_memory else '否'})"
            )
            return True

    def cancel_cleanup(self, session_id: str) -> bool:
        """
        取消待执行的延迟清理。用于页面刷新时。
        返回 True 表示已取消，False 表示没有待执行的清理。
        """
        with self._lock:
            if session_id in self._pending_cleanups:
                self._pending_cleanups[session_id].cancel()
                del self._pending_cleanups[session_id]
                print(f"[Session] 已取消延迟清理: {session_id[:12]}... (可能是页面刷新)")
                return True
            return False

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
    sessions_base = Path("data/knowledge_base/sessions")
    if not sessions_base.exists():
        return {"cleaned_dirs": 0, "skipped_dirs": 0}

    cleaned_dirs = 0
    skipped_dirs = 0
    now = time.time()
    max_age_seconds = max_age_hours * 3600

    for session_dir in sessions_base.iterdir():
        if not session_dir.is_dir():
            continue

        try:
            # 检查目录的最后修改时间
            mtime = session_dir.stat().st_mtime
            age_seconds = now - mtime

            if age_seconds < max_age_seconds:
                # 目录还很新，可能是活跃用户，跳过
                skipped_dirs += 1
                continue

            shutil.rmtree(session_dir)
            cleaned_dirs += 1
            age_hours = age_seconds / 3600
            print(f"[SessionCleanup] 清理 session 目录: {session_dir.name} (空闲 {age_hours:.1f}h)")
        except Exception as e:
            print(f"[SessionCleanup] 清理失败: {session_dir} - {e}")

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
            except Exception as e:
                print(f"[SessionCleanup] 清理出错: {e}")

    thread = threading.Thread(target=cleanup_loop, daemon=True, name="SessionCleanupThread")
    thread.start()
