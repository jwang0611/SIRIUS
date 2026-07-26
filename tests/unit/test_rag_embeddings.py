"""Batching coverage for production RAG knowledge-base embeddings."""

from __future__ import annotations

from src.rag import embeddings
from src.rag.chunker import Chunk


def test_build_embeddings_batches_large_knowledge_base(monkeypatch):
    chunks = [Chunk(id=str(index), text=f"chunk {index}", meta={"index": index}) for index in range(205)]
    observed_batches: list[list[str]] = []

    class FakeChunker:
        def __init__(self, kb_path):
            assert kb_path == "structured-kb"

        def load_all_parquet(self):
            return chunks

    class FakeClient:
        def __init__(self, model):
            assert model == "openai/text-embedding-3-small"

        def embed_texts(self, texts):
            observed_batches.append(list(texts))
            return [[float(len(observed_batches)), float(index)] for index, _ in enumerate(texts)]

    monkeypatch.setattr(embeddings, "Chunker", FakeChunker)
    monkeypatch.setattr(embeddings, "OpenRouterEmbeddingClient", FakeClient)

    documents = embeddings.build_embeddings(
        "structured-kb",
        model="openai/text-embedding-3-small",
        batch_size=100,
    )

    assert [len(batch) for batch in observed_batches] == [100, 100, 5]
    assert len(documents) == 205
    assert documents[0]["id"] == "0"
    assert documents[-1]["meta"] == {"index": 204}
