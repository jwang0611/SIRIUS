"""Unit tests for spec_mapper router project KB ingestion integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.web.session_manager import session_manager


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

        tpl_dir = tmp_path / "data" / "knowledge_base" / "template_spec"
        tpl_dir.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        out_dir = session_manager.get_session_als_dir("sess_123")
        (out_dir / "fixture_als.xlsx").write_bytes(b"als-marker")
        (tpl_dir / "fixture_template.xlsx").write_bytes(b"template-marker")

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

    def test_run_omitted_als_sheet_defaults_to_sheet1(
        self, client: TestClient, patch_ingest_and_job: Any, tmp_path, monkeypatch
    ):
        """Web 入口的 sheet 默认值由 Pydantic 字段提供，恒为显式值。

        因此 ``ALS_DEFAULT_SHEET`` 与 spec_mapper 配置层对 Web 都不生效——即使环境变量
        被设成别的值，请求里省略 ``als_sheet`` 仍然应该得到 ``"Sheet1"``。
        优先级表见 ``src/spec_mapper/README.md``「ALS sheet 解析优先级」。
        """
        mock_ingest, mock_job, _ = patch_ingest_and_job
        mock_ingest.return_value = MagicMock()
        mock_job.return_value = True
        monkeypatch.setenv("ALS_DEFAULT_SHEET", "EnvironmentSheet")

        tpl_dir = tmp_path / "data" / "knowledge_base" / "template_spec"
        tpl_dir.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        session_id = "spec-default-sheet"
        als_dir = session_manager.get_session_als_dir(session_id)
        (als_dir / "fixture_als.xlsx").write_bytes(b"als-marker")
        (tpl_dir / "fixture_template.xlsx").write_bytes(b"template-marker")

        response = client.post(
            "/api/spec-mapper/run",
            json={
                "als_file": "fixture_als.xlsx",
                "template_file": "fixture_template.xlsx",
                "output_name": "out",
            },
            headers={"X-Session-ID": session_id},
        )

        assert response.status_code == 200
        assert mock_ingest.call_args.kwargs["sheet_name"] == "Sheet1"
        assert mock_job.call_args.kwargs["als_sheet"] == "Sheet1"

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
        mock_ingest, mock_job, mock_jm = patch_ingest_and_job
        call_order: list[str] = []

        def record_ingest(**_kwargs):
            call_order.append("ingest")
            return MagicMock()

        def record_job(**_kwargs):
            call_order.append("job")
            return True

        mock_ingest.side_effect = record_ingest
        mock_job.side_effect = record_job

        # Handler requires these paths to exist (404 otherwise). CI has no local fixtures.
        tpl_dir = tmp_path / "data" / "knowledge_base" / "template_spec"
        tpl_dir.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        out_dir = session_manager.get_session_als_dir("sess_123")
        (out_dir / "fixture_als.xlsx").write_bytes(b"als-marker")
        (tpl_dir / "fixture_template.xlsx").write_bytes(b"template-marker")

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
        assert mock_ingest.call_args.kwargs["_writer_locked"] is True

        mock_job.assert_called_once()
        assert mock_jm.create_job.call_args.kwargs["owner_session_id"] == "sess_123"
        assert mock_job.call_args.kwargs["session_id"] == "sess_123"
        snapshot = mock_ingest.call_args.kwargs["als_file_path"]
        assert Path(mock_job.call_args.kwargs["als_file"]) == snapshot.resolve()
        original = session_manager.get_session_als_dir("sess_123") / "fixture_als.xlsx"
        assert snapshot != original
        original.write_bytes(b"replacement")
        assert snapshot.read_bytes() == b"als-marker"
        template_snapshot = Path(mock_job.call_args.kwargs["template_file"])
        assert template_snapshot.read_bytes() == b"template-marker"
        # Both KB ingestion and the worker consume immutable job inputs, and
        # ingestion must complete before the worker is launched.
        assert call_order == ["ingest", "job"]

    def test_unknown_project_ingest_error_is_generic(
        self, client: TestClient, patch_ingest_and_job: Any, tmp_path, monkeypatch
    ):
        mock_ingest, mock_job, _ = patch_ingest_and_job
        mock_ingest.side_effect = RuntimeError("secret metadata at /server/private/path")
        tpl_dir = tmp_path / "data" / "knowledge_base" / "template_spec"
        tpl_dir.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        out_dir = session_manager.get_session_als_dir("sess_123")
        (out_dir / "fixture_als.xlsx").write_bytes(b"als-marker")
        (tpl_dir / "fixture_template.xlsx").write_bytes(b"template-marker")

        response = client.post(
            "/api/spec-mapper/run",
            json={
                "als_file": "fixture_als.xlsx",
                "template_file": "fixture_template.xlsx",
                "output_name": "out",
            },
            headers={"X-Session-ID": "sess_123"},
        )
        assert response.status_code == 500
        assert response.json() == {"detail": "项目 KB 回注失败，请查看服务端日志。"}
        assert "secret metadata" not in response.text
        assert "/server/private/path" not in response.text
        mock_job.assert_not_called()
        session_spec_root = tmp_path / "data/spec_output/sessions" / session_manager.session_dir_key("sess_123")
        assert not list(session_spec_root.glob("*"))

    def test_rejected_project_ingest_returns_422_and_removes_snapshots(
        self,
        client: TestClient,
        patch_ingest_and_job: Any,
        tmp_path,
        monkeypatch,
    ):
        mock_ingest, mock_job, _ = patch_ingest_and_job
        mock_ingest.side_effect = ValueError("raw workbook detail")
        monkeypatch.chdir(tmp_path)
        session_id = "spec-ingest-rejected"
        als_dir = session_manager.get_session_als_dir(session_id)
        als_dir.joinpath("fixture_als.xlsx").write_bytes(b"als-marker")
        template_dir = tmp_path / "data/knowledge_base/template_spec"
        template_dir.mkdir(parents=True)
        template_dir.joinpath("fixture_template.xlsx").write_bytes(b"template-marker")

        response = client.post(
            "/api/spec-mapper/run",
            json={
                "als_file": "fixture_als.xlsx",
                "template_file": "fixture_template.xlsx",
                "output_name": "out",
            },
            headers={"X-Session-ID": session_id},
        )

        assert response.status_code == 422
        assert "raw workbook detail" not in response.text
        mock_job.assert_not_called()
        session_spec_root = tmp_path / "data/spec_output/sessions" / session_manager.session_dir_key(session_id)
        assert not list(session_spec_root.glob("*"))

    def test_worker_start_failure_restores_previous_project_shard_and_removes_snapshots(
        self,
        client: TestClient,
        patch_ingest_and_job: Any,
        tmp_path,
        monkeypatch,
    ):
        mock_ingest, mock_job, _ = patch_ingest_and_job
        monkeypatch.chdir(tmp_path)
        session_id = "spec-start-rollback"
        als_dir = session_manager.get_session_als_dir(session_id)
        als_dir.joinpath("fixture_als.xlsx").write_bytes(b"als-marker")
        template_dir = tmp_path / "data/knowledge_base/template_spec"
        template_dir.mkdir(parents=True)
        template_dir.joinpath("fixture_template.xlsx").write_bytes(b"template-marker")
        project_shard = session_manager.get_session_kb_dir(session_id) / "project_web.parquet"
        project_shard.write_bytes(b"old-project-kb")

        def replace_project_shard(**_kwargs):
            project_shard.write_bytes(b"new-project-kb")
            return project_shard

        mock_ingest.side_effect = replace_project_shard
        mock_job.return_value = False

        response = client.post(
            "/api/spec-mapper/run",
            json={
                "als_file": "fixture_als.xlsx",
                "template_file": "fixture_template.xlsx",
                "output_name": "out",
            },
            headers={"X-Session-ID": session_id},
        )

        assert response.status_code == 409
        assert project_shard.read_bytes() == b"old-project-kb"
        session_spec_root = tmp_path / "data/spec_output/sessions" / session_manager.session_dir_key(session_id)
        assert not list(session_spec_root.glob("*"))

    def test_session_cannot_start_spec_from_another_sessions_als(
        self, client: TestClient, patch_ingest_and_job: Any, tmp_path, monkeypatch
    ):
        mock_ingest, mock_job, _ = patch_ingest_and_job
        session_a = "spec-owner-a"
        session_b = "spec-owner-b"
        tpl_dir = tmp_path / "data" / "knowledge_base" / "template_spec"
        tpl_dir.mkdir(parents=True)
        (tpl_dir / "fixture_template.xlsx").write_bytes(b"template")
        monkeypatch.chdir(tmp_path)
        session_manager.get_or_create(session_a)
        owner_file = session_manager.get_session_als_dir(session_a) / "private.xlsx"
        owner_file.write_bytes(b"private")
        session_manager.add_file(session_a, str(owner_file))

        try:
            response = client.post(
                "/api/spec-mapper/run",
                json={
                    "als_file": "private.xlsx",
                    "template_file": "fixture_template.xlsx",
                    "output_name": "forbidden",
                },
                headers={"X-Session-ID": session_b},
            )
            assert response.status_code == 404
            mock_ingest.assert_not_called()
            mock_job.assert_not_called()
        finally:
            session_manager.cleanup_session(session_a)
            session_manager.cleanup_session(session_b)


def test_convert_invalid_workbook_returns_safe_422(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_dir = session_manager.get_session_kb_dir("sess_123")
    (session_dir / "invalid.xlsx").write_bytes(b"not-an-excel-workbook")

    from src.web.security import InvalidWorkbookError

    with patch(
        "src.web.routers.spec_mapper.run_command",
        side_effect=InvalidWorkbookError("工作簿格式不符合要求，请检查必需的工作表和列"),
    ):
        response = client.post(
            "/api/convert-als2sdtm",
            json={"file_path": "invalid.xlsx", "sheet_name": "eCRF"},
            headers={"X-Session-ID": "sess_123"},
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "工作簿格式不符合要求，请检查必需的工作表和列"}
