"""Unit tests for ``src.processors.cascade``."""

from __future__ import annotations

import pytest

from src.config.settings import CascadeSettings
from src.processors.cascade import CascadeDecision, CascadePredictor


@pytest.fixture
def settings() -> CascadeSettings:
    return CascadeSettings(
        enabled=True,
        kb_min_confidence=0.8,
        kb_high_conf=0.85,
        rag_high_conf=0.7,
        kb_domain_override_conf=0.85,
    )


@pytest.fixture
def predictor(settings: CascadeSettings) -> CascadePredictor:
    return CascadePredictor(settings)


class TestLevel1KbDirect:
    def test_short_circuits_on_direct_match(self, predictor):
        kb = {
            "source": "eCRF_direct_match",
            "confidence": 0.95,
            "suggestions": [{"domain": "AE", "sdtm_variable": "AETERM"}],
        }
        d = predictor.decide(kb_context=kb)
        assert d.level == 1
        assert d.source == "KB_DIRECT"
        assert d.short_circuited is True
        assert d.confidence == pytest.approx(0.95)
        assert d.recommendations

    def test_skips_when_confidence_below_min(self, predictor):
        kb = {
            "source": "eCRF_direct_match",
            "confidence": 0.5,
            "suggestions": [{"domain": "AE"}],
        }
        d = predictor.decide(kb_context=kb)
        assert d.level == 4

    def test_skips_when_no_suggestions(self, predictor):
        kb = {"source": "eCRF_direct_match", "confidence": 0.95, "suggestions": []}
        d = predictor.decide(kb_context=kb)
        assert d.level == 4


class TestLevel2KbHigh:
    def test_short_circuits_on_high_conf(self, predictor):
        kb = {
            "source": "KB_semantic",
            "confidence": 0.9,
            "suggestions": [{"domain": "AE"}],
        }
        d = predictor.decide(kb_context=kb)
        assert d.level == 2
        assert d.source == "KB_HIGH"
        assert d.short_circuited is True

    def test_boundary_at_kb_high_conf_exactly(self, predictor, settings):
        kb = {
            "source": "KB_semantic",
            "confidence": settings.kb_high_conf,
            "suggestions": [{"domain": "AE"}],
        }
        d = predictor.decide(kb_context=kb)
        assert d.level == 2

    def test_below_high_falls_through(self, predictor):
        kb = {"source": "KB_semantic", "confidence": 0.8, "suggestions": [{"domain": "AE"}]}
        d = predictor.decide(kb_context=kb)
        assert d.level == 4


class TestLevel3Rag:
    def test_short_circuits_on_high_rag_score(self, predictor):
        kb = {"suggestions": [], "confidence": 0.0}
        rag_info = {"top_score": 0.8}
        rag_ctx = [{"snippet": "AE term definition..."}]
        d = predictor.decide(kb_context=kb, rag_contexts=rag_ctx, rag_info=rag_info)
        assert d.level == 3
        assert d.source == "RAG_HIGH"
        assert d.short_circuited is True
        assert d.rag_contexts == rag_ctx
        assert d.recommendations == []

    def test_skips_when_rag_score_too_low(self, predictor):
        d = predictor.decide(
            kb_context={"suggestions": [], "confidence": 0.0},
            rag_contexts=[{"x": 1}],
            rag_info={"top_score": 0.5},
        )
        assert d.level == 4

    def test_skips_when_no_contexts(self, predictor):
        d = predictor.decide(
            kb_context={"suggestions": [], "confidence": 0.0},
            rag_contexts=[],
            rag_info={"top_score": 0.95},
        )
        assert d.level == 4


class TestLevel4Fallthrough:
    def test_no_kb_no_rag_returns_llm(self, predictor):
        d = predictor.decide(kb_context={"suggestions": []})
        assert d.level == 4
        assert d.source == "LLM"
        assert d.short_circuited is False
        assert d.reason.startswith("No short-circuit")

    def test_confidence_is_max_of_signals(self, predictor):
        d = predictor.decide(
            kb_context={"suggestions": [], "confidence": 0.4},
            rag_info={"top_score": 0.6},
            rag_contexts=[{"x": 1}],
        )
        assert d.confidence == pytest.approx(0.6)

    def test_passes_rag_contexts_through(self, predictor):
        ctxs = [{"foo": "bar"}]
        info = {"top_score": 0.1}
        d = predictor.decide(kb_context={"suggestions": []}, rag_contexts=ctxs, rag_info=info)
        assert d.rag_contexts == ctxs
        assert d.rag_info == info


class TestShouldEnforceDomain:
    def test_below_threshold_returns_true(self, predictor):
        assert predictor.should_enforce_domain(rag_top_score=0.5) is True

    def test_at_or_above_threshold_returns_false(self, predictor, settings):
        assert predictor.should_enforce_domain(rag_top_score=settings.kb_domain_override_conf) is False
        assert predictor.should_enforce_domain(rag_top_score=0.95) is False


class TestCascadePriority:
    """Level 1 should always win over Level 2 when both apply."""

    def test_level1_beats_level2(self, predictor):
        kb = {
            "source": "eCRF_direct_match",
            "confidence": 0.95,
            "suggestions": [{"domain": "AE"}],
        }
        d = predictor.decide(kb_context=kb)
        assert d.level == 1

    def test_level2_beats_level3(self, predictor):
        d = predictor.decide(
            kb_context={
                "source": "KB_semantic",
                "confidence": 0.9,
                "suggestions": [{"domain": "AE"}],
            },
            rag_contexts=[{"x": 1}],
            rag_info={"top_score": 0.95},
        )
        assert d.level == 2


def test_decision_dataclass_defaults():
    d = CascadeDecision(level=4, source="LLM", short_circuited=False)
    assert d.recommendations == []
    assert d.rag_contexts == []
    assert d.rag_info == {}
    assert d.confidence == 0.0
