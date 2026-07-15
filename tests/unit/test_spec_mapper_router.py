"""Unit tests for spec_mapper router project KB ingestion integration."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Deferred import to avoid side effects at import time."""
    from app import app

    return TestClient(app)


class TestSpecMapperRun:
    @pytest.fixture(autouse=True)
    def patch_ingest_and_job(self):
        with (
            patch("src.web.routers.spec_mapper.ingest_project_kb") as mock_ingest,
            patch("src.web.routers.spec_mapper.start_spec_mapper_job") as mock_job,
            patch("src.web.routers.spec_mapper.job_manager") as mock_jm,
        ):
            mock_jm.create_job.return_value = None
            yield mock_ingest, mock_job, mock_jm

    def test_run_omitted_project_name_uses_default_web(
        self, client: TestClient, patch_ingest_and_job: Any, tmp_path, monkeypatch
    ):
        """Web UI 可不传 project_name，后端默认 web 并与 Step3 一致。"""
        mock_ingest, _mock_job, _ = patch_ingest_and_job
        mock_ingest.return_value = MagicMock()
        mock_ingest.return_value.__str__ = lambda self: "/tmp/kb/project_web.parquet"

        out_dir = tmp_path / "data" / "output"
        tpl_dir = tmp_path / "data" / "knowledge_base" / "template_spec"
        out_dir.mkdir(parents=True)
        tpl_dir.mkdir(parents=True)
        (out_dir / "fixture_als.xlsx").write_bytes(b"")
        (tpl_dir / "fixture_template.xlsx").write_bytes(b"")
        monkeypatch.chdir(tmp_path)

        payload = {
            "als_file": "fixture_als.xlsx",
            "template_file": "fixture_template.xlsx",
            "output_name": "out",
            "als_sheet": "Sheet1",
            "highlight": True,
            "create_test_sheets": True,
        }
        response = client.post(
            "/api/spec-mapper/run",
            json=payload,
            headers={"X-Session-ID": "sess_123"},
        )
        assert response.status_code == 200
        mock_ingest.assert_called_once()
        assert mock_ingest.call_args.kwargs["project_name"] == "web"

    @pytest.mark.parametrize(
        "project_name",
        [
            "",
            "a" * 65,
            "project/name",
            "project name!",
        ],
    )
    def test_run_invalid_project_name_returns_422(self, client: TestClient, project_name: str):
        payload = {
            "als_file": "fixture_als.xlsx",
            "template_file": "fixture_template.xlsx",
            "output_name": "out",
            "als_sheet": "Sheet1",
            "highlight": True,
            "create_test_sheets": True,
            "project_name": project_name,
        }
        response = client.post(
            "/api/spec-mapper/run",
            json=payload,
            headers={"X-Session-ID": "sess_123"},
        )
        assert response.status_code == 422

    def test_run_valid_request_calls_ingest_before_job_start(
        self, client: TestClient, patch_ingest_and_job: Any, tmp_path, monkeypatch
    ):
        mock_ingest, mock_job, _ = patch_ingest_and_job
        mock_ingest.return_value = MagicMock()
        mock_ingest.return_value.__str__ = lambda self: "/tmp/kb/project_test.parquet"

        # Handler requires these paths to exist (404 otherwise). CI has no local fixtures.
        out_dir = tmp_path / "data" / "output"
        tpl_dir = tmp_path / "data" / "knowledge_base" / "template_spec"
        out_dir.mkdir(parents=True)
        tpl_dir.mkdir(parents=True)
        (out_dir / "fixture_als.xlsx").write_bytes(b"")
        (tpl_dir / "fixture_template.xlsx").write_bytes(b"")
        monkeypatch.chdir(tmp_path)

        payload = {
            "als_file": "fixture_als.xlsx",
            "template_file": "fixture_template.xlsx",
            "output_name": "out",
            "als_sheet": "Sheet1",
            "highlight": True,
            "create_test_sheets": True,
            "project_name": "TestProject",
        }
        response = client.post(
            "/api/spec-mapper/run",
            json=payload,
            headers={"X-Session-ID": "sess_123"},
        )
        assert response.status_code == 200
        assert response.json()["job_id"]

        mock_ingest.assert_called_once()
        assert mock_ingest.call_args.kwargs["session_id"] == "sess_123"
        assert mock_ingest.call_args.kwargs["project_name"] == "TestProject"
        assert mock_ingest.call_args.kwargs["sheet_name"] == "Sheet1"

        mock_job.assert_called_once()
        # Ensure ingest was called before job start by call order
        assert mock_ingest.call_count == 1
        assert mock_job.call_count == 1


def test_convert_invalid_workbook_returns_safe_422(client: TestClient, tmp_path, monkeypatch):
    documents_dir = tmp_path / "data" / "knowledge_base" / "documents"
    documents_dir.mkdir(parents=True)
    (documents_dir / "invalid.xlsx").write_bytes(b"not-an-excel-workbook")
    monkeypatch.chdir(tmp_path)

    from src.web.security import InvalidWorkbookError

    with patch(
        "src.web.routers.spec_mapper.run_command",
        side_effect=InvalidWorkbookError("工作簿格式不符合要求，请检查必需的工作表和列"),
    ):
        response = client.post(
            "/api/convert-als2sdtm",
            json={"file_path": "invalid.xlsx", "sheet_name": "eCRF"},
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "工作簿格式不符合要求，请检查必需的工作表和列"}
