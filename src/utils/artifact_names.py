"""Safe, collision-resistant names for model-scoped artifacts."""

from __future__ import annotations

import hashlib
import re

_UNSAFE_ARTIFACT_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_REPEATED_SEPARATORS = re.compile(r"[_-]{2,}")


def model_artifact_slug(model_name: str) -> str:
    """Return a bounded filename token tied to the complete model identity."""
    if not isinstance(model_name, str) or not model_name.strip():
        raise ValueError("model_name must be a non-empty string")
    normalized = _UNSAFE_ARTIFACT_CHARS.sub("_", model_name.strip())
    normalized = _REPEATED_SEPARATORS.sub("_", normalized).strip("._-") or "model"
    digest = hashlib.sha256(model_name.encode("utf-8")).hexdigest()[:24]
    return f"{normalized[:40]}-{digest}"


__all__ = ["model_artifact_slug"]
