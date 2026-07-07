"""Unit tests for :mod:`src.knowledge_base.ranking_utils`."""

from __future__ import annotations

import pandas as pd
import pytest

from src.knowledge_base import ranking_utils


class TestExtractKeyTerms:
    def test_empty_input(self):
        assert ranking_utils.extract_key_terms("") == []
        assert ranking_utils.extract_key_terms(None) == []  # type: ignore[arg-type]

    def test_filters_stop_words(self):
        assert ranking_utils.extract_key_terms("the adverse event") == ["adverse", "event"]

    def test_short_terms_dropped(self):
        assert ranking_utils.extract_key_terms("AE is bad") == ["bad"]

    def test_punctuation_stripped(self):
        assert set(ranking_utils.extract_key_terms("adverse-event, reaction!")) == {
            "adverse",
            "event",
            "reaction",
        }

    def test_lowercase_output(self):
        assert ranking_utils.extract_key_terms("AETERM") == ["aeterm"]


class TestValidateNamingConvention:
    def test_empty_name(self):
        assert ranking_utils.validate_naming_convention("", "AE") == 0.0

    def test_too_long_invalid(self):
        assert ranking_utils.validate_naming_convention("AETOOLONGNAME", "AE") == 0.0

    def test_length_only(self):
        assert ranking_utils.validate_naming_convention("xx", "AE") == pytest.approx(0.3)

    def test_domain_prefix_bonus(self):
        assert ranking_utils.validate_naming_convention("AETERM", "AE") == pytest.approx(1.0)

    def test_valid_chars_bonus(self):
        # matches A-Z0-9_ but no domain prefix
        assert ranking_utils.validate_naming_convention("ABCDEF", "XX") == pytest.approx(0.6)

    def test_invalid_chars_no_bonus(self):
        assert ranking_utils.validate_naming_convention("ae-term", "AE") == pytest.approx(0.3)

    def test_boundary_eight_chars(self):
        assert ranking_utils.validate_naming_convention("ABCDEFGH", "") == pytest.approx(0.6)


class TestCalculateConfidence:
    def test_empty(self):
        assert ranking_utils.calculate_confidence([]) == 0.0

    def test_single_suggestion(self):
        assert ranking_utils.calculate_confidence([{"score": 0.7}]) == pytest.approx(0.7)

    def test_runner_up_bonus(self):
        suggestions = [{"score": 0.6}, {"score": 0.4}]
        assert ranking_utils.calculate_confidence(suggestions) == pytest.approx(0.7)

    def test_runner_up_too_weak_no_bonus(self):
        suggestions = [{"score": 0.6}, {"score": 0.2}]
        assert ranking_utils.calculate_confidence(suggestions) == pytest.approx(0.6)

    def test_capped_at_one(self):
        suggestions = [{"score": 0.99}, {"score": 0.95}]
        assert ranking_utils.calculate_confidence(suggestions) == 1.0

    def test_missing_score_defaults_to_zero(self):
        assert ranking_utils.calculate_confidence([{}, {}]) == 0.0

    def test_invalid_score_handled(self):
        assert ranking_utils.calculate_confidence([{"score": "oops"}]) == 0.0


class TestRankSuggestions:
    def _df(self, rows):
        return pd.DataFrame(rows)

    def test_empty_dataframe(self):
        df = self._df([])
        assert ranking_utils.rank_suggestions(df, query="AETERM") == []

    def test_variable_name_match_scores_highest(self):
        df = self._df(
            [
                {
                    "Dataset Name": "AE",
                    "Variable Name": "AETERM",
                    "Variable Label": "Term",
                    "Role": "Topic",
                    "Core": "Req",
                    "Type": "Char",
                    "CDISC Notes": "n",
                },
                {
                    "Dataset Name": "DM",
                    "Variable Name": "SUBJID",
                    "Variable Label": "Subject",
                    "Role": "Identifier",
                    "Core": "Req",
                    "Type": "Char",
                    "CDISC Notes": "n",
                },
            ]
        )
        out = ranking_utils.rank_suggestions(df, query="AETERM")
        assert out[0]["sdtm_variable"] == "AETERM"
        assert out[0]["score"] >= 0.4

    def test_label_match_contributes(self):
        df = self._df(
            [
                {
                    "Dataset Name": "AE",
                    "Variable Name": "X1",
                    "Variable Label": "reaction term",
                    "Role": "Topic",
                    "Core": "Req",
                    "Type": "Char",
                },
            ]
        )
        out = ranking_utils.rank_suggestions(df, query="reaction")
        assert out[0]["score"] == pytest.approx(0.4)  # 0.3 label + 0.1 Req core

    def test_context_role_match(self):
        df = self._df(
            [
                {
                    "Dataset Name": "AE",
                    "Variable Name": "X",
                    "Variable Label": "y",
                    "Role": "Qualifier",
                    "Core": "Perm",
                },
            ]
        )
        out = ranking_utils.rank_suggestions(df, query="unrelated", context="qualifier assessment")
        assert out[0]["score"] == pytest.approx(0.2)

    def test_top_n_applied(self):
        rows = [
            {"Dataset Name": "AE", "Variable Name": f"V{i}", "Variable Label": "", "Role": "", "Core": ""}
            for i in range(15)
        ]
        out = ranking_utils.rank_suggestions(self._df(rows), query="v", top_n=5)
        assert len(out) == 5

    def test_sorted_descending(self):
        df = self._df(
            [
                {"Dataset Name": "AE", "Variable Name": "NOMATCH", "Variable Label": "x", "Role": "", "Core": ""},
                {"Dataset Name": "AE", "Variable Name": "AETERM", "Variable Label": "term", "Role": "", "Core": "Req"},
            ]
        )
        out = ranking_utils.rank_suggestions(df, query="AETERM")
        scores = [s["score"] for s in out]
        assert scores == sorted(scores, reverse=True)


class TestDomainDescription:
    def test_known_domains(self):
        assert ranking_utils.get_domain_description("AE").startswith("Adverse Events")
        assert "Demographics" in ranking_utils.get_domain_description("DM")

    def test_unknown_domain_default(self):
        assert "Clinical data domain" in ranking_utils.get_domain_description("ZZZ")

    def test_empty_domain(self):
        assert ranking_utils.get_domain_description("") == "Unknown domain - Clinical data domain"


class TestFindSimilarVariables:
    def test_empty_target(self):
        assert ranking_utils.find_similar_variable_names("", ["AETERM"]) == []

    def test_substring_match(self):
        candidates = ["AETERM", "AEDECOD", "SUBJID"]
        out = ranking_utils.find_similar_variable_names("AE", candidates)
        assert "AETERM" in out and "AEDECOD" in out

    def test_reverse_substring(self):
        out = ranking_utils.find_similar_variable_names("AETERMXX", ["AETERM"])
        assert out == ["AETERM"]

    def test_top_n_limit(self):
        candidates = [f"AE{i:02d}" for i in range(10)]
        out = ranking_utils.find_similar_variable_names("AE", candidates, top_n=3)
        assert len(out) == 3


class TestGenerateFunctionalQuery:
    def test_identifier_role(self):
        q = ranking_utils.generate_functional_query({"Role": "Identifier", "Variable Label": "X"})
        assert q == "Map subject identifier field"

    def test_topic_role_uses_label(self):
        q = ranking_utils.generate_functional_query({"Role": "Topic", "Variable Label": "Adverse Event"})
        assert q == "Map the main topic field for adverse event"

    def test_timing_role(self):
        q = ranking_utils.generate_functional_query({"Role": "Timing", "Variable Label": "Start Date"})
        assert "timing information" in q

    def test_qualifier_role(self):
        q = ranking_utils.generate_functional_query({"Role": "Qualifier", "Variable Label": "Severity"})
        assert "qualifier information" in q

    def test_unknown_role_falls_back(self):
        q = ranking_utils.generate_functional_query({"Role": "Other", "Variable Label": "Stuff"})
        assert q == "Map field for stuff"
