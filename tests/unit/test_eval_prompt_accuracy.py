"""Tests for the explicit full-pipeline held-out evaluator."""

from __future__ import annotations

import json

import pytest

from scripts.eval_prompt_accuracy import (
    _render_structured_variable,
    build_ab_report,
    build_parser,
    evaluate,
    generate_benchmark_input,
    load_ai_output,
    load_ground_truth,
    main,
    print_report,
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
        "evaluation_cohort": "KB_AGREE",
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


def test_nested_processor_output_preserves_provenance_and_quality_fields(tmp_path):
    output_path = tmp_path / "output.json"
    output_path.write_text(
        json.dumps(
            [
                {
                    "table_name": "TH6",
                    "original_mappings": [
                        {
                            "metadata_variable": "THPTYPO",
                            "annotation_table": "Tumor History",
                            "annotation_variable": "Other pathology type",
                        }
                    ],
                    "domain_recommendations": [
                        {
                            "variable_name": "THPTYPO",
                            "domain": "FA",
                            "sdtm_variable": "QVAL",
                            "sdtm_variable_type": "supp",
                            "supp_dataset": "SUPPFA",
                            "supp_variable": "FAOROTH",
                            "testcd": "THCLA",
                            "score": 0.9,
                            "source": "LLM",
                            "cascade_level": 4,
                            "non_standard_variable": True,
                            "fallback_reason": "",
                        }
                    ],
                    "consistency_issues": [
                        {
                            "severity": "error",
                            "issue_type": "synthetic",
                            "description": "synthetic critic error",
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    rows = load_ai_output(output_path)

    assert rows[0]["cascade_level"] == 4
    assert rows[0]["sdtm_variable"] == "QVAL"
    assert rows[0]["supp_dataset"] == "SUPPFA"
    assert rows[0]["supp_variable"] == "FAOROTH"
    assert rows[0]["testcd"] == "THCLA"
    assert rows[0]["validation_flags"] == {"non_standard_variable": True}
    assert rows[0]["consistency_issues"][0]["severity"] == "error"


def test_evaluate_reports_cohort_cascade_scenario_rows_and_quality(tmp_path):
    records = [
        _gt_entry(
            evaluation_id="FPH-0001",
            metadata_table="TH6",
            metadata_variable="THPTYPO",
            annotation_table="Tumor History",
            annotation_variable="Other pathology type",
            SDTM_Domain="FA",
            SDTM_Variable="QVAL when QNAM=FAOROTH when FATESTCD=THCLA",
            reference_source="LLM",
            evaluation_cohort="AI_RECOMMENDATION",
        ),
        _gt_entry(
            evaluation_id="FPH-0002",
            metadata_table="SUBJECT",
            metadata_variable="CRFVER",
            annotation_table="Subject",
            annotation_variable="CRF version",
            SDTM_Domain="",
            SDTM_Variable="NOT SUBMITTED",
            evaluation_cohort="KB_AGREE",
        ),
        _gt_entry(
            evaluation_id="FPH-0003",
            metadata_table="CUSTOM",
            metadata_variable="CUSVAR",
            annotation_table="Custom",
            annotation_variable="Custom variable",
            SDTM_Domain="CUSTOM",
            SDTM_Variable="CUSVAR",
            evaluation_cohort="KB_DISAGREE",
        ),
    ]
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    rows = [
        _ai_row(
            metadata_table="TH6",
            metadata_variable="THPTYPO",
            annotation_table="Tumor History",
            annotation_variable="Other pathology type",
            ai_domain="FA",
            ai_variable="QVAL WHEN QNAM=FAOROTH WHEN FATESTCD=THCLA",
            domain="FA",
            sdtm_variable="QVAL",
            sdtm_variable_type="supp",
            supp_dataset="SUPPFA",
            supp_variable="FAOROTH",
            testcd="THCLA",
            source="LLM",
            cascade_level=4,
        ),
        _ai_row(
            metadata_table="SUBJECT",
            metadata_variable="CRFVER",
            annotation_table="Subject",
            annotation_variable="CRF version",
            ai_domain="",
            ai_variable="NOT SUBMITTED",
            domain="",
            sdtm_variable="NOT SUBMITTED",
            sdtm_variable_type="not_submitted",
            source="KB_NOT_SUBMITTED",
            cascade_level=0,
        ),
        _ai_row(
            metadata_table="CUSTOM",
            metadata_variable="CUSVAR",
            annotation_table="Custom",
            annotation_variable="Custom variable",
            ai_domain="CUSTOM",
            ai_variable="CUSVAR",
            domain="CUSTOM",
            sdtm_variable="CUSVAR",
            sdtm_variable_type="standard",
            source="KB",
            cascade_level=1,
        ),
    ]

    metrics = evaluate(rows, load_ground_truth(gt_path))

    assert metrics["coverage"] == 1
    assert metrics["cohort_stats"]["AI_RECOMMENDATION"]["match"] == 1
    assert metrics["cohort_stats"]["KB_AGREE"]["match"] == 1
    assert metrics["cohort_stats"]["KB_DISAGREE"]["match"] == 1
    assert metrics["cascade_stats"]["4"]["match"] == 1
    assert metrics["cascade_stats"]["0"]["match"] == 1
    assert metrics["scenario_stats"]["SUPP"]["match"] == 1
    assert metrics["scenario_stats"]["TESTCD"]["match"] == 1
    assert metrics["scenario_stats"]["NOT_SUBMITTED"]["match"] == 1
    assert metrics["scenario_stats"]["NON_STANDARD_DOMAIN"]["match"] == 1
    assert len(metrics["row_results"]) == 3
    assert {row["evaluation_id"] for row in metrics["row_results"]} == {
        "FPH-0001",
        "FPH-0002",
        "FPH-0003",
    }
    assert not any(metrics["quality_issues"].values())
    assert metrics["cohort_policy"]["KB_DISAGREE"] == {
        "diagnostic_only": True,
        "release_gate": False,
    }


def test_nested_processor_output_warns_when_original_mapping_is_missing(tmp_path, caplog):
    output_path = tmp_path / "output.json"
    output_path.write_text(
        json.dumps(
            [
                {
                    "table_name": "TH6",
                    "original_mappings": [],
                    "domain_recommendations": [
                        {
                            "variable_name": "THPTYPO",
                            "domain": "FA",
                            "sdtm_variable": "FAORRES",
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with caplog.at_level("WARNING"):
        rows = load_ai_output(output_path)

    assert len(rows) == 1
    assert "TH6.THPTYPO" in caplog.text
    assert "cannot be scored" in caplog.text


def test_evaluate_uses_full_gt_denominator_and_reports_coverage(tmp_path):
    records = [
        _gt_entry(),
        _gt_entry(evaluation_id="FPH-0002", metadata_table="TL"),
    ]
    path = tmp_path / "gt.json"
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

    metrics = evaluate(
        [
            _ai_row(),
            _ai_row(annotation_table="not in gt", metadata_variable="EXTRA"),
        ],
        load_ground_truth(path),
    )

    assert metrics["gt_size"] == 2
    assert metrics["total_evaluated"] == 1
    assert metrics["coverage"] == 0.5
    assert metrics["missing_gt_rows"] == 1
    assert metrics["extra_ai_rows"] == 1
    assert metrics["exact_rate"] == 0.5
    assert metrics["evaluated_exact_rate"] == 1.0
    assert metrics["cohort_stats"]["KB_AGREE"] == {
        "gt_size": 2,
        "total": 1,
        "match": 1,
        "domain_match": 1,
    }
    assert metrics["domain_stats"]["TU|TR"] == {
        "gt_size": 2,
        "total": 1,
        "match": 1,
        "domain_match": 1,
    }


def test_report_warns_when_coverage_is_incomplete(tmp_path, capsys):
    records = [_gt_entry(), _gt_entry(evaluation_id="FPH-0002", metadata_table="TL")]
    path = tmp_path / "gt.json"
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    metrics = evaluate([_ai_row()], load_ground_truth(path))

    print_report(metrics)

    report = capsys.readouterr().out
    assert "Coverage:              1/2 = 50.0%" in report
    assert "WARNING: Coverage is below 100%" in report


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


def test_cli_full_coverage_gate_exits_nonzero(tmp_path, monkeypatch, capsys):
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(
        json.dumps(
            [_gt_entry(), _gt_entry(evaluation_id="FPH-0002", metadata_table="TL")],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "output.json"
    output_path.write_text(
        json.dumps(
            [
                {
                    "metadata_table": "TLB",
                    "metadata_variable": "TUDAT",
                    "annotation_table": "靶病灶明细",
                    "annotation_variable": "检查日期",
                    "SDTM_Domain": "TU|TR",
                    "SDTM_Variable": "TUDTC|TRDTC",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "eval_prompt_accuracy.py",
            "--ground-truth",
            str(gt_path),
            "--ai-output",
            str(output_path),
            "--require-full-coverage",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert "full coverage is required" in capsys.readouterr().err


def _ab_records():
    return [
        _gt_entry(
            evaluation_id="FPH-AI",
            metadata_table="TH6",
            metadata_variable="THPTYPO",
            annotation_table="Tumor History",
            annotation_variable="Other pathology type",
            SDTM_Domain="FA",
            SDTM_Variable="QVAL when QNAM=FAOROTH when FATESTCD=THCLA",
            reference_source="LLM",
            evaluation_cohort="AI_RECOMMENDATION",
        ),
        _gt_entry(
            evaluation_id="FPH-KB",
            metadata_table="SUBJECT",
            metadata_variable="SUBJID",
            annotation_table="Subject",
            annotation_variable="Subject identifier",
            SDTM_Domain="DM",
            SDTM_Variable="SUBJID",
            evaluation_cohort="KB_AGREE",
        ),
        _gt_entry(
            evaluation_id="FPH-DIAG",
            metadata_table="CUSTOM",
            metadata_variable="CUSVAR",
            annotation_table="Custom",
            annotation_variable="Custom variable",
            SDTM_Domain="CUSTOM",
            SDTM_Variable="CUSVAR",
            evaluation_cohort="KB_DISAGREE",
        ),
        _gt_entry(
            evaluation_id="FPH-MULTI",
            metadata_table="TLB",
            metadata_variable="TUDAT",
            annotation_table="Target Lesion",
            annotation_variable="Assessment date",
            SDTM_Domain="TU|TR",
            SDTM_Variable="TUDTC|TRDTC",
            evaluation_cohort="KB_AGREE",
        ),
        _gt_entry(
            evaluation_id="FPH-NS",
            metadata_table="SUBJECT",
            metadata_variable="CRFVER",
            annotation_table="Subject",
            annotation_variable="CRF version",
            SDTM_Domain="",
            SDTM_Variable="NOT SUBMITTED",
            evaluation_cohort="KB_AGREE",
        ),
    ]


def _processor_table(
    reference,
    *,
    sdtm_variable,
    source,
    cascade_level,
    sdtm_variable_type="standard",
    supp_dataset="",
    supp_variable="",
    testcd="",
):
    return {
        "table_name": reference["metadata_table"],
        "original_mappings": [
            {
                "metadata_variable": reference["metadata_variable"],
                "annotation_table": reference["annotation_table"],
                "annotation_variable": reference["annotation_variable"],
            }
        ],
        "domain_recommendations": [
            {
                "variable_name": reference["metadata_variable"],
                "domain": reference["SDTM_Domain"],
                "sdtm_variable": sdtm_variable,
                "sdtm_variable_type": sdtm_variable_type,
                "supp_dataset": supp_dataset,
                "supp_variable": supp_variable,
                "testcd": testcd,
                "score": 0.9,
                "source": source,
                "cascade_level": cascade_level,
            }
        ],
    }


def _ab_outputs(records, *, improved_ai_match):
    ai_qnam = "FAOROTH" if improved_ai_match else "FAWRONG"
    return [
        _processor_table(
            records[0],
            sdtm_variable="QVAL",
            sdtm_variable_type="supp",
            supp_dataset="SUPPFA",
            supp_variable=ai_qnam,
            testcd="THCLA",
            source="LLM",
            cascade_level=4,
        ),
        _processor_table(
            records[1],
            sdtm_variable="SUBJID",
            source="KB",
            cascade_level=1,
        ),
        _processor_table(
            records[2],
            sdtm_variable="CUSVAR",
            source="KB",
            cascade_level=2,
        ),
        _processor_table(
            records[3],
            sdtm_variable="TUDTC|TRDTC",
            source="KB",
            cascade_level=1,
        ),
        _processor_table(
            records[4],
            sdtm_variable="NOT SUBMITTED",
            sdtm_variable_type="not_submitted",
            source="KB_NOT_SUBMITTED",
            cascade_level=0,
        ),
    ]


def _manifest(*, git_sha, prompt_sha, model="fixed/model"):
    return {
        "schema_version": "1.0.0",
        "status": "completed",
        "git": {"sha": git_sha, "dirty": False},
        "inputs": {
            "benchmark": {"sha256": "benchmark", "row_count": 5},
            "heldout": {"sha256": "heldout", "row_count": 5},
        },
        "knowledge_base": [{"path": "kb.json", "sha256": "kb"}],
        "prompts": {"template": {"version": "1.0.0", "sha256": prompt_sha}},
        "configuration": {
            "model": model,
            "generation": {"temperature": 0},
            "rag": {"enabled": True},
            "concurrency": {"workers": 5},
        },
    }


def _write_ab_inputs(tmp_path, *, improved_ai_match=True, improved_model="fixed/model"):
    records = _ab_records()
    gt_path = tmp_path / "gt.json"
    baseline_path = tmp_path / "baseline.json"
    improved_path = tmp_path / "improved.json"
    baseline_manifest_path = tmp_path / "baseline.manifest.json"
    improved_manifest_path = tmp_path / "improved.manifest.json"
    tests_path = tmp_path / "required-tests.json"
    gt_path.write_text(json.dumps(records), encoding="utf-8")
    baseline_path.write_text(json.dumps(_ab_outputs(records, improved_ai_match=False)), encoding="utf-8")
    improved_path.write_text(
        json.dumps(_ab_outputs(records, improved_ai_match=improved_ai_match)),
        encoding="utf-8",
    )
    baseline_manifest_path.write_text(
        json.dumps(_manifest(git_sha="baseline", prompt_sha="old")),
        encoding="utf-8",
    )
    improved_manifest_path.write_text(
        json.dumps(
            _manifest(
                git_sha="improved",
                prompt_sha="new",
                model=improved_model,
            )
        ),
        encoding="utf-8",
    )
    tests_path.write_text(
        json.dumps({"all_passed": True, "commands": [{"command": "pytest", "exit_code": 0}]}),
        encoding="utf-8",
    )
    return {
        "ground_truth": gt_path,
        "baseline": baseline_path,
        "improved": improved_path,
        "baseline_manifest": baseline_manifest_path,
        "improved_manifest": improved_manifest_path,
        "required_tests": tests_path,
    }


def test_cli_exposes_machine_report_arguments():
    args = build_parser().parse_args(
        [
            "--ground-truth",
            "gt.json",
            "--baseline",
            "baseline.json",
            "--improved",
            "improved.json",
            "--baseline-manifest",
            "baseline.manifest.json",
            "--improved-manifest",
            "improved.manifest.json",
            "--report-json",
            "report.json",
            "--bootstrap-seed",
            "7",
            "--bootstrap-replicates",
            "100",
            "--required-tests-json",
            "tests.json",
            "--require-acceptance",
        ]
    )

    assert args.bootstrap_seed == 7
    assert args.bootstrap_replicates == 100
    assert args.require_acceptance is True


def test_build_ab_report_keeps_complete_audit_and_slice_metrics(tmp_path):
    paths = _write_ab_inputs(tmp_path)
    gt = load_ground_truth(paths["ground_truth"])
    baseline = evaluate(load_ai_output(paths["baseline"]), gt, label="Baseline")
    improved = evaluate(load_ai_output(paths["improved"]), gt, label="Improved")

    report = build_ab_report(
        baseline,
        improved,
        baseline_manifest=json.loads(paths["baseline_manifest"].read_text()),
        improved_manifest=json.loads(paths["improved_manifest"].read_text()),
        required_tests=json.loads(paths["required_tests"].read_text()),
        bootstrap_seed=20260726,
        bootstrap_replicates=1000,
    )

    assert report["schema_version"] == "1.0.0"
    assert report["decision"] == "ACCEPT"
    assert report["baseline"]["cohort_stats"].keys() == {
        "AI_RECOMMENDATION",
        "KB_AGREE",
        "KB_DISAGREE",
    }
    assert report["improved"]["domain_stats"]
    assert report["improved"]["scenario_stats"].keys() == {
        "SUPP",
        "TESTCD",
        "NON_STANDARD_DOMAIN",
        "MULTI_DOMAIN",
        "NOT_SUBMITTED",
    }
    assert report["paired_analysis"]["cohort"] == "AI_RECOMMENDATION"
    assert report["quality"]["baseline"] == baseline["quality_issues"]
    assert report["manifest_comparison"]["equal"] is True
    assert report["required_tests"]["all_passed"] is True
    assert report["gates"]


def test_cli_writes_machine_report_and_creates_parent_directory(tmp_path, monkeypatch):
    paths = _write_ab_inputs(tmp_path)
    report_path = tmp_path / "nested" / "ab-report.json"
    argv = ["eval_prompt_accuracy.py"]
    for option, path in paths.items():
        argv.extend([f"--{option.replace('_', '-')}", str(path)])
    argv.extend(
        [
            "--report-json",
            str(report_path),
            "--bootstrap-replicates",
            "100",
            "--require-acceptance",
        ]
    )
    monkeypatch.setattr("sys.argv", argv)

    main()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["decision"] == "ACCEPT"
    assert report["baseline"]["cohort_stats"]["KB_DISAGREE"]["gt_size"] == 1


def test_cli_require_acceptance_exits_one_on_rollback(tmp_path, monkeypatch):
    paths = _write_ab_inputs(tmp_path, improved_ai_match=False)
    report_path = tmp_path / "rollback.json"
    argv = ["eval_prompt_accuracy.py"]
    for option, path in paths.items():
        argv.extend([f"--{option.replace('_', '-')}", str(path)])
    argv.extend(["--report-json", str(report_path), "--require-acceptance"])
    monkeypatch.setattr("sys.argv", argv)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert json.loads(report_path.read_text())["decision"] == "ROLLBACK"


def test_cli_manifest_mismatch_exits_two(tmp_path, monkeypatch):
    paths = _write_ab_inputs(tmp_path, improved_model="different/model")
    report_path = tmp_path / "mismatch.json"
    argv = ["eval_prompt_accuracy.py"]
    for option, path in paths.items():
        argv.extend([f"--{option.replace('_', '-')}", str(path)])
    argv.extend(["--report-json", str(report_path), "--require-acceptance"])
    monkeypatch.setattr("sys.argv", argv)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2
