"""Atomic binary publication regressions."""

from __future__ import annotations

import threading
from io import BytesIO
from pathlib import Path

import pytest

from src.utils import atomic_file


def test_binary_copy_keeps_previous_destination_visible_until_replace(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "upload.xlsx"
    destination.write_bytes(b"old-complete")
    replace_entered = threading.Event()
    allow_replace = threading.Event()
    real_replace = atomic_file.os.replace

    def paused_replace(source, target):
        replace_entered.set()
        assert allow_replace.wait(timeout=2)
        real_replace(source, target)

    monkeypatch.setattr(atomic_file.os, "replace", paused_replace)
    writer = threading.Thread(
        target=atomic_file.atomic_copy_fileobj,
        args=(BytesIO(b"new-complete"), destination),
    )
    writer.start()
    try:
        assert replace_entered.wait(timeout=1)
        assert destination.read_bytes() == b"old-complete"
        staging = list(tmp_path.glob(".upload.xlsx.*.part"))
        assert len(staging) == 1
        assert staging[0].read_bytes() == b"new-complete"
    finally:
        allow_replace.set()
        writer.join(timeout=2)

    assert not writer.is_alive()
    assert destination.read_bytes() == b"new-complete"
    assert not list(tmp_path.glob("*.part"))


def test_snapshot_copy_failure_preserves_old_destination_and_cleans_staging(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.xlsx"
    destination = tmp_path / "snapshot.xlsx"
    source.write_bytes(b"new-source")
    destination.write_bytes(b"old-complete")

    def fail_after_partial_write(_source, target, *_args, **_kwargs):
        target.write(b"partial")
        target.flush()
        raise OSError("synthetic read failure")

    monkeypatch.setattr(atomic_file.shutil, "copyfileobj", fail_after_partial_write)

    with pytest.raises(OSError, match="synthetic read failure"):
        atomic_file.atomic_snapshot_file(source, destination)

    assert destination.read_bytes() == b"old-complete"
    assert not list(tmp_path.glob(".snapshot.xlsx.*.part"))
