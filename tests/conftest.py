"""Shared pytest fixtures for the SIRIUS test suite.

Provides deterministic fakes for the AI client, knowledge base, and
settings so unit tests do not require network access, embeddings, or
real fixture files.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# Make ``src`` importable without installing the package
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.clients.base_client import BaseAIClient  # noqa: E402


# ---------------------------------------------------------------------------
# Generic path fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path of the repository root."""
    return _REPO_ROOT


@pytest.fixture(scope="session")
def fixtures_dir(repo_root: Path) -> Path:
    """Path to ``tests/fixtures``."""
    return repo_root / "tests" / "fixtures"


@pytest.fixture
def tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated workspace with data/ subdirectories and CWD switched to it."""
    (tmp_path / "data" / "output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "processed").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "audit_logs").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all SIRIUS-specific env vars so Settings defaults apply."""
    prefixes = (
        "SDTM_",
        "KB_",
        "CASCADE_",
        "RAG_",
        "AUDIT_",
        "DATA_MASKING_",
        "RATE_LIMIT_",
        "OPENROUTER_",
        "DEFAULT_",
        "TOP_K_",
        "SPEC_",
        "ALS_",
        "OUTPUT_",
        "BATCH_",
    )
    for key in list(os.environ.keys()):
        if key.startswith(prefixes):
            monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# FakeAIClient
# ---------------------------------------------------------------------------
class FakeAIClient(BaseAIClient):
    """Deterministic AI client for tests.

    Matches prompts against ``responses`` using substring rules; falls back
    to ``default_response`` when nothing matches. Records every call in
    ``self.calls`` for assertion purposes.
    """

    def __init__(
        self,
        responses: Optional[Dict[str, str]] = None,
        default_response: str = "[]",
    ) -> None:
        super().__init__(api_key="fake-key")
        self.responses = responses or {}
        self.default_response = default_response
        self.calls: List[Dict[str, Any]] = []
        self._model_name = "fake/model"
        self.last_usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def initialize(self) -> bool:
        return True

    def generate_content(
        self,
        prompt: str,
        max_output_tokens: int = 2000,
        temperature: float = 0.7,
        top_p: float = 0.98,
        top_k: int = 80,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
                "top_p": top_p,
                "top_k": top_k,
            }
        )
        for fragment, response in self.responses.items():
            if fragment in prompt:
                return response
        return self.default_response

    def get_error_message(self, error: Exception) -> str:
        return str(error)

    def get_sanitized_model_name(self, model_name: Optional[str] = None) -> str:
        name = model_name or self._model_name
        return name.replace("/", "_").replace(":", "_")

    def set_model(self, model_name: str) -> None:
        self._model_name = model_name


@pytest.fixture
def fake_ai_client() -> FakeAIClient:
    """Basic fake AI client returning an empty JSON array for every call."""
    return FakeAIClient()


# ---------------------------------------------------------------------------
# Recommendation / mapping fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_mappings() -> List[Dict[str, Any]]:
    """Canonical set of mapping inputs used across many unit tests."""
    return [
        {
            "metadata_table": "DM",
            "metadata_variable": "SUBJID",
            "annotation_table": "人口学",
            "annotation_variable": "受试者编号",
            "SDTM_Domain": None,
            "SDTM_Variable": None,
        },
        {
            "metadata_table": "DM",
            "metadata_variable": "SEX",
            "annotation_table": "人口学",
            "annotation_variable": "性别",
            "SDTM_Domain": None,
            "SDTM_Variable": None,
        },
        {
            "metadata_table": "VS",
            "metadata_variable": "SYSBP",
            "annotation_table": "生命体征",
            "annotation_variable": "收缩压",
            "SDTM_Domain": None,
            "SDTM_Variable": None,
        },
        {
            "metadata_table": "AE",
            "metadata_variable": "AETERM",
            "annotation_table": "不良事件",
            "annotation_variable": "不良事件名称",
            "SDTM_Domain": None,
            "SDTM_Variable": None,
        },
    ]


@pytest.fixture
def sample_domain_rec() -> Dict[str, Any]:
    """Minimal well-formed domain recommendation for normaliser tests."""
    return {
        "domain": "AE",
        "sdtm_variable": "AETERM",
        "sdtm_variable_type": "standard",
        "score": 0.92,
        "variable_name": "AETERM",
        "source": "LLM",
    }


# ---------------------------------------------------------------------------
# InMemoryKB
# ---------------------------------------------------------------------------
class InMemoryKB:
    """Minimal stand-in for ``LLMKnowledgeQueryInterface``.

    Only exposes the subset of the API that the cascade + processor
    actually depend on.  Tests can seed the KB with explicit suggestions.
    """

    def __init__(self, suggestions: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> None:
        self.suggestions = suggestions or {}
        self.verbose = False
        self.get_kb_sources_calls: int = 0
        self.lookup_calls: List[Dict[str, Any]] = []

    # -- diagnostic helpers expected by the processor ---------------
    def get_kb_sources(self) -> Dict[str, Any]:
        self.get_kb_sources_calls += 1
        return {"default": "", "extra": [], "total_records": len(self.suggestions)}

    def set_verbose(self, value: bool) -> None:
        self.verbose = bool(value)

    # -- lookup API -------------------------------------------------
    def _key(self, variable_name: str, annotation_variable: Optional[str]) -> str:
        return f"{(variable_name or '').upper()}|{(annotation_variable or '').upper()}"

    def query_variable_mapping(
        self,
        variable_name: str,
        annotation_variable: Optional[str] = None,
        table_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        self.lookup_calls.append(
            {
                "variable_name": variable_name,
                "annotation_variable": annotation_variable,
                "table_name": table_name,
            }
        )
        return list(self.suggestions.get(self._key(variable_name, annotation_variable), []))


@pytest.fixture
def empty_kb() -> InMemoryKB:
    return InMemoryKB()


@pytest.fixture
def seeded_kb() -> InMemoryKB:
    return InMemoryKB(
        suggestions={
            "AETERM|不良事件名称": [
                {
                    "domain": "AE",
                    "sdtm_variable": "AETERM",
                    "confidence": 0.95,
                    "source": "eCRF_direct_match",
                }
            ],
            "SUBJID|受试者编号": [
                {
                    "domain": "DM",
                    "sdtm_variable": "SUBJID",
                    "confidence": 0.95,
                    "source": "eCRF_direct_match",
                }
            ],
        }
    )


# ---------------------------------------------------------------------------
# Golden JSON helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def golden_loader(fixtures_dir: Path):
    """Factory returning ``load_or_write(path, obj)`` for snapshot tests.

    If ``UPDATE_GOLDENS=1`` is set, the expected file is rewritten. Otherwise
    the fixture compares the given ``obj`` with the stored JSON.
    """

    update_mode = os.getenv("UPDATE_GOLDENS", "0") == "1"

    def load_or_write(relative_path: str, obj: Any) -> Any:
        target = fixtures_dir / "golden" / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if update_mode or not target.exists():
            target.write_text(
                json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False),
                encoding="utf-8",
            )
            return obj
        return json.loads(target.read_text(encoding="utf-8"))

    return load_or_write
