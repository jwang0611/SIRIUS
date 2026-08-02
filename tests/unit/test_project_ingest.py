"""Unit tests for project KB auto-ingestion."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.processors.project_ingest import (
    _slug,
    _validate_project_name,
    ingest_project_kb,
    load_session_reference_kb,
)


class TestSlug:
    def test_slug_sanitizes_project_name(self):
        assert _slug("My Project") == "My_Project"
        assert _slug("  spaced  ") == "spaced"
        assert _slug("project@v2") == "projectv2"
        assert _slug("a-b_c.d") == "a-b_c.d"


class TestValidateProjectName:
    @pytest.mark.parametrize(
        "name",
        [
            "",
            "a" * 65,
            "project/name",
            "project name!",
            " project! ",
        ],
    )
    def test_ingest_rejects_invalid_project_name(self, name: str):
        with pytest.raises(ValueError):
            _validate_project_name(name)

    def test_accepts_valid_names(self):
        _validate_project_name("Valid_Project-1.0")
        _validate_project_name("a")
        _validate_project_name("A" * 64)


class TestIngestProjectKb:
    @pytest.fixture
    def mock_session_manager(self, tmp_path: Path):
        with patch("src.processors.project_ingest.session_manager") as mock_sm:
            kb_dir = tmp_path / "kb"
            kb_dir.mkdir()
            mock_sm.get_session_kb_dir.return_value = kb_dir
            mock_sm.session_dir_key.return_value = "sid_test_reference"
            yield mock_sm

    @pytest.fixture
    def mock_convert(self):
        with patch("src.processors.project_ingest.convert_als2sdtm") as mock_conv:
            yield mock_conv

    @pytest.fixture
    def mock_audit(self):
        with patch("src.processors.project_ingest.AuditLogger") as mock_audit_cls:
            instance = MagicMock()
            mock_audit_cls.return_value = instance
            yield mock_audit_cls, instance

    def test_ingest_writes_parquet_with_metadata_columns(
        self,
        mock_session_manager: Any,
        mock_convert: Any,
        mock_audit: Any,
        tmp_path: Path,
    ):
        als_file = tmp_path / "als.xlsx"
        als_file.write_text("dummy")

        df = pd.DataFrame(
            {
                "annotation_table": ["DM"],
                "annotation_variable": ["VAR1"],
                "metadata_table": ["DM"],
                "metadata_variable": ["VAR1"],
            }
        )
        parquet_path = tmp_path / "_ingest_proj.parquet"
        df.to_parquet(parquet_path, index=False)
        mock_convert.return_value = {"parquet": str(parquet_path)}

        result = ingest_project_kb(
            session_id="sess_123",
            als_file_path=als_file,
            project_name="proj",
            sheet_name="Sheet1",
        )

        assert result.name == "project_proj.parquet"
        written = pd.read_parquet(result)
        assert "_kb_source" in written.columns
        assert "_session_ref" in written.columns
        assert "_ingested_at" in written.columns
        assert written["_kb_source"].iloc[0] == "project:proj"
        assert written["_session_ref"].iloc[0] == "sid_test_reference"

    def test_ingest_is_idempotent_dedups_latest_ingested_at(
        self,
        mock_session_manager: Any,
        mock_convert: Any,
        mock_audit: Any,
        tmp_path: Path,
    ):
        kb_dir = tmp_path / "kb"
        kb_dir.mkdir(exist_ok=True)
        mock_session_manager.get_session_kb_dir.return_value = kb_dir

        als_file = tmp_path / "als.xlsx"
        als_file.write_text("dummy")

        base_df = pd.DataFrame(
            {
                "annotation_table": ["DM", "VS"],
                "annotation_variable": ["VAR1", "VAR2"],
                "metadata_table": ["DM", "VS"],
                "metadata_variable": ["VAR1", "VAR2"],
            }
        )

        # First ingestion
        parquet1 = tmp_path / "_ingest_proj.parquet"
        base_df.to_parquet(parquet1, index=False)
        mock_convert.return_value = {"parquet": str(parquet1)}

        ingest_project_kb(
            session_id="sess_123",
            als_file_path=als_file,
            project_name="proj",
            sheet_name="Sheet1",
        )

        # Second ingestion with same data
        parquet2 = tmp_path / "_ingest_proj2.parquet"
        base_df.to_parquet(parquet2, index=False)
        mock_convert.return_value = {"parquet": str(parquet2)}

        ingest_project_kb(
            session_id="sess_123",
            als_file_path=als_file,
            project_name="proj",
            sheet_name="Sheet1",
        )

        shard = kb_dir / "project_proj.parquet"
        written = pd.read_parquet(shard)
        # Same 2 rows, deduped to latest ingest
        assert len(written) == 2
        assert written["_kb_source"].nunique() == 1

    def test_ingest_updates_existing_with_new_rows(
        self,
        mock_session_manager: Any,
        mock_convert: Any,
        mock_audit: Any,
        tmp_path: Path,
    ):
        kb_dir = tmp_path / "kb"
        kb_dir.mkdir(exist_ok=True)
        mock_session_manager.get_session_kb_dir.return_value = kb_dir

        als_file = tmp_path / "als.xlsx"
        als_file.write_text("dummy")

        base_df = pd.DataFrame(
            {
                "annotation_table": ["DM"],
                "annotation_variable": ["VAR1"],
                "metadata_table": ["DM"],
                "metadata_variable": ["VAR1"],
            }
        )

        # First ingestion
        parquet1 = tmp_path / "_ingest_proj.parquet"
        base_df.to_parquet(parquet1, index=False)
        mock_convert.return_value = {"parquet": str(parquet1)}

        ingest_project_kb(
            session_id="sess_123",
            als_file_path=als_file,
            project_name="proj",
            sheet_name="Sheet1",
        )

        # Second ingestion with additional row
        new_df = pd.DataFrame(
            {
                "annotation_table": ["VS"],
                "annotation_variable": ["VAR2"],
                "metadata_table": ["VS"],
                "metadata_variable": ["VAR2"],
            }
        )
        parquet2 = tmp_path / "_ingest_proj2.parquet"
        new_df.to_parquet(parquet2, index=False)
        mock_convert.return_value = {"parquet": str(parquet2)}

        ingest_project_kb(
            session_id="sess_123",
            als_file_path=als_file,
            project_name="proj",
            sheet_name="Sheet1",
        )

        shard = kb_dir / "project_proj.parquet"
        written = pd.read_parquet(shard)
        assert len(written) == 2


class TestLoadSessionReferenceKb:
    @patch("src.processors.project_ingest.session_manager")
    def test_load_session_reference_kb_returns_none_when_absent(self, mock_sm: Any):
        mock_sm.get_session_kb_dir.return_value = Path("/nonexistent")
        assert load_session_reference_kb("sess_123") is None

    @patch("src.processors.project_ingest.session_manager")
    def test_load_session_reference_kb_concatenates_project_shards(self, mock_sm: Any, tmp_path: Path):
        kb_dir = tmp_path / "kb"
        kb_dir.mkdir()
        mock_sm.get_session_kb_dir.return_value = kb_dir

        df1 = pd.DataFrame(
            {
                "annotation_table": ["DM"],
                "annotation_variable": ["VAR1"],
                "metadata_table": ["DM"],
                "metadata_variable": ["VAR1"],
            }
        )
        df2 = pd.DataFrame(
            {
                "annotation_table": ["VS"],
                "annotation_variable": ["VAR2"],
                "metadata_table": ["VS"],
                "metadata_variable": ["VAR2"],
            }
        )
        df1.to_parquet(kb_dir / "project_alpha.parquet", index=False)
        df2.to_parquet(kb_dir / "project_beta.parquet", index=False)

        result = load_session_reference_kb("sess_123")
        assert result is not None
        assert len(result) == 2
        assert set(result["annotation_table"]) == {"DM", "VS"}
