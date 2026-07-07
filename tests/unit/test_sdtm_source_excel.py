"""Tests for Excel Source column mapping in SDTM output."""

import pytest

from src.processors.sdtm_processor import _recommendation_source_excel_label


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("KB", "KB"),
        ("kb", "KB"),
        ("RAG", "RAG"),
        ("rag", "RAG"),
        ("LLM", "LLM"),
        ("UNMAPPED", "UNMAPPED"),
        ("FALLBACK", "LLM"),
        ("", "LLM"),
        (None, "LLM"),
    ],
)
def test_recommendation_source_excel_label(source: str | None, expected: str) -> None:
    assert _recommendation_source_excel_label(source) == expected
