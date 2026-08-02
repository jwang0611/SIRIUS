"""File upload routes."""

import logging
from pathlib import Path
from typing import Any, cast

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from src.web.dependencies import session_writer_operation
from src.web.security import (
    ALLOWED_EXTENSIONS,
    PYTHON_BIN,
    RATE_LIMIT_UPLOAD,
    UPLOAD_CONFIG,
    InvalidWorkbookError,
    limiter,
    run_command,
    sanitize_filename,
    save_upload,
    validate_upload_file,
)
from src.web.session_manager import session_manager

router = APIRouter()
logger = logging.getLogger(__name__)


def _register_session_artifact(session_id: str, path: Path, *, knowledge_base: bool = False) -> None:
    """Publish an artifact only if the session is still accepting writes."""
    registered = (
        session_manager.add_kb_file(session_id, str(path))
        if knowledge_base
        else session_manager.add_file(session_id, str(path))
    )
    if not registered:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="Session 正在清理，请重试")


@router.post("/upload/{category}")
@limiter.limit(RATE_LIMIT_UPLOAD)
async def upload_file(
    request: Request,
    category: str,
    file: UploadFile = File(...),
    x_session_id: str = Depends(session_writer_operation, scope="request"),
):
    category = category.lower()
    if category not in UPLOAD_CONFIG:
        raise HTTPException(status_code=400, detail="未知的上传类别")
    if category == "standards":
        # Updating the global CDISC reference changes every session's mapping
        # behavior. A bearer session ID is not an operator authorization
        # boundary, so this mutation is intentionally unavailable over Web.
        raise HTTPException(status_code=403, detail="Web 端不允许更新全局标准库")

    config: dict[str, Any] = cast(dict[str, Any], UPLOAD_CONFIG[category])
    allowed_extensions = cast(set[str], config.get("allowed_extensions", ALLOWED_EXTENSIONS["excel"]))

    validate_upload_file(file, allowed_extensions)

    safe_filename = sanitize_filename(file.filename or "")
    # Project examples and reviewed ALS workbooks are session-owned. Never
    # place a session upload in the shared legacy directory, where a same-name
    # upload from another browser could overwrite it.
    if category in ("example", "example_raw", "als_example_raw"):
        target_dir = session_manager.get_session_kb_dir(x_session_id)
        destination = target_dir / safe_filename
        save_upload(file, destination)
        _register_session_artifact(x_session_id, destination, knowledge_base=True)
    elif category == "als_output":
        target_dir = session_manager.get_session_als_dir(x_session_id)
        destination = target_dir / safe_filename
        save_upload(file, destination)
        _register_session_artifact(x_session_id, destination)
    elif category in ("raw", "raw_taimei"):
        target_dir = session_manager.get_session_raw_dir(x_session_id)
        destination = target_dir / safe_filename
        save_upload(file, destination)
        _register_session_artifact(x_session_id, destination)
    else:
        target_dir = cast(Path, config["target"])
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / safe_filename
        save_upload(file, destination)

        is_knowledge_base = "knowledge_base" in str(destination)
        if not is_knowledge_base:
            _register_session_artifact(x_session_id, destination)

    output = ""
    sheets: list[str] | None = None
    derived_files: list[str] = []
    kb_files: list[str] = []

    try:
        cmd = config.get("command")

        if category in ("raw", "raw_taimei"):
            processed_dir = session_manager.get_session_processed_dir(x_session_id)
            script = "scripts/extract_taimei_sheet.py" if category == "raw_taimei" else "scripts/extract_ecrf_sheet.py"
            output = run_command(
                [
                    PYTHON_BIN,
                    script,
                    "--input",
                    str(destination),
                    "--output-dir",
                    str(processed_dir),
                ]
            )
            for ext in (".json", ".xlsx"):
                derived = processed_dir / f"{destination.stem}{ext}"
                if derived.exists():
                    derived_files.append(str(derived))
        elif category == "example":
            kb_output_dir = session_manager.get_session_kb_dir(x_session_id)
            custom_cmd = [
                PYTHON_BIN,
                "scripts/convert_als2sdtm.py",
                "--input",
                str(destination),
                "--output-dir",
                str(kb_output_dir),
            ]
            output = run_command(custom_cmd)

            for parquet_file in kb_output_dir.glob(f"{destination.stem}*.parquet"):
                _register_session_artifact(x_session_id, parquet_file, knowledge_base=True)
                kb_files.append(str(parquet_file))
        elif callable(cmd):
            output = run_command(cmd(destination))

        elif cmd is None:
            output = ""
        else:
            raise RuntimeError("Invalid upload command configuration")

        if category in ("example_raw", "als_example_raw", "als_output"):
            try:
                xl = pd.ExcelFile(destination)
                non_empty = []
                for name in xl.sheet_names:
                    tmp_df = xl.parse(sheet_name=name)
                    if not tmp_df.dropna(how="all").empty:
                        non_empty.append(name)
                sheets = non_empty if non_empty else list(xl.sheet_names)
            except Exception:
                sheets = None
    except HTTPException:
        raise
    except InvalidWorkbookError as exc:
        logger.info("Upload rejected because workbook structure is invalid (category=%s)", category)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Upload processing failed for category=%s (%s)", category, type(exc).__name__)
        raise HTTPException(status_code=500, detail="文件处理失败，请查看服务端日志") from exc

    if derived_files:
        for derived_path in derived_files:
            _register_session_artifact(x_session_id, Path(derived_path))

    return {
        "message": "上传成功",
        "category": category,
        # The caller only needs an opaque display/file identifier. Do not
        # expose the server-side session directory layout.
        "stored_to": destination.name,
        # Command stdout can contain source paths or metadata emitted by legacy
        # scripts. Preserve the response field without returning raw output.
        "script_output": "completed" if output else "",
        "sheets": sheets,
        "derived_files": [Path(path).name for path in derived_files],
        "kb_files": [Path(path).name for path in kb_files],
    }
