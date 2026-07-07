"""Characterization (golden) tests for the normaliser pipeline.

Captures the current end-to-end behaviour of the pure normaliser helpers
so we can safely replace ``PostprocessMixin`` call-sites in upcoming
refactors. The JSON snapshots live under ``tests/fixtures/golden/``.

Run ``UPDATE_GOLDENS=1 pytest tests/characterization`` (PowerShell:
``$env:UPDATE_GOLDENS='1'; pytest tests/characterization``) to refresh
after an intentional behaviour change.
"""

from __future__ import annotations

from typing import Dict

import pytest

from src.processors.normalizer import (
    classify_variable_type,
    decompose_when_clause,
    dedupe_by_key,
    filter_recs_by_domain,
    resolve_multi_domain,
    to_cleaned_dict,
)

VALID_DOMAINS = {"AE", "FA", "LB", "VS", "DM", "EX", "CM"}


def _is_valid(domain: str) -> bool:
    return domain in VALID_DOMAINS


def _run_pipeline(variable_name: str, target_domain, raw_recs):
    """Mimic the PostprocessMixin orchestration order using pure helpers."""
    stage1 = filter_recs_by_domain(raw_recs, target_domain)
    stage2 = [decompose_when_clause(r) for r in stage1]
    stage3 = [resolve_multi_domain(r, _is_valid) for r in stage2]
    for rec in stage3:
        rec["sdtm_variable_type"] = classify_variable_type(rec, variable_name)
    stage4 = dedupe_by_key(stage3)
    return [to_cleaned_dict(r, variable_name=variable_name) for r in stage4]


class TestCharacterizationPipeline:
    def test_ae_aeterm_basic(self, golden_loader):
        raw = [
            {
                "domain": "ae",
                "sdtm_variable": "aeterm",
                "sdtm_variable_type": "standard",
                "score": 0.92,
                "source": "LLM",
            }
        ]
        out = _run_pipeline("不良事件名称", "AE", raw)
        expected = golden_loader("normalizer/ae_aeterm_basic.json", out)
        assert out == expected

    def test_filters_wrong_domain(self, golden_loader):
        raw = [
            {"domain": "DM", "sdtm_variable": "SUBJID", "sdtm_variable_type": "standard"},
            {"domain": "AE", "sdtm_variable": "AETERM", "sdtm_variable_type": "standard"},
        ]
        out = _run_pipeline("AETERM", "AE", raw)
        expected = golden_loader("normalizer/filters_wrong_domain.json", out)
        assert out == expected

    def test_when_clause_expansion(self, golden_loader):
        raw = [
            {
                "domain": "FA",
                "sdtm_variable": "FAORRES when FATESTCD=THDIAG",
                "sdtm_variable_type": "standard",
                "score": 0.85,
                "source": "LLM",
            }
        ]
        out = _run_pipeline("THDIAG", "FA", raw)
        expected = golden_loader("normalizer/when_clause_expansion.json", out)
        assert out == expected
        assert out[0]["testcd"] == "THDIAG"

    def test_multi_domain_resolution(self, golden_loader):
        raw = [
            {
                "domain": "AE|FA",
                "sdtm_variable": "AETERM|FAORRES",
                "sdtm_variable_type": "standard",
                "score": 0.75,
                "source": "LLM",
            }
        ]
        out = _run_pipeline("term", None, raw)
        expected = golden_loader("normalizer/multi_domain_resolution.json", out)
        assert out == expected

    def test_supp_variable_promotion(self, golden_loader):
        raw = [
            {
                "domain": "SUPPAE",
                "sdtm_variable": "QVAL",
                "sdtm_variable_type": "standard",
                "supp_dataset": "SUPPAE",
                "supp_variable": "AESEV",
                "score": 0.88,
                "source": "LLM",
            }
        ]
        out = _run_pipeline("严重性", None, raw)
        expected = golden_loader("normalizer/supp_variable_promotion.json", out)
        assert out == expected
        assert out[0]["sdtm_variable_type"] == "supp"

    def test_comment_variable_autopromoted(self, golden_loader):
        raw = [
            {
                "domain": "AE",
                "sdtm_variable": "AEOTHER",
                "sdtm_variable_type": "standard",
                "score": 0.8,
                "source": "LLM",
            }
        ]
        out = _run_pipeline("其他说明", "AE", raw)
        expected = golden_loader("normalizer/comment_autopromoted.json", out)
        assert out == expected
        assert out[0]["sdtm_variable_type"] == "supp"

    def test_dedupe_keeps_highest_score(self, golden_loader):
        raw = [
            {"domain": "AE", "sdtm_variable": "AETERM", "sdtm_variable_type": "standard", "score": 0.6, "source": "KB"},
            {
                "domain": "AE",
                "sdtm_variable": "AETERM",
                "sdtm_variable_type": "standard",
                "score": 0.95,
                "source": "LLM",
            },
            {
                "domain": "AE",
                "sdtm_variable": "AETERM",
                "sdtm_variable_type": "standard",
                "score": 0.85,
                "source": "RAG",
            },
        ]
        out = _run_pipeline("term", "AE", raw)
        expected = golden_loader("normalizer/dedupe_highest_score.json", out)
        assert out == expected
        assert len(out) == 1
        assert out[0]["score"] == pytest.approx(0.95)

    def test_complex_mixed_batch(self, golden_loader):
        """Realistic multi-variable batch combining all pipeline stages."""
        raw = [
            {
                "domain": "DM",
                "sdtm_variable": "SUBJID",
                "sdtm_variable_type": "standard",
                "score": 0.99,
                "source": "KB",
            },
            {"domain": "XY", "sdtm_variable": "BOGUS", "sdtm_variable_type": "standard", "score": 0.3, "source": "LLM"},
            {
                "domain": "LB|FA",
                "sdtm_variable": "LBORRES|FAORRES",
                "sdtm_variable_type": "standard",
                "score": 0.7,
                "source": "LLM",
            },
        ]
        out = _run_pipeline("x", None, raw)
        expected = golden_loader("normalizer/complex_mixed_batch.json", out)
        assert out == expected


class TestCharacterizationStability:
    """Cheap checks: the pipeline must be deterministic and pure."""

    def test_pipeline_is_idempotent(self):
        raw = [{"domain": "AE", "sdtm_variable": "AETERM", "sdtm_variable_type": "standard", "score": 0.9}]
        first = _run_pipeline("x", "AE", list(raw))
        second = _run_pipeline("x", "AE", list(raw))
        assert first == second

    def test_does_not_mutate_input(self):
        raw: list[Dict[str, object]] = [
            {"domain": "ae", "sdtm_variable": "aeterm", "sdtm_variable_type": "standard", "score": 0.9}
        ]
        original = [dict(r) for r in raw]
        _run_pipeline("x", "AE", raw)
        assert raw == original
