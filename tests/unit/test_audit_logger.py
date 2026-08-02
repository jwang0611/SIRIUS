"""Unit tests for ``src.infrastructure.audit_logger``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.infrastructure.audit_logger import AuditLogger


@pytest.fixture
def auditor(tmp_path: Path) -> AuditLogger:
    return AuditLogger(session_id="unit-test-session", log_dir=str(tmp_path), enabled=True)


def _read_all_entries(log_file: Path):
    return [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line]


def _log_file(tmp_path: Path) -> Path:
    """Locate the single audit log in ``tmp_path``.

    The filename embeds a safe-session-key hash suffix, so tests glob for it
    instead of hard-coding ``audit_<session_id>.jsonl``.
    """
    files = list(tmp_path.glob("audit_*.jsonl"))
    assert len(files) == 1, f"expected exactly one audit log, found {files}"
    return files[0]


class TestLogMapping:
    def test_writes_single_jsonl_entry(self, auditor, tmp_path):
        auditor.log_mapping(
            variable_data={"metadata_table": "DM", "metadata_variable": "SUBJID"},
            result={"domain": "DM", "sdtm_variable": "SUBJID", "source": "KB", "score": 1.0},
            cascade_level=1,
        )
        log_file = _log_file(tmp_path)
        assert log_file.exists()
        entries = _read_all_entries(log_file)
        assert len(entries) == 1
        assert entries[0]["operation"] == "sdtm_mapping"
        assert entries[0]["output"]["domain"] == "DM"
        assert entries[0]["cascade_level"] == 1

    def test_includes_processing_time_when_provided(self, auditor, tmp_path):
        auditor.log_mapping(
            variable_data={},
            result={"domain": "AE", "sdtm_variable": "AETERM"},
            processing_time_ms=42.5,
        )
        entry = _read_all_entries(_log_file(tmp_path))[0]
        assert entry["processing_time_ms"] == pytest.approx(42.5)

    def test_omits_processing_time_when_absent(self, auditor, tmp_path):
        auditor.log_mapping(variable_data={}, result={"domain": "AE"})
        entry = _read_all_entries(_log_file(tmp_path))[0]
        assert "processing_time_ms" not in entry

    def test_validation_issues_default_to_empty_list(self, auditor, tmp_path):
        auditor.log_mapping(variable_data={}, result={"domain": "AE"})
        entry = _read_all_entries(_log_file(tmp_path))[0]
        assert entry["validation_issues"] == []

    def test_increments_entry_count(self, auditor):
        assert auditor.entry_count == 0
        auditor.log_mapping(variable_data={}, result={"domain": "AE"})
        auditor.log_mapping(variable_data={}, result={"domain": "VS"})
        assert auditor.entry_count == 2


class TestLogBatchSummary:
    def test_writes_summary(self, auditor, tmp_path):
        auditor.log_batch_summary(
            total_variables=10,
            kb_hits=6,
            rag_hits=2,
            llm_calls=2,
            total_time_ms=1234.5,
            avg_confidence=0.87,
        )
        entry = _read_all_entries(_log_file(tmp_path))[0]
        assert entry["operation"] == "batch_summary"
        assert entry["stats"]["total_variables"] == 10
        assert entry["stats"]["llm_call_rate"] == pytest.approx(0.2)

    def test_handles_zero_variables(self, auditor, tmp_path):
        auditor.log_batch_summary(
            total_variables=0,
            kb_hits=0,
            rag_hits=0,
            llm_calls=0,
            total_time_ms=0,
            avg_confidence=0,
        )
        entry = _read_all_entries(_log_file(tmp_path))[0]
        assert entry["stats"]["llm_call_rate"] == 0.0


class TestLogLlmRetry:
    def test_writes_structured_retry_without_raw_prompt_or_response(self, auditor, tmp_path):
        auditor.log_llm_retry(
            variable_data={
                "metadata_table": "AE",
                "metadata_variable": "AETERM",
            },
            model_name="test/model",
            retry_count=1,
            reason="invalid_json",
            instruction_version="json-repair-v1",
        )

        entry = _read_all_entries(_log_file(tmp_path))[0]
        assert entry == {
            "timestamp": entry["timestamp"],
            "session_ref": auditor._log_key,
            "operation": "llm_retry",
            "model": "test/model",
            "retry_count": 1,
            "reason": "invalid_json",
            "instruction_version": "json-repair-v1",
            "input": {
                "metadata_table": "AE",
                "metadata_variable": "AETERM",
            },
        }
        serialized = json.dumps(entry)
        assert "prompt" not in serialized.lower()
        assert "response" not in serialized.lower()


class TestLogCorrection:
    def test_writes_correction_entry(self, auditor, tmp_path):
        auditor.log_correction(
            variable_data={"metadata_variable": "AETERM"},
            old_result={"domain": "VS", "sdtm_variable": "AETERM", "source": "LLM", "score": 0.6},
            new_result={"domain": "AE", "sdtm_variable": "AETERM"},
        )
        entry = _read_all_entries(_log_file(tmp_path))[0]
        assert entry["operation"] == "mapping_correction"
        assert entry["new_mapping"]["domain"] == "AE"
        assert entry["new_mapping"]["source"] == "user_correction"
        assert entry["new_mapping"]["confidence"] == 1.0
        assert entry["old_mapping"]["domain"] == "VS"

    def test_corrected_by_default_user(self, auditor, tmp_path):
        auditor.log_correction(variable_data={}, old_result={}, new_result={})
        entry = _read_all_entries(_log_file(tmp_path))[0]
        assert entry["corrected_by"] == "user"

    def test_corrected_by_custom(self, auditor, tmp_path):
        auditor.log_correction(variable_data={}, old_result={}, new_result={}, corrected_by="reviewer_A")
        entry = _read_all_entries(_log_file(tmp_path))[0]
        assert entry["corrected_by"] == "reviewer_A"

    def test_sensitive_values_are_redacted_before_persistence(self, auditor, tmp_path):
        auditor.log_correction(
            variable_data={
                "metadata_table": "DOB: 1980-05-15",
                "metadata_variable": "Subject 001-0023",
                "annotation_variable": "Smith, John",
            },
            old_result={"domain": "VS", "sdtm_variable": "john@example.com"},
            new_result={"domain": "AE", "sdtm_variable": "AETERM"},
        )
        serialized = _log_file(tmp_path).read_text(encoding="utf-8")
        for sentinel in (
            "1980-05-15",
            "001-0023",
            "Smith, John",
            "john@example.com",
            "unit-test-session",
        ):
            assert sentinel not in serialized
        assert "[REDACTED]" in serialized

    def test_masker_failure_redacts_instead_of_failing_open(self, auditor, tmp_path, monkeypatch):
        def fail_mask(_text: str) -> str:
            raise RuntimeError("masker unavailable")

        monkeypatch.setattr(auditor._data_masker, "mask_text", fail_mask)
        auditor.log_mapping(
            variable_data={"metadata_variable": "Subject 001-0023"},
            result={"domain": "AE"},
        )
        serialized = _log_file(tmp_path).read_text(encoding="utf-8")
        assert "001-0023" not in serialized
        assert "[REDACTED]" in serialized


class TestDisabled:
    def test_disabled_writes_nothing(self, tmp_path):
        aud = AuditLogger(session_id="x", log_dir=str(tmp_path), enabled=False)
        aud.log_mapping(variable_data={}, result={"domain": "AE"})
        aud.log_batch_summary(1, 0, 0, 0, 0, 0)
        aud.log_correction(variable_data={}, old_result={}, new_result={})
        assert list(tmp_path.glob("*.jsonl")) == []
        assert aud.entry_count == 0


class TestThreadSafety:
    def test_concurrent_writes_all_recorded(self, auditor, tmp_path):
        import threading

        def _write(i):
            auditor.log_mapping(
                variable_data={"metadata_variable": f"V{i}"},
                result={"domain": "AE"},
            )

        threads = [threading.Thread(target=_write, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        entries = _read_all_entries(_log_file(tmp_path))
        assert len(entries) == 20


class TestHostileSessionId:
    """A malicious X-Session-ID must not break the audit write (which was
    silently swallowed as a warning) or escape the log directory."""

    def test_slash_in_session_id_still_writes_within_log_dir(self, tmp_path):
        aud = AuditLogger(session_id="a/b/../c", log_dir=str(tmp_path), enabled=True)
        aud.log_correction(
            variable_data={"metadata_variable": "AETERM"},
            old_result={"domain": "VS"},
            new_result={"domain": "AE"},
        )
        files = list(tmp_path.glob("audit_*.jsonl"))
        assert len(files) == 1
        # Stays directly under log_dir — no traversal, no missing parent dir.
        assert files[0].parent == tmp_path
        assert "/" not in files[0].name
        # The write actually succeeded (was previously lost to FileNotFoundError).
        assert aud.entry_count == 1
        entry = _read_all_entries(files[0])[0]
        assert entry["session_ref"] == aud._log_key
        assert "a/b/../c" not in json.dumps(entry)

    def test_distinct_hostile_ids_do_not_share_a_log_file(self, tmp_path):
        AuditLogger(session_id="a/b", log_dir=str(tmp_path)).log_mapping(variable_data={}, result={"domain": "AE"})
        AuditLogger(session_id="ab", log_dir=str(tmp_path)).log_mapping(variable_data={}, result={"domain": "VS"})
        # "a/b" and "ab" sanitise to the same prefix but must not collide.
        assert len(list(tmp_path.glob("audit_*.jsonl"))) == 2
