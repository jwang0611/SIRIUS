"""Characterization (golden) tests for the cascade short-circuit logic."""

from __future__ import annotations

import pytest

from src.config.settings import CascadeSettings
from src.processors.cascade import CascadePredictor


@pytest.fixture
def predictor() -> CascadePredictor:
    return CascadePredictor(
        CascadeSettings(
            enabled=True,
            kb_min_confidence=0.8,
            kb_high_conf=0.85,
            rag_high_conf=0.7,
            kb_domain_override_conf=0.85,
        )
    )


def _decision_to_dict(d) -> dict:
    return {
        "level": d.level,
        "source": d.source,
        "short_circuited": d.short_circuited,
        "confidence": round(d.confidence, 4),
        "reason": d.reason,
        "rec_count": len(d.recommendations),
        "rag_context_count": len(d.rag_contexts),
    }


class TestCascadeGolden:
    def test_kb_direct_match(self, predictor, golden_loader):
        d = predictor.decide(
            kb_context={
                "source": "eCRF_direct_match",
                "confidence": 0.95,
                "suggestions": [{"domain": "AE", "sdtm_variable": "AETERM"}],
            }
        )
        snapshot = _decision_to_dict(d)
        expected = golden_loader("cascade/kb_direct_match.json", snapshot)
        assert snapshot == expected

    def test_kb_high_conf(self, predictor, golden_loader):
        d = predictor.decide(
            kb_context={
                "source": "KB_semantic",
                "confidence": 0.9,
                "suggestions": [{"domain": "AE", "sdtm_variable": "AETERM"}],
            }
        )
        snapshot = _decision_to_dict(d)
        expected = golden_loader("cascade/kb_high_conf.json", snapshot)
        assert snapshot == expected

    def test_rag_high_conf(self, predictor, golden_loader):
        d = predictor.decide(
            kb_context={"suggestions": [], "confidence": 0.0},
            rag_contexts=[{"snippet": "AETERM maps to AE domain"}],
            rag_info={"top_score": 0.85},
        )
        snapshot = _decision_to_dict(d)
        expected = golden_loader("cascade/rag_high_conf.json", snapshot)
        assert snapshot == expected

    def test_llm_fallthrough(self, predictor, golden_loader):
        d = predictor.decide(
            kb_context={"suggestions": [], "confidence": 0.3},
            rag_contexts=[{"snippet": "weak match"}],
            rag_info={"top_score": 0.4},
        )
        snapshot = _decision_to_dict(d)
        expected = golden_loader("cascade/llm_fallthrough.json", snapshot)
        assert snapshot == expected

    def test_empty_context_falls_through(self, predictor, golden_loader):
        d = predictor.decide(kb_context={})
        snapshot = _decision_to_dict(d)
        expected = golden_loader("cascade/empty_context.json", snapshot)
        assert snapshot == expected


class TestCascadeStability:
    def test_same_input_same_output(self, predictor):
        ctx = {
            "source": "eCRF_direct_match",
            "confidence": 0.95,
            "suggestions": [{"domain": "AE"}],
        }
        d1 = predictor.decide(kb_context=ctx)
        d2 = predictor.decide(kb_context=ctx)
        assert _decision_to_dict(d1) == _decision_to_dict(d2)

    def test_tuning_kb_high_threshold_changes_outcome(self):
        ctx = {
            "source": "KB_semantic",
            "confidence": 0.82,
            "suggestions": [{"domain": "AE"}],
        }
        low_bar = CascadePredictor(
            CascadeSettings(
                kb_min_confidence=0.8,
                kb_high_conf=0.80,
                rag_high_conf=0.7,
            )
        )
        high_bar = CascadePredictor(
            CascadeSettings(
                kb_min_confidence=0.8,
                kb_high_conf=0.90,
                rag_high_conf=0.7,
            )
        )
        assert low_bar.decide(kb_context=ctx).level == 2
        assert high_bar.decide(kb_context=ctx).level == 4
