"""Unit tests for ``RecommendationNormalizer``."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.processors.recommendation_normalizer import RecommendationNormalizer


@pytest.fixture
def normalizer() -> RecommendationNormalizer:
    return RecommendationNormalizer(debug=False, domain_override_threshold=0.85)


class TestNormalize:
    def test_empty_input(self, normalizer):
        assert normalizer.normalize(table_name="T", variable_name="x", domain_recs=[]) == []

    def test_enforces_target_domain(self, normalizer):
        recs = [
            {"domain": "DM", "sdtm_variable": "SUBJID", "sdtm_variable_type": "standard"},
            {"domain": "AE", "sdtm_variable": "AETERM", "sdtm_variable_type": "standard"},
        ]
        with patch(
            "src.processors.deterministic_validator._get_domain_standard_vars",
            return_value={"AETERM"},
        ):
            out = normalizer.normalize(
                table_name="T",
                variable_name="x",
                domain_recs=recs,
                target_domain="AE",
            )
        assert all(r["domain"] == "AE" for r in out)

    def test_expands_supp_alias(self, normalizer):
        recs = [{"domain": "SUPPAE", "sdtm_variable": "QVAL", "sdtm_variable_type": "supp", "supp_variable": "AESEV"}]
        with patch(
            "src.processors.deterministic_validator._get_domain_standard_vars",
            return_value=set(),
        ):
            out = normalizer.normalize(
                table_name="T",
                variable_name="severity",
                domain_recs=recs,
                target_domain="AE",
            )
        assert len(out) == 1
        assert out[0]["sdtm_variable_type"] == "supp"

    def test_caps_to_max_recommendations(self):
        norm = RecommendationNormalizer(max_recommendations=2)
        recs = [{"domain": "AE", "sdtm_variable": f"AE{i:02d}", "score": i / 10} for i in range(5)]
        with patch(
            "src.processors.deterministic_validator._get_domain_standard_vars",
            return_value=set(),
        ):
            out = norm.normalize(
                table_name="T",
                variable_name="x",
                domain_recs=recs,
                target_domain=None,
            )
        assert len(out) == 2

    def test_sorts_by_descending_score(self, normalizer):
        recs = [
            {"domain": "AE", "sdtm_variable": "A1", "score": 0.3},
            {"domain": "AE", "sdtm_variable": "A2", "score": 0.9},
            {"domain": "AE", "sdtm_variable": "A3", "score": 0.5},
        ]
        with patch(
            "src.processors.deterministic_validator._get_domain_standard_vars",
            return_value=set(),
        ):
            out = normalizer.normalize(
                table_name="T",
                variable_name="x",
                domain_recs=recs,
            )
        scores = [r["score"] for r in out]
        assert scores == sorted(scores, reverse=True)

    def test_enforce_false_keeps_non_target_domain(self, normalizer):
        recs = [{"domain": "DM", "sdtm_variable": "SUBJID", "sdtm_variable_type": "standard"}]
        with patch(
            "src.processors.deterministic_validator._get_domain_standard_vars",
            return_value={"SUBJID"},
        ):
            out = normalizer.normalize(
                table_name="T",
                variable_name="x",
                domain_recs=recs,
                target_domain="AE",
                enforce_domain=False,
            )
        assert len(out) == 1
        assert out[0]["domain"] == "DM"

    def test_when_clause_decomposed(self, normalizer):
        recs = [{"domain": "FA", "sdtm_variable": "FAORRES when FATESTCD=X"}]
        with patch(
            "src.processors.deterministic_validator._get_domain_standard_vars",
            return_value={"FAORRES"},
        ):
            out = normalizer.normalize(
                table_name="T",
                variable_name="x",
                domain_recs=recs,
                target_domain="FA",
            )
        assert out and out[0]["sdtm_variable"] == "FAORRES"
        assert out[0]["testcd"] == "X"

    def test_general_when_clause_uses_structured_conditions(self, normalizer):
        recs = [{"domain": "EC", "sdtm_variable": "ECDOSE when ECMOOD=ADMINISTERED"}]
        with patch(
            "src.processors.deterministic_validator._get_domain_standard_vars",
            return_value={"ECDOSE"},
        ):
            out = normalizer.normalize(
                table_name="T",
                variable_name="dose",
                domain_recs=recs,
                target_domain="EC",
            )

        assert out[0]["sdtm_variable"] == "ECDOSE"
        assert out[0]["conditions"] == [{"variable": "ECMOOD", "value": "ADMINISTERED"}]

    def test_uses_injected_is_valid_domain(self):
        hits = []

        def fake_valid(d: str) -> bool:
            hits.append(d)
            return d == "AE"

        norm = RecommendationNormalizer(is_valid_domain_fn=fake_valid)
        recs = [{"domain": "XX|AE", "sdtm_variable": "AETERM"}]
        with patch(
            "src.processors.deterministic_validator._get_domain_standard_vars",
            return_value={"AETERM"},
        ):
            norm.normalize(
                table_name="T",
                variable_name="x",
                domain_recs=recs,
                target_domain=None,
            )
        assert "AE" in hits


class TestStaticHelpers:
    def test_filter_by_domain_wraps_pure_helper(self):
        recs = [{"domain": "AE"}]
        assert RecommendationNormalizer.filter_by_domain(recs, "AE") == recs
        assert RecommendationNormalizer.filter_by_domain(recs, "DM") == []


class TestConstructorValidation:
    def test_override_threshold_clamped(self):
        assert RecommendationNormalizer(domain_override_threshold=1.5).domain_override_threshold == 1.0
        assert RecommendationNormalizer(domain_override_threshold=-0.3).domain_override_threshold == 0.0

    def test_max_recommendations_floor_one(self):
        assert RecommendationNormalizer(max_recommendations=0).max_recommendations == 1
