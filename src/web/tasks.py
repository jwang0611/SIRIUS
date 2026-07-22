from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

from src.clients.openrouter_client import OpenRouterClient  # noqa: E402
from src.config.settings import get_settings  # noqa: E402
from src.models.sdtm_models import GenerationConfig, RateLimitConfig  # noqa: E402
from src.processors.sdtm_processor import SDTMProcessor  # noqa: E402
from src.web.job_manager import job_manager  # noqa: E402
from src.web.security import is_server_default_llm_endpoint  # noqa: E402
from src.web.session_manager import session_manager  # noqa: E402


def _build_generation_config() -> GenerationConfig:
    return GenerationConfig(
        max_output_tokens=int(os.getenv("WEB_MAX_OUTPUT_TOKENS", "2000")),
        temperature=float(os.getenv("WEB_TEMPERATURE", "0.15")),
        top_p=float(os.getenv("WEB_TOP_P", "0.95")),
        top_k=int(os.getenv("WEB_TOP_K", "40")),
    )


def _build_rate_limit_config() -> RateLimitConfig:
    return RateLimitConfig(
        requests_per_minute=int(os.getenv("WEB_RATE_LIMIT", "60")),
    )


def _resolve_json_path(json_file: str) -> Path:
    candidate = Path(json_file)
    if not candidate.is_absolute():
        candidate = Path("data/processed") / candidate
    if not candidate.exists():
        raise FileNotFoundError(f"JSON mapping file not found: {candidate}")
    return candidate


def _prepare_output_base(json_path: Path, use_timestamp: bool = True) -> Path:
    """
    Prepare output file base path.

    Args:
        json_path: Path to input JSON file
        use_timestamp: If True, append timestamp; if False, use stable path for resume

    Returns:
        Path object for output base (without extension)
    """
    output_dir = Path("data/output")
    output_dir.mkdir(parents=True, exist_ok=True)

    if use_timestamp:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"{json_path.stem}_web_{timestamp}"
    else:
        # Use stable path without timestamp for resume functionality
        base_name = f"{json_path.stem}_web"

    return output_dir / base_name


def _sanitize_model_name(model_name: str) -> str:
    """
    Sanitize model name for use in filenames.

    Args:
        model_name: Model name like "google/gemini-2.5-flash"

    Returns:
        Sanitized name like "google_gemini-2.5-flash"
    """
    # Replace forward slashes with underscores
    sanitized = model_name.replace("/", "_")
    # Remove other special characters
    for char in [":", "*", "?", '"', "<", ">", "|"]:
        sanitized = sanitized.replace(char, "_")
    return sanitized


def _find_existing_output_base(json_path: Path, model_name: str) -> Path | None:
    """
    Find existing output base path with tmp.json file for resume.

    Args:
        json_path: Path to input JSON file
        model_name: Model name to construct the suffix

    Returns:
        Path to existing output base if found, None otherwise
    """
    output_dir = Path("data/output")
    if not output_dir.exists():
        return None

    # Get sanitized model name for suffix
    model_suffix = f"_{_sanitize_model_name(model_name)}"

    # Pattern: {json_stem}_web*{model_suffix}.tmp.json
    # Try multiple patterns - with and without timestamp
    patterns = [
        f"{json_path.stem}_web_*{model_suffix}.tmp.json",  # With timestamp
        f"{json_path.stem}_web{model_suffix}.tmp.json",  # Without timestamp
    ]

    tmp_files: list[Path] = []
    for pattern in patterns:
        tmp_files.extend(output_dir.glob(pattern))

    if tmp_files:
        # Sort by modification time, newest first
        tmp_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        # Return the base path (remove .tmp.json and model_suffix)
        newest = tmp_files[0]
        base_name = newest.name.replace(".tmp.json", "").replace(model_suffix, "")
        return output_dir / base_name

    return None


def _count_existing_progress(output_file: str, model_name: str) -> tuple[int, int]:
    """
    Count existing progress from tmp.json file.

    Args:
        output_file: Base output file path
        model_name: Model name for suffix

    Returns:
        Tuple of (processed_count, total_count)
    """
    model_suffix = f"_{_sanitize_model_name(model_name)}"
    temp_file = f"{output_file}{model_suffix}.tmp.json"

    if os.path.exists(temp_file):
        try:
            with open(temp_file, encoding="utf-8") as f:
                temp_data = json.load(f)

            if isinstance(temp_data, dict) and "completed_pairs" in temp_data:
                completed = temp_data.get("completed_pairs", 0)
                return (completed, 0)  # total will be set later from mappings
        except Exception:
            pass

    return (0, 0)


def _summarize_recommendation_quality(recommendations: list[dict]) -> tuple[int, int]:
    """Count failed variables and unique error-level consistency findings."""
    failed_variables: set[tuple[str, str]] = set()
    consistency_errors: set[str] = set()

    for table_rec in recommendations:
        table_name = str(table_rec.get("table_name", ""))
        for rec in table_rec.get("domain_recommendations", []) or []:
            source = str(rec.get("source", "")).upper()
            sdtm_variable = str(rec.get("sdtm_variable", ""))
            if source == "FALLBACK" or sdtm_variable.upper().endswith("_PENDING"):
                variable_name = str(rec.get("variable_name") or sdtm_variable or "unknown")
                failed_variables.add((table_name, variable_name))

        for issue in table_rec.get("consistency_issues", []) or []:
            if str(issue.get("severity", "")).lower() == "error":
                consistency_errors.add(json.dumps(issue, ensure_ascii=False, sort_keys=True, default=str))

    return len(failed_variables), len(consistency_errors)


def _run_recommendations_job(
    job_id: str,
    json_file: str,
    language: str = "en",
    enable_kb: bool = True,
    model_name_override: str | None = None,
    resume: bool = False,
    session_id: str | None = None,
    base_url_override: str | None = None,
    api_key_override: str | None = None,
) -> None:
    # Determine model name early for resume path lookup
    load_dotenv()
    model_name = model_name_override or get_settings().ai.default_model

    # Initial job status
    initial_processed = 0
    initial_message = "准备中..."

    if resume:
        initial_message = "正在查找断点进度..."

    job_manager.update_job(
        job_id,
        state="running",
        message=initial_message,
        processed=initial_processed,
        total=0,
        current_table=None,
        current_variable=None,
        output_excel=None,
        output_json=None,
        json_file=json_file,
    )
    try:
        json_path = _resolve_json_path(json_file)
        mappings = json.loads(json_path.read_text(encoding="utf-8"))

        # 过滤掉空的 metadata_table 条目，与 process_mappings 保持一致
        valid_mappings = [m for m in mappings if m.get("metadata_table")]
        total_mappings = len(valid_mappings)

        if len(mappings) != total_mappings:
            print(
                f"[Task] 注意: JSON 中有 {len(mappings)} 条记录，其中 {len(mappings) - total_mappings} 条缺少 metadata_table，实际处理 {total_mappings} 条"
            )

        # Determine output base path
        # Track skipped count for resume display
        skipped_from_resume = 0

        if resume:
            # Try to find existing output with tmp.json
            existing_base = _find_existing_output_base(json_path, model_name)
            if existing_base:
                output_base = existing_base
                # Count existing progress
                existing_processed, _ = _count_existing_progress(str(output_base), model_name)
                skipped_from_resume = existing_processed
                job_manager.update_job(
                    job_id,
                    total=total_mappings,
                    processed=existing_processed,
                    message=f"📌 从断点恢复：已完成 {existing_processed}/{total_mappings} 个变量，继续处理中...",
                )
            else:
                # No existing tmp.json found, use stable path without timestamp
                output_base = _prepare_output_base(json_path, use_timestamp=False)
                job_manager.update_job(job_id, total=total_mappings, message="未找到断点文件，从头开始...")
        else:
            # New job: use timestamp path
            output_base = _prepare_output_base(json_path, use_timestamp=True)
            job_manager.update_job(job_id, total=total_mappings, message="正在初始化处理...")

        # 密钥与 endpoint 绑定（防服务器回退密钥外泄）：
        # env 回退密钥只发往服务器自身配置的默认 endpoint；任何非默认 endpoint 必须自带 token。
        # Endpoint overrides remain request/runtime-scoped; unlike the model
        # default, operators may supply this after process import via dotenv.
        default_base = os.getenv("OPENROUTER_BASE_URL") or get_settings().ai.openrouter_base_url
        api_key: str | None
        if base_url_override and not is_server_default_llm_endpoint(base_url_override):
            if not api_key_override:
                raise RuntimeError("使用非默认 Base URL 时必须提供 API Token（不会使用服务器密钥）")
            api_key = api_key_override
            base_url = base_url_override
        else:
            api_key = api_key_override or os.getenv("OPENROUTER_API_KEY")
            if not api_key:
                raise RuntimeError("未配置 API Key：请在左下角「模型设置」中填写，或在服务器配置 OPENROUTER_API_KEY")
            base_url = base_url_override or default_base

        client = OpenRouterClient(api_key=api_key, base_url=base_url)
        client.set_model(model_name)
        if not client.initialize():
            raise RuntimeError("无法初始化 OpenRouter 客户端")

        # 获取 session 的 KB 文件（用户上传的 example 文件转换后的 parquet）
        rag_config = {}
        if session_id:
            from src.web.session_manager import session_manager

            session_kb_files = session_manager.get_kb_files(session_id)
            if session_kb_files:
                rag_config["extra_kb_files"] = session_kb_files
                print(f"[Task] 使用 session KB 文件: {session_kb_files}")

        # Parallel processing config from env
        enable_parallel = os.getenv("SDTM_ENABLE_PARALLEL", "true").lower() in ("1", "true", "yes", "on")
        try:
            max_workers = int(os.getenv("SDTM_MAX_WORKERS", "5"))
        except (TypeError, ValueError):
            max_workers = 5

        processor = SDTMProcessor(
            client=client,
            model_name=model_name,
            generation_config=_build_generation_config(),
            rate_limit_config=_build_rate_limit_config(),
            debug=False,
            language=language,
            enable_knowledge_base=enable_kb,
            rag_config=rag_config,
            enable_parallel=enable_parallel,
            max_workers=max_workers,
        )

        def _progress_callback(processed: int, total: int, table: str | None, variable: str | None) -> bool:
            """Progress callback that returns False to stop processing when cancelled.

            注意: 此回调在每个变量处理完成后被调用，所以 processed 是已完成的数量。
            """
            # Check if job was cancelled
            if job_manager.is_cancelled(job_id):
                return False  # Signal to stop processing

            # Build detailed progress message
            if table and variable:
                # 回调是在处理完成后调用的，所以 processed 就是当前已完成的数量（包括刚处理的这个）
                # 显示为 "已处理第 X/Y 个: table.variable"
                if skipped_from_resume > 0:
                    # Show resume context
                    message = f"📌 已跳过 {skipped_from_resume} 个变量 | 已处理 {processed}/{total}: {table}.{variable}"
                else:
                    message = f"已处理 {processed}/{total}: {table}.{variable}"
            else:
                # Initial or final callback without specific variable
                if skipped_from_resume > 0 and processed == skipped_from_resume:
                    message = f"📌 已跳过 {skipped_from_resume} 个已处理的变量，开始处理剩余变量..."
                elif processed == total:
                    message = f"✅ 已完成全部 {total} 个变量的处理"
                elif processed == 0:
                    message = f"开始处理，共 {total} 个变量..."
                else:
                    message = f"处理进度: {processed}/{total}"

            job_manager.update_job(
                job_id,
                processed=processed,
                total=total,
                current_table=table,
                current_variable=variable,
                message=message,
            )
            return True  # Continue processing

        start_time = time.time()
        recommendations = processor.process_mappings(
            mappings,
            dry_run=False,
            resume=resume,  # Enable resume from tmp.json
            output_file=str(output_base),
            progress_callback=_progress_callback,
            input_file=str(json_path),  # 用于 eCRF sheet merge
        )

        # Check if cancelled
        if job_manager.is_cancelled(job_id):
            job_manager.update_job(
                job_id,
                state="cancelled",
                message="任务已终止，进度已保存。可以恢复继续。",
            )
            return

        elapsed = time.time() - start_time
        processor.save_recommendations(
            recommendations,
            str(output_base),
            original_mappings=mappings,
            input_file=str(json_path),  # 用于 eCRF sheet merge
        )
        model_suffix = f"_{client.get_sanitized_model_name(model_name)}"
        output_with_model = str(output_base) + model_suffix
        excel_path = Path(output_with_model + ".xlsx")
        json_output = Path(output_with_model + ".json")

        # Measure the serialized artifact when available because
        # save_recommendations may add coverage fallbacks for missing variables.
        quality_recommendations = recommendations
        if json_output.exists():
            try:
                serialized = json.loads(json_output.read_text(encoding="utf-8"))
                if isinstance(serialized, list):
                    quality_recommendations = serialized
            except (OSError, json.JSONDecodeError):
                pass

        # 记录输出文件归属于当前 session
        if session_id:
            if excel_path.exists():
                session_manager.add_file(session_id, str(excel_path))
            if json_output.exists():
                session_manager.add_file(session_id, str(json_output))
            # 也跟踪临时文件
            tmp_file = Path(f"{output_with_model}.tmp.json")
            if tmp_file.exists():
                session_manager.add_file(session_id, str(tmp_file))

        final_status = job_manager.get_job(job_id)
        final_total = final_status.total if final_status and final_status.total else len(mappings)
        failed_variables, consistency_errors = _summarize_recommendation_quality(quality_recommendations)
        completed_state = "completed_with_errors" if failed_variables or consistency_errors else "completed"
        if completed_state == "completed_with_errors":
            result_message = (
                f"⚠️ 处理完成但需要人工复核：{failed_variables} 个变量使用失败占位，"
                f"{consistency_errors} 个一致性错误 | Duration: {elapsed:.1f}s"
            )
        else:
            result_message = f"✅ 已完成全部 {final_total} 个变量的处理 | Duration: {elapsed:.1f}s"
        job_manager.update_job(
            job_id,
            state=completed_state,
            processed=final_total,
            total=final_total,
            output_excel=str(excel_path),
            output_json=str(json_output),
            current_table=None,
            current_variable=None,
            failed_variables=failed_variables,
            consistency_errors=consistency_errors,
            message=result_message,
        )
    except Exception as exc:
        # Don't overwrite cancelled state
        current = job_manager.get_job(job_id)
        if current and current.state != "cancelled":
            job_manager.update_job(job_id, state="failed", message=str(exc))


def start_recommendations_job(
    job_id: str,
    json_file: str,
    language: str = "en",
    enable_kb: bool = True,
    model_name_override: str | None = None,
    resume: bool = False,
    session_id: str | None = None,
    base_url_override: str | None = None,
    api_key_override: str | None = None,
) -> None:
    thread = threading.Thread(
        target=_run_recommendations_job,
        kwargs={
            "job_id": job_id,
            "json_file": json_file,
            "language": language,
            "enable_kb": enable_kb,
            "model_name_override": model_name_override,
            "resume": resume,
            "session_id": session_id,
            "base_url_override": base_url_override,
            "api_key_override": api_key_override,
        },
        daemon=True,
    )
    thread.start()


# ==================== Spec Mapper Task ====================


def _run_spec_mapper_job(
    job_id: str,
    als_file: str,
    template_file: str,
    output_name: str,
    als_sheet: str = "Sheet1",
    highlight: bool = True,
    create_test_sheets: bool = True,
    session_id: str | None = None,
) -> None:
    """运行 spec_mapper 任务的后台线程."""
    job_manager.update_job(job_id, state="running", message="正在初始化 Spec Mapper...", processed=0, total=5)

    try:
        # 构建文件路径
        als_path = Path("data/output") / als_file
        template_path = Path("data/knowledge_base/template_spec") / template_file
        output_dir = Path("data/spec_output")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{output_name}.xlsx"

        # 创建日志目录和日志文件路径
        log_dir = output_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{output_name}.log"

        # 验证文件存在
        if not als_path.exists():
            raise FileNotFoundError(f"ALS2SDTM 文件不存在: {als_file}")
        if not template_path.exists():
            raise FileNotFoundError(f"模板文件不存在: {template_file}")

        # 导入必要的模块
        import logging

        from src.spec_mapper import SpecMapper

        # 设置日志文件处理器
        log_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        log_handler.setLevel(logging.DEBUG)
        log_formatter = logging.Formatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        log_handler.setFormatter(log_formatter)

        # 获取 root logger 并添加文件处理器
        root_logger = logging.getLogger()
        original_level = root_logger.level
        root_logger.setLevel(logging.DEBUG)  # 捕获所有级别
        root_logger.addHandler(log_handler)

        try:
            # Log only file *names*, never absolute/relative server paths, since
            # this log is user-downloadable via the download-log endpoint.
            logging.info(f"[Spec Mapper Job {job_id}] Started")
            logging.info(f"ALS file: {als_path.name}")
            logging.info(f"Template file: {template_path.name}")
            logging.info(f"Output file: {output_path.name}")

            # 初始化 SpecMapper
            mapper = SpecMapper(als_file=als_path, template_file=template_path, als_sheet=als_sheet, log_level="INFO")

            def report_progress(message: str, processed: int, total: int) -> None:
                job_manager.update_job(job_id, message=message, processed=processed, total=total)

            stats = mapper.process(
                output_file=output_path,
                highlight=highlight,
                dry_run=False,
                create_test_sheets=create_test_sheets,
                progress_callback=report_progress,
            )

            if job_manager.is_cancelled(job_id):
                job_manager.update_job(
                    job_id,
                    state="cancelled",
                    message="Spec Mapper 任务已被用户终止",
                )
                return

            total_als_records = int(stats.get("als_records", 0))
            # Actual (not planned) workbook write outcome.
            actual = stats.get("actual") or {}
            attempted = int(actual.get("attempted", 0))
            written = int(actual.get("written", 0))
            skipped = int(actual.get("skipped", 0))
            warn_count = int(actual.get("warnings", 0))
            err_count = int(actual.get("errors", 0))

            # 记录输出文件归属于当前 session
            if session_id and output_path.exists():
                session_manager.add_file(session_id, str(output_path))

            # 记录日志文件归属于当前 session
            if session_id and log_path.exists():
                session_manager.add_file(session_id, str(log_path))

            # Defensive: save() reported success but the artifact is missing.
            # Treat as a fatal failure rather than reporting (partial) success.
            if not output_path.exists():
                raise RuntimeError("generated workbook is unavailable")

            # Decide the terminal state from the *actual* write result:
            #   * some planned writes failed or were skipped -> completed_with_errors
            #     (the workbook is still saved and downloadable for manual review)
            #   * every planned write succeeded              -> completed
            if written < attempted or err_count > 0:
                state = "completed_with_errors"
                message = (
                    f"⚠️ Spec 已生成但需人工复核：成功写入 {written}/{attempted} 项，"
                    f"跳过 {skipped}，警告 {warn_count}，错误 {err_count}"
                )
            else:
                state = "completed"
                message = f"✓ 完成！处理 {total_als_records} 条记录，成功写入 {written}/{attempted} 项操作"

            job_manager.update_job(
                job_id,
                state=state,
                message=message,
                processed=5,
                total=5,
                output_excel=str(output_path),
                output_log=str(log_path),
                spec_attempted=attempted,
                spec_written=written,
                spec_skipped=skipped,
                spec_warnings=warn_count,
                spec_errors=err_count,
            )
        finally:
            # 移除日志处理器并恢复原始日志级别
            root_logger.removeHandler(log_handler)
            log_handler.close()
            root_logger.setLevel(original_level)

            logging.info(f"[Spec Mapper Job {job_id}] Finished")

    except Exception as exc:
        # Never surface raw exception text (may embed paths/tracebacks) in the
        # user-facing job message; report a safe, typed summary instead.
        current = job_manager.get_job(job_id)
        if current and current.state != "cancelled":
            if isinstance(exc, FileNotFoundError):
                safe_message = "Spec 生成失败：ALS2SDTM 文件或模板文件不存在。"
            else:
                safe_message = (
                    f"Spec 生成失败：无法生成工作簿（{type(exc).__name__}）。"
                    "请检查 ALS2SDTM 文件、模板与 sheet 名称后重试。"
                )
            job_manager.update_job(job_id, state="failed", message=safe_message)


def start_spec_mapper_job(
    job_id: str,
    als_file: str,
    template_file: str,
    output_name: str,
    als_sheet: str = "Sheet1",
    highlight: bool = True,
    create_test_sheets: bool = True,
    session_id: str | None = None,
) -> None:
    """启动 spec_mapper 后台任务."""
    thread = threading.Thread(
        target=_run_spec_mapper_job,
        args=(job_id, als_file, template_file, output_name, als_sheet, highlight, create_test_sheets, session_id),
        daemon=True,
    )
    thread.start()
