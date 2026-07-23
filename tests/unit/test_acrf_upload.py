"""raw_acrf web upload: session isolation + als2sdtm artifact tracking.

The extractor subprocess is stubbed (its own behaviour is covered by the module
tests); these tests exercise the route's directory scoping and returned files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _stub_run_command():
    """Simulate scripts/extract_acrf_pdf.py writing its three outputs."""

    def _run(command, timeout=None):
        opts = {command[i]: command[i + 1] for i in range(len(command) - 1)}
        stem = Path(opts["--input"]).stem
        out_dir = Path(opts["--output-dir"])
        als_dir = Path(opts["--als2sdtm-dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        als_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{stem}.json").write_text("[]", encoding="utf-8")
        (out_dir / f"{stem}.xlsx").write_bytes(b"xlsx")
        (als_dir / f"{stem}_ALS2SDTM.xlsx").write_bytes(b"xlsx")
        return ""

    return _run


def _upload(client, session_id: str):
    return client.post(
        "/api/upload/raw_acrf",
        files={"file": ("proj.pdf", b"%PDF-1.4 test", "application/pdf")},
        headers={"X-Session-ID": session_id},
    )


def test_raw_acrf_upload_returns_als2sdtm_and_scopes_by_session(tmp_workspace, monkeypatch):
    import src.web.routers.upload as upload_mod

    monkeypatch.setattr(upload_mod, "run_command", _stub_run_command())

    from fastapi.testclient import TestClient

    from app import app

    client = TestClient(app)
    resp = _upload(client, "sess_abc")
    assert resp.status_code == 200, resp.text
    derived = resp.json()["derived_files"]

    # The portable als2sdtm workbook (P2 fix) is surfaced alongside json/xlsx.
    assert any(p.endswith("proj.json") for p in derived)
    assert any(p.endswith("proj.xlsx") for p in derived)
    assert any(p.endswith("proj_ALS2SDTM.xlsx") for p in derived)
    # Everything is session-scoped, not written to the global data/ roots.
    assert all("sessions" in Path(p).parts for p in derived)


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
