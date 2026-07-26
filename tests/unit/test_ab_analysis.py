"""Pure A/B analysis contracts for auditable SDTM evaluation."""

from __future__ import annotations

import pytest

from src.evaluation.ab_analysis import classify_scenarios, count_quality_issues


@pytest.mark.parametrize(
    ("domain", "variable", "expected"),
    [
        (
            "FA",
            "QVAL when QNAM=FAOROTH when FATESTCD=THCLA",
            {"SUPP", "TESTCD"},
        ),
        (
            "TU|TR",
            "TULOC|TRORRES when TRTESTCD=DIAMETER",
            {"MULTI_DOMAIN", "TESTCD"},
        ),
        ("", "NOT SUBMITTED", {"NOT_SUBMITTED"}),
        ("CUSTOM", "CUSVAR", {"NON_STANDARD_DOMAIN"}),
        ("AE", "AETERM", set()),
    ],
)
def test_classify_scenarios(domain, variable, expected):
    assert (
        classify_scenarios(
            {
                "SDTM_Domain": domain,
                "SDTM_Variable": variable,
            }
        )
        == expected
    )


def test_quality_issue_counts_are_row_based_and_critic_errors_are_deduplicated():
    rows = [
        {
            "domain": "AE",
            "sdtm_variable": "AETERM",
            "sdtm_variable_type": "standard",
            "source": "KB",
            "cascade_level": 1,
        },
        {
            "domain": "AE",
            "sdtm_variable": "AETERMLONG",
            "sdtm_variable_type": "standard",
            "source": "LLM",
            "cascade_level": 4,
            "validation_flags": {"non_standard_variable": True},
        },
        {
            "domain": "FA",
            "sdtm_variable": "FAOROTH",
            "sdtm_variable_type": "supp",
            "supp_dataset": "",
            "supp_variable": "123INVALID",
            "source": "LLM",
            "cascade_level": 4,
        },
        {
            "domain": "AE",
            "sdtm_variable": "AE_PENDING",
            "sdtm_variable_type": "standard",
            "source": "FALLBACK",
            "cascade_level": 4,
            "fallback_reason": "Failed to parse AI JSON",
        },
        {
            "domain": "AE",
            "sdtm_variable": "AE_UNMAPPED",
            "sdtm_variable_type": "standard",
            "source": "UNMAPPED",
            "cascade_level": None,
        },
    ]
    critic_error = {
        "severity": "error",
        "issue_type": "inconsistent_domain",
        "description": "synthetic consistency error",
    }

    counts = count_quality_issues(
        rows,
        consistency_issues=[critic_error, dict(critic_error), {"severity": "warning"}],
    )

    assert counts == {
        "deterministic_validation_errors": 1,
        "illegal_sdtm_variables": 1,
        "illegal_supp_qnam": 1,
        "parse_failures": 1,
        "unmapped_outputs": 1,
        "missing_cascade_provenance": 1,
        "mapping_critic_errors": 1,
    }


@pytest.mark.parametrize(
    "row",
    [
        {
            "domain": "FA",
            "sdtm_variable": "QVAL",
            "sdtm_variable_type": "supp",
            "supp_dataset": "SUPPFA",
            "supp_variable": "FAOROTH",
            "source": "LLM",
            "cascade_level": 4,
        },
        {
            "domain": "",
            "sdtm_variable": "NOT SUBMITTED",
            "sdtm_variable_type": "not_submitted",
            "source": "KB_NOT_SUBMITTED",
            "cascade_level": 0,
        },
    ],
)
def test_valid_supp_and_not_submitted_outputs_have_no_quality_issues(row):
    counts = count_quality_issues([row], consistency_issues=[])

    assert not any(counts.values())
