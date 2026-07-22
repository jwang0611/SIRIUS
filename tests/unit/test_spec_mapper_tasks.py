"""Background Spec Mapper job state machine + observability + safety.

Exercises :func:`src.web.tasks._run_spec_mapper_job` synchronously against the
**real** IG 3.2 template (copied under a temp CWD) and asserts:

  * a fully-successful run  -> ``completed``           (spec_written == spec_attempted)
  * a partial write failure -> ``completed_with_errors`` (artifact still downloadable)
  * a fatal save failure    -> ``failed``              (never reported as completed*)
  * job messages / downloadable logs never leak absolute paths or tracebacks
"""

from __future__ import annotations

import shutil
import threading
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from src.spec_mapper.core.excel_writer import ExcelWriter
from src.web.job_manager import job_manager
from src.web.tasks import _run_spec_mapper_job

V32_TEMPLATE = Path("data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx")

pytestmark = pytest.mark.skipif(not V32_TEMPLATE.exists(), reason="IG 3.2 template not present")


def _write_synthetic_als(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["表", "变量", "变量名", "组件类型", "编码名称", "metadata_table", "SDTM_Domain", "SDTM_Variable"])
    for row in [
        ["SUBJECT", "STUDYID", "研究代码", "文本", None, "SUBJECT", "DM", "STUDYID"],
        ["SUBJECT", "SITENAME", "中心名称", "文本", None, "SUBJECT", "DM", "QVAL when QNAM=SITENAM"],
        ["EG_RAW", "QTCF", "QT间期", "单选", "EG_CT", "EG_RAW", "EG", "EGORRES when EGTESTCD=QT"],
    ]:
        ws.append(row)
    wb.save(path)


@pytest.fixture
def spec_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Lay out data/output + template dirs under a temp CWD and return names."""
    out_dir = tmp_path / "data" / "output"
    tpl_dir = tmp_path / "data" / "knowledge_base" / "template_spec"
    out_dir.mkdir(parents=True)
    tpl_dir.mkdir(parents=True)
    _write_synthetic_als(out_dir / "als.xlsx")
    shutil.copy2(V32_TEMPLATE, tpl_dir / "tpl.xlsx")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run_job(session_id: str | None = None) -> str:
    job_id = uuid.uuid4().hex
    job_manager.create_job(job_id)
    _run_spec_mapper_job(
        job_id=job_id,
        als_file="als.xlsx",
        template_file="tpl.xlsx",
        output_name="spec_out",
        als_sheet="Sheet1",
        highlight=True,
        create_test_sheets=True,
        session_id=session_id,
    )
    return job_id


def test_clean_run_marks_completed(spec_workspace: Path) -> None:
    job = job_manager.get_job(_run_job())
    assert job.state == "completed"
    assert job.spec_attempted > 0
    assert job.spec_written == job.spec_attempted
    assert job.spec_errors == 0
    assert job.output_excel and Path(job.output_excel).exists()


def test_partial_failure_marks_completed_with_errors(spec_workspace: Path, monkeypatch) -> None:
    # A *recoverable* (classified) per-op failure -> completed_with_errors.
    def boom(self, *a, **k):
        raise ValueError("injected recoverable failure")

    monkeypatch.setattr(ExcelWriter, "add_content_link_to_domain", boom)

    job = job_manager.get_job(_run_job())
    assert job.state == "completed_with_errors"
    assert job.spec_written < job.spec_attempted
    assert job.spec_errors >= 1
    # completed_with_errors must still yield a downloadable artifact.
    assert job.output_excel and Path(job.output_excel).exists()
    # The job message must not leak the raw injected exception text.
    assert "injected" not in job.message


def test_unknown_error_marks_failed(spec_workspace: Path, monkeypatch) -> None:
    """An unknown (non-recoverable) write exception must fail the job, not be
    masked as completed_with_errors."""

    def boom(self, *a, **k):
        raise RuntimeError("unexpected programming error")

    monkeypatch.setattr(ExcelWriter, "add_content_link_to_domain", boom)

    job = job_manager.get_job(_run_job())
    assert job.state == "failed"
    assert job.state not in {"completed", "completed_with_errors"}
    assert "unexpected programming error" not in job.message


def test_completed_with_errors_exposes_structured_issues(spec_workspace: Path, monkeypatch) -> None:
    """The job/API must expose *which* items failed, not just counts."""

    def boom(self, *a, **k):
        raise ValueError("recoverable")

    monkeypatch.setattr(ExcelWriter, "add_content_link_to_domain", boom)
    job = job_manager.get_job(_run_job())

    assert job.state == "completed_with_errors"
    assert job.spec_issues, "structured issues must be surfaced on the job"
    issue = job.spec_issues[0]
    # Locatable + safe fields only.
    assert set(issue) >= {"code", "stage", "operation"}
    assert issue["detail"] in (None, "ValueError")
    # Exposed through the job API payload.
    assert job.to_dict()["spec_issues"]


def test_completed_with_errors_is_downloadable_via_api(spec_workspace: Path, monkeypatch) -> None:
    def boom(self, *a, **k):
        raise ValueError("injected recoverable failure")

    monkeypatch.setattr(ExcelWriter, "add_content_link_to_domain", boom)
    job_id = _run_job()
    assert job_manager.get_job(job_id).state == "completed_with_errors"

    from app import app

    resp = TestClient(app).get(f"/api/jobs/{job_id}/download?format=excel")
    assert resp.status_code == 200
    assert len(resp.content) > 0


def test_fatal_save_failure_marks_failed(spec_workspace: Path, monkeypatch) -> None:
    def boom(self, *a, **k):
        raise OSError("disk full at /server/secret/path")

    monkeypatch.setattr(ExcelWriter, "save", boom)

    job = job_manager.get_job(_run_job())
    assert job.state == "failed"
    # Never masquerade a fatal failure as (partial) success.
    assert job.state not in {"completed", "completed_with_errors"}
    # The failure message must be safe: no raw exception text, no server path.
    assert "disk full" not in job.message
    assert "/server/secret/path" not in job.message
    assert "Traceback" not in job.message


def test_missing_input_marks_failed_with_safe_message(spec_workspace: Path) -> None:
    job_id = uuid.uuid4().hex
    job_manager.create_job(job_id)
    _run_spec_mapper_job(
        job_id=job_id,
        als_file="does_not_exist.xlsx",
        template_file="tpl.xlsx",
        output_name="spec_out",
    )
    job = job_manager.get_job(job_id)
    assert job.state == "failed"
    assert "不存在" in job.message  # helpful but safe


def test_downloadable_log_has_no_absolute_paths(spec_workspace: Path) -> None:
    import src.spec_mapper as sm_pkg
    from src.web.tasks import _ABS_PATH_RE

    job = job_manager.get_job(_run_job())
    assert job.output_log
    log_text = Path(job.output_log).read_text(encoding="utf-8")
    # The user-downloadable log must not embed the absolute workspace path…
    assert str(spec_workspace) not in log_text
    # …nor the server-side package/repo path (e.g. ConfigLoader's config dir)…
    assert str(Path(sm_pkg.__file__).parent) not in log_text
    # …nor any absolute / multi-segment filesystem path, nor a traceback.
    assert _ABS_PATH_RE.search(log_text) is None
    assert "Traceback (most recent call last)" not in log_text


def test_concurrent_jobs_logs_are_isolated(spec_workspace: Path) -> None:
    """Two jobs running at once must not bleed records into each other's log."""
    ids: dict[str, str] = {}

    def run(name: str) -> None:
        jid = uuid.uuid4().hex
        job_manager.create_job(jid)
        _run_spec_mapper_job(
            job_id=jid,
            als_file="als.xlsx",
            template_file="tpl.xlsx",
            output_name=name,
            als_sheet="Sheet1",
            create_test_sheets=True,
        )
        ids[name] = jid

    t1 = threading.Thread(target=run, args=("cjob_a",))
    t2 = threading.Thread(target=run, args=("cjob_b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    log_a = Path("data/spec_output/logs/cjob_a.log").read_text(encoding="utf-8")
    log_b = Path("data/spec_output/logs/cjob_b.log").read_text(encoding="utf-8")
    a, b = ids["cjob_a"], ids["cjob_b"]
    assert a in log_a and b not in log_a
    assert b in log_b and a not in log_b
