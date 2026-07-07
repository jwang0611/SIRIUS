"""Unit tests for :class:`CatalogMixin`."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict

import pandas as pd

from src.processors.catalog import CatalogMixin


class _PromptGen:
    def __init__(self) -> None:
        self.domain_titles: Dict[str, Dict[str, str]] = {}
        self._set_catalog: Dict[str, Any] = {}

    def set_standard_catalog(self, catalog):
        self._set_catalog = catalog


class _Host(CatalogMixin):
    def __init__(self, rag_config: Dict[str, Any] | None = None):
        self.rag_config = rag_config or {}
        self.prompt_generator = _PromptGen()
        self.standard_catalog: Dict[str, Any] = {"domains": {}}
        self.CHINESE_TABLE_DOMAIN_MAP = {"不良事件": "AE", "人口学": "DM"}


class TestExtractLabel:
    def test_returns_empty_when_not_string(self):
        assert CatalogMixin._extract_label(None, "label") == ""
        assert CatalogMixin._extract_label(123, "label") == ""

    def test_extracts_matching_line(self):
        text = "domain: AE\nlabel: Adverse Event Term\nvariable: AETERM"
        assert CatalogMixin._extract_label(text, "label") == "Adverse Event Term"

    def test_case_insensitive_prefix(self):
        assert CatalogMixin._extract_label("LABEL: X", "label") == "X"

    def test_no_matching_line(self):
        assert CatalogMixin._extract_label("domain: AE", "label") == ""


class TestBuildDomainNameLookup:
    def test_includes_chinese_table_map(self):
        host = _Host()
        out = host._build_domain_name_lookup()
        assert out["不良事件"] == "AE"
        assert out["人口学"] == "DM"

    def test_includes_prompt_generator_titles(self):
        host = _Host()
        host.prompt_generator.domain_titles = {
            "AE": {"en": "Adverse Events", "cn": "不良事件 CN"},
            "DM": {"en": "Demographics", "cn": None},
        }
        out = host._build_domain_name_lookup()
        assert out["adverse events"] == "AE"
        assert out["不良事件 cn"] == "AE"
        assert out["demographics"] == "DM"

    def test_catalog_titles_setdefault(self):
        host = _Host()
        host.standard_catalog = {
            "domains": {
                "LB": {"title_en": "Laboratory", "title_cn": "实验室"},
                "VS": {"title_en": None, "title_cn": None},
            }
        }
        out = host._build_domain_name_lookup()
        assert out["laboratory"] == "LB"
        assert out["实验室"] == "LB"

    def test_prompt_generator_without_domain_titles_attr(self):
        host = _Host()
        host.prompt_generator = SimpleNamespace()  # no domain_titles
        out = host._build_domain_name_lookup()
        # Still returns CHINESE_TABLE_DOMAIN_MAP entries
        assert "不良事件" in out


class TestBuildStandardCatalog:
    def test_builds_from_parquet(self, tmp_path):
        host = _Host()
        df = pd.DataFrame(
            [
                {"domain": "AE", "variable": "AETERM", "label": "AE Term", "chunk_text": ""},
                {"domain": "ae", "variable": "AESEV", "label": "AE Severity", "chunk_text": ""},
                {"domain": "", "variable": "X", "label": "", "chunk_text": ""},  # skipped
                {"domain": "AE", "variable": "AETERM", "label": "dup", "chunk_text": ""},  # dup skipped
            ]
        )
        path = tmp_path / "spec.parquet"
        df.to_parquet(path)
        catalog = host._build_standard_catalog(path)
        assert "AE" in catalog["domains"]
        vars_cn = catalog["domains"]["AE"]["variables_cn"]
        assert len(vars_cn) == 2
        assert {v["variable"] for v in vars_cn} == {"AETERM", "AESEV"}

    def test_extracts_label_from_chunk_text_when_absent(self, tmp_path):
        host = _Host()
        df = pd.DataFrame(
            [
                {
                    "domain": "AE",
                    "variable": "AETERM",
                    "label": None,
                    "chunk_text": "label: Extracted Label\nvariable: AETERM",
                }
            ]
        )
        path = tmp_path / "spec.parquet"
        df.to_parquet(path)
        catalog = host._build_standard_catalog(path)
        assert catalog["domains"]["AE"]["variables_cn"][0]["label_cn"] == "Extracted Label"

    def test_missing_file_returns_empty_catalog(self, tmp_path, capsys):
        host = _Host()
        catalog = host._build_standard_catalog(tmp_path / "does_not_exist.parquet")
        assert catalog == {"domains": {}}
        out = capsys.readouterr().out
        assert "not found" in out

    def test_none_path_returns_empty_catalog(self):
        host = _Host()
        catalog = host._build_standard_catalog(None)
        assert catalog == {"domains": {}}


class TestInitializeStandardCatalog:
    def test_configures_prompt_generator_when_loaded(self, tmp_path, monkeypatch):
        host = _Host(rag_config={"structured_path": str(tmp_path)})
        df = pd.DataFrame([{"domain": "AE", "variable": "AETERM", "label": "T", "chunk_text": ""}])
        df.to_parquet(tmp_path / "sdtm_spec_enhanced.parquet")
        catalog = host._initialize_standard_catalog()
        assert "AE" in catalog["domains"]
        assert host.prompt_generator._set_catalog == catalog
        # Config is updated with the resolved standard_cn_file path
        assert "standard_cn_file" in host.rag_config

    def test_absolute_standard_cn_file_override(self, tmp_path):
        abs_path = tmp_path / "override.parquet"
        df = pd.DataFrame([{"domain": "DM", "variable": "AGE", "label": "Age", "chunk_text": ""}])
        df.to_parquet(abs_path)
        host = _Host(
            rag_config={
                "structured_path": str(tmp_path / "ignored"),
                "standard_cn_file": str(abs_path),
            }
        )
        catalog = host._initialize_standard_catalog()
        assert "DM" in catalog["domains"]

    def test_missing_spec_file_still_returns(self, tmp_path, capsys):
        host = _Host(rag_config={"structured_path": str(tmp_path)})
        catalog = host._initialize_standard_catalog()
        assert catalog == {"domains": {}}
        out = capsys.readouterr().out
        assert "not loaded" in out
