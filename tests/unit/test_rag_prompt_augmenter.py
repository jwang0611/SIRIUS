from __future__ import annotations

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

    assert "**XX.XXVAR when XXFLAG=Y**" in rendered
    assert "Synthetic Table/RAWVAR" in rendered
