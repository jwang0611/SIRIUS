"""Unit tests for ``src.processors.deterministic_validator``."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.processors.deterministic_validator import (
    CROSS_DOMAIN_PREFIXES,
    MAX_VARIABLE_NAME_LENGTH,
    DeterministicValidator,
    find_closest_valid_variable,
    validate_variable_domain_consistency,
)


class TestValidateVariableDomainConsistency:
    def test_matching_prefix(self):
        ok, desc = validate_variable_domain_consistency("AE", "AETERM")
        assert ok is True
        assert desc is None

    def test_mismatched_prefix(self):
        ok, desc = validate_variable_domain_consistency("AE", "LBTEST")
        assert ok is False
        assert "LB" in desc

    def test_cross_domain_variable_ok(self):
        ok, _ = validate_variable_domain_consistency("AE", "USUBJID")
        assert ok is True

    def test_suppae_maps_to_ae_prefix(self):
        ok, _ = validate_variable_domain_consistency("SUPPAE", "AETERM")
        assert ok is True

    def test_legitimate_cross_dm_rf(self):
        ok, _ = validate_variable_domain_consistency("DM", "RFSTDTC")
        assert ok is True

    def test_empty_inputs_are_consistent(self):
        assert validate_variable_domain_consistency("", "AETERM") == (True, None)
        assert validate_variable_domain_consistency("AE", "") == (True, None)


class TestFindClosestValidVariable:
    def test_empty_returns_none(self):
        assert find_closest_valid_variable("", "AE") is None
        assert find_closest_valid_variable("AETERM", "") is None

    def test_known_exact_match_via_standard_vars(self):
        result = find_closest_valid_variable("AETERM", "AE", standard_vars={"AETERM", "AESEV"})
        assert result == "AETERM"

    def test_common_suffix_strip(self):
        result = find_closest_valid_variable("AETERMDESC", "AE", standard_vars={"AETERM", "AESEV"})
        assert result == "AETERM"

    def test_similarity_match(self):
        result = find_closest_valid_variable("AETRMX", "AE", standard_vars={"AETERM", "AESEV"}, min_similarity=0.6)
        assert result == "AETERM"

    def test_no_match_when_similarity_too_low(self):
        assert find_closest_valid_variable("ZZZZZZ", "AE", standard_vars={"AETERM"}) is None

    def test_empty_vars_set_no_similarity_match(self):
        with patch(
            "src.processors.deterministic_validator._get_domain_standard_vars",
            return_value=set(),
        ):
            assert find_closest_valid_variable("UNRELATEDXX", "AE") is None


class TestDeterministicValidator:
    @pytest.fixture
    def validator(self):
        return DeterministicValidator(debug=False)

    def test_long_variable_name_corrected_via_suffix(self, validator):
        with patch(
            "src.processors.deterministic_validator._get_domain_standard_vars",
            return_value={"AETERM", "AESEV"},
        ):
            rec, issues = validator.validate_and_correct(
                {"domain": "AE", "sdtm_variable": "AETERMDESC", "sdtm_variable_type": "standard"},
                variable_name="term",
            )
        assert rec["sdtm_variable"] == "AETERM"
        assert rec.get("variable_name_corrected") is True
        assert any("variable_name_corrected" in i for i in issues)

    def test_long_variable_name_truncated_when_no_match(self, validator):
        with patch(
            "src.processors.deterministic_validator._get_domain_standard_vars",
            return_value={"AETERM"},
        ):
            rec, issues = validator.validate_and_correct(
                {
                    "domain": "AE",
                    "sdtm_variable": "UNRELATEDXY",
                    "sdtm_variable_type": "standard",
                    "score": 0.9,
                },
                variable_name="term",
            )
        assert len(rec["sdtm_variable"]) <= MAX_VARIABLE_NAME_LENGTH
        assert rec.get("variable_name_truncated") is True
        assert rec["score"] <= 0.5
        assert any("variable_name_truncated" in i for i in issues)

    def test_invalid_domain_corrected_via_hint(self, validator):
        with patch(
            "src.processors.deterministic_validator.is_valid_domain",
            side_effect=lambda d: d == "AE",
        ):
            rec, issues = validator.validate_and_correct(
                {"domain": "XX", "sdtm_variable": "AETERM", "score": 0.9},
                variable_name="term",
                domain_hint="AE",
            )
        assert rec["domain"] == "AE"
        assert rec["original_domain"] == "XX"
        assert rec["invalid_domain_corrected"] is True
        assert rec["score"] <= 0.6
        assert any("invalid_domain_corrected" in i for i in issues)

    def test_invalid_domain_no_fallback(self, validator):
        with patch(
            "src.processors.deterministic_validator.is_valid_domain",
            return_value=False,
        ):
            _rec, issues = validator.validate_and_correct(
                {"domain": "XX", "sdtm_variable": "FOO"},
                variable_name="term",
            )
        assert any("invalid_domain" in i for i in issues)

    def test_prefix_mismatch_flagged(self, validator):
        with patch(
            "src.processors.deterministic_validator._get_domain_standard_vars",
            return_value={"AETERM"},
        ):
            rec, issues = validator.validate_and_correct(
                {"domain": "AE", "sdtm_variable": "LBTEST", "sdtm_variable_type": "standard"},
                variable_name="x",
            )
        assert rec.get("domain_prefix_mismatch") is True
        assert any("domain_prefix_mismatch" in i for i in issues)

    def test_supp_qval_skips_standard_domain_prefix_check(self, validator):
        rec, issues = validator.validate_and_correct(
            {
                "domain": "FA",
                "sdtm_variable": "QVAL",
                "sdtm_variable_type": "supp",
                "supp_dataset": "SUPPFA",
                "supp_variable": "FANOTE",
            },
            variable_name="fanote",
        )

        assert rec.get("domain_prefix_mismatch") is not True
        assert not any("domain_prefix_mismatch" in issue for issue in issues)

    def test_non_standard_variable_flagged(self, validator):
        with patch(
            "src.processors.deterministic_validator._get_domain_standard_vars",
            return_value={"AETERM", "AESEV"},
        ):
            rec, issues = validator.validate_and_correct(
                {"domain": "AE", "sdtm_variable": "AEFOO", "sdtm_variable_type": "standard"},
                variable_name="x",
            )
        assert rec.get("non_standard_variable") is True
        assert any("non_standard_variable" in i for i in issues)

    def test_cross_domain_variable_not_flagged(self, validator):
        with patch(
            "src.processors.deterministic_validator._get_domain_standard_vars",
            return_value={"AETERM"},
        ):
            rec, _issues = validator.validate_and_correct(
                {"domain": "AE", "sdtm_variable": "USUBJID", "sdtm_variable_type": "standard"},
                variable_name="x",
            )
        assert rec.get("non_standard_variable") is not True

    def test_not_submitted_skips_all_validation(self, validator):
        rec, issues = validator.validate_and_correct(
            {"domain": "AE", "sdtm_variable": "NOT SUBMITTED", "sdtm_variable_type": "standard"},
            variable_name="x",
        )
        assert rec["sdtm_variable"] == "NOT SUBMITTED"
        assert not issues
        assert rec.get("variable_name_corrected") is None
        assert rec.get("variable_name_truncated") is None

    def test_not_submitted_case_insensitive(self, validator):
        rec, issues = validator.validate_and_correct(
            {"domain": "AE", "sdtm_variable": "not submitted", "sdtm_variable_type": "standard"},
            variable_name="x",
        )
        assert rec["sdtm_variable"] == "not submitted"
        assert not issues

    def test_compound_variable_with_slash_skips_validation(self, validator):
        rec, issues = validator.validate_and_correct(
            {"domain": "AE", "sdtm_variable": "AETERM/AESEV", "sdtm_variable_type": "standard"},
            variable_name="x",
        )
        assert rec["sdtm_variable"] == "AETERM/AESEV"
        assert not issues
        assert rec.get("variable_name_corrected") is None
        assert rec.get("variable_name_truncated") is None

    def test_compound_domain_with_pipe_skips_validation(self, validator):
        rec, issues = validator.validate_and_correct(
            {"domain": "AE|FA", "sdtm_variable": "AETERM", "sdtm_variable_type": "standard"},
            variable_name="x",
        )
        assert rec["domain"] == "AE|FA"
        assert rec["sdtm_variable"] == "AETERM"
        assert not issues
        assert rec.get("invalid_domain_corrected") is None

    def test_compound_both_domain_and_variable(self, validator):
        rec, issues = validator.validate_and_correct(
            {"domain": "AE|FA", "sdtm_variable": "AETERM/FAORRES", "sdtm_variable_type": "standard"},
            variable_name="x",
        )
        assert rec["domain"] == "AE|FA"
        assert rec["sdtm_variable"] == "AETERM/FAORRES"
        assert not issues

    def test_semicolon_separator_skips_validation(self, validator):
        rec, issues = validator.validate_and_correct(
            {"domain": "AE;FA", "sdtm_variable": "AETERM", "sdtm_variable_type": "standard"},
            variable_name="x",
        )
        assert rec["domain"] == "AE;FA"
        assert not issues

    def test_does_not_mutate_input(self, validator):
        input_rec = {"domain": "AE", "sdtm_variable": "AETERM"}
        with patch(
            "src.processors.deterministic_validator._get_domain_standard_vars",
            return_value={"AETERM"},
        ):
            rec, _ = validator.validate_and_correct(input_rec, variable_name="x")
        rec["foo"] = "bar"
        assert "foo" not in input_rec

    def test_cache_reuses_vars_lookup(self, validator):
        with patch(
            "src.processors.deterministic_validator._get_domain_standard_vars",
            return_value={"AETERM"},
        ) as mock_get:
            validator.validate_and_correct(
                {"domain": "AE", "sdtm_variable": "AETERM", "sdtm_variable_type": "standard"},
                variable_name="x",
            )
            validator.validate_and_correct(
                {"domain": "AE", "sdtm_variable": "AETERM", "sdtm_variable_type": "standard"},
                variable_name="x",
            )
        assert mock_get.call_count == 1


def test_cross_domain_prefixes_includes_expected():
    for v in ("USUBJID", "STUDYID", "DOMAIN", "VISIT"):
        assert v in CROSS_DOMAIN_PREFIXES
