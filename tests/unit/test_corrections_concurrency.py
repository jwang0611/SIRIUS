"""Concurrency and failure semantics for the session corrections KB."""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from src.web.routers import corrections as corrections_router
from src.web.session_manager import session_manager


def _payload(variable: str) -> dict:
    return {
        "corrections": [
            {
                "annotation_table": "Adverse Events",
                "metadata_table": "AE",
                "annotation_variable": variable,
                "metadata_variable": variable,
                "old_domain": "VS",
                "old_sdtm_variable": "VSTEST",
                "new_domain": "AE",
                "new_sdtm_variable": "AETERM",
            }
        ]
    }


def test_same_session_concurrent_posts_do_not_lose_corrections(tmp_path: Path, monkeypatch) -> None:
    from app import app

    monkeypatch.chdir(tmp_path)
    session_id = f"corrections-concurrent-{uuid.uuid4().hex}"
    headers = {"X-Session-ID": session_id}
    first_entered = threading.Event()
    release_first = threading.Event()
    call_lock = threading.Lock()
    calls = 0
    original_load = corrections_router._load_existing_corrections

    def paused_load(path: Path):
        nonlocal calls
        with call_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            first_entered.set()
            assert release_first.wait(timeout=3)
        return original_load(path)

    monkeypatch.setattr(corrections_router, "_load_existing_corrections", paused_load)

    def submit(variable: str):
        with TestClient(app) as client:
            return client.post("/api/corrections", headers=headers, json=_payload(variable))

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(submit, "AETERM")
            assert first_entered.wait(timeout=2)
            second = pool.submit(submit, "AEDECOD")

            # Wait until both ASGI leases are active. The second request must
            # still be blocked on the per-session writer lock.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with session_manager._lock:
                    if session_manager._active_operations.get(session_id, 0) >= 2:
                        break
                time.sleep(0.01)
            with call_lock:
                assert calls == 1

            release_first.set()
            assert first.result(timeout=3).status_code == 200
            assert second.result(timeout=3).status_code == 200

        with TestClient(app) as client:
            response = client.get("/api/corrections", headers=headers)
        assert response.status_code == 200
        assert response.json()["total"] == 2
        assert {item["metadata_variable"] for item in response.json()["corrections"]} == {
            "AETERM",
            "AEDECOD",
        }
    finally:
        release_first.set()
        session_manager.cleanup_session(session_id)


def test_corrupt_existing_corrections_are_never_overwritten(tmp_path: Path, monkeypatch) -> None:
    from app import app

    monkeypatch.chdir(tmp_path)
    session_id = f"corrections-corrupt-{uuid.uuid4().hex}"
    headers = {"X-Session-ID": session_id}
    session_manager.get_or_create(session_id)
    path = corrections_router._get_corrections_file(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = b"not-a-parquet-file"
    path.write_bytes(original)

    try:
        with TestClient(app) as client:
            post = client.post("/api/corrections", headers=headers, json=_payload("AETERM"))
            get = client.get("/api/corrections", headers=headers)
        assert post.status_code == 500
        assert post.json()["detail"] == "现有更正数据无法读取，未保存任何修改"
        assert get.status_code == 500
        assert path.read_bytes() == original
    finally:
        session_manager.cleanup_session(session_id)


def test_equal_timestamp_duplicate_uses_last_write_deterministically() -> None:
    timestamp = "2026-01-01T00:00:00+00:00"
    shared = {
        "annotation_table": "Adverse Events",
        "metadata_table": "AE",
        "annotation_variable": "Term",
        "metadata_variable": "AETERM",
        "_kb_source": "correction:safe-ref",
        "_corrected_at": timestamp,
    }
    frame = pd.DataFrame(
        [
            {**shared, "SDTM_Domain": "VS", "SDTM_Variable": "VSTEST"},
            {**shared, "SDTM_Domain": "AE", "SDTM_Variable": "AETERM"},
        ]
    )

    result = corrections_router._deduplicate_corrections(frame)

    assert len(result) == 1
    assert result.iloc[0]["SDTM_Domain"] == "AE"
    assert result.iloc[0]["SDTM_Variable"] == "AETERM"


def test_incomplete_existing_schema_is_rejected_without_overwrite(tmp_path: Path, monkeypatch) -> None:
    from app import app

    monkeypatch.chdir(tmp_path)
    session_id = f"corrections-schema-{uuid.uuid4().hex}"
    headers = {"X-Session-ID": session_id}
    session_manager.get_or_create(session_id)
    path = corrections_router._get_corrections_file(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"metadata_variable": "AETERM"}]).to_parquet(path, index=False)
    original = path.read_bytes()

    try:
        with TestClient(app) as client:
            response = client.post("/api/corrections", headers=headers, json=_payload("AETERM"))
        assert response.status_code == 500
        assert path.read_bytes() == original
    finally:
        session_manager.cleanup_session(session_id)


def test_colliding_legacy_prefix_sessions_keep_corrections_isolated(tmp_path: Path, monkeypatch) -> None:
    from app import app

    monkeypatch.chdir(tmp_path)
    shared_prefix = "sess_1234567"
    session_a = f"{shared_prefix}_a_{uuid.uuid4().hex}"
    session_b = f"{shared_prefix}_b_{uuid.uuid4().hex}"
    headers_a = {"X-Session-ID": session_a}
    headers_b = {"X-Session-ID": session_b}

    try:
        with TestClient(app) as client:
            assert client.post("/api/corrections", headers=headers_a, json=_payload("AETERM")).status_code == 200
            assert client.post("/api/corrections", headers=headers_b, json=_payload("VSTEST")).status_code == 200
            records_a = client.get("/api/corrections", headers=headers_a).json()["corrections"]
            records_b = client.get("/api/corrections", headers=headers_b).json()["corrections"]
        assert {row["metadata_variable"] for row in records_a} == {"AETERM"}
        assert {row["metadata_variable"] for row in records_b} == {"VSTEST"}
        assert corrections_router._get_corrections_file(session_a) != corrections_router._get_corrections_file(
            session_b
        )
    finally:
        session_manager.cleanup_session(session_a)
        session_manager.cleanup_session(session_b)
