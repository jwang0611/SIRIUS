"""Filesystem-safe, collision-free key derivation for client session ids.

Client-supplied ``X-Session-ID`` values are used to name per-session KB
directories and audit-log files. They must be sanitised so a hostile header
cannot traverse the filesystem, and the mapping must be *injective* so two
distinct session ids never share a key — otherwise a crafted header could
land in another session's directory (breaking isolation) or collapse two
sessions' audit trails into one file.
"""

from __future__ import annotations

import hashlib
import re

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def safe_session_key(session_id: str) -> str:
    """Return a filesystem-safe, collision-free key for ``session_id``.

    The key always embeds a hex digest of the *raw* ``session_id``, so two
    distinct ids never collide even when their sanitised prefixes are equal
    (e.g. ``"a/b"`` vs ``"ab"``, ``"a..b"`` vs ``"ab"`` — both would otherwise
    reduce to ``"ab"``). A stripped, human-readable prefix is kept for
    debuggability. The result contains only ``[A-Za-z0-9_.-]`` with no ``..``
    sequence and no leading dot, so it can never escape its parent directory.

    Raises:
        ValueError: if ``session_id`` is empty or not a string.
    """
    if not session_id or not isinstance(session_id, str):
        raise ValueError("session_id must be a non-empty string")
    cleaned = _UNSAFE.sub("", session_id.strip()).replace("..", "").lstrip(".")
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
    prefix = cleaned[:40]
    return f"{prefix}_{digest}" if prefix else f"sid_{digest}"


__all__ = ["safe_session_key"]
