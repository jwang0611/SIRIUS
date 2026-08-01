"""Atomic helpers for binary uploads, snapshots, and writer-managed artifacts."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


@contextmanager
def atomic_staging_path(destination: str | Path) -> Iterator[Path]:
    """Yield a same-directory staging path, then fsync and atomically replace."""
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".part",
    )
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    try:
        yield temporary
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_copy_fileobj(source: BinaryIO, destination: str | Path) -> Path:
    """Copy a binary stream without exposing a partially written destination."""
    target = Path(destination)
    with atomic_staging_path(target) as temporary, temporary.open("wb") as stream:
        shutil.copyfileobj(source, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return target


def atomic_snapshot_file(source: str | Path, destination: str | Path) -> Path:
    """Create a complete, private snapshot of an existing file."""
    source_path = Path(source)
    target = Path(destination)
    with source_path.open("rb") as stream:
        return atomic_copy_fileobj(stream, target)


__all__ = ["atomic_copy_fileobj", "atomic_snapshot_file", "atomic_staging_path"]
