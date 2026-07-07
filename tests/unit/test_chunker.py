"""Unit tests for :mod:`src.rag.chunker`."""

from __future__ import annotations

import pandas as pd

from src.rag.chunker import Chunk, Chunker, load_and_chunk_kb


class TestChunkerConstruction:
    def test_default_exclude_set(self, tmp_path):
        chunker = Chunker(str(tmp_path))
        assert chunker.base_path == str(tmp_path)
        assert chunker.exclude_files == set()

    def test_custom_exclude_set(self, tmp_path):
        chunker = Chunker(str(tmp_path), exclude_files={"skip.parquet"})
        assert chunker.exclude_files == {"skip.parquet"}

    def test_env_exclude_files_overrides(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RAG_EXCLUDE_FILES", "a.parquet, b.parquet ,")
        chunker = Chunker(str(tmp_path), exclude_files={"ignored.parquet"})
        assert chunker.exclude_files == {"a.parquet", "b.parquet"}


class TestCleanHelper:
    def test_none_returns_empty(self):
        assert Chunker._clean(None) == ""

    def test_nan_returns_empty(self):
        assert Chunker._clean(pd.NA) == ""

    def test_strips_whitespace(self):
        assert Chunker._clean("  hello  ") == "hello"


class TestParseMetadata:
    def test_dict_passthrough(self, tmp_path):
        chunker = Chunker(str(tmp_path))
        assert chunker._parse_metadata({"k": "v"}) == {"k": "v"}

    def test_json_string(self, tmp_path):
        chunker = Chunker(str(tmp_path))
        assert chunker._parse_metadata('{"k": 1}') == {"k": 1}

    def test_invalid_json_wraps_raw(self, tmp_path):
        chunker = Chunker(str(tmp_path))
        assert chunker._parse_metadata("not json") == {"raw_metadata": "not json"}

    def test_none_or_empty(self, tmp_path):
        chunker = Chunker(str(tmp_path))
        assert chunker._parse_metadata(None) == {}
        assert chunker._parse_metadata("") == {}


class TestCaseInsensitiveAccess:
    def test_dict_exact(self):
        assert Chunker._get_case_insensitive({"Key": "v"}, "Key") == "v"

    def test_dict_case_fold(self):
        assert Chunker._get_case_insensitive({"KEY": "v"}, "key") == "v"

    def test_missing_returns_none(self):
        assert Chunker._get_case_insensitive({"a": 1}, "b") is None

    def test_series_support(self):
        series = pd.Series({"SDTM_Domain": "AE"})
        assert Chunker._get_case_insensitive(series, "sdtm_domain") == "AE"


class TestBuildECRFChunk:
    def test_builds_chunk_with_core_fields(self, tmp_path):
        chunker = Chunker(str(tmp_path))
        row = {
            "SDTM_Domain": "AE",
            "SDTM_Variable": "AETERM",
            "annotation_table": "Adverse Events",
            "annotation_variable": "AE Term",
            "metadata_table": "ae",
            "metadata_variable": "aeterm",
            "rawdata": "ae_term_raw",
        }
        chunk = chunker._build_ecrf_chunk(row, "ecrf.parquet", 0)
        assert isinstance(chunk, Chunk)
        assert chunk.id == "eCRF_0"
        assert "Variable: AETERM" in chunk.text
        assert "Domain: AE" in chunk.text
        assert chunk.meta["type"] == "sdtm_mapping_example"
        assert chunk.meta["language"] == "zh"
        assert chunk.meta["domain"] == "AE"

    def test_returns_none_when_domain_missing(self, tmp_path):
        chunker = Chunker(str(tmp_path))
        row = {"SDTM_Domain": "", "SDTM_Variable": "AETERM"}
        assert chunker._build_ecrf_chunk(row, "f.parquet", 0) is None

    def test_returns_none_when_variable_missing(self, tmp_path):
        chunker = Chunker(str(tmp_path))
        row = {"SDTM_Domain": "AE", "SDTM_Variable": ""}
        assert chunker._build_ecrf_chunk(row, "f.parquet", 0) is None

    def test_placeholder_rawdata_cleaned(self, tmp_path):
        chunker = Chunker(str(tmp_path))
        row = {
            "SDTM_Domain": "AE",
            "SDTM_Variable": "AETERM",
            "annotation_table": "AE",
            "annotation_variable": "Term",
            "metadata_table": "ae",
            "metadata_variable": "aeterm",
            "rawdata": "--",
        }
        chunk = chunker._build_ecrf_chunk(row, "f.parquet", 1)
        assert chunk is not None
        assert chunk.meta["rawdata"] == ""


class TestBuildStructuredChunks:
    def test_builds_from_chunk_text(self, tmp_path):
        chunker = Chunker(str(tmp_path))
        df = pd.DataFrame(
            [
                {
                    "chunk_text": "Variable: AETERM",
                    "metadata": '{"type": "variable_def"}',
                    "domain": "AE",
                    "variable": "AETERM",
                    "language": "en",
                    "type": "def",
                    "confidence_default": "0.9",
                    "source": "spec",
                    "chunk_id": "spec_ae_1",
                },
            ]
        )
        chunks = chunker._build_structured_chunks(df, "spec.parquet")
        assert len(chunks) == 1
        assert chunks[0].id == "spec_ae_1"
        assert chunks[0].meta["domain"] == "AE"
        assert chunks[0].meta["confidence_default"] == 0.9

    def test_skips_empty_text(self, tmp_path):
        chunker = Chunker(str(tmp_path))
        df = pd.DataFrame([{"chunk_text": "", "metadata": "{}"}, {"chunk_text": "ok", "metadata": "{}"}])
        chunks = chunker._build_structured_chunks(df, "x.parquet")
        assert len(chunks) == 1

    def test_auto_chunk_id_when_missing(self, tmp_path):
        chunker = Chunker(str(tmp_path))
        df = pd.DataFrame([{"chunk_text": "Hello world text", "metadata": "{}"}])
        chunks = chunker._build_structured_chunks(df, "file.parquet")
        assert chunks[0].id == "file_0"


class TestChunkParquet:
    def test_routes_structured(self, tmp_path):
        df = pd.DataFrame([{"chunk_text": "This is a text", "metadata": "{}"}])
        parquet = tmp_path / "s.parquet"
        df.to_parquet(parquet)
        chunker = Chunker(str(tmp_path))
        chunks = chunker.chunk_parquet(str(parquet))
        assert len(chunks) == 1

    def test_routes_ecrf(self, tmp_path):
        df = pd.DataFrame(
            [
                {
                    "annotation_table": "AE",
                    "metadata_table": "ae",
                    "annotation_variable": "Term",
                    "metadata_variable": "aeterm",
                    "SDTM_domain": "AE",
                    "SDTM_variable": "AETERM",
                    "rawdata": "x",
                }
            ]
        )
        parquet = tmp_path / "ecrf.parquet"
        df.to_parquet(parquet)
        chunker = Chunker(str(tmp_path))
        chunks = chunker.chunk_parquet(str(parquet))
        assert len(chunks) == 1
        assert chunks[0].meta["source"] == "eCRF"

    def test_routes_generic(self, tmp_path):
        df = pd.DataFrame([{"col_a": "value_a_needs_to_be_long_enough", "col_b": "value_b"}])
        parquet = tmp_path / "g.parquet"
        df.to_parquet(parquet)
        chunker = Chunker(str(tmp_path))
        chunks = chunker.chunk_parquet(str(parquet))
        assert len(chunks) == 1
        assert chunks[0].meta["source"] == "generic"


class TestLoadAllParquet:
    def test_loads_and_excludes(self, tmp_path):
        df1 = pd.DataFrame([{"chunk_text": "hello world foo bar", "metadata": "{}"}])
        df2 = pd.DataFrame([{"chunk_text": "other content here zzz", "metadata": "{}"}])
        df1.to_parquet(tmp_path / "keep.parquet")
        df2.to_parquet(tmp_path / "skip.parquet")

        chunker = Chunker(str(tmp_path), exclude_files={"skip.parquet"})
        chunks = chunker.load_all_parquet()
        assert len(chunks) == 1

    def test_load_and_chunk_kb_helper(self, tmp_path):
        df = pd.DataFrame([{"chunk_text": "sample chunk text here", "metadata": "{}"}])
        df.to_parquet(tmp_path / "f.parquet")
        chunks = load_and_chunk_kb(str(tmp_path))
        assert len(chunks) == 1
