"""
Structured audit logger for SDTM mapping operations.

Provides GxP-compliant audit trails by recording every mapping decision
with timestamps, inputs, outputs, and sources. Log files are append-only
and stored per session for traceability.
"""

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.infrastructure.session_key import safe_session_key

logger = logging.getLogger(__name__)


class AuditLogger:
    """Append-only structured audit log for mapping operations.

    Each mapping operation is recorded as a JSON line entry containing:
    - Timestamp (UTC)
    - Session ID
    - Operation type (sdtm_mapping, spec_generation, etc.)
    - Input metadata (table, variable)
    - Output result (domain, sdtm_variable, source, confidence)
    - Cascade level used (Level 1-4)
    - Validation issues (if any)

    Usage:
        auditor = AuditLogger(session_id="abc123")
        auditor.log_mapping(
            variable_data={"metadata_table": "DM", "metadata_variable": "BRTHDTC"},
            result={"domain": "DM", "sdtm_variable": "BRTHDTC", "source": "KB", "score": 1.0},
            cascade_level=1,
        )
    """

    def __init__(
        self,
        session_id: str,
        log_dir: str = "data/audit_logs",
        enabled: bool = True,
    ):
        self.session_id = session_id
        # Filesystem-safe, collision-free key for the log FILENAME. The raw
        # session_id is still recorded in each entry body for traceability,
        # but a hostile X-Session-ID (e.g. containing "/") must not be able to
        # traverse the path or silently break the audit write.
        self._log_key = safe_session_key(session_id) if session_id else "unknown"
        self.log_dir = Path(log_dir)
        self.enabled = enabled
        self._lock = threading.Lock()
        self._entry_count: int = 0

        if self.enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)

    def log_mapping(
        self,
        variable_data: dict[str, Any],
        result: dict[str, Any],
        cascade_level: int = 0,
        validation_issues: list[str] | None = None,
        processing_time_ms: float | None = None,
    ) -> None:
        """Record a single mapping operation to the audit log.

        Args:
            variable_data: Input variable metadata
            result: Mapping result (domain, sdtm_variable, etc.)
            cascade_level: Which cascade level produced this result (1-4)
            validation_issues: Any deterministic validation issues found
            processing_time_ms: Time taken for this mapping in milliseconds
        """
        if not self.enabled:
            return

        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": self.session_id,
            "operation": "sdtm_mapping",
            "cascade_level": cascade_level,
            "input": {
                "metadata_table": variable_data.get("metadata_table", ""),
                "metadata_variable": variable_data.get("metadata_variable", ""),
                "annotation_table": variable_data.get("annotation_table", ""),
                "annotation_variable": variable_data.get("annotation_variable", ""),
            },
            "output": {
                "domain": result.get("domain", ""),
                "sdtm_variable": result.get("sdtm_variable", ""),
                "sdtm_variable_type": result.get("sdtm_variable_type", ""),
                "source": result.get("source", ""),
                "confidence": result.get("score", 0.0),
                "testcd": result.get("testcd", ""),
                "supp_variable": result.get("supp_variable", ""),
            },
            "validation_issues": validation_issues or [],
        }
        if processing_time_ms is not None:
            entry["processing_time_ms"] = round(processing_time_ms, 2)

        self._append_entry(entry)

    def log_batch_summary(
        self,
        total_variables: int,
        kb_hits: int,
        rag_hits: int,
        llm_calls: int,
        total_time_ms: float,
        avg_confidence: float,
    ) -> None:
        """Record a batch processing summary.

        Args:
            total_variables: Total number of variables processed
            kb_hits: Number resolved by KB (Level 1-2)
            rag_hits: Number resolved by RAG (Level 3)
            llm_calls: Number requiring LLM inference (Level 4)
            total_time_ms: Total processing time in milliseconds
            avg_confidence: Average confidence score across all results
        """
        if not self.enabled:
            return

        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": self.session_id,
            "operation": "batch_summary",
            "stats": {
                "total_variables": total_variables,
                "kb_hits": kb_hits,
                "rag_hits": rag_hits,
                "llm_calls": llm_calls,
                "total_time_ms": round(total_time_ms, 2),
                "avg_confidence": round(avg_confidence, 4),
                "llm_call_rate": round(llm_calls / max(total_variables, 1), 4),
            },
        }
        self._append_entry(entry)

    def log_llm_retry(
        self,
        *,
        variable_data: dict[str, Any],
        model_name: str,
        retry_count: int,
        reason: str,
        instruction_version: str,
    ) -> None:
        """Record a model retry without persisting prompt or response content."""
        if not self.enabled:
            return

        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": self.session_id,
            "operation": "llm_retry",
            "model": model_name,
            "retry_count": retry_count,
            "reason": reason,
            "instruction_version": instruction_version,
            "input": {
                "metadata_table": variable_data.get("metadata_table", ""),
                "metadata_variable": variable_data.get("metadata_variable", ""),
            },
        }
        self._append_entry(entry)

    def log_correction(
        self,
        variable_data: dict[str, Any],
        old_result: dict[str, Any],
        new_result: dict[str, Any],
        corrected_by: str = "user",
    ) -> None:
        """Record a user correction to a mapping result.

        Args:
            variable_data: Input variable metadata
            old_result: The previous (incorrect) mapping
            new_result: The corrected mapping
            corrected_by: Who made the correction (default: "user")
        """
        if not self.enabled:
            return

        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": self.session_id,
            "operation": "mapping_correction",
            "corrected_by": corrected_by,
            "input": {
                "metadata_table": variable_data.get("metadata_table", ""),
                "metadata_variable": variable_data.get("metadata_variable", ""),
                "annotation_table": variable_data.get("annotation_table", ""),
                "annotation_variable": variable_data.get("annotation_variable", ""),
            },
            "old_mapping": {
                "domain": old_result.get("domain", ""),
                "sdtm_variable": old_result.get("sdtm_variable", ""),
                "source": old_result.get("source", ""),
                "confidence": old_result.get("score", 0.0),
            },
            "new_mapping": {
                "domain": new_result.get("domain", ""),
                "sdtm_variable": new_result.get("sdtm_variable", ""),
                "source": "user_correction",
                "confidence": 1.0,
            },
        }
        self._append_entry(entry)

    def log_project_ingest(
        self,
        project_name: str,
        record_count: int,
        kb_file: str,
    ) -> None:
        """Record a project KB ingestion event."""
        if not self.enabled:
            return
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": self.session_id,
            "operation": "project_ingest",
            "project_name": project_name,
            "record_count": record_count,
            "kb_file": kb_file,
        }
        self._append_entry(entry)

    def _append_entry(self, entry: dict[str, Any]) -> None:
        """Append a JSON line entry to the session audit log file."""
        with self._lock:
            try:
                log_file = self.log_dir / f"audit_{self._log_key}.jsonl"
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._entry_count += 1
            except Exception as e:
                logger.warning(f"Failed to write audit log entry: {e}")

    @property
    def entry_count(self) -> int:
        return self._entry_count
