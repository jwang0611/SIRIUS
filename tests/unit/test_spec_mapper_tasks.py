"""Background Spec Mapper job state machine + observability + safety.

Exercises :func:`src.web.tasks._run_spec_mapper_job` synchronously against the
**real** IG 3.2 template (copied under a temp CWD) and asserts:

  * a fully-successful run  -> ``completed``           (spec_written == spec_attempted)
  * a partial write failure -> ``completed_with_errors`` (artifact still downloadable)
  * a fatal save failure    -> ``failed``              (never reported as completed*)
  * job messages / downloadable logs never leak absolute paths or tracebacks
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from src.spec_mapper.core.excel_writer import ExcelWriter
from src.spec_mapper.models.write_result import RecoverableWriteError
from src.web.job_manager import job_manager
from src.web.session_manager import session_manager
from src.web.tasks import _remove_session_snapshot_tree, _run_spec_mapper_job

REPO_ROOT = Path(__file__).resolve().parents[2]
V32_TEMPLATE = REPO_ROOT / "data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx"

assert V32_TEMPLATE.is_file(), f"required repo template missing: {V32_TEMPLATE}"


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
    if session_id:
        session_manager.get_or_create(session_id)
        source = Path("data/output/als.xlsx")
        session_input = session_manager.get_session_als_dir(session_id) / source.name
        shutil.copy2(source, session_input)
        session_manager.add_file(session_id, str(session_input))
        session_manager.add_job(session_id, job_id)
    job_manager.create_job(job_id, owner_session_id=session_id)
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
    # A *recognized* recoverable per-op failure -> completed_with_errors.
    def boom(self, *a, **k):
        raise RecoverableWriteError("injected recoverable failure")

    monkeypatch.setattr(ExcelWriter, "add_content_link_to_domain", boom)

    job = job_manager.get_job(_run_job())
    assert job.state == "completed_with_errors"
    assert job.spec_written < job.spec_attempted
    assert job.spec_errors >= 1
    # completed_with_errors must still yield a downloadable artifact.
    assert job.output_excel and Path(job.output_excel).exists()
    # The job message must not leak the raw injected exception text.
    assert "injected" not in job.message


def test_warning_only_run_requires_review(spec_workspace: Path, monkeypatch) -> None:
    """Warnings remain visible even when every attempted write succeeds."""
    from src.spec_mapper import SpecMapper

    real_process = SpecMapper.process

    def process_with_warning(self, *args, **kwargs):
        stats = real_process(self, *args, **kwargs)
        warning = {
            "code": "supp_multi_source",
            "stage": "supp_rows",
            "operation": "insert_supp_row",
            "sheet": "DM",
            "row": None,
            "column": None,
            "detail": None,
        }
        stats["write_result"]["warnings"].append(warning)
        stats["actual"]["warnings"] += 1
        return stats

    monkeypatch.setattr(SpecMapper, "process", process_with_warning)
    job = job_manager.get_job(_run_job())
    assert job.state == "completed_with_errors"
    assert job.spec_written == job.spec_attempted
    assert job.spec_warnings >= 1
    assert any(issue["code"] == "supp_multi_source" for issue in job.spec_issues)


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
        raise RecoverableWriteError("recoverable")

    monkeypatch.setattr(ExcelWriter, "add_content_link_to_domain", boom)
    job = job_manager.get_job(_run_job())

    assert job.state == "completed_with_errors"
    assert job.spec_issues, "structured issues must be surfaced on the job"
    issue = job.spec_issues[0]
    # Locatable + safe fields only.
    assert set(issue) >= {"code", "stage", "operation"}
    assert issue["detail"] in (None, "RecoverableWriteError")
    # Exposed through the job API payload.
    assert job.to_dict()["spec_issues"]


def test_completed_spec_job_releases_dedicated_logger(spec_workspace: Path) -> None:
    job_id = _run_job()

    assert f"sirius.spec_job.{job_id}" not in logging.Logger.manager.loggerDict


def test_completed_job_input_tree_is_removed_and_untracked(spec_workspace: Path) -> None:
    session_id = "snapshot-release-session"
    session_manager.get_or_create(session_id)
    snapshot_root = session_manager.get_session_processed_dir(session_id) / "jobs" / "job" / "input"
    snapshot = snapshot_root / "source.xlsx"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(b"snapshot")
    assert session_manager.add_file(session_id, str(snapshot))

    assert _remove_session_snapshot_tree(session_id, snapshot_root) is True

    assert not snapshot_root.exists()
    info = session_manager.get_session_info(session_id, include_files=True)
    assert info is not None
    assert "source.xlsx" not in info["files"]
    session_manager.cleanup_session(session_id)


@pytest.mark.parametrize("sheet", ["EGTEST", "FATEST", "LBTEST", "SUPPQUAL"])
def test_structured_issue_preserves_safe_template_sheet_location(sheet: str) -> None:
    from src.web.tasks import _all_spec_issues

    issues = _all_spec_issues(
        {
            "write_result": {
                "errors": [
                    {
                        "code": "sheet_not_found",
                        "stage": "conditional_mappings",
                        "operation": "write_conditional_columns",
                        "sheet": sheet,
                        "row": 2,
                        "column": 3,
                        "detail": "sheet_not_found",
                    }
                ],
                "warnings": [],
            }
        }
    )

    assert issues[0]["sheet"] == sheet


@pytest.mark.parametrize(
    ("variable", "expected"),
    [
        ("AEDECOD", "AEDECOD"),  # a real configured external-coding variable
        ("MHBDSYCD", "MHBDSYCD"),  # 8 chars, the CDISC maximum
        # A subject-like token fits any identifier shape/length regex, which is
        # exactly why membership in the packaged config — not shape — decides.
        ("SUBJ0001", None),
        ("QNAM_1", None),  # identifier-shaped but not a configured variable
        ("PHI_SENTINEL_DO_NOT_EXPOSE", None),  # free text is never echoed
        ("受试者姓名", None),  # non-identifier text is never echoed
        ("aedecod", None),  # variable names reach the writer upper-cased
        (42, None),
    ],
)
def test_structured_issue_keeps_variable_names_and_drops_free_text(variable: object, expected: str | None) -> None:
    """A per-item skip must name its variable, without becoming a free-text channel."""
    from src.web.tasks import _all_spec_issues

    issues = _all_spec_issues(
        {
            "write_result": {
                "errors": [],
                "warnings": [
                    {
                        "code": "variable_not_found",
                        "stage": "external_coding",
                        "operation": "update_existing_variables",
                        "sheet": "AE",
                        "row": None,
                        "column": None,
                        "variable": variable,
                        "detail": None,
                    }
                ],
            }
        }
    )

    assert issues[0]["code"] == "variable_not_found"
    assert issues[0]["variable"] == expected


def test_structured_issue_drops_variable_for_non_skip_codes() -> None:
    """Only the two per-item skip codes may carry a variable, even a configured one."""
    from src.web.tasks import _all_spec_issues

    issues = _all_spec_issues(
        {
            "write_result": {
                "errors": [],
                "warnings": [
                    {
                        "code": "no_op",
                        "stage": "external_coding",
                        "operation": "update_existing_variables",
                        "sheet": "AE",
                        "row": None,
                        "column": None,
                        "variable": "AEDECOD",
                        "detail": None,
                    }
                ],
            }
        }
    )

    assert issues[0]["code"] == "no_op"
    assert issues[0]["variable"] is None


def test_mapper_issue_cannot_leak_free_text_or_extra_fields(spec_workspace: Path, monkeypatch) -> None:
    """Mapper issue dictionaries are treated as untrusted at the API boundary."""
    from app import app
    from src.spec_mapper import SpecMapper

    # This deliberately matches the old generic token regex. Only a semantic
    # allowlist can distinguish it from a legitimate machine value.
    sentinel = "PHI_SENTINEL_DO_NOT_EXPOSE"
    real_process = SpecMapper.process

    def process_with_sensitive_issue(self, *args, **kwargs):
        stats = real_process(self, *args, **kwargs)
        stats["write_result"]["warnings"].append(
            {
                "code": sentinel,
                "stage": sentinel,
                "operation": sentinel,
                "sheet": sentinel,
                "row": None,
                "column": None,
                "detail": sentinel,
                "variable": sentinel,
                "raw_value": sentinel,
            }
        )
        stats["actual"]["warnings"] += 1
        return stats

    monkeypatch.setattr(SpecMapper, "process", process_with_sensitive_issue)

    session_id = f"safe-issue-{uuid.uuid4().hex}"
    job_id = _run_job(session_id=session_id)
    job = job_manager.get_job(job_id)
    assert job and job.state == "completed_with_errors"

    payload_text = json.dumps(job.to_dict(), ensure_ascii=False)
    assert sentinel not in payload_text
    issue = job.spec_issues[-1]
    assert set(issue) == {"code", "stage", "operation", "sheet", "row", "column", "variable", "detail"}
    assert issue == {
        "code": "unknown",
        "stage": "unknown",
        "operation": "unknown",
        "sheet": None,
        "row": None,
        "column": None,
        # The sentinel is a valid generic token, but it is not a configured
        # external-coding variable, so it is dropped rather than echoed.
        "variable": None,
        "detail": None,
    }
    assert "raw_value" not in issue

    client = TestClient(app)
    headers = {"X-Session-ID": session_id}
    issues_response = client.get(f"/api/jobs/{job_id}/download-issues", headers=headers)
    assert issues_response.status_code == 200
    assert sentinel not in issues_response.text
    assert all("raw_value" not in item for item in issues_response.json())

    log_response = client.get(f"/api/jobs/{job_id}/download-log", headers=headers)
    assert log_response.status_code == 200
    assert sentinel not in log_response.text


def test_completed_with_errors_is_downloadable_via_api(spec_workspace: Path, monkeypatch) -> None:
    def boom(self, *a, **k):
        raise RecoverableWriteError("injected recoverable failure")

    monkeypatch.setattr(ExcelWriter, "add_content_link_to_domain", boom)
    session_id = "download-session"
    job_id = _run_job(session_id=session_id)
    assert job_manager.get_job(job_id).state == "completed_with_errors"

    from app import app

    resp = TestClient(app).get(
        f"/api/jobs/{job_id}/download?format=excel",
        headers={"X-Session-ID": session_id},
    )
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
    assert job.output_log
    assert "Spec Mapper job finished" in Path(job.output_log).read_text(encoding="utf-8")


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


def test_terminal_state_is_published_after_log_is_closed(spec_workspace: Path, monkeypatch) -> None:
    """Polling clients must see a complete log as soon as state is terminal."""
    real_update = job_manager.update_job
    terminal_publications = 0

    def checking_update(job_id: str, **updates):
        nonlocal terminal_publications
        if updates.get("state") in {"completed", "completed_with_errors", "failed", "cancelled"}:
            terminal_publications += 1
            output_log = updates.get("output_log")
            assert output_log
            log_text = Path(output_log).read_text(encoding="utf-8")
            assert "Spec Mapper job finished" in log_text
        return real_update(job_id, **updates)

    monkeypatch.setattr(job_manager, "update_job", checking_update)
    job = job_manager.get_job(_run_job())
    assert job.state == "completed"
    assert terminal_publications == 1


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

    a, b = ids["cjob_a"], ids["cjob_b"]
    job_a = job_manager.get_job(a)
    job_b = job_manager.get_job(b)
    assert job_a and job_a.output_log
    assert job_b and job_b.output_log
    log_a = Path(job_a.output_log).read_text(encoding="utf-8")
    log_b = Path(job_b.output_log).read_text(encoding="utf-8")
    assert a in log_a and b not in log_a
    assert b in log_b and a not in log_b
    assert Path(job_a.output_log).parent != Path(job_b.output_log).parent


def test_same_output_name_isolated_by_session_and_job(spec_workspace: Path) -> None:
    session_a = f"session-a-{uuid.uuid4().hex}"
    session_b = f"session-b-{uuid.uuid4().hex}"
    session_manager.set_job_manager(job_manager)
    jobs: list[str] = []

    try:
        for session_id in (session_a, session_b):
            session_manager.get_or_create(session_id)
            als_path = session_manager.get_session_als_dir(session_id) / "als.xlsx"
            _write_synthetic_als(als_path)
            session_manager.add_file(session_id, str(als_path))
            job_id = uuid.uuid4().hex
            jobs.append(job_id)
            job_manager.create_job(job_id, owner_session_id=session_id)
            session_manager.add_job(session_id, job_id)
            _run_spec_mapper_job(
                job_id=job_id,
                als_file="als.xlsx",
                template_file="tpl.xlsx",
                output_name="same_name",
                als_sheet="Sheet1",
                create_test_sheets=True,
                session_id=session_id,
            )

        job_a = job_manager.get_job(jobs[0])
        job_b = job_manager.get_job(jobs[1])
        assert job_a and job_a.output_excel and job_a.output_log
        assert job_b and job_b.output_excel and job_b.output_log
        assert Path(job_a.output_excel).parent != Path(job_b.output_excel).parent
        assert Path(job_a.output_log).parent != Path(job_b.output_log).parent
        assert Path(job_a.output_excel).exists()
        assert Path(job_b.output_excel).exists()

        output_b = Path(job_b.output_excel)
        session_manager.cleanup_session(session_a)
        assert output_b.exists(), "cleaning session A must not remove session B's artifact"
    finally:
        session_manager.cleanup_session(session_a)
        session_manager.cleanup_session(session_b)


def test_same_session_same_output_name_isolated_by_job(spec_workspace: Path) -> None:
    """Overlapping runs in one session must isolate workbook, log, and issues."""
    from src.spec_mapper import SpecMapper

    session_id = f"same-session-{uuid.uuid4().hex}"
    session_manager.set_job_manager(job_manager)
    session_manager.get_or_create(session_id)
    als_path = session_manager.get_session_als_dir(session_id) / "als.xlsx"
    _write_synthetic_als(als_path)
    session_manager.add_file(session_id, str(als_path))
    jobs: list[str] = []
    expected_sheet: dict[str, str] = {}
    barrier = threading.Barrier(2)

    try:
        for _ in range(2):
            job_id = uuid.uuid4().hex
            jobs.append(job_id)
            job_manager.create_job(job_id, owner_session_id=session_id)
            session_manager.add_job(session_id, job_id)

        def lightweight_init(_self, *_args, **_kwargs) -> None:
            """Skip template-version I/O, which is unrelated to job isolation."""

        def overlapping_process(self, *args, **kwargs):
            """Produce a minimal artifact after proving both jobs overlap.

            Real mapper initialization and workbook mapping are covered by the
            other tests in this module. Keeping this isolation test lightweight
            prevents runner speed from turning its deadlock guard into a flaky
            performance deadline.
            """
            barrier.wait(timeout=5)
            output_file = kwargs["output_file"]
            workbook = Workbook()
            workbook.save(output_file)
            workbook.close()

            sheet = "DM" if threading.current_thread().name.endswith("a") else "EG"
            return {
                "als_records": 3,
                "actual": {
                    "attempted": 1,
                    "written": 1,
                    "skipped": 0,
                    "warnings": 1,
                    "errors": 0,
                },
                "write_result": {
                    "errors": [],
                    "warnings": [
                        {
                            "code": "supp_multi_source",
                            "stage": "supp_rows",
                            "operation": "insert_supp_row",
                            "sheet": sheet,
                        }
                    ],
                },
            }

        def run(job_id: str, thread_name: str) -> None:
            expected_sheet[job_id] = "DM" if thread_name.endswith("a") else "EG"
            _run_spec_mapper_job(
                job_id=job_id,
                als_file="als.xlsx",
                template_file="tpl.xlsx",
                output_name="same_name",
                als_sheet="Sheet1",
                create_test_sheets=False,
                session_id=session_id,
            )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(SpecMapper, "__init__", lightweight_init)
            mp.setattr(SpecMapper, "process", overlapping_process)
            threads = [
                threading.Thread(target=run, args=(jobs[0], "job-a"), name="job-a"),
                threading.Thread(target=run, args=(jobs[1], "job-b"), name="job-b"),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            assert not [thread.name for thread in threads if thread.is_alive()]

        job_a = job_manager.get_job(jobs[0])
        job_b = job_manager.get_job(jobs[1])
        assert job_a and job_a.output_excel and job_a.output_log and job_a.output_issues
        assert job_b and job_b.output_excel and job_b.output_log and job_b.output_issues
        assert job_a.state == job_b.state == "completed_with_errors"
        assert Path(job_a.output_excel).parent != Path(job_b.output_excel).parent
        assert Path(job_a.output_log).parent != Path(job_b.output_log).parent
        assert Path(job_a.output_issues).parent != Path(job_b.output_issues).parent

        for job in (job_a, job_b):
            assert Path(job.output_excel).stat().st_size > 0
            log_text = Path(job.output_log).read_text(encoding="utf-8")
            assert job.job_id in log_text
            other_id = job_b.job_id if job is job_a else job_a.job_id
            assert other_id not in log_text
            issues = json.loads(Path(job.output_issues).read_text(encoding="utf-8"))
            assert issues[-1]["sheet"] == expected_sheet[job.job_id]
    finally:
        session_manager.cleanup_session(session_id)


def test_bare_valueerror_marks_failed(spec_workspace: Path, monkeypatch) -> None:
    """A bare ValueError (not the dedicated RecoverableWriteError) at the guard
    boundary is unknown/fatal and must fail the job, not be masked as
    completed_with_errors."""

    def boom(self, *a, **k):
        raise ValueError("looks recoverable but is not classified")

    monkeypatch.setattr(ExcelWriter, "add_content_link_to_domain", boom)

    job = job_manager.get_job(_run_job())
    assert job.state == "failed"
    assert job.state not in {"completed", "completed_with_errors"}
    assert "looks recoverable" not in job.message


def test_internal_exception_record_is_excluded_from_downloadable_log(spec_workspace: Path, monkeypatch) -> None:
    """Internal mapper records are not part of the curated downloadable log."""
    import logging as _logging

    from src.spec_mapper import SpecMapper

    real_process = SpecMapper.process

    def noisy_process(self, *args, **kwargs):
        # Emit an exception record whose traceback embeds a fake server path.
        try:
            raise RuntimeError("boom at /server/secret/oops.py line 42")
        except RuntimeError:
            _logging.getLogger("src.spec_mapper").exception("PHI_SENTINEL_INTERNAL_METADATA")
        return real_process(self, *args, **kwargs)

    monkeypatch.setattr(SpecMapper, "process", noisy_process)

    job = job_manager.get_job(_run_job())
    assert job.output_log
    log_text = Path(job.output_log).read_text(encoding="utf-8")
    assert "PHI_SENTINEL_INTERNAL_METADATA" not in log_text
    assert "Traceback (most recent call last)" not in log_text
    assert "/server/secret/oops.py" not in log_text
    assert "boom at" not in log_text
    # The run itself still finishes normally (the log noise is incidental).
    assert job.state in {"completed", "completed_with_errors"}


def test_truncated_issues_are_downloadable(spec_workspace: Path, monkeypatch) -> None:
    """When issues exceed the API cap, the job exposes the true total and keeps
    the complete, safe list downloadable — nothing is silently dropped."""
    from src.web import tasks as tasks_mod

    # Shrink the cap so the run's real issues deterministically exceed it.
    monkeypatch.setattr(tasks_mod, "_SPEC_ISSUE_CAP", 1)

    def boom(self, *a, **k):
        raise RecoverableWriteError("recoverable")

    # add_content_link_to_domain runs once per ALS domain sheet (DM, EG) -> >1 error.
    monkeypatch.setattr(ExcelWriter, "add_content_link_to_domain", boom)

    session_id = "issues-session"
    job_id = _run_job(session_id=session_id)
    job = job_manager.get_job(job_id)
    assert job.state == "completed_with_errors"
    # The in-payload list is capped, but the true total is larger and exposed.
    assert len(job.spec_issues) == 1
    assert job.spec_issues_total > len(job.spec_issues)
    assert job.to_dict()["spec_issues_total"] == job.spec_issues_total
    # The full, safe list is persisted and downloadable via the API.
    assert job.output_issues and Path(job.output_issues).exists()

    import json as _json

    from app import app

    resp = TestClient(app).get(
        f"/api/jobs/{job_id}/download-issues",
        headers={"X-Session-ID": session_id},
    )
    assert resp.status_code == 200
    payload = _json.loads(resp.content)
    assert isinstance(payload, list)
    assert len(payload) == job.spec_issues_total


def test_issue_persistence_failure_falls_back_to_full_payload(spec_workspace: Path, monkeypatch) -> None:
    """If the full issue file cannot be written, the COMPLETE list must fall
    back into the job payload (nothing invisible) and no dead download link is
    advertised (output_issues stays unset)."""
    import pathlib

    from src.web import tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "_SPEC_ISSUE_CAP", 1)

    def boom(self, *a, **k):
        raise RecoverableWriteError("recoverable")

    monkeypatch.setattr(ExcelWriter, "add_content_link_to_domain", boom)

    real_write_text = pathlib.Path.write_text

    def failing_write_text(self, *args, **kwargs):
        if str(self).endswith(".issues.json"):
            raise OSError("disk full")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", failing_write_text)

    job = job_manager.get_job(_run_job())
    assert job.state == "completed_with_errors"
    # No file -> no download path exposed (the UI hides the link).
    assert job.output_issues is None
    # Fallback: the FULL list is in the payload, so items beyond the cap are
    # still visible despite the persistence failure.
    assert job.spec_issues_total > 1
    assert len(job.spec_issues) == job.spec_issues_total
    assert job.to_dict()["output_issues"] is None
