from __future__ import annotations

import json
import threading

import numpy as np
import pandas as pd

from src.infrastructure.data_masker import DataMasker
from src.rag.prompt_augmenter import RAGContext, RAGPromptAugmenter


def test_structured_context_preserves_ecrf_chunk_variable_metadata():
    augmenter = object.__new__(RAGPromptAugmenter)
    context = RAGContext(
        score=0.91,
        text="synthetic mapping example",
        metadata={
            "domain": "XX",
            "variable": "XXVAR when XXFLAG=Y",
            "annotation_table": "Synthetic Table",
            "metadata_variable": "RAWVAR",
        },
    )

    rendered = augmenter.build_context_block([context], structured=True)

    assert "**XX.XXVAR**" in rendered
    assert "condition=XXFLAG=Y" in rendered
    assert "XXVAR when XXFLAG=Y" not in rendered
    assert "Synthetic Table/RAWVAR" in rendered


class _CapturingEmbeddingClient:
    def __init__(self) -> None:
        self.payloads: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.payloads.append(list(texts))
        return [[1.0, 0.0] for _ in texts]


def test_session_kb_text_and_metadata_are_masked_before_embedding(tmp_path):
    kb_file = tmp_path / "private-project.parquet"
    pd.DataFrame(
        [
            {
                "annotation_table": "DOB: 1980-05-15",
                "metadata_variable": "SUBJECT 001-0023",
                "annotation_variable": "Smith, John",
                "SDTM_Domain": "DM",
                "SDTM_Variable": "BRTHDTC",
            }
        ]
    ).to_parquet(kb_file, index=False)

    augmenter = object.__new__(RAGPromptAugmenter)
    augmenter.data_masker = DataMasker()
    augmenter.embed_client = _CapturingEmbeddingClient()
    augmenter._extra_docs = []

    augmenter._load_extra_kb_files([str(kb_file)])

    sent = json.dumps(augmenter.embed_client.payloads, ensure_ascii=False)
    stored = json.dumps(
        [{"text": doc["text"], "meta": doc["meta"]} for doc in augmenter._extra_docs],
        ensure_ascii=False,
    )
    for payload in (sent, stored):
        assert "1980-05-15" not in payload
        assert "001-0023" not in payload
        assert "Smith, John" not in payload
        assert "[REDACTED]" in payload


def test_query_and_retrieved_context_are_masked_before_remote_or_prompt_use():
    class _Retriever:
        def search(self, _vector, top_k):
            assert top_k == 3
            return [
                {
                    "score": 0.9,
                    "text": "DOB: 1980-05-15",
                    "metadata": {"annotation_table": "Subject 001-0023"},
                    "source": "session",
                }
            ]

    augmenter = object.__new__(RAGPromptAugmenter)
    augmenter.data_masker = DataMasker()
    augmenter.embedding_model = "test-embed"
    augmenter.top_k = 3
    augmenter.embed_client = _CapturingEmbeddingClient()
    augmenter._query_vector_cache = {}
    augmenter._cache_lock = threading.Lock()
    augmenter._retriever = _Retriever()

    contexts = augmenter.retrieve("DOB: 1980-05-15 Subject 001-0023")

    assert np.asarray(contexts[0].score).item() == 0.9
    sent = json.dumps(augmenter.embed_client.payloads, ensure_ascii=False)
    rendered = augmenter.build_context_block(contexts, structured=False)
    for payload in (sent, rendered, json.dumps(contexts[0].metadata, ensure_ascii=False)):
        assert "1980-05-15" not in payload
        assert "001-0023" not in payload
        assert "[REDACTED]" in payload
