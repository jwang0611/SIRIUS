"""Release-data, leakage, SUPP metric, and offline gate contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.eval_prompt_accuracy import evaluate, ground_truth_from_rows
from scripts.run_offline_eval_gate import main
from src.evaluation.heldout import (
    MANIFEST_SCHEMA_VERSION,
    manifest_fingerprint,
    scan_for_leakage,
    validate_dataset_manifest,
)
from src.evaluation.offline_gate import (
    BASELINE_SCHEMA_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    evaluate_regression_gate,
)


def _row(index: int, *, supp: bool = False) -> dict:
    return {
        "evaluation_id": f"EVAL-{index:04d}",
        "annotation_table": f"Opaque panel {index}",
        "metadata_table": f"FORM{index}",
        "annotation_variable": f"Opaque item {index}",
        "metadata_variable": f"X{index:03d}",
        "SDTM_Domain": "DM",
        "SDTM_Variable": "QVAL when QNAM=XQUAL" if supp else "SUBJID",
    }


def _ai(row: dict, *, source: str = "LLM", variable: str | None = None) -> dict:
    return {
        **{
            field: row[field]
            for field in ("annotation_table", "metadata_table", "annotation_variable", "metadata_variable")
        },
        "ai_domain": row["SDTM_Domain"],
        "ai_variable": variable or row["SDTM_Variable"].upper(),
        "domain": row["SDTM_Domain"],
        "sdtm_variable": "QVAL" if "QNAM=" in (variable or row["SDTM_Variable"]).upper() else "SUBJID",
        "sdtm_variable_type": "supp" if "QNAM=" in (variable or row["SDTM_Variable"]).upper() else "standard",
        "supp_dataset": "SUPPDM" if "QNAM=" in (variable or row["SDTM_Variable"]).upper() else "",
        "supp_variable": "XQUAL" if "QNAM=" in (variable or row["SDTM_Variable"]).upper() else "",
        "source": source,
        "cascade_level": 4,
    }


def _write_release_manifest(tmp_path: Path, studies: list[list[dict]]) -> Path:
    datasets = []
    for index, rows in enumerate(studies, start=1):
        path = tmp_path / f"study-{index:03d}.json"
        path.write_text(json.dumps(rows, ensure_ascii=False) + "\n", encoding="utf-8")
        import hashlib

        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        datasets.append(
            {
                "dataset_id": f"study-{index:03d}",
                "source_class": "metadata_only_als",
                "deidentified": True,
                "authorized_for_engineering": True,
                "schema_version": "als-metadata/v1",
                "file": path.name,
                "sha256": digest,
                "row_count": len(rows),
            }
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "evaluation_profile": "release",
                "distinct_studies_confirmed": True,
                "datasets": datasets,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def _evidence(*, dirty: bool = False) -> dict:
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "ai_output": {"sha256": "b" * 64},
        "git": {"sha": "a" * 40, "dirty": dirty},
        "prompts": {
            "template": {"version": "1.2.0", "sha256": "c" * 64},
            "rules": {"version": "1.2.0", "sha256": "d" * 64},
            "examples": {"version": "1.1.0", "sha256": "e" * 64},
        },
    }


def _baseline(metrics: dict, manifest_hash: str, *, evidence: dict | None = None) -> dict:
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "dataset_manifest_sha256": manifest_hash,
        "evidence": evidence or _evidence(),
        "metrics": {
            "coverage": metrics["coverage"],
            "exact_rate": metrics["exact_rate"],
            "domain_rate": metrics["domain_rate"],
            "supp": metrics["supp"],
        },
        "max_regression": {
            "coverage": 0,
            "exact_rate": 0,
            "domain_rate": 0,
            "supp": {"precision": 0, "recall": 0, "f1": 0},
        },
        "quality_issues": metrics["quality_issues"],
        "outcome_counts": metrics["outcome_counts"],
        "max_increase": {
            "quality_issues": dict.fromkeys(metrics["quality_issues"], 0),
            "outcome_counts": dict.fromkeys(metrics["outcome_counts"], 0),
        },
    }


def test_release_manifest_requires_two_opaque_authorized_deidentified_studies(tmp_path):
    manifest = _write_release_manifest(tmp_path, [[_row(1)], [_row(2)]])

    report, rows = validate_dataset_manifest(manifest)

    assert report["valid"] is True
    assert report["dataset_count"] == 2
    assert report["row_count"] == 2
    assert len(rows) == 2
    assert all(item["deidentified"] and item["authorized_for_engineering"] for item in report["datasets"])
    assert "annotation_table" not in json.dumps(report)


def test_release_manifest_rejects_one_study_and_identifying_manifest_fields(tmp_path):
    manifest = _write_release_manifest(tmp_path, [[_row(1)]])
    payload = json.loads(manifest.read_text())
    payload["study_name"] = "must not be recorded"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    report, _ = validate_dataset_manifest(manifest)

    assert report["valid"] is False
    assert any("at least two" in error for error in report["errors"])
    assert any("identifying fields" in error for error in report["errors"])
    assert "must not be recorded" not in json.dumps(report)


def test_invalid_manifest_values_and_domain_are_redacted(tmp_path):
    manifest = _write_release_manifest(tmp_path, [[_row(1)], [_row(2)]])
    payload = json.loads(manifest.read_text())
    payload["datasets"][0]["dataset_id"] = "actual-study-name"
    payload["datasets"][0]["source_class"] = "actual-sponsor-name"
    payload["datasets"][0]["schema_version"] = "actual-protocol-name"
    payload["evaluation_profile"] = "secret-evaluation-profile"
    second_study = tmp_path / "study-002.json"
    second_rows = json.loads(second_study.read_text())
    second_rows[0]["SDTM_Domain"] = "NOT_A_DOMAIN"
    second_study.write_text(json.dumps(second_rows) + "\n", encoding="utf-8")
    import hashlib

    payload["datasets"][1]["sha256"] = hashlib.sha256(second_study.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    report, _ = validate_dataset_manifest(manifest)

    assert report["valid"] is False
    serialized = json.dumps(report)
    assert "actual-study-name" not in serialized
    assert "actual-sponsor-name" not in serialized
    assert "actual-protocol-name" not in serialized
    assert "secret-evaluation-profile" not in serialized
    assert any("invalid SDTM domain" in error for error in report["errors"])


def test_injected_knowledge_overlap_fails_without_disclosing_metadata(tmp_path):
    rows = [_row(1), _row(2)]
    kb = tmp_path / "project-kb.json"
    kb.write_text(json.dumps([rows[0]]), encoding="utf-8")

    report = scan_for_leakage(rows, knowledge_roots=[kb])

    assert report["valid"] is False
    assert report["overlap_count"] >= 1
    serialized = json.dumps(report)
    assert rows[0]["annotation_table"] not in serialized
    assert rows[0]["annotation_variable"] not in serialized


def test_chinese_project_kb_columns_and_semantic_pair_overlap_are_detected(tmp_path):
    rows = [_row(1), _row(2)]
    kb = tmp_path / "project-kb.json"
    kb.write_text(
        json.dumps(
            [
                {
                    "表名": rows[0]["annotation_table"],
                    "表": "DIFFERENT_FORM",
                    "变量名": rows[0]["annotation_variable"],
                    "变量": "DIFFERENT_VARIABLE",
                }
            ]
        ),
        encoding="utf-8",
    )

    report = scan_for_leakage(rows, knowledge_roots=[kb])

    assert report["valid"] is False
    assert any(overlap["kind"] == "knowledge_semantic_pair" for overlap in report["overlaps"])


def test_production_deep_normalization_variants_are_detected(tmp_path):
    heldout = _row(1)
    heldout.update(
        {
            "annotation_table": "AE_TERM",
            "metadata_table": "AE-FORM",
            "annotation_variable": "AE TERM (verbatim)",
            "metadata_variable": "AE_TERM",
        }
    )
    source = {**heldout, "annotation_table": "AETERM", "metadata_table": "AEFORM"}
    source["annotation_variable"] = "AE TERM"
    source["metadata_variable"] = "AETERM"
    kb = tmp_path / "project-kb.json"
    kb.write_text(json.dumps([source]), encoding="utf-8")

    report = scan_for_leakage([heldout], knowledge_roots=[kb])

    assert report["valid"] is False
    assert any(overlap["kind"] == "knowledge_exact" for overlap in report["overlaps"])


def test_semantic_keyword_coverage_is_informational_and_short_tokens_are_bounded(tmp_path):
    knowledge = tmp_path / "empty-kb"
    knowledge.mkdir()
    covered = _row(1)
    covered["annotation_variable"] = "adverse event reaction"

    coverage_report = scan_for_leakage([covered], knowledge_roots=[knowledge])
    short_token_report = scan_for_leakage(
        [
            {
                **_row(2),
                "annotation_table": "opaque",
                "metadata_table": "opaque",
                "annotation_variable": "opaque",
                "metadata_variable": "opaque",
            }
        ],
        knowledge_roots=[knowledge],
    )

    assert coverage_report["valid"] is True
    assert coverage_report["semantic_coverage_count"] >= 1
    assert short_token_report["semantic_coverage_count"] == 0


@pytest.mark.parametrize(("suffix", "separator"), [(".jsonl", None), (".csv", ","), (".tsv", "\t")])
def test_supported_project_knowledge_text_formats_are_inspected(tmp_path, suffix, separator):
    row = _row(1)
    kb = tmp_path / f"project-kb{suffix}"
    if suffix == ".jsonl":
        kb.write_text(json.dumps(row) + "\n", encoding="utf-8")
    else:
        fields = ["annotation_table", "metadata_table", "annotation_variable", "metadata_variable"]
        kb.write_text(separator.join(fields) + "\n" + separator.join(str(row[field]) for field in fields) + "\n")

    report = scan_for_leakage([row], knowledge_roots=[kb])

    assert report["valid"] is False
    assert report["overlap_count"] >= 1


def test_unsupported_and_zero_mapping_knowledge_sources_fail_closed(tmp_path):
    unsupported = tmp_path / "README.txt"
    unsupported.write_text("not inspectable", encoding="utf-8")
    unsupported_report = scan_for_leakage([_row(1)], knowledge_roots=[unsupported])
    empty_mapping = tmp_path / "project-kb.json"
    empty_mapping.write_text(json.dumps({"unrelated": "data"}), encoding="utf-8")
    empty_report = scan_for_leakage([_row(1)], knowledge_roots=[empty_mapping])

    assert unsupported_report["valid"] is False
    assert unsupported_report["errors"]
    assert empty_report["valid"] is False
    assert "zero inspectable" in empty_report["errors"][0]


def test_prompt_examples_are_inspected_as_mapping_sources():
    prompt_examples = Path("src/prompts/examples/pattern_examples.yaml")

    report = scan_for_leakage([_row(1)], knowledge_roots=[prompt_examples])

    assert report["sources"][0]["mapping_rows"] > 0


def test_missing_knowledge_root_fails_closed(tmp_path):
    report = scan_for_leakage([_row(1)], knowledge_roots=[tmp_path / "missing"])

    assert report["valid"] is False
    assert report["errors"]


def test_evaluate_reports_supp_precision_recall_f1_and_pending_counts():
    references = [_row(1, supp=True), _row(2), _row(3, supp=True)]
    predictions = [
        _ai(references[0]),
        _ai(references[1], variable="QVAL WHEN QNAM=XQUAL"),
        _ai(references[2], source="FALLBACK", variable="DM_X003_PENDING"),
    ]

    metrics = evaluate(predictions, ground_truth_from_rows(references))

    assert metrics["supp"] == {
        "true_positive": 1,
        "false_positive": 1,
        "false_negative": 1,
        "precision": 0.5,
        "recall": 0.5,
        "f1": 0.5,
    }
    assert metrics["outcome_counts"] == {"fallback_outputs": 1, "pending_outputs": 1}


def test_evaluate_redacts_unknown_source_stratum():
    row = _row(1)

    metrics = evaluate([_ai(row, source="raw-study-name")], ground_truth_from_rows([row]))

    assert set(metrics["source_stats"]) == {"OTHER"}
    assert set(metrics["source_counts"]) == {"OTHER"}


def test_regression_gate_fails_when_a_metric_is_lowered():
    rows = [_row(1, supp=True), _row(2)]
    metrics = evaluate([_ai(row) for row in rows], ground_truth_from_rows(rows))
    baseline = _baseline(metrics, "a" * 64)
    regressed = {**metrics, "exact_rate": metrics["exact_rate"] - 0.01}

    result = evaluate_regression_gate(
        regressed,
        baseline,
        dataset_manifest_sha256="a" * 64,
        current_evidence=_evidence(),
    )

    assert result["passed"] is False
    assert next(gate for gate in result["gates"] if gate["metric"] == "exact_rate")["passed"] is False


def test_regression_gate_rejects_dirty_or_unbound_replay_evidence():
    rows = [_row(1)]
    metrics = evaluate([_ai(row) for row in rows], ground_truth_from_rows(rows))
    baseline = _baseline(metrics, "a" * 64)

    result = evaluate_regression_gate(
        metrics,
        baseline,
        dataset_manifest_sha256="a" * 64,
        current_evidence=_evidence(dirty=True),
    )

    assert result["passed"] is False
    assert result["evidence"] == {"baseline_valid": True, "current_valid": False}
    assert any("git.dirty" in error for error in result["errors"])


def test_offline_cli_writes_json_and_markdown_without_external_client(tmp_path, monkeypatch):
    studies = [[_row(1, supp=True)], [_row(2)]]
    manifest = _write_release_manifest(tmp_path, studies)
    all_rows = [row for study in studies for row in study]
    ai_output = tmp_path / "replay.json"
    ai_output.write_text(json.dumps([_ai(row) for row in all_rows]), encoding="utf-8")
    empty_knowledge = tmp_path / "empty-kb"
    empty_knowledge.mkdir()
    metrics = evaluate([_ai(row) for row in all_rows], ground_truth_from_rows(all_rows))
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(_baseline(metrics, manifest_fingerprint(manifest))), encoding="utf-8")
    report_json = tmp_path / "report.json"
    report_markdown = tmp_path / "report.md"
    monkeypatch.setattr("scripts.run_offline_eval_gate.collect_replay_evidence", lambda _path: _evidence())
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_offline_eval_gate.py",
            "--dataset-manifest",
            str(manifest),
            "--ai-output",
            str(ai_output),
            "--baseline",
            str(baseline),
            "--project-knowledge-root",
            str(empty_knowledge),
            "--report-json",
            str(report_json),
            "--report-markdown",
            str(report_markdown),
        ],
    )

    main()

    report = json.loads(report_json.read_text())
    assert report["regression_gate"]["passed"] is True
    assert "mismatches" not in report["metrics"]
    assert "row_results" not in report["metrics"]
    assert "cascade_stats" not in report["metrics"]
    assert "Opaque panel" not in report_json.read_text()
    assert "SUPP F1" in report_markdown.read_text()
    assert report["evidence"]["ai_output"]["sha256"] == "b" * 64


def test_validate_only_preflights_before_writing_benchmark(tmp_path, monkeypatch):
    studies = [[_row(1)], [_row(2)]]
    manifest = _write_release_manifest(tmp_path, studies)
    empty_knowledge = tmp_path / "empty-kb"
    empty_knowledge.mkdir()
    benchmark = tmp_path / "benchmark.json"
    report_json = tmp_path / "report.json"
    report_markdown = tmp_path / "report.md"
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_offline_eval_gate.py",
            "--dataset-manifest",
            str(manifest),
            "--validate-only",
            "--benchmark-output",
            str(benchmark),
            "--project-knowledge-root",
            str(empty_knowledge),
            "--report-json",
            str(report_json),
            "--report-markdown",
            str(report_markdown),
        ],
    )

    main()

    assert len(json.loads(benchmark.read_text())) == 2
    assert json.loads(report_json.read_text())["mode"] == "validation_only"
    assert "Validation-only preflight completed" in report_markdown.read_text()


def test_offline_cli_rejects_leakage_before_scoring(tmp_path, monkeypatch):
    studies = [[_row(1)], [_row(2)]]
    manifest = _write_release_manifest(tmp_path, studies)
    ai_output = tmp_path / "replay.json"
    ai_output.write_text(json.dumps([_ai(row) for study in studies for row in study]), encoding="utf-8")
    kb = tmp_path / "kb.json"
    kb.write_text(json.dumps([studies[0][0]]), encoding="utf-8")
    report_json = tmp_path / "report.json"
    report_markdown = tmp_path / "report.md"
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_offline_eval_gate.py",
            "--dataset-manifest",
            str(manifest),
            "--ai-output",
            str(ai_output),
            "--project-knowledge-root",
            str(kb),
            "--report-json",
            str(report_json),
            "--report-markdown",
            str(report_markdown),
            "--no-gate",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert json.loads(report_json.read_text())["metrics"] is None


def test_replay_without_baseline_is_a_usage_error_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_offline_eval_gate.py",
            "--dataset-manifest",
            str(tmp_path / "manifest.json"),
            "--ai-output",
            str(tmp_path / "replay.json"),
            "--project-knowledge-root",
            str(tmp_path),
            "--report-json",
            str(tmp_path / "report.json"),
            "--report-markdown",
            str(tmp_path / "report.md"),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2
