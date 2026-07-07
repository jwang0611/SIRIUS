"""Unit tests for ``src.processors.mapping_critic``."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.processors.mapping_critic import ConsistencyIssue, MappingCritic


@pytest.fixture
def critic() -> MappingCritic:
    return MappingCritic(debug=False)


@pytest.fixture(autouse=True)
def _valid_domains():
    valid = {"AE", "DM", "LB", "VS", "FA", "EX", "CM", "PE", "EG", "PR", "QS", "SC"}
    with patch(
        "src.processors.mapping_critic.is_valid_domain",
        side_effect=lambda d: d in valid,
    ):
        yield


class TestConsistencyIssue:
    def test_to_dict_roundtrip(self):
        issue = ConsistencyIssue("error", "check_x", "desc", ["v1"], "fix")
        d = issue.to_dict()
        assert d["severity"] == "error"
        assert d["check_name"] == "check_x"
        assert d["affected_variables"] == ["v1"]

    def test_repr_includes_severity(self):
        issue = ConsistencyIssue("warning", "check_x", "desc")
        assert "WARNING" in repr(issue)


class TestCriticize:
    def test_no_issues_on_clean_batch(self, critic):
        recs = [
            {
                "annotation_table": "T1",
                "domain": "AE",
                "sdtm_variable": "AETERM",
                "variable_name": "v1",
                "sdtm_variable_type": "standard",
            },
            {
                "annotation_table": "T1",
                "domain": "AE",
                "sdtm_variable": "AESEV",
                "variable_name": "v2",
                "sdtm_variable_type": "standard",
            },
            {
                "annotation_table": "T1",
                "domain": "AE",
                "sdtm_variable": "AESTDTC",
                "variable_name": "v3",
                "sdtm_variable_type": "standard",
            },
        ]
        issues = critic.criticize(recs)
        assert issues == []

    def test_issues_sorted_by_severity(self, critic):
        recs = [
            {"domain": "AE", "sdtm_variable": "INVALIDNAMEXX", "variable_name": "v1"},
            {"domain": "NOTADOMAIN", "sdtm_variable": "FOO", "variable_name": "v2"},
        ]
        issues = critic.criticize(recs)
        severities = [i.severity for i in issues]
        assert severities == sorted(severities, key=lambda s: {"error": 0, "warning": 1, "info": 2}.get(s, 3))


class TestSameTableDomainConsistency:
    def test_flags_fragmented_table(self, critic):
        recs = [
            {"annotation_table": "T1", "domain": "AE", "variable_name": "v1"},
            {"annotation_table": "T1", "domain": "DM", "variable_name": "v2"},
            {"annotation_table": "T1", "domain": "LB", "variable_name": "v3"},
            {"annotation_table": "T1", "domain": "VS", "variable_name": "v4"},
        ]
        issues = critic._check_same_table_domain_consistency(recs)
        assert any(i.check_name == "same_table_domain_consistency" for i in issues)

    def test_skips_tables_with_too_few_vars(self, critic):
        recs = [
            {"annotation_table": "T1", "domain": "AE", "variable_name": "v1"},
            {"annotation_table": "T1", "domain": "DM", "variable_name": "v2"},
        ]
        assert critic._check_same_table_domain_consistency(recs) == []


class TestFindingsTestcdCoherence:
    def test_flags_missing_testcd_for_result_var(self, critic):
        recs = [
            {"domain": "LB", "sdtm_variable": "LBORRES", "variable_name": "v1", "testcd": ""},
        ]
        issues = critic._check_findings_testcd_coherence(recs)
        assert any("TESTCD" in i.description for i in issues)

    def test_orphan_testcd_flagged_as_info(self, critic):
        recs = [
            {"domain": "LB", "sdtm_variable": "LBORRES", "variable_name": "v1", "testcd": "GLUC"},
        ]
        issues = critic._check_findings_testcd_coherence(recs)
        assert any(i.severity == "info" for i in issues)

    def test_multiple_vars_same_testcd_ok(self, critic):
        recs = [
            {"domain": "LB", "sdtm_variable": "LBORRES", "variable_name": "v1", "testcd": "GLUC"},
            {"domain": "LB", "sdtm_variable": "LBORRESU", "variable_name": "v2", "testcd": "GLUC"},
        ]
        issues = critic._check_findings_testcd_coherence(recs)
        assert not any(i.severity == "info" and "Orphan" in i.description for i in issues)

    def test_non_findings_domain_ignored(self, critic):
        recs = [{"domain": "AE", "sdtm_variable": "AEORRES", "variable_name": "v1", "testcd": ""}]
        assert critic._check_findings_testcd_coherence(recs) == []


class TestVariableNameValidity:
    def test_flags_long_names(self, critic):
        recs = [{"sdtm_variable": "AETERMDESCLONG"}]
        issues = critic._check_variable_name_validity(recs)
        assert any(i.severity == "error" for i in issues)

    def test_flags_spaces(self, critic):
        recs = [{"sdtm_variable": "AE TERM"}]
        issues = critic._check_variable_name_validity(recs)
        assert issues

    def test_valid_names_ok(self, critic):
        recs = [{"sdtm_variable": "AETERM"}, {"sdtm_variable": "AESEV"}]
        assert critic._check_variable_name_validity(recs) == []


class TestDomainValidity:
    def test_flags_invalid_domain(self, critic):
        recs = [{"domain": "XYZ"}]
        issues = critic._check_domain_validity(recs)
        assert issues
        assert issues[0].severity == "error"

    def test_valid_domains_ok(self, critic):
        recs = [{"domain": "AE"}, {"domain": "DM"}]
        assert critic._check_domain_validity(recs) == []

    def test_supp_prefix_stripped(self, critic):
        recs = [{"domain": "SUPPAE"}]
        assert critic._check_domain_validity(recs) == []


class TestDuplicateMappings:
    def test_flags_duplicates(self, critic):
        recs = [
            {"variable_name": "v1", "domain": "AE", "sdtm_variable": "AETERM", "source": "KB"},
            {"variable_name": "v1", "domain": "AE", "sdtm_variable": "AETERM", "source": "LLM"},
        ]
        issues = critic._check_duplicate_mappings(recs)
        assert any(i.check_name == "duplicate_mappings" for i in issues)

    def test_distinct_testcd_not_duplicate(self, critic):
        recs = [
            {"variable_name": "v1", "domain": "FA", "sdtm_variable": "FAORRES", "testcd": "A"},
            {"variable_name": "v1", "domain": "FA", "sdtm_variable": "FAORRES", "testcd": "B"},
        ]
        assert critic._check_duplicate_mappings(recs) == []


def test_criticize_combines_all_checks(critic):
    recs = [
        {
            "annotation_table": "T1",
            "domain": "AE",
            "sdtm_variable": "AETERM",
            "variable_name": "v1",
            "sdtm_variable_type": "standard",
            "source": "LLM",
        },
        {
            "annotation_table": "T1",
            "domain": "INVALID",
            "sdtm_variable": "TOOLONGNAMEXY",
            "variable_name": "v2",
            "source": "LLM",
        },
    ]
    issues = critic.criticize(recs)
    check_names = {i.check_name for i in issues}
    assert "variable_name_validity" in check_names
    assert "domain_validity" in check_names
