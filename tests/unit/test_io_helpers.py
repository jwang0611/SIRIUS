"""Unit tests for :class:`IOHelpersMixin`."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pandas as pd

from src.processors.io_helpers import IOHelpersMixin
from src.utils import atomic_json


class _Host(IOHelpersMixin):
    def __init__(self, *, input_file: str | None = None, checkpoint_context: dict | None = None):
        self._input_file = input_file
        self.debug = False
        self.log_ai_interactions = False
        self.generation_config = SimpleNamespace(
            max_output_tokens=1000,
            temperature=0.0,
            top_p=1.0,
            top_k=1,
        )
        self.language = "en"
        self.model_name = "test/model"
        self.checkpoint_context = checkpoint_context


class TestGetExistingRecommendationsJSON:
    def test_loads_json_file(self, tmp_path):
        host = _Host()
        data = [
            {
                "table_name": "demo",
                "domain_recommendations": [{"variable_name": "age", "domain": "DM", "sdtm_variable": "AGE"}],
            }
        ]
        output_base = tmp_path / "out"
        (tmp_path / "out.json").write_text(json.dumps(data), encoding="utf-8")
        got = host._get_existing_recommendations(str(output_base))
        assert "demo" in got
        assert "age" in got["demo"]

    def test_loads_temp_file_with_metadata(self, tmp_path):
        host = _Host()
        temp_data = {
            "recommendations": [
                {
                    "table_name": "demo",
                    "domain_recommendations": [{"variable_name": "age", "domain": "DM"}],
                }
            ],
            "timestamp": 1700000000.0,
            "completed_pairs": 1,
            "version": "1.0",
            "model_name": "test/model",
        }
        output_base = tmp_path / "out"
        (tmp_path / "out.tmp.json").write_text(json.dumps(temp_data), encoding="utf-8")
        got = host._get_existing_recommendations(str(output_base))
        assert "demo" in got

    def test_temp_file_without_model_identity_is_not_reused(self, tmp_path):
        host = _Host()
        # list at top level (old format)
        data = [
            {
                "table_name": "demo",
                "domain_recommendations": [{"variable_name": "age"}],
            }
        ]
        output_base = tmp_path / "out"
        (tmp_path / "out.tmp.json").write_text(json.dumps(data), encoding="utf-8")
        got = host._get_existing_recommendations(str(output_base))
        assert got == {}

    def test_temp_file_for_other_model_is_not_reused(self, tmp_path):
        host = _Host()
        temp_data = {
            "recommendations": [
                {
                    "table_name": "demo",
                    "domain_recommendations": [{"variable_name": "age"}],
                }
            ],
            "completed_pairs": 1,
            "model_name": "other/model",
        }
        output_base = tmp_path / "out"
        (tmp_path / "out.tmp.json").write_text(json.dumps(temp_data), encoding="utf-8")

        assert host._get_existing_recommendations(str(output_base)) == {}

    def test_temp_file_for_other_input_context_is_not_reused(self, tmp_path):
        host = _Host(checkpoint_context={"input": "new"})
        temp_data = {
            "recommendations": [
                {
                    "table_name": "demo",
                    "domain_recommendations": [{"variable_name": "age"}],
                }
            ],
            "completed_pairs": 1,
            "model_name": "test/model",
            "checkpoint_context": {"input": "old"},
        }
        output_base = tmp_path / "out"
        (tmp_path / "out.tmp.json").write_text(json.dumps(temp_data), encoding="utf-8")

        assert host._get_existing_recommendations(str(output_base)) == {}

    def test_corrupt_temp_falls_through_to_json(self, tmp_path):
        host = _Host()
        output_base = tmp_path / "out"
        (tmp_path / "out.tmp.json").write_text("not-json", encoding="utf-8")
        (tmp_path / "out.json").write_text(
            json.dumps([{"table_name": "t", "domain_recommendations": [{"variable_name": "v"}]}]),
            encoding="utf-8",
        )
        got = host._get_existing_recommendations(str(output_base))
        assert "t" in got

    def test_returns_empty_when_nothing_exists(self, tmp_path):
        host = _Host()
        output_base = tmp_path / "nonexistent"
        got = host._get_existing_recommendations(str(output_base))
        assert got == {}

    def test_loads_from_excel_fallback(self, tmp_path):
        host = _Host()
        df = pd.DataFrame(
            [
                {
                    "表": "demo",
                    "变量": "age",
                    "SDTM_Domain": "DM",
                    "SDTM_Variable": "AGE",
                    "Score": 0.9,
                    "Priority": 1,
                }
            ]
        )
        output_base = tmp_path / "out"
        df.to_excel(tmp_path / "out.xlsx", index=False)
        got = host._get_existing_recommendations(str(output_base))
        assert "demo" in got
        assert "age" in got["demo"]
        assert got["demo"]["age"][0]["sdtm_variable"] == "AGE"

    def test_skips_rows_without_table_or_variable(self, tmp_path):
        host = _Host()
        data = [
            {"table_name": "", "domain_recommendations": []},
            {"table_name": "valid", "domain_recommendations": [{"variable_name": ""}]},
            {
                "table_name": "good",
                "domain_recommendations": [{"variable_name": "v"}],
            },
        ]
        output_base = tmp_path / "out"
        (tmp_path / "out.json").write_text(json.dumps(data), encoding="utf-8")
        got = host._get_existing_recommendations(str(output_base))
        # "good" has a real variable; "valid" got empty dict
        assert "good" in got
        assert "v" in got["good"]


class TestSaveProgressToTempFile:
    def test_writes_sorted_and_counts(self, tmp_path):
        host = _Host()
        output_base = tmp_path / "out"
        table_recs = {
            "tbl": {
                "v1": [{"score": 0.5, "variable_name": "v1"}],
                "v2": [{"score": 0.9, "variable_name": "v2"}],
            }
        }
        host._save_progress_to_temp_file(str(output_base), table_recs)
        temp_path = tmp_path / "out.tmp.json"
        assert temp_path.exists()
        loaded = json.loads(temp_path.read_text(encoding="utf-8"))
        assert loaded["completed_pairs"] == 2
        assert loaded["model_name"] == "test/model"
        assert loaded["checkpoint_context"] is None
        recs = loaded["recommendations"][0]["domain_recommendations"]
        assert recs[0]["score"] == 0.9  # sorted desc

    def test_replaces_complete_checkpoint_atomically(self, tmp_path, monkeypatch):
        host = _Host()
        output_base = tmp_path / "out"
        host._save_progress_to_temp_file(
            str(output_base),
            {"tbl": {"v1": [{"score": 0.5, "variable_name": "v1"}]}},
        )

        replace_entered = threading.Event()
        allow_replace = threading.Event()
        real_replace = atomic_json.os.replace
        real_fsync = atomic_json.os.fsync
        fsync_calls = []

        def _paused_replace(source, destination):
            replace_entered.set()
            assert allow_replace.wait(timeout=5)
            real_replace(source, destination)

        def _recording_fsync(file_descriptor):
            fsync_calls.append(file_descriptor)
            real_fsync(file_descriptor)

        monkeypatch.setattr(atomic_json.os, "replace", _paused_replace)
        monkeypatch.setattr(atomic_json.os, "fsync", _recording_fsync)
        writer = threading.Thread(
            target=host._save_progress_to_temp_file,
            args=(
                str(output_base),
                {
                    "tbl": {
                        "v1": [{"score": 0.5, "variable_name": "v1"}],
                        "v2": [{"score": 0.9, "variable_name": "v2"}],
                    }
                },
            ),
        )
        writer.start()
        try:
            assert replace_entered.wait(timeout=5)
            checkpoint = tmp_path / "out.tmp.json"
            # Until os.replace happens, concurrent readers still see the prior
            # complete checkpoint rather than a partially-truncated document.
            assert json.loads(checkpoint.read_text(encoding="utf-8"))["completed_pairs"] == 1

            staging_files = list(tmp_path.glob(".out.tmp.json.*.part"))
            assert len(staging_files) == 1
            assert json.loads(staging_files[0].read_text(encoding="utf-8"))["completed_pairs"] == 2
            assert fsync_calls
        finally:
            allow_replace.set()
            writer.join(timeout=5)

        assert not writer.is_alive()
        assert json.loads(checkpoint.read_text(encoding="utf-8"))["completed_pairs"] == 2
        assert not list(tmp_path.glob(".out.tmp.json.*.part"))

    def test_swallows_write_errors(self, tmp_path, monkeypatch, capsys):
        host = _Host()

        def _raise(*_a, **_kw):
            raise IOError("nope")

        monkeypatch.setattr("src.processors.io_helpers.atomic_write_json", _raise)
        # Does not raise
        host._save_progress_to_temp_file(str(tmp_path / "out"), {})
        captured = capsys.readouterr()
        assert "Could not save progress" in captured.out


class TestLogAIInteraction:
    def test_writes_input_log(self, tmp_path, monkeypatch):
        host = _Host()
        monkeypatch.chdir(tmp_path)
        host._log_ai_interaction(
            table_name="tbl",
            variable_name="v",
            interaction_type="INPUT",
            content="prompt body",
            content_type="text",
        )
        log_dir = tmp_path / "data" / "output" / "logs"
        assert log_dir.exists()
        # one log file written
        files = list(log_dir.glob("ai_interactions_*.log"))
        assert len(files) == 1
        text = files[0].read_text(encoding="utf-8")
        assert "AI MODEL INPUT" in text
        assert "Content SHA-256:" in text
        assert "prompt body" not in text
        assert "tbl" not in text

    def test_writes_output_log_with_duration(self, tmp_path, monkeypatch):
        host = _Host()
        monkeypatch.chdir(tmp_path)
        host._log_ai_interaction(
            table_name="tbl",
            variable_name="v",
            interaction_type="OUTPUT",
            content="response body",
            content_type="json",
            api_duration=1.25,
        )
        log_dir = tmp_path / "data" / "output" / "logs"
        files = list(log_dir.glob("ai_interactions_*.log"))
        text = files[0].read_text(encoding="utf-8")
        assert "AI MODEL OUTPUT" in text
        assert "API duration: 1.25s" in text


class TestMergeWithECRFSheet:
    def test_returns_none_when_input_file_unset(self, capsys):
        host = _Host(input_file=None)
        result = host._merge_with_ecrf_sheet(pd.DataFrame())
        assert result is None
        captured = capsys.readouterr()
        assert "input_file" in captured.out

    def test_returns_none_when_processed_missing(self, tmp_path, capsys):
        host = _Host(input_file=str(tmp_path / "missing.json"))
        result = host._merge_with_ecrf_sheet(pd.DataFrame())
        assert result is None

    def test_job_snapshot_layout_preserves_real_ecrf_merge(self, tmp_path):
        job_dir = tmp_path / "job"
        processed_dir = job_dir / "processed"
        raw_dir = job_dir / "raw"
        processed_dir.mkdir(parents=True)
        raw_dir.mkdir()
        input_json = processed_dir / "fixture.json"
        input_json.write_text("[]", encoding="utf-8")
        pd.DataFrame(
            [
                {
                    "metadata_table": "AE",
                    "metadata_variable": "AETERM",
                    "num": "1",
                }
            ]
        ).to_excel(processed_dir / "fixture.xlsx", index=False)
        ecrf_columns = [
            "编号",
            "表名",
            "表",
            "标准表",
            "分类",
            "引用表",
            "变量名",
            "变量",
            "组件类型",
            "编码名称",
            "SAS导出格式",
        ]
        raw_row = dict.fromkeys(ecrf_columns, "")
        raw_row.update({"编号": "1", "表名": "Adverse Events", "变量名": "AETERM"})
        with pd.ExcelWriter(raw_dir / "fixture.xlsx") as writer:
            pd.DataFrame([raw_row]).to_excel(writer, sheet_name="eCRF", index=False)

        mapping = pd.DataFrame(
            [
                {
                    "metadata_table": "AE",
                    "metadata_variable": "AETERM",
                    "annotation_table": "Adverse Events",
                    "SDTM_Domain": "AE",
                    "SDTM_Variable": "AETERM",
                    "Score": 0.95,
                    "Source": "LLM",
                }
            ]
        )
        merged = _Host(input_file=str(input_json))._merge_with_ecrf_sheet(mapping)

        assert merged is not None
        assert merged.loc[0, "SDTM_Domain"] == "AE"
        assert merged.loc[0, "SDTM_Variable"] == "AETERM"
