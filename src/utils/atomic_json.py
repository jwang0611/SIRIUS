"""Durable, atomic JSON writes for local artifacts."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: str | Path, payload: Any, *, indent: int | None = 2) -> None:
    """Serialize *payload* and atomically replace *path*.

    The staging file lives beside the destination so ``os.replace`` stays on
    the same filesystem. Flushing and syncing the staging file before the
    replace prevents readers from observing a partially-written JSON document.
    """

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".part",
    )
    temporary = Path(temporary_name)

    try:
        stream = os.fdopen(file_descriptor, "w", encoding="utf-8")
        file_descriptor = -1
        with stream:
            json.dump(payload, stream, ensure_ascii=False, indent=indent)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        temporary.unlink(missing_ok=True)
