"""Unit tests for :class:`IOHelpersMixin`."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd

from src.processors.io_helpers import IOHelpersMixin


class _Host(IOHelpersMixin):
    def __init__(self, *, input_file: str | None = None):
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
        }
        output_base = tmp_path / "out"
        (tmp_path / "out.tmp.json").write_text(json.dumps(temp_data), encoding="utf-8")
        got = host._get_existing_recommendations(str(output_base))
        assert "demo" in got

    def test_temp_file_old_format(self, tmp_path):
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
        assert "demo" in got

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
        recs = loaded["recommendations"][0]["domain_recommendations"]
        assert recs[0]["score"] == 0.9  # sorted desc

    def test_swallows_write_errors(self, tmp_path, monkeypatch, capsys):
        host = _Host()

        def _raise(*_a, **_kw):
            raise IOError("nope")

        monkeypatch.setattr("builtins.open", _raise)
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
        assert "prompt body" in text

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
