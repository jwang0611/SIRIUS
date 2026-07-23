"""File upload routes."""

import logging
from pathlib import Path
from typing import Any, cast

import pandas as pd
from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile

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


@router.post("/upload/{category}")
@limiter.limit(RATE_LIMIT_UPLOAD)
async def upload_file(
    request: Request,
    category: str,
    file: UploadFile = File(...),
    x_session_id: str | None = Header(None),
):
    category = category.lower()
    if category not in UPLOAD_CONFIG:
        raise HTTPException(status_code=400, detail="未知的上传类别")

    config: dict[str, Any] = cast(dict[str, Any], UPLOAD_CONFIG[category])
    allowed_extensions = cast(set[str], config.get("allowed_extensions", ALLOWED_EXTENSIONS["excel"]))

    validate_upload_file(file, allowed_extensions)

    safe_filename = sanitize_filename(file.filename or "")

    # 对于 example_raw 和 als_example_raw 类别，使用 session 专属目录
    if category in ("example_raw", "als_example_raw") and x_session_id:
        session_manager.get_or_create(x_session_id)
        target_dir = session_manager.get_session_kb_dir(x_session_id)
        destination = target_dir / safe_filename
        save_upload(file, destination)
        session_manager.add_kb_file(x_session_id, str(destination))
    elif category == "raw_acrf" and x_session_id:
        # Isolate the uploaded PDF per session so two sessions uploading the
        # same filename cannot overwrite or clean up each other's input.
        session_manager.get_or_create(x_session_id)
        target_dir = Path("data/raw/sessions") / session_manager.session_dir_key(x_session_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / safe_filename
        save_upload(file, destination)
        session_manager.add_file(x_session_id, str(destination))
    else:
        target_dir = cast(Path, config["target"])
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / safe_filename
        save_upload(file, destination)

        is_knowledge_base = "knowledge_base" in str(destination)
        if x_session_id and not is_knowledge_base:
            session_manager.get_or_create(x_session_id)
            session_manager.add_file(x_session_id, str(destination))

    output = ""
    sheets: list[str] | None = None
    derived_files: list[str] = []
    kb_files: list[str] = []

    try:
        cmd = config.get("command")

        if category == "example" and x_session_id:
            session_manager.get_or_create(x_session_id)
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
                kb_files.append(str(parquet_file))
                session_manager.add_kb_file(x_session_id, str(parquet_file))
        elif category == "raw_acrf":
            # Session-scoped output dirs when a session id is present, so both
            # the processed JSON/xlsx and the portable als2sdtm workbook stay
            # isolated and are tracked/cleaned per session (below).
            if x_session_id:
                session_key = session_manager.session_dir_key(x_session_id)
                processed_dir = Path("data/processed/sessions") / session_key
                als_output_dir = Path("data/output/sessions") / session_key
            else:
                processed_dir = Path("data/processed")
                als_output_dir = Path("data/output")
            processed_dir.mkdir(parents=True, exist_ok=True)
            als_output_dir.mkdir(parents=True, exist_ok=True)
            output = run_command(
                [
                    PYTHON_BIN,
                    "scripts/extract_acrf_pdf.py",
                    "--input",
                    str(destination),
                    "--output-dir",
                    str(processed_dir),
                    "--als2sdtm-dir",
                    str(als_output_dir),
                ]
            )
            for ext in (".json", ".xlsx"):
                derived = processed_dir / f"{destination.stem}{ext}"
                if derived.exists():
                    derived_files.append(str(derived))
            als_file = als_output_dir / f"{destination.stem}_ALS2SDTM.xlsx"
            if als_file.exists():
                derived_files.append(str(als_file))
        elif callable(cmd):
            output = run_command(cmd(destination))

            if category in ("raw", "raw_taimei"):
                for ext in (".json", ".xlsx"):
                    derived = Path("data/processed") / f"{destination.stem}{ext}"
                    if derived.exists():
                        derived_files.append(str(derived))

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
    except InvalidWorkbookError as exc:
        logger.info("Upload rejected because workbook structure is invalid (category=%s)", category)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Upload processing failed for category=%s", category)
        raise HTTPException(status_code=500, detail="文件处理失败，请查看服务端日志") from exc

    if x_session_id and derived_files:
        for derived_path in derived_files:
            session_manager.add_file(x_session_id, derived_path)

    return {
        "message": "上传成功",
        "category": category,
        "stored_to": str(destination),
        "script_output": output,
        "sheets": sheets,
        "derived_files": derived_files,
        "kb_files": kb_files,
    }
