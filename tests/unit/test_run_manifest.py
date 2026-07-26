"""Tests for reproducible, secret-free SDTM experiment manifests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_sdtm_experiment import (
    _validate_inputs,
    _validate_pinned_generation,
    build_generator_command,
    build_parser,
    build_pinned_environment,
    estimate_external_requests,
)
from src.evaluation.run_manifest import (
    build_run_manifest,
    compare_shared_configuration,
    hash_file,
    sanitize_endpoint,
)


def test_hash_file_normalizes_text_line_endings(tmp_path):
    lf = tmp_path / "lf.json"
    crlf = tmp_path / "crlf.json"
    lf.write_bytes(b'{\n"x": 1\n}\n')
    crlf.write_bytes(b'{\r\n"x": 1\r\n}\r\n')

    assert hash_file(lf, normalize_text=True) == hash_file(crlf, normalize_text=True)
    assert hash_file(lf) != hash_file(crlf)


def test_endpoint_manifest_drops_credentials_and_path():
    assert sanitize_endpoint("https://user:secret@example.test:8443/private/v1?token=x") == {
        "scheme": "https",
        "host": "example.test",
        "port": 8443,
    }


def test_shared_config_comparison_allows_only_run_identity_changes():
    baseline = {
        "configuration": {"model": "m", "temperature": 0},
        "inputs": {"benchmark": {"sha256": "input"}, "heldout": {"sha256": "gt"}},
        "knowledge_base": [{"path": "kb.json", "sha256": "kb"}],
        "runner": {"script_sha256": "same-runner"},
        "git": {"sha": "a"},
        "prompts": {"template": {"version": "1.0.0"}},
    }
    improved = {
        "configuration": {"model": "m", "temperature": 0},
        "inputs": {"benchmark": {"sha256": "input"}, "heldout": {"sha256": "gt"}},
        "knowledge_base": [{"path": "kb.json", "sha256": "kb"}],
        "runner": {"script_sha256": "same-runner"},
        "git": {"sha": "b"},
        "prompts": {"template": {"version": "1.1.0"}},
    }

    comparison = compare_shared_configuration(baseline, improved)

    assert comparison["equal"] is True
    assert comparison["differences"] == []


def test_shared_config_comparison_reports_model_difference():
    baseline = {
        "configuration": {"model": "baseline", "temperature": 0},
        "inputs": {},
        "knowledge_base": [],
    }
    improved = {
        "configuration": {"model": "improved", "temperature": 0},
        "inputs": {},
        "knowledge_base": [],
    }

    comparison = compare_shared_configuration(baseline, improved)

    assert comparison["equal"] is False
    assert comparison["differences"] == ["configuration.model"]


def test_shared_config_comparison_reports_runner_difference():
    baseline = {
        "configuration": {},
        "inputs": {},
        "knowledge_base": [],
        "runner": {"script_sha256": "baseline"},
    }
    improved = {
        "configuration": {},
        "inputs": {},
        "knowledge_base": [],
        "runner": {"script_sha256": "improved"},
    }

    comparison = compare_shared_configuration(baseline, improved)

    assert comparison == {
        "equal": False,
        "differences": ["runner.script_sha256"],
    }


def test_build_manifest_pins_inputs_kb_prompts_and_has_no_sensitive_keys(tmp_path):
    code_root = tmp_path / "code"
    prompts = {
        "template": code_root / "src/prompts/templates/variable_mapping.yaml",
        "rules": code_root / "src/prompts/rules/sdtm_rules.yaml",
        "examples": code_root / "src/prompts/examples/pattern_examples.yaml",
    }
    for path in prompts.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('version: "1.0.0"\n', encoding="utf-8")

    input_path = tmp_path / "benchmark.json"
    heldout_path = tmp_path / "heldout.json"
    input_path.write_text(json.dumps([{"metadata_table": "AE"}]), encoding="utf-8")
    heldout_path.write_text(json.dumps([{"evaluation_id": "FPH-0001"}]), encoding="utf-8")

    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    (kb_root / "production.json").write_text("[]\n", encoding="utf-8")
    (kb_root / "rag.parquet").write_bytes(b"PAR1")
    (kb_root / "ignored.txt").write_text("not part of KB identity", encoding="utf-8")

    manifest = build_run_manifest(
        run_label="baseline",
        code_root=code_root,
        git_sha="abc123",
        git_dirty=False,
        input_path=input_path,
        heldout_path=heldout_path,
        kb_root=kb_root,
        provider="openrouter",
        model="google/gemini-3-flash-preview",
        endpoint="https://user:secret@example.test/v1",
        generation={
            "temperature": 0,
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 2000,
            "language": "cn",
        },
        rag={
            "enabled": True,
            "top_k": 3,
            "embedding_model": "Qwen3-Embed",
            "min_score": 0.4,
            "char_limit": 1500,
            "force": False,
        },
        concurrency={"parallel": True, "max_workers": 5},
        cascade={
            "kb_min_confidence": 0.8,
            "kb_high_confidence": 0.85,
            "rag_high_confidence": 0.7,
            "domain_override_confidence": 0.85,
        },
        started_at="2026-07-26T00:00:00Z",
    )

    assert manifest["schema_version"] == "1.0.0"
    assert manifest["status"] == "running"
    assert manifest["git"] == {"sha": "abc123", "dirty": False}
    assert manifest["inputs"]["benchmark"]["row_count"] == 1
    assert manifest["inputs"]["heldout"]["row_count"] == 1
    assert [item["path"] for item in manifest["knowledge_base"]] == [
        "production.json",
        "rag.parquet",
    ]
    assert set(manifest["prompts"]) == {"template", "rules", "examples"}
    assert manifest["configuration"]["endpoint"] == {
        "scheme": "https",
        "host": "example.test",
        "port": None,
    }

    def all_keys(value) -> set[str]:
        if isinstance(value, dict):
            return set(value).union(*(all_keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(all_keys(item) for item in value))
        return set()

    forbidden = {"api_key", "prompt", "response", "secret", "token"}
    assert forbidden.isdisjoint({key.lower() for key in all_keys(manifest)})
    serialized = json.dumps(manifest)
    assert "user:secret" not in serialized
    assert "/private/" not in serialized


def _runner_args(tmp_path: Path):
    return build_parser().parse_args(
        [
            "--run-label",
            "baseline",
            "--code-root",
            str(tmp_path / "code"),
            "--input",
            str(tmp_path / "benchmark.json"),
            "--heldout",
            str(tmp_path / "heldout.json"),
            "--kb-root",
            str(tmp_path / "kb"),
            "--output-base",
            str(tmp_path / "baseline"),
            "--manifest",
            str(tmp_path / "baseline.manifest.json"),
        ]
    )


def test_runner_defaults_to_estimate_only_with_pinned_generation(tmp_path):
    args = _runner_args(tmp_path)

    assert args.execute is False
    assert args.provider == "openrouter"
    assert args.model == "google/gemini-3-flash-preview"
    assert args.temperature == 0
    assert args.top_p == 0.95
    assert args.top_k == 40
    assert args.max_output_tokens == 2000
    assert args.parallel is True
    assert args.max_workers == 5


def test_generator_command_uses_exact_pinned_values_without_api_key(tmp_path):
    args = _runner_args(tmp_path)
    command = build_generator_command(args, python_executable=Path("python.exe"))
    rendered = " ".join(command)

    assert command[:2] == [
        "python.exe",
        str(args.code_root / "scripts/generate_sdtm_recommendations.py"),
    ]
    assert "--temperature 0.0" in rendered
    assert "--model google/gemini-3-flash-preview" in rendered
    assert f"--kb-path {args.kb_root / 'structured'}" in rendered
    assert f"--kb-path {args.kb_root} " not in f"{rendered} "
    assert "--rag-top-k 3" in rendered
    assert "--rag-embedding-model openai/text-embedding-3-small" in rendered
    assert "--rag-min-score 0.4" in rendered
    assert "--rag-char-limit 1500" in rendered
    assert "--parallel" in command
    assert "--max-workers 5" in rendered
    assert "api-key" not in rendered.lower()
    assert "secret" not in rendered.lower()


def test_pinned_environment_sets_cascade_rag_and_concurrency(tmp_path):
    args = _runner_args(tmp_path)

    environment = build_pinned_environment(
        args,
        base_environment={"OPENROUTER_API_KEY": "kept-out-of-command"},
    )

    assert environment["OPENROUTER_API_KEY"] == "kept-out-of-command"
    assert environment["KNOWLEDGE_STRUCTURED_PATH"] == str(args.kb_root / "structured")
    assert environment["RAG_KB_BASE_PATH"] == str(args.kb_root)
    assert environment["RAG_KB_PATH"] == str(args.kb_root / "structured")
    assert environment["KB_MIN_CONFIDENCE"] == "0.8"
    assert environment["CASCADE_KB_HIGH_CONF"] == "0.85"
    assert environment["CASCADE_RAG_HIGH_CONF"] == "0.7"
    assert environment["KB_DOMAIN_OVERRIDE_CONF"] == "0.85"
    assert environment["RAG_ENABLED"] == "1"
    assert environment["RAG_KB_DEFAULT_FILE"] == "ALS2SDTM_Mapping_Template_v1.0.parquet"
    assert environment["SDTM_ENABLE_PARALLEL"] == "1"
    assert environment["SDTM_MAX_WORKERS"] == "5"
    assert environment["SDTM_LOG_AI"] == "0"


def test_request_estimate_uses_current_heldout_cohort_counts(repo_root):
    estimate = estimate_external_requests(
        repo_root / "data/evaluation/full_pipeline_heldout_v1.json",
        runs=2,
        rag_kb_path=repo_root / "data/knowledge_base/structured",
    )

    assert estimate == {
        "heldout_rows": 490,
        "ai_recommendation_rows": 309,
        "generation_calls_per_run_max": 309,
        "rag_query_embedding_items_per_run_max": 490,
        "rag_query_embedding_requests_per_run_max": 5,
        "rag_kb_embedding_items_per_run_max": 2616,
        "rag_kb_embedding_requests_per_run_max": 27,
        "embedding_items_per_run_max": 3106,
        "embedding_requests_per_run_max": 32,
        "runs": 2,
        "generation_calls_total_max": 618,
        "embedding_items_total_max": 6212,
        "embedding_requests_total_max": 64,
    }


def test_runner_rejects_generation_values_target_cli_cannot_apply(tmp_path):
    args = _runner_args(tmp_path)
    _validate_pinned_generation(args)

    args.top_p = 0.8
    with pytest.raises(ValueError, match="top-p"):
        _validate_pinned_generation(args)

    args.top_p = 0.95
    args.top_k = 20
    with pytest.raises(ValueError, match="top-k"):
        _validate_pinned_generation(args)


def test_execute_rejects_preexisting_rag_vector_cache(tmp_path):
    args = _runner_args(tmp_path)
    args.execute = True
    args.code_root.mkdir(parents=True)
    args.input_path.write_text(
        json.dumps([{"name": str(index)} for index in range(490)]),
        encoding="utf-8",
    )
    args.heldout_path.write_text(
        json.dumps([{"evaluation_cohort": "AI_RECOMMENDATION"} for _ in range(490)]),
        encoding="utf-8",
    )
    (args.kb_root / "structured").mkdir(parents=True)
    cache_dir = args.code_root / "data" / "cache" / "rag_vectors"
    cache_dir.mkdir(parents=True)
    (cache_dir / "stale.pkl").write_bytes(b"stale")

    with pytest.raises(RuntimeError, match="empty isolated RAG vector cache"):
        _validate_inputs(args)
