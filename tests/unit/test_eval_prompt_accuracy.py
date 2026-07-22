"""Tests for the explicit full-pipeline held-out evaluator."""

from __future__ import annotations

import json

import pytest

from scripts.eval_prompt_accuracy import (
    _render_structured_variable,
    build_parser,
    evaluate,
    generate_benchmark_input,
    load_ai_output,
    load_ground_truth,
)


def _gt_entry(**overrides):
    entry = {
        "evaluation_id": "FPH-0001",
        "metadata_table": "TLB",
        "metadata_variable": "TUDAT",
        "annotation_table": "靶病灶明细",
        "annotation_variable": "检查日期",
        "SDTM_Domain": "TU|TR",
        "SDTM_Variable": "TUDTC|TRDTC",
        "reference_source": "KB",
        "evaluation_cohort": "KB_OVERLAP",
    }
    entry.update(overrides)
    return entry


def _ai_row(**overrides):
    row = {
        "metadata_table": "TLB",
        "metadata_variable": "TUDAT",
        "annotation_table": "靶病灶明细",
        "annotation_variable": "检查日期",
        "ai_domain": "TU|TR",
        "ai_variable": "TUDTC|TRDTC",
        "score": 1.0,
        "source": "KB",
    }
    row.update(overrides)
    return row


def test_ground_truth_key_keeps_metadata_table_distinctions(tmp_path):
    records = [
        _gt_entry(),
        _gt_entry(
            evaluation_id="FPH-0002",
            metadata_table="TL",
            SDTM_Domain="TR|RS",
            SDTM_Variable="TRDTC|RSDTC",
        ),
    ]
    path = tmp_path / "gt.json"
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

    assert len(load_ground_truth(path)) == 2


def test_ground_truth_rejects_duplicate_full_input_key(tmp_path):
    records = [_gt_entry(), _gt_entry(evaluation_id="FPH-0002")]
    path = tmp_path / "gt.json"
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate ground-truth input key"):
        load_ground_truth(path)


def test_ground_truth_rejects_incomplete_full_input_key(tmp_path):
    path = tmp_path / "gt.json"
    path.write_text(
        json.dumps([_gt_entry(annotation_variable="")], ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Incomplete ground-truth input key"):
        load_ground_truth(path)


def test_evaluate_compares_complete_conditional_expression(tmp_path):
    path = tmp_path / "gt.json"
    path.write_text(
        json.dumps(
            [
                _gt_entry(
                    SDTM_Domain="DM",
                    SDTM_Variable="QVAL when QNAM=EXPECTED",
                    evaluation_cohort="AI_RECOMMENDATION",
                )
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    gt = load_ground_truth(path)
    metrics = evaluate(
        [
            _ai_row(
                ai_domain="DM",
                ai_variable="QVAL when QNAM=WRONG",
                source="LLM",
            )
        ],
        gt,
    )

    assert metrics["exact_match"] == 0
    assert metrics["statuses"] == {"var_diff": 1}
    assert metrics["cohort_stats"]["AI_RECOMMENDATION"]["total"] == 1


def test_evaluate_matches_domainless_not_submitted(tmp_path):
    path = tmp_path / "gt.json"
    path.write_text(
        json.dumps([_gt_entry(SDTM_Domain="", SDTM_Variable="NOT SUBMITTED")], ensure_ascii=False),
        encoding="utf-8",
    )
    metrics = evaluate(
        [_ai_row(ai_domain="", ai_variable="NOT SUBMITTED")],
        load_ground_truth(path),
    )

    assert metrics["exact_match"] == 1
    assert metrics["domain_stats"]["NOT_SUBMITTED"]["match"] == 1


def test_render_structured_supp_mapping_keeps_qnam_and_testcd():
    rendered = _render_structured_variable(
        {
            "domain": "FA",
            "sdtm_variable": "QVAL",
            "sdtm_variable_type": "supp",
            "supp_variable": "FAOROTH",
            "testcd": "THPGRD",
        }
    )

    assert rendered == "QVAL WHEN QNAM=FAOROTH WHEN FATESTCD=THPGRD"


def test_nested_processor_output_recovers_full_key_and_supp_expression(tmp_path):
    gt_record = _gt_entry(
        metadata_table="TH6",
        metadata_variable="THPTYPO",
        annotation_table="结肠癌病史",
        annotation_variable="其他，请说明",
        SDTM_Domain="FA",
        SDTM_Variable="QVAL when QNAM=FAOROTH when FATESTCD=THPGRD",
    )
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(json.dumps([gt_record], ensure_ascii=False), encoding="utf-8")
    output_path = tmp_path / "output.json"
    output_path.write_text(
        json.dumps(
            [
                {
                    "table_name": "TH6",
                    "original_mappings": [
                        {
                            "metadata_variable": "THPTYPO",
                            "annotation_table": "结肠癌病史",
                            "annotation_variable": "其他，请说明",
                        }
                    ],
                    "domain_recommendations": [
                        {
                            "variable_name": "THPTYPO",
                            "domain": "FA",
                            "sdtm_variable": "QVAL",
                            "sdtm_variable_type": "supp",
                            "supp_variable": "FAOROTH",
                            "testcd": "THPGRD",
                            "score": 0.9,
                            "source": "LLM",
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    metrics = evaluate(load_ai_output(output_path), load_ground_truth(gt_path))

    assert metrics["exact_match"] == 1


def test_generate_benchmark_keeps_both_cohorts_and_complex_mappings(tmp_path):
    records = [
        _gt_entry(),
        _gt_entry(
            evaluation_id="FPH-0002",
            metadata_table="SUBJECT",
            metadata_variable="CRFVER",
            annotation_table="受试者信息",
            annotation_variable="eCRF版本号",
            SDTM_Domain="",
            SDTM_Variable="NOT SUBMITTED",
            reference_source="LLM",
            evaluation_cohort="AI_RECOMMENDATION",
        ),
    ]
    gt_path = tmp_path / "gt.json"
    output_path = tmp_path / "benchmark.json"
    gt_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

    generate_benchmark_input(gt_path, output_path, sample_size=2)
    benchmark = json.loads(output_path.read_text(encoding="utf-8"))

    assert len(benchmark) == 2
    assert {row["metadata_table"] for row in benchmark} == {"TLB", "SUBJECT"}
    assert all(
        set(row) == {"metadata_table", "metadata_variable", "annotation_table", "annotation_variable"}
        for row in benchmark
    )


def test_cli_requires_explicit_ground_truth():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--ai-output", "result.json"])
