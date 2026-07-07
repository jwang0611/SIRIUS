"""Unit tests for ``RecommendationOrchestrator``."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.config.settings import CascadeSettings
from src.processors.cascade import CascadePredictor
from src.processors.llm_inference_service import LLMInferenceService
from src.processors.recommendation_normalizer import RecommendationNormalizer
from src.processors.recommendation_orchestrator import RecommendationOrchestrator
from tests.conftest import FakeAIClient


@pytest.fixture
def cascade() -> CascadePredictor:
    return CascadePredictor(
        CascadeSettings(
            enabled=True,
            kb_min_confidence=0.8,
            kb_high_conf=0.85,
            rag_high_conf=0.7,
            kb_domain_override_conf=0.85,
        )
    )


@pytest.fixture
def normalizer() -> RecommendationNormalizer:
    return RecommendationNormalizer()


@pytest.fixture(autouse=True)
def _stub_standard_vars():
    """Keep the deterministic validator predictable across tests."""
    with patch(
        "src.processors.deterministic_validator._get_domain_standard_vars",
        return_value={"AETERM", "SUBJID", "FAORRES"},
    ):
        yield


class TestCascadeShortCircuits:
    def test_level1_kb_direct_match(self, cascade, normalizer):
        orch = RecommendationOrchestrator(cascade=cascade, normalizer=normalizer)
        result = orch.process_variable(
            table_name="AE",
            variable_name="AETERM",
            variable_data={},
            kb_context={
                "source": "eCRF_direct_match",
                "confidence": 0.95,
                "suggestions": [{"domain": "AE", "sdtm_variable": "AETERM"}],
            },
            target_domain="AE",
        )
        assert result.short_circuited is True
        assert result.cascade_level == 1
        assert result.source == "KB_DIRECT"
        assert result.recommendations
        assert result.recommendations[0]["sdtm_variable"] == "AETERM"

    def test_level2_kb_high_conf(self, cascade, normalizer):
        orch = RecommendationOrchestrator(cascade=cascade, normalizer=normalizer)
        result = orch.process_variable(
            table_name="AE",
            variable_name="AETERM",
            variable_data={},
            kb_context={
                "source": "KB_semantic",
                "confidence": 0.9,
                "suggestions": [{"domain": "AE", "sdtm_variable": "AETERM"}],
            },
            target_domain="AE",
        )
        assert result.short_circuited is True
        assert result.cascade_level == 2
        assert result.source == "KB_HIGH"

    def test_level3_rag_still_goes_to_llm(self, cascade, normalizer):
        """RAG short-circuits in the cascade but the orchestrator should
        continue to the LLM with the RAG context attached."""
        client = FakeAIClient(default_response='[{"domain":"AE","sdtm_variable":"AETERM"}]')
        orch = RecommendationOrchestrator(
            cascade=cascade,
            normalizer=normalizer,
            inference=LLMInferenceService(client=client),
        )
        result = orch.process_variable(
            table_name="AE",
            variable_name="x",
            variable_data={},
            kb_context={"suggestions": [], "confidence": 0.0},
            rag_contexts=[{"snippet": "match"}],
            rag_info={"top_score": 0.85},
            target_domain="AE",
            llm_prompt="infer",
        )
        assert result.short_circuited is False
        assert result.cascade_level == 4
        assert result.rag_contexts == [{"snippet": "match"}]
        assert result.recommendations


class TestLLMFallthrough:
    def test_llm_invoked_when_no_shortcircuit(self, cascade, normalizer):
        client = FakeAIClient(default_response='[{"domain":"AE","sdtm_variable":"AETERM","score":0.9}]')
        orch = RecommendationOrchestrator(
            cascade=cascade,
            normalizer=normalizer,
            inference=LLMInferenceService(client=client),
        )
        result = orch.process_variable(
            table_name="AE",
            variable_name="x",
            variable_data={},
            kb_context={"suggestions": [], "confidence": 0.2},
            target_domain="AE",
            llm_prompt="please map",
        )
        assert result.cascade_level == 4
        assert result.source == "LLM"
        assert result.recommendations[0]["source"] == "LLM"
        assert result.recommendations[0]["cascade_level"] == 4

    def test_missing_prompt_returns_error(self, cascade, normalizer):
        orch = RecommendationOrchestrator(
            cascade=cascade,
            normalizer=normalizer,
            inference=LLMInferenceService(client=FakeAIClient()),
        )
        result = orch.process_variable(
            table_name="AE",
            variable_name="x",
            variable_data={},
            kb_context={},
        )
        assert result.recommendations == []
        assert "no_llm_prompt_or_service" in result.errors

    def test_missing_service_returns_error(self, cascade, normalizer):
        orch = RecommendationOrchestrator(cascade=cascade, normalizer=normalizer)
        result = orch.process_variable(
            table_name="AE",
            variable_name="x",
            variable_data={},
            kb_context={},
            llm_prompt="p",
        )
        assert "no_llm_prompt_or_service" in result.errors

    def test_llm_exception_recorded(self, cascade, normalizer):
        class BoomClient(FakeAIClient):
            def generate_content(self, prompt, **kwargs):  # type: ignore[override]
                raise RuntimeError("api down")

        orch = RecommendationOrchestrator(
            cascade=cascade,
            normalizer=normalizer,
            inference=LLMInferenceService(client=BoomClient()),
        )
        result = orch.process_variable(
            table_name="AE",
            variable_name="x",
            variable_data={},
            kb_context={},
            llm_prompt="p",
        )
        assert result.recommendations == []
        assert any("llm_error" in e for e in result.errors)


class TestTiming:
    def test_processing_time_recorded(self, cascade, normalizer):
        client = FakeAIClient(default_response="[]")
        orch = RecommendationOrchestrator(
            cascade=cascade,
            normalizer=normalizer,
            inference=LLMInferenceService(client=client),
        )
        result = orch.process_variable(
            table_name="T",
            variable_name="x",
            variable_data={},
            kb_context={},
            llm_prompt="p",
        )
        assert result.processing_time_ms >= 0
