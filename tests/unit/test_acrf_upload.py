"""raw_acrf web upload: per-session isolation + als2sdtm tracking + recommend path.

The extractor subprocess is stubbed (its behaviour is covered by the module
tests); these tests exercise the route's session-scoped filenames, the returned
artifacts, and that a session upload can actually reach the recommendation flow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _stub_run_command():
    """Simulate scripts/extract_acrf_pdf.py writing its three global outputs."""

    def _run(command, timeout=None):
        opts = {command[i]: command[i + 1] for i in range(len(command) - 1)}
        stem = Path(opts["--input"]).stem
        Path("data/processed").mkdir(parents=True, exist_ok=True)
        Path("data/output").mkdir(parents=True, exist_ok=True)
        (Path("data/processed") / f"{stem}.json").write_text("[]", encoding="utf-8")
        (Path("data/processed") / f"{stem}.xlsx").write_bytes(b"xlsx")
        (Path("data/output") / f"{stem}_ALS2SDTM.xlsx").write_bytes(b"xlsx")
        return ""

    return _run


def _upload(client, session_id: str):
    return client.post(
        "/api/upload/raw_acrf",
        files={"file": ("proj.pdf", b"%PDF-1.4 test", "application/pdf")},
        headers={"X-Session-ID": session_id},
    )


def test_raw_acrf_upload_returns_als2sdtm_and_scopes_filename_by_session(tmp_workspace, monkeypatch):
    import src.web.routers.upload as upload_mod

    monkeypatch.setattr(upload_mod, "run_command", _stub_run_command())

    from fastapi.testclient import TestClient

    from app import app
    from src.web.session_manager import session_manager

    client = TestClient(app)
    resp = _upload(client, "sess_abc")
    assert resp.status_code == 200, resp.text
    derived = resp.json()["derived_files"]

    assert any(p.endswith(".json") for p in derived)
    assert any(p.endswith(".xlsx") and not p.endswith("_ALS2SDTM.xlsx") for p in derived)
    assert any(p.endswith("_ALS2SDTM.xlsx") for p in derived)  # portable workbook surfaced
    key = session_manager.session_dir_key("sess_abc")
    assert all(key in Path(p).name for p in derived)  # session-scoped filenames


def test_raw_acrf_uploads_do_not_collide_across_sessions(tmp_workspace, monkeypatch):
    import src.web.routers.upload as upload_mod

    monkeypatch.setattr(upload_mod, "run_command", _stub_run_command())

    from fastapi.testclient import TestClient

    from app import app

    client = TestClient(app)
    first = set(_upload(client, "sess_one").json()["derived_files"])
    second = set(_upload(client, "sess_two").json()["derived_files"])

    # Same filename, different sessions → disjoint, non-overwriting paths.
    assert first and second
    assert first.isdisjoint(second)


def test_raw_acrf_upload_then_recommend_resolves_path(tmp_workspace, monkeypatch):
    import src.web.routers.jobs as jobs_mod
    import src.web.routers.upload as upload_mod

    monkeypatch.setattr(upload_mod, "run_command", _stub_run_command())
    # Don't launch a real job — only prove the path resolves (no 404).
    monkeypatch.setattr(jobs_mod, "start_recommendations_job", lambda **_kw: None)

    from fastapi.testclient import TestClient

    from app import app

    client = TestClient(app)
    derived = _upload(client, "sess_e2e").json()["derived_files"]
    json_name = next(Path(p).name for p in derived if p.endswith(".json"))

    # The global processed-files listing surfaces the session-scoped file...
    listed = client.get("/api/processed-files").json()["files"]
    assert json_name in listed

    # ...and the recommendation endpoint resolves it (previously a 404 dead-end).
    resp = client.post(
        "/api/recommendations",
        json={"json_file": json_name, "language": "cn"},
        headers={"X-Session-ID": "sess_e2e"},
    )
    assert resp.status_code == 200, resp.text
