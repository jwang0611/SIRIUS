"""Unit tests for :mod:`src.spec_mapper.rules.transformation_helpers`."""

from __future__ import annotations

from src.spec_mapper.rules import transformation_helpers as rules


class TestIsNotSubmitted:
    def test_empty(self):
        assert rules.is_not_submitted("") is False
        assert rules.is_not_submitted(None) is False

    def test_exact_match(self):
        assert rules.is_not_submitted("NOT SUBMITTED") is True

    def test_case_insensitive(self):
        assert rules.is_not_submitted("not submitted") is True
        assert rules.is_not_submitted("Not Submitted") is True

    def test_whitespace_stripped(self):
        assert rules.is_not_submitted("  NOT SUBMITTED  ") is True

    def test_not_a_match(self):
        assert rules.is_not_submitted("SUBJID") is False


class TestExtractSuppAdditionalCondition:
    def test_empty(self):
        assert rules.extract_supp_additional_condition("") == ""
        assert rules.extract_supp_additional_condition(None) == ""

    def test_qnam_only(self):
        assert rules.extract_supp_additional_condition("when QNAM=SITENAM") == ""

    def test_single_additional_condition(self):
        out = rules.extract_supp_additional_condition("when QNAM=MIRESOTH when MITEST=MLH1")
        assert out == "when MITEST=MLH1"

    def test_multiple_additional_conditions(self):
        out = rules.extract_supp_additional_condition("when QNAM=XXX when A=1 when B=2")
        assert out == "when A=1 when B=2"

    def test_case_insensitive(self):
        out = rules.extract_supp_additional_condition("When QNAM=X WHEN A=1")
        assert out == "when A=1"


class TestGetVariableHints:
    def test_empty_input_no_hints(self):
        assert rules.get_variable_hints(None) == []
        assert rules.get_variable_hints("") == []

    def test_spid_match(self):
        hints = rules.get_variable_hints("CMSPID", spid_grpid_note="SPID_NOTE")
        assert hints == ["SPID_NOTE"]

    def test_grpid_match(self):
        hints = rules.get_variable_hints(
            "AEGRPID",
            spid_grpid_patterns=("SPID", "GRPID"),
            spid_grpid_note="SPID_NOTE",
        )
        assert hints == ["SPID_NOTE"]

    def test_dtc_match(self):
        hints = rules.get_variable_hints("AESTDTC", dtc_note="DTC_NOTE")
        assert hints == ["DTC_NOTE"]

    def test_dtc_match_via_qnam(self):
        hints = rules.get_variable_hints("QVAL", qnam_value="OTHRDTC", dtc_note="DTC_NOTE")
        assert hints == ["DTC_NOTE"]

    def test_both_hints(self):
        hints = rules.get_variable_hints(
            "AESPIDDTC",  # matches both SPID and DTC suffixes
            spid_grpid_note="SPID_NOTE",
            dtc_note="DTC_NOTE",
        )
        assert hints == ["DTC_NOTE"]  # DTC wins (endswith check)
        # Reset: AESPID then AEDTC via qnam
        hints = rules.get_variable_hints(
            "AESPID",
            qnam_value="AEDTC",
            spid_grpid_note="SPID_NOTE",
            dtc_note="DTC_NOTE",
        )
        assert hints == ["SPID_NOTE", "DTC_NOTE"]

    def test_no_note_produces_nothing(self):
        assert rules.get_variable_hints("CMSPID", spid_grpid_note="") == []

    def test_unknown_variable(self):
        assert rules.get_variable_hints("RANDOMVAR", spid_grpid_note="A", dtc_note="B") == []


class TestAppendHintsToTransformation:
    def test_no_hints_returns_unchanged(self):
        assert rules.append_hints_to_transformation("[RAW]DM.SUBJID") == "[RAW]DM.SUBJID"

    def test_codelist_note_added(self):
        out = rules.append_hints_to_transformation(
            "[RAW]AE.AESEV",
            needs_codelist_hint=True,
            codelist_note="CL",
        )
        assert out == "[RAW]AE.AESEV;\nCL"

    def test_extra_hints_appended(self):
        out = rules.append_hints_to_transformation(
            "[RAW]AE.AESTDTC",
            extra_hints=["DTC"],
        )
        assert out == "[RAW]AE.AESTDTC;\nDTC"

    def test_deduplicates_hints(self):
        out = rules.append_hints_to_transformation(
            "X",
            extra_hints=["A", "A", "B"],
        )
        assert out == "X;\nA\nB"

    def test_respects_existing_semicolon(self):
        out = rules.append_hints_to_transformation(
            "X;",
            extra_hints=["A"],
        )
        # rstrip strips whitespace, then endswith ; -> no extra semi added
        assert out == "X;\nA"

    def test_empty_transformation(self):
        out = rules.append_hints_to_transformation(
            "",
            extra_hints=["A"],
        )
        assert out == "\nA"

    def test_codelist_hint_flag_without_note(self):
        out = rules.append_hints_to_transformation(
            "X",
            needs_codelist_hint=True,
            codelist_note="",
        )
        assert out == "X"

    def test_combined_codelist_and_variable_hints(self):
        out = rules.append_hints_to_transformation(
            "[RAW]AE.AESEV",
            extra_hints=["DTC_NOTE"],
            needs_codelist_hint=True,
            codelist_note="CL_NOTE",
        )
        assert out == "[RAW]AE.AESEV;\nCL_NOTE\nDTC_NOTE"
