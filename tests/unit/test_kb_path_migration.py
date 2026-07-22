"""Compatibility tests for the versioned default KB filename."""

from __future__ import annotations

from pathlib import Path

from src.knowledge_base.llm_query_interface import LLMKnowledgeQueryInterface


def _interface(structured_path: Path) -> LLMKnowledgeQueryInterface:
    interface = LLMKnowledgeQueryInterface.__new__(LLMKnowledgeQueryInterface)
    interface.structured_path = structured_path
    return interface


def test_legacy_json_filename_resolves_to_versioned_replacement(tmp_path, caplog):
    structured = tmp_path / "knowledge_base/structured"
    structured.mkdir(parents=True)
    replacement = structured / "ALS2SDTM_Mapping_Template_v1.0.json"
    replacement.write_text("[]", encoding="utf-8")

    resolved = _interface(structured)._resolve_kb_path(Path("ALS2SDTM_TEST.json"))

    assert resolved == replacement
    assert "deprecated" in caplog.text


def test_legacy_parquet_relative_path_resolves_without_double_prefix(tmp_path, monkeypatch):
    structured = tmp_path / "data/knowledge_base/structured"
    structured.mkdir(parents=True)
    replacement = structured / "ALS2SDTM_Mapping_Template_v1.0.parquet"
    replacement.write_bytes(b"parquet-placeholder")
    monkeypatch.chdir(tmp_path)

    resolved = _interface(structured)._resolve_kb_path(Path("data/knowledge_base/structured/ALS2SDTM_TEST.parquet"))

    assert resolved == Path("data/knowledge_base/structured/ALS2SDTM_Mapping_Template_v1.0.parquet")


def test_missing_nonlegacy_path_is_returned_for_existing_warning_flow(tmp_path):
    structured = tmp_path / "knowledge_base/structured"

    resolved = _interface(structured)._resolve_kb_path(Path("custom.json"))

    assert resolved == structured / "custom.json"


def test_nested_relative_path_can_still_resolve_under_structured_directory(tmp_path, monkeypatch):
    structured = tmp_path / "knowledge_base/structured"
    nested = structured / "session/example.json"
    nested.parent.mkdir(parents=True)
    nested.write_text("[]", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    resolved = _interface(structured)._resolve_kb_path(Path("session/example.json"))

    assert resolved == nested
