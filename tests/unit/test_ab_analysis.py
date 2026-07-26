"""Pure A/B analysis contracts for auditable SDTM evaluation."""

from __future__ import annotations

import copy

import pytest

from src.evaluation.ab_analysis import (
    classify_scenarios,
    count_quality_issues,
    evaluate_acceptance,
    paired_bootstrap,
    paired_cohort_outcomes,
)


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
        "duplicate_supp_qnam": 0,
        "parse_failures": 1,
        "unmapped_outputs": 1,
        "missing_cascade_provenance": 1,
        "mapping_critic_errors": 1,
    }


def test_duplicate_supp_qnam_counts_distinct_raw_variables_only():
    base = {
        "domain": "CM",
        "sdtm_variable": "QVAL",
        "sdtm_variable_type": "supp",
        "supp_dataset": "SUPPCM",
        "supp_variable": "COMMENT",
        "cascade_level": 4,
    }
    rows = [
        {**base, "metadata_variable": "COMMENT_A"},
        {**base, "metadata_variable": "COMMENT_B"},
        {**base, "metadata_variable": "COMMENT_A"},
    ]

    counts = count_quality_issues(rows, consistency_issues=[])

    assert counts["duplicate_supp_qnam"] == 1


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


def test_paired_bootstrap_is_deterministic_and_preserves_pairs():
    baseline = [0, 0, 1, 1]
    improved = [1, 0, 1, 1]

    result = paired_bootstrap(
        baseline,
        improved,
        seed=20260726,
        replicates=10_000,
    )

    assert result["observed_delta"] == pytest.approx(0.25)
    assert result["improved_rows"] == 1
    assert result["worsened_rows"] == 0
    assert result["unchanged_rows"] == 3
    assert result == paired_bootstrap(
        baseline,
        improved,
        seed=20260726,
        replicates=10_000,
    )


def test_paired_bootstrap_rejects_unpaired_inputs():
    with pytest.raises(ValueError, match="same length"):
        paired_bootstrap([0, 1], [1], seed=1, replicates=10)


def test_paired_cohort_outcomes_aligns_by_evaluation_id_not_input_order():
    baseline = {
        "row_results": [
            {"evaluation_id": "B", "cohort": "AI_RECOMMENDATION", "exact_match": True},
            {"evaluation_id": "A", "cohort": "AI_RECOMMENDATION", "exact_match": False},
        ]
    }
    improved = {
        "row_results": [
            {"evaluation_id": "A", "cohort": "AI_RECOMMENDATION", "exact_match": True},
            {"evaluation_id": "B", "cohort": "AI_RECOMMENDATION", "exact_match": True},
        ]
    }

    paired = paired_cohort_outcomes(baseline, improved, cohort="AI_RECOMMENDATION")

    assert paired == {
        "evaluation_ids": ["A", "B"],
        "baseline": [0, 1],
        "improved": [1, 1],
    }


def _passing_acceptance_inputs():
    quality = {
        "deterministic_validation_errors": 0,
        "illegal_sdtm_variables": 0,
        "illegal_supp_qnam": 0,
        "duplicate_supp_qnam": 0,
        "parse_failures": 0,
        "unmapped_outputs": 0,
        "missing_cascade_provenance": 0,
        "mapping_critic_errors": 0,
    }
    baseline = {
        "coverage": 1.0,
        "domain_match": 450,
        "domain_rate": 450 / 490,
        "cohort_stats": {
            "KB_AGREE": {"gt_size": 107, "match": 107, "domain_match": 107},
            "KB_DISAGREE": {"gt_size": 74, "match": 10, "domain_match": 50},
            "AI_RECOMMENDATION": {"gt_size": 309, "match": 100, "domain_match": 280},
        },
        "quality_issues": dict(quality),
    }
    improved = copy.deepcopy(baseline)
    improved["cohort_stats"]["AI_RECOMMENDATION"]["match"] = 110
    paired = {
        "observed_delta": 10 / 309,
        "ci_95": [-0.001, 0.07],
        "improved_rows": 10,
        "worsened_rows": 0,
        "unchanged_rows": 299,
    }
    manifest_comparison = {"equal": True, "differences": []}
    required_tests = {"all_passed": True, "commands": []}
    return baseline, improved, paired, manifest_comparison, required_tests


def _decision_for(
    baseline,
    improved,
    paired,
    manifest_comparison,
    required_tests,
):
    return evaluate_acceptance(
        baseline,
        improved,
        paired=paired,
        manifest_comparison=manifest_comparison,
        required_tests=required_tests,
    )


def test_acceptance_passes_when_every_gate_passes():
    inputs = _passing_acceptance_inputs()
    result = _decision_for(*inputs)

    assert result["decision"] == "ACCEPT"
    assert all(gate["passed"] for gate in result["gates"])


def test_acceptance_rejects_incomplete_coverage():
    inputs = list(_passing_acceptance_inputs())
    inputs[1]["coverage"] = 489 / 490

    assert _decision_for(*inputs)["decision"] == "ROLLBACK"


def test_acceptance_rejects_kb_agree_exact_regression():
    inputs = list(_passing_acceptance_inputs())
    inputs[1]["cohort_stats"]["KB_AGREE"]["match"] = 106

    assert _decision_for(*inputs)["decision"] == "ROLLBACK"


def test_acceptance_rejects_small_uncertain_ai_improvement():
    inputs = list(_passing_acceptance_inputs())
    inputs[2]["observed_delta"] = 0.01
    inputs[2]["ci_95"] = [-0.01, 0.03]

    assert _decision_for(*inputs)["decision"] == "ROLLBACK"


def test_acceptance_allows_sub_two_point_gain_when_ci_lower_is_positive():
    inputs = list(_passing_acceptance_inputs())
    inputs[2]["observed_delta"] = 0.01
    inputs[2]["ci_95"] = [0.001, 0.02]

    assert _decision_for(*inputs)["decision"] == "ACCEPT"


@pytest.mark.parametrize("scope", ["overall", "ai"])
def test_acceptance_rejects_domain_regression(scope):
    inputs = list(_passing_acceptance_inputs())
    if scope == "overall":
        inputs[1]["domain_match"] = 449
        inputs[1]["domain_rate"] = 449 / 490
    else:
        inputs[1]["cohort_stats"]["AI_RECOMMENDATION"]["domain_match"] = 279

    assert _decision_for(*inputs)["decision"] == "ROLLBACK"


@pytest.mark.parametrize(
    "counter",
    [
        "deterministic_validation_errors",
        "illegal_sdtm_variables",
        "illegal_supp_qnam",
        "duplicate_supp_qnam",
        "parse_failures",
        "unmapped_outputs",
        "missing_cascade_provenance",
        "mapping_critic_errors",
    ],
)
def test_acceptance_rejects_any_quality_counter_increase(counter):
    inputs = list(_passing_acceptance_inputs())
    inputs[1]["quality_issues"][counter] = 1

    assert _decision_for(*inputs)["decision"] == "ROLLBACK"


def test_acceptance_rejects_manifest_mismatch():
    inputs = list(_passing_acceptance_inputs())
    inputs[3] = {"equal": False, "differences": ["configuration.model"]}

    assert _decision_for(*inputs)["decision"] == "ROLLBACK"


def test_acceptance_rejects_failed_required_tests():
    inputs = list(_passing_acceptance_inputs())
    inputs[4] = {"all_passed": False, "commands": []}

    assert _decision_for(*inputs)["decision"] == "ROLLBACK"


def test_kb_disagree_regression_is_diagnostic_only():
    inputs = list(_passing_acceptance_inputs())
    inputs[1]["cohort_stats"]["KB_DISAGREE"]["match"] = 0

    result = _decision_for(*inputs)

    assert result["decision"] == "ACCEPT"
    assert all("KB_DISAGREE" not in gate["name"] for gate in result["gates"])
