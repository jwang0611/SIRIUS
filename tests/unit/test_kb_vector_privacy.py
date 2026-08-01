"""Privacy boundaries for direct-KB embedding and vector caching."""

from __future__ import annotations

import logging
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from src.infrastructure.data_masker import DataMasker
from src.knowledge_base.llm_query_interface import LLMKnowledgeQueryInterface


class RecordingEmbedClient:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]


def _interface(
    tmp_path: Path,
    *,
    extra_kb_files: list[Path] | None = None,
) -> tuple[LLMKnowledgeQueryInterface, RecordingEmbedClient]:
    interface = LLMKnowledgeQueryInterface.__new__(LLMKnowledgeQueryInterface)
    interface.data_masker = DataMasker()
    interface.default_kb_filename = None
    interface.extra_kb_files = list(extra_kb_files or [])
    interface.structured_path = tmp_path / "structured"
    interface.ecrf_data = pd.DataFrame(
        [
            {
                "annotation_table": "DOB: 1980-05-15",
                "annotation_variable": "Subject 001-0023",
                "metadata_variable": "patient@example.com",
                "metadata_table": "DEMOGRAPHICS",
                "SDTM_Domain": "DM",
                "SDTM_Variable": "BRTHDTC",
            }
        ]
    )
    interface._embedding_model = "fake-embedding-model"
    embed_client = RecordingEmbedClient()
    interface._embed_client = embed_client
    interface._vector_cache_dir = tmp_path / "vector-cache"
    interface._vector_cache_dir.mkdir()
    interface._kb_vectors = None
    interface._kb_vector_indices = None
    interface._kb_texts = None
    interface._kb_vector_signature = None
    interface._enable_vector_matching = True
    interface._kb_verbose = False
    interface._allow_persistent_vector_cache = not bool(interface.extra_kb_files)
    return interface, embed_client


def test_kb_and_query_embedding_payloads_are_masked(tmp_path, capsys):
    session_file = tmp_path / "session-kb.json"
    interface, embed_client = _interface(tmp_path, extra_kb_files=[session_file])

    assert interface._ensure_kb_vectors() is True
    results = interface._vector_fuzzy_search(
        {
            "annotation_table": "DOB: 1975-06-30",
            "annotation_variable": "Subject 101-9999",
            "metadata_variable": "query@example.com",
            "metadata_table": "DEMOGRAPHICS",
        },
        min_score=0.0,
        verbose=True,
    )

    assert results == [(1.0, 0)]
    assert len(embed_client.calls) == 2
    transmitted = " ".join(text for call in embed_client.calls for text in call)
    for sensitive_value in [
        "1980-05-15",
        "001-0023",
        "patient@example.com",
        "1975-06-30",
        "101-9999",
        "query@example.com",
    ]:
        assert sensitive_value not in transmitted
    assert DataMasker.REDACTION_MARKER in transmitted

    verbose_output = capsys.readouterr().out
    assert "101-9999" not in verbose_output
    assert "query@example.com" not in verbose_output
    assert DataMasker.REDACTION_MARKER in verbose_output


def test_session_kb_never_reads_or_writes_persistent_vector_cache(tmp_path):
    session_file = tmp_path / "session-kb.json"
    interface, _ = _interface(tmp_path, extra_kb_files=[session_file])

    def unexpected_cache_path() -> Path:
        raise AssertionError("session KB attempted to access persistent vector cache")

    interface._get_kb_vector_cache_path = unexpected_cache_path

    assert interface._ensure_kb_vectors() is True
    assert list(interface._vector_cache_dir.iterdir()) == []


def test_static_vector_cache_is_versioned_and_omits_record_text(tmp_path):
    interface, _ = _interface(tmp_path)
    interface._kb_vectors = np.array([[1.0, 0.0]], dtype=np.float32)
    interface._kb_vector_indices = [0]
    interface._kb_texts = ["DOB: [REDACTED]"]
    interface._kb_vector_signature = interface._compute_kb_signature()

    interface._save_kb_vectors_to_cache()

    cache_path = interface._get_kb_vector_cache_path()
    with cache_path.open("rb") as cache_file:
        payload = pickle.load(cache_file)
    assert payload["cache_version"] == LLMKnowledgeQueryInterface.VECTOR_CACHE_VERSION
    assert "texts" not in payload

    interface._kb_vectors = None
    interface._kb_vector_indices = None
    interface._kb_texts = ["must be cleared"]
    interface._kb_vector_signature = None
    assert interface._load_kb_vectors_from_cache() is True
    assert interface._kb_texts is None


def test_legacy_vector_cache_with_raw_text_is_rejected(tmp_path):
    interface, _ = _interface(tmp_path)
    signature = interface._compute_kb_signature()
    cache_path = interface._get_kb_vector_cache_path()
    with cache_path.open("wb") as cache_file:
        pickle.dump(
            {
                "signature": signature,
                "vectors": [[1.0, 0.0]],
                "indices": [0],
                "texts": ["DOB: 1980-05-15"],
            },
            cache_file,
        )

    assert interface._load_kb_vectors_from_cache() is False
    assert interface._kb_vectors is None


def test_kb_file_error_log_omits_absolute_path_and_exception_message(tmp_path, monkeypatch, caplog):
    secret_directory = tmp_path / "client-001-0023"
    secret_directory.mkdir()
    kb_file = secret_directory / "mapping.json"
    kb_file.write_text("{}", encoding="utf-8")
    interface, _ = _interface(tmp_path)

    def fail_read_json(*_args, **_kwargs):
        raise RuntimeError("patient@example.com could not be parsed")

    monkeypatch.setattr(pd, "read_json", fail_read_json)
    caplog.set_level(logging.ERROR)

    assert interface._load_single_kb_file(kb_file) is None
    assert str(secret_directory) not in caplog.text
    assert "patient@example.com" not in caplog.text
    assert "mapping.json" in caplog.text
    assert "RuntimeError" in caplog.text


def test_kb_interaction_log_masks_sensitive_values(tmp_path, monkeypatch):
    interface, _ = _interface(tmp_path)
    interface.log_ai_interactions = True
    monkeypatch.chdir(tmp_path)

    interface._log_kb_event("subject=001-0023 DOB: 1980-05-15 patient@example.com")

    log_file = next((tmp_path / "data/output/logs").glob("ai_interactions_*.log"))
    log_text = log_file.read_text(encoding="utf-8")
    assert "001-0023" not in log_text
    assert "1980-05-15" not in log_text
    assert "patient@example.com" not in log_text
    assert DataMasker.REDACTION_MARKER in log_text
