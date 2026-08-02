"""Filesystem-safe, non-reversible key derivation for client session ids.

Client-supplied ``X-Session-ID`` values are used to name per-session KB
directories and audit-log files. The raw bearer capability must not appear in
paths or logs, and a hostile header must never traverse the filesystem.
"""

from __future__ import annotations

import hashlib


def safe_session_key(session_id: str) -> str:
    """Return a filesystem-safe, collision-resistant key for ``session_id``.

    A full SHA-256 digest keeps the bearer value out of directory names, audit
    filenames, and cleanup logs while providing deterministic isolation. The
    result contains only a fixed ``sid_`` prefix and lowercase hex.

    Raises:
        ValueError: if ``session_id`` is empty or not a string.
    """
    if not session_id or not isinstance(session_id, str):
        raise ValueError("session_id must be a non-empty string")
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return f"sid_{digest}"


__all__ = ["safe_session_key"]
