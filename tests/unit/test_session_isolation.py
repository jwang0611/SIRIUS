"""Regression tests for session directory isolation.

The historical implementation keyed each session's on-disk KB directory on
``session_id[:12]``. Front-end session IDs look like
``sess_<13-digit-ms-timestamp>_<random>``, so the 12-char prefix collapses to
``sess_`` + 7 timestamp digits — identical for every user inside the same
~16.7-minute window. That made distinct users share one directory, leaking
project KB / corrections across sessions and letting one session's cleanup
delete another's files.

These tests lock in the fix: the directory key is a hash-only pure function of
the *full* session_id (no truncation), so it is filesystem-safe and does not
expose the bearer capability.
"""

from __future__ import annotations

import pytest

from src.web.session_manager import SessionManager


def _key(session_id: str) -> str:
    return SessionManager.session_dir_key(session_id)


def test_same_12char_prefix_does_not_collide():
    """Two IDs that share the old ``[:12]`` prefix must map to different keys."""
    a = "sess_1751900000000_aaaaaaaaa"
    b = "sess_1751900999999_bbbbbbbbb"
    # Precondition: the old truncation scheme WOULD have collided.
    assert a[:12] == b[:12] == "sess_1751900"
    # Fixed scheme: distinct sessions get distinct directory keys.
    assert _key(a) != _key(b)


def test_key_is_stable_for_same_id():
    sid = "sess_1751900000000_abcdef123"
    assert _key(sid) == _key(sid)


def test_bearer_value_is_not_exposed_in_directory_key():
    sid = "sess_1751900000000_abcdef123"
    key = _key(sid)
    assert key.startswith("sid_")
    assert sid not in key
    assert len(key) == 68


def test_sanitized_forms_do_not_collide():
    """Distinct raw IDs whose *sanitised* forms are identical must still map
    to different keys — the collision the reviewer flagged. The hash suffix is
    derived from the raw id, so equal prefixes still yield different keys."""
    assert _key("abc") != _key("a/bc")  # both strip to "abc"
    assert _key("ab") != _key("a..b")  # "a..b" -> "ab" after ".." removal
    assert _key("ab") != _key("a/b")  # "a/b" -> "ab"
    assert _key("abc").startswith("sid_")
    assert _key("a/bc").startswith("sid_")


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../../etc/passwd",
        "..\\..\\windows\\system32",
        "....//....//secret",
        "/absolute/path",
        "a/../../b",
    ],
)
def test_traversal_ids_are_neutralised(hostile: str):
    key = _key(hostile)
    # No path separators or traversal sequences survive.
    assert "/" not in key
    assert "\\" not in key
    assert ".." not in key
    assert not key.startswith(".")


def test_empty_or_punctuation_only_id_falls_back_to_hash():
    # Nothing safe survives sanitisation -> stable hash, still isolated.
    key = _key("////")
    assert key.startswith("sid_")
    assert key == _key("////")  # stable


def test_blank_id_rejected():
    with pytest.raises(ValueError):
        _key("")


def test_overlong_id_is_bounded_but_unique():
    long_a = "sess_" + ("x" * 200) + "_a"
    long_b = "sess_" + ("x" * 200) + "_b"
    key_a, key_b = _key(long_a), _key(long_b)
    assert len(key_a) == 68
    # Bounded length must not reintroduce collisions between distinct IDs.
    assert key_a != key_b


def test_get_session_kb_dir_isolates_colliding_prefixes(tmp_path, monkeypatch):
    """End-to-end: colliding-prefix sessions get distinct real directories,
    both contained within the sessions base."""
    monkeypatch.chdir(tmp_path)
    mgr = SessionManager()

    a = "sess_1751900000000_aaaaaaaaa"
    b = "sess_1751900999999_bbbbbbbbb"
    dir_a = mgr.get_session_kb_dir(a)
    dir_b = mgr.get_session_kb_dir(b)

    assert dir_a != dir_b
    assert dir_a.is_dir() and dir_b.is_dir()

    sessions_base = (tmp_path / "data" / "knowledge_base" / "sessions").resolve()
    assert dir_a.resolve().parent == sessions_base
    assert dir_b.resolve().parent == sessions_base


def test_get_session_kb_dir_traversal_stays_within_base(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mgr = SessionManager()
    sessions_base = (tmp_path / "data" / "knowledge_base" / "sessions").resolve()

    hostile_dir = mgr.get_session_kb_dir("../../../../etc/evil").resolve()
    # The created directory must remain inside the sessions base.
    assert str(hostile_dir).startswith(str(sessions_base))
    assert hostile_dir.parent == sessions_base
