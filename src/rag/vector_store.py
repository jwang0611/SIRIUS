"""
Utilities for caching and reusing RAG embeddings.

The goal of this module is to avoid rebuilding embeddings every time the
retriever runs by persisting them to disk.  Embeddings can be reloaded as long
as the underlying knowledge base has not changed.
"""

from __future__ import annotations

import hashlib
import logging
import pickle
import time
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def _compute_kb_signature(kb_path: str) -> str:
    """Compute a hash signature for the knowledge base directory.

    The signature is based on file paths, modification times, and sizes.  It is
    used to invalidate cached embeddings when the knowledge base changes.
    """

    kb_root = Path(kb_path).resolve()
    if not kb_root.exists():
        raise FileNotFoundError(f"Knowledge base path does not exist: {kb_root}")

    entries: list[str] = []
    for file_path in sorted(kb_root.rglob("*.parquet")):
        try:
            stat = file_path.stat()
        except OSError:
            continue
        entries.append(f"{file_path.relative_to(kb_root)}::{int(stat.st_mtime)}::{stat.st_size}")

    payload = "|".join(entries).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class RAGVectorStore:
    """Simple on-disk cache for RAG embedding documents."""

    def __init__(self, cache_dir: str | None = None):
        default_cache = Path("data/cache/rag_vectors")
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, kb_path: str, model: str) -> str:
        normalized = f"{Path(kb_path).resolve()}::{model}"
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _cache_file(self, kb_path: str, model: str) -> Path:
        key = self._cache_key(kb_path, model)
        return self.cache_dir / f"{key}.pkl"

    def load(self, kb_path: str, model: str) -> list[dict[str, Any]] | None:
        """Load cached embedding docs if present and still valid."""

        cache_file = self._cache_file(kb_path, model)
        if not cache_file.exists():
            return None

        try:
            with cache_file.open("rb") as fh:
                payload = pickle.load(fh)
        except (pickle.UnpicklingError, EOFError, ValueError) as exc:
            logger.warning("Corrupt RAG cache file %s, will rebuild: %s", cache_file, exc)
            return None
        except Exception as exc:
            logger.warning("Failed to load RAG cache %s: %s", cache_file, exc)
            return None

        kb_signature = payload.get("kb_signature")
        try:
            current_signature = _compute_kb_signature(kb_path)
        except FileNotFoundError:
            return None

        if kb_signature != current_signature:
            return None

        docs = payload.get("docs") or []
        normalized_docs: list[dict[str, Any]] = []
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            embedding = doc.get("embedding")
            if embedding is not None and not isinstance(embedding, np.ndarray):
                embedding = np.array(embedding, dtype=np.float32)
                doc = dict(doc)
                doc["embedding"] = embedding
            normalized_docs.append(doc)

        return normalized_docs

    def save(self, kb_path: str, model: str, docs: list[dict[str, Any]]) -> None:
        """Persist embedding docs together with metadata."""

        if not docs:
            return

        try:
            kb_signature = _compute_kb_signature(kb_path)
        except FileNotFoundError:
            return

        serializable_docs: list[dict[str, Any]] = []
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            record = dict(doc)
            emb = record.get("embedding")
            if isinstance(emb, np.ndarray):
                record["embedding"] = emb.astype(np.float32).tolist()
            serializable_docs.append(record)

        payload = {
            "model": model,
            "kb_signature": kb_signature,
            "docs": serializable_docs,
            "saved_at": time.time(),
        }

        cache_file = self._cache_file(kb_path, model)
        with cache_file.open("wb") as fh:
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)

    def invalidate(self, kb_path: str, model: str) -> None:
        cache_file = self._cache_file(kb_path, model)
        if cache_file.exists():
            cache_file.unlink(missing_ok=True)


__all__ = ["RAGVectorStore"]
