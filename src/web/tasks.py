from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime
from functools import lru_cache
from glob import escape as glob_escape
from pathlib import Path

import yaml

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

from src.clients.openrouter_client import OpenRouterClient  # noqa: E402
from src.config.settings import get_settings  # noqa: E402
from src.models.sdtm_models import GenerationConfig, RateLimitConfig  # noqa: E402
from src.processors.sdtm_processor import SDTMProcessor  # noqa: E402
from src.utils.artifact_names import model_artifact_slug  # noqa: E402
from src.utils.atomic_json import atomic_write_json  # noqa: E402
from src.utils.recommendation_context import build_recommendation_context  # noqa: E402
from src.web.job_manager import job_manager  # noqa: E402
from src.web.security import is_server_default_llm_endpoint  # noqa: E402
from src.web.session_manager import session_manager  # noqa: E402


def _remove_session_snapshot_tree(session_id: str | None, root: Path) -> bool:
    """Remove a completed job's private input tree and forget its tracked files."""
    if not session_id or not root.exists() or not session_manager._is_managed_session_path(session_id, root):
        return False
    tracked_files = [path for path in root.rglob("*") if path.is_file()]
    try:
        shutil.rmtree(root)
    except OSError:
        return False
    for path in tracked_files:
        session_manager.discard_file(session_id, path)
    return True


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


def _prepare_output_base(
    json_path: Path,
    use_timestamp: bool = True,
    output_dir: Path | None = None,
    job_id: str | None = None,
) -> Path:
    """
    Prepare output file base path.

    Args:
        json_path: Path to input JSON file
        use_timestamp: If True, append timestamp; if False, use stable path for resume

    Returns:
        Path object for output base (without extension)
    """
    output_dir = output_dir or Path("data/output")
    output_dir.mkdir(parents=True, exist_ok=True)

    if use_timestamp:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"{json_path.stem}_web_{timestamp}"
    else:
        base_name = f"{json_path.stem}_web_resume"
    if job_id:
        # Per-job suffix prevents same-second starts and concurrent resumes from
        # sharing checkpoints or final artifacts.
        base_name = f"{base_name}_{job_id[:12]}"

    return output_dir / base_name


def _sanitize_model_name(model_name: str) -> str:
    """Compatibility wrapper around the shared model artifact slug."""
    return model_artifact_slug(model_name)


def _find_existing_output_base(
    json_path: Path,
    model_name: str,
    output_dir: Path | None = None,
    expected_context: dict | None = None,
) -> Path | None:
    """
    Find existing output base path with tmp.json file for resume.

    Args:
        json_path: Path to input JSON file
        model_name: Model name to construct the suffix

    Returns:
        Path to existing output base if found, None otherwise
    """
    output_dir = output_dir or Path("data/output")
    if not output_dir.exists():
        return None

    # Get sanitized model name for suffix
    model_suffix = f"_{_sanitize_model_name(model_name)}"

    # Pattern: {json_stem}_web*{model_suffix}.tmp.json
    # Try multiple patterns - with and without timestamp
    escaped_stem = glob_escape(json_path.stem)
    patterns = [
        f"{escaped_stem}_web_*{model_suffix}.tmp.json",  # With timestamp
        f"{escaped_stem}_web{model_suffix}.tmp.json",  # Without timestamp
    ]

    tmp_files: list[Path] = []
    for pattern in patterns:
        tmp_files.extend(output_dir.glob(pattern))

    # A legacy writer or interrupted process may have left malformed JSON.
    # Never copy such a file into a new job; fall through to the next newest
    # complete checkpoint instead.
    tmp_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for checkpoint in tmp_files:
        try:
            _read_recommendation_checkpoint(
                checkpoint,
                expected_model=model_name,
                expected_context=expected_context,
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        base_name = checkpoint.name.removesuffix(f"{model_suffix}.tmp.json")
        return output_dir / base_name

    return None


def _read_recommendation_checkpoint(
    path: Path,
    *,
    expected_model: str | None = None,
    expected_context: dict | None = None,
) -> tuple[object, int]:
    """Read and validate one complete recommendation checkpoint."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        if expected_model is not None:
            raise ValueError("legacy checkpoint has no model identity")
        if not all(isinstance(item, dict) for item in payload):
            raise ValueError("invalid legacy recommendation checkpoint")
        return payload, 0
    if not isinstance(payload, dict) or not isinstance(payload.get("recommendations"), list):
        raise ValueError("invalid recommendation checkpoint")
    if expected_model is not None and payload.get("model_name") != expected_model:
        raise ValueError("recommendation checkpoint model mismatch")
    if expected_context is not None and payload.get("checkpoint_context") != expected_context:
        raise ValueError("recommendation checkpoint context mismatch")
    if not all(isinstance(item, dict) for item in payload["recommendations"]):
        raise ValueError("invalid recommendation checkpoint entries")

    completed = payload.get("completed_pairs", 0)
    if isinstance(completed, bool) or not isinstance(completed, int) or completed < 0:
        raise ValueError("invalid recommendation checkpoint progress")
    return payload, completed


def _snapshot_recommendation_checkpoint(
    source: Path,
    target: Path,
    *,
    expected_model: str | None = None,
    expected_context: dict | None = None,
) -> int:
    """Create a validated, private resume snapshot and return its progress."""

    payload, completed = _read_recommendation_checkpoint(
        source,
        expected_model=expected_model,
        expected_context=expected_context,
    )
    atomic_write_json(target, payload)
    return completed


def _count_existing_progress(
    output_file: str,
    model_name: str,
    expected_context: dict | None = None,
) -> tuple[int, int]:
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

    try:
        _, completed = _read_recommendation_checkpoint(
            Path(temp_file),
            expected_model=model_name,
            expected_context=expected_context,
        )
        return (completed, 0)  # total will be set later from mappings
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
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
    kb_files_snapshot: list[str] | None = None,
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
        json_file=Path(json_file).name,
        model_name=model_name,
    )
    try:
        json_path = _resolve_json_path(json_file)
        mappings = json.loads(json_path.read_text(encoding="utf-8"))
        if kb_files_snapshot is not None:
            session_kb_files = list(kb_files_snapshot)
        elif session_id:
            session_kb_files = session_manager.get_kb_files(session_id)
        else:
            session_kb_files = []
        checkpoint_context = build_recommendation_context(
            json_path,
            kb_files=session_kb_files,
            language=language,
            enable_kb=enable_kb,
        )
        job_manager.update_job(job_id, checkpoint_context=checkpoint_context)

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
        output_dir = session_manager.get_session_als_dir(session_id) if session_id else Path("data/output")

        if resume:
            # Try to find existing output with tmp.json
            existing_base = _find_existing_output_base(
                json_path,
                model_name,
                output_dir,
                expected_context=checkpoint_context,
            )
            output_base = _prepare_output_base(
                json_path,
                use_timestamp=False,
                output_dir=output_dir,
                job_id=job_id,
            )
            if existing_base:
                # Resume from a private snapshot. Two resume workers may read
                # the same source checkpoint, but never write the same file.
                model_suffix = f"_{_sanitize_model_name(model_name)}"
                source_checkpoint = Path(f"{existing_base}{model_suffix}.tmp.json")
                target_checkpoint = Path(f"{output_base}{model_suffix}.tmp.json")
                existing_processed = _snapshot_recommendation_checkpoint(
                    source_checkpoint,
                    target_checkpoint,
                    expected_model=model_name,
                    expected_context=checkpoint_context,
                )
                skipped_from_resume = existing_processed
                job_manager.update_job(
                    job_id,
                    total=total_mappings,
                    processed=existing_processed,
                    message=f"📌 从断点恢复：已完成 {existing_processed}/{total_mappings} 个变量，继续处理中...",
                )
            else:
                job_manager.update_job(job_id, total=total_mappings, message="未找到断点文件，从头开始...")
        else:
            # New job: use timestamp path
            output_base = _prepare_output_base(
                json_path,
                use_timestamp=True,
                output_dir=output_dir,
                job_id=job_id,
            )
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
        if session_id or kb_files_snapshot is not None:
            # Presence of this key (including an explicit empty list) marks a
            # frozen Web snapshot. Downstream save/diff code must not fall back
            # to mutable live session KB state.
            rag_config["extra_kb_files"] = session_kb_files
        if session_kb_files:
            print(f"[Task] 使用 {len(session_kb_files)} 个 session KB 文件")

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
            session_id=session_id,
            checkpoint_context=checkpoint_context,
            # Web inputs can contain clinical metadata. Never let a process-
            # wide environment switch persist raw prompts or model responses.
            log_ai_interactions=False,
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
                state="cancelling",
                message="任务正在安全终止，正在保存可恢复进度...",
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
            message = f"推荐任务失败（{type(exc).__name__}）。请检查输入文件、模型配置与服务端日志后重试。"
            if isinstance(exc, RuntimeError) and "API Token" in str(exc):
                message = "推荐任务失败：自定义模型端点必须提供本次请求专用的 API Token。"
            elif isinstance(exc, RuntimeError) and ("API Key" in str(exc) or "OPENROUTER_API_KEY" in str(exc)):
                message = "推荐任务失败：请配置 API Key 后重试。"
            job_manager.update_job(job_id, state="failed", message=message)


def start_recommendations_job(
    job_id: str,
    json_file: str,
    language: str = "en",
    enable_kb: bool = True,
    model_name_override: str | None = None,
    resume: bool = False,
    session_id: str | None = None,
    kb_files_snapshot: list[str] | None = None,
    base_url_override: str | None = None,
    api_key_override: str | None = None,
) -> bool:
    def run_managed() -> None:
        try:
            _run_recommendations_job(
                job_id=job_id,
                json_file=json_file,
                language=language,
                enable_kb=enable_kb,
                model_name_override=model_name_override,
                resume=resume,
                session_id=session_id,
                kb_files_snapshot=kb_files_snapshot,
                base_url_override=base_url_override,
                api_key_override=api_key_override,
            )
        finally:
            if session_id and kb_files_snapshot is not None:
                snapshot_path = Path(json_file).resolve()
                if len(snapshot_path.parents) >= 2:
                    _remove_session_snapshot_tree(session_id, snapshot_path.parents[1])
            # The target has now stopped touching checkpoints/artifacts. Publish
            # cancelled (or reconcile an unexpected non-terminal exit) only at
            # this safe worker boundary.
            job_manager.finish_worker(
                job_id,
                threading.current_thread(),
                cancellation_message="任务已终止，进度已安全保存。",
            )

    thread = threading.Thread(
        target=run_managed,
        daemon=True,
    )
    return job_manager.start_worker(job_id, thread)


# ==================== Spec Mapper Task ====================

# Maximum number of structured write issues surfaced on the job (keeps the
# API payload bounded; the full detail stays in the workbook + server logs).
_SPEC_ISSUE_CAP = 50

# Structured issue fields cross a user-downloadable trust boundary. Keep the
# schema and machine-readable values deliberately small: mapper bugs or future
# extensions must not accidentally serialize raw source values or exception
# messages into the job payload / issues JSON.
_SPEC_ISSUE_FIELDS = ("code", "stage", "operation", "sheet", "row", "column", "variable", "detail")
_SPEC_ISSUE_STAGES = frozenset(
    {
        "cell_updates",
        "supp_rows",
        "unmatched_rows",
        "conditional_mappings",
        "codelist_records",
        "fixed_variable_rules",
        "formulas_and_links",
        "source_columns",
        "external_coding",
        "content_domains",
        "styles",
    }
)
_SPEC_ISSUE_CODES = frozenset(
    {
        "cell_write_failed",
        "codelist_unchanged",
        "content_update_failed",
        "domain_not_found",
        "external_coding_failed",
        "fixed_rule_failed",
        "formula_write_failed",
        "hyperlink_fix_failed",
        "illegal_characters",
        "no_op",
        "sheet_not_found",
        "source_update_failed",
        "style_update_failed",
        "supp_label_too_long",
        "supp_multi_source",
        "variable_already_present",
        "variable_not_found",
        "write_failed",
    }
)
_SPEC_ISSUE_OPERATIONS = frozenset(
    {
        "add_content_link_to_domain",
        "add_external_coding_variables",
        "add_nonstandard_domain_to_content",
        "add_supp_to_content_sheet",
        "apply_fixed_variable_rules",
        "fix_content_sheet_hyperlinks",
        "highlight_modified_sheet_tabs",
        "insert_supp_row",
        "insert_unmatched_row",
        "set_active_sheet",
        "set_column_wrap_text",
        "update_cell",
        "update_content_f_column",
        "update_domain_sort_key_formula",
        "update_domain_source_column",
        "update_existing_variables",
        "write_codelist",
        "write_conditional_columns",
    }
)
_SPEC_ISSUE_DETAILS = frozenset({"RecoverableWriteError", "sheet_not_found"})
_SPEC_ISSUE_STRUCTURAL_SHEETS = frozenset({"CONTENT", "CODELIST", "RELREC", "XXTEST"})
_SPEC_ISSUE_DOMAIN_SHEET_RE = re.compile(r"[A-Z][A-Z0-9_]{1,15}\Z")
# The only two issue codes that may carry a variable name. Both are emitted for
# items of the packaged external-coding config, so the trusted value set is the
# config itself — a shape/length check is NOT enough at this boundary (a
# subject-like token such as ``SUBJ0001`` fits any identifier regex).
_SPEC_ISSUE_VARIABLE_CODES = frozenset({"variable_already_present", "variable_not_found"})


@lru_cache(maxsize=1)
def _configured_external_coding_variables() -> frozenset[str]:
    """Variable names declared by the packaged ``external_coding_variables``.

    ``variable_not_found`` / ``variable_already_present`` skips can only ever
    name a variable that came out of a packaged Spec Mapper config, so that
    closed set is the trust boundary for echoing the name back through the
    job payload and the downloadable issues JSON. Unreadable or malformed
    configs fail closed to the empty set (the field is dropped, never guessed).
    """
    names: set[str] = set()
    config_dir = Path(__file__).resolve().parents[1] / "spec_mapper" / "config"
    for config_path in sorted(config_dir.glob("*.yaml")):
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        external = payload.get("external_coding_variables")
        if not isinstance(external, dict):
            continue
        for domain_configs in external.values():
            configs = domain_configs if isinstance(domain_configs, list) else [domain_configs]
            for domain_config in configs:
                if not isinstance(domain_config, dict):
                    continue
                for variable in domain_config.get("variables") or []:
                    name = variable.get("name") if isinstance(variable, dict) else None
                    if isinstance(name, str) and name.strip():
                        names.add(name.strip().upper())
    return frozenset(names)


# Absolute (or 2+ segment) filesystem paths, redacted from the downloadable log.
_ABS_PATH_RE = re.compile(r"(?:[A-Za-z]:\\[^\s'\"]*|(?:/[^\s'\"/]+){2,})")


class _SpecJobLogFormatter(logging.Formatter):
    """Formatter for the user-downloadable per-job log.

    The handler is attached only to a dedicated, non-propagating job logger.
    Mapper/openpyxl/root records are deliberately excluded because debug and
    warning messages can contain raw metadata values. Two additional safety
    guarantees apply to the curated messages:

    * absolute / multi-segment filesystem paths in the message are redacted; and
    * exception tracebacks and stack info are NEVER appended. The stdlib
      ``Formatter.format`` would otherwise tack ``record.exc_info`` /
      ``record.stack_info`` onto the line *after* any filter runs, so a stray
      ``logger.exception(...)`` or ``exc_info=True`` on this worker thread would
      leak server paths and internal stacks into the downloadable log.
    """

    def format(self, record: logging.LogRecord) -> str:
        # getMessage() is pure (returns msg % args) and does not mutate record.
        message = _ABS_PATH_RE.sub("<path>", record.getMessage())
        asctime = self.formatTime(record, self.datefmt)
        # Deliberately ignore exc_info / exc_text / stack_info: no traceback.
        return f"{asctime} - {record.name} - {record.levelname} - {message}"


def _all_spec_issues(stats: dict) -> list[dict]:
    """All safe, structured write issues (errors first, then warnings).

    Each item exposes only code/stage/operation/sheet/row/column/detail — never
    a path, clinical value, or traceback — so the full list is safe to persist
    and download.
    """

    def allowlisted(value: object, allowed: frozenset[str], *, fallback: str | None) -> str | None:
        if isinstance(value, str) and value in allowed:
            return value
        return fallback

    def positive_int(value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None

    def safe_sheet(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        if value in _SPEC_ISSUE_STRUCTURAL_SHEETS or _SPEC_ISSUE_DOMAIN_SHEET_RE.fullmatch(value):
            return value
        return None

    def safe_variable(issue_code: object, value: object) -> str | None:
        """Echo a variable name only when it is a *configured* one.

        This is an untrusted boundary, so membership in the packaged
        external-coding config — not an identifier shape — decides. A value
        like ``SUBJ0001`` passes any regex but can never appear in the config,
        and only the two per-item skip codes may carry the field at all.
        The type checks come first: an unhashable code (a mapper bug emitting
        a list/dict) must degrade like every other field here, never raise.
        """
        if not isinstance(issue_code, str) or not isinstance(value, str):
            return None
        if issue_code not in _SPEC_ISSUE_VARIABLE_CODES:
            return None
        return value if value in _configured_external_coding_variables() else None

    def serialize(issue: object) -> dict | None:
        if not isinstance(issue, dict):
            return None
        serialized = {
            "code": allowlisted(issue.get("code"), _SPEC_ISSUE_CODES, fallback="unknown"),
            "stage": allowlisted(issue.get("stage"), _SPEC_ISSUE_STAGES, fallback="unknown"),
            "operation": allowlisted(issue.get("operation"), _SPEC_ISSUE_OPERATIONS, fallback="unknown"),
            "sheet": safe_sheet(issue.get("sheet")),
            "row": positive_int(issue.get("row")),
            "column": positive_int(issue.get("column")),
            "variable": safe_variable(issue.get("code"), issue.get("variable")),
            # ``detail`` is never required for locating an issue. Drop any
            # free-form or otherwise invalid value instead of risking exposure.
            "detail": allowlisted(issue.get("detail"), _SPEC_ISSUE_DETAILS, fallback=None),
        }
        # A direct comprehension makes the allowlist visible at the final
        # serialization point and guarantees future mapper-only keys are lost.
        return {field: serialized[field] for field in _SPEC_ISSUE_FIELDS}

    write_result = stats.get("write_result") or {}
    errors = write_result.get("errors") or []
    warnings = write_result.get("warnings") or []
    return [safe for issue in [*errors, *warnings] if (safe := serialize(issue)) is not None]


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
    log_path: Path | None = None

    try:
        # 构建文件路径
        als_path = (
            session_manager.get_session_als_dir(session_id) / als_file if session_id else Path("data/output") / als_file
        )
        template_path = Path("data/knowledge_base/template_spec") / template_file
        if session_id:
            output_dir = session_manager.get_session_spec_job_dir(session_id, job_id)
        else:
            output_dir = Path("data/spec_output/jobs") / session_manager.session_dir_key(job_id)
            output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{output_name}.xlsx"

        # Workbook, log, and issue manifest share one job-isolated directory,
        # so two sessions can safely choose the same output_name.
        log_path = output_dir / f"{output_name}.log"

        # The downloadable log is a curated audit summary. Never attach its
        # handler to root/spec-mapper loggers, whose messages may contain raw
        # cell values, labels, transformations, or source metadata.
        log_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        log_handler.setLevel(logging.INFO)
        log_handler.setFormatter(_SpecJobLogFormatter(datefmt="%Y-%m-%d %H:%M:%S"))
        job_logger = logging.getLogger(f"sirius.spec_job.{job_id}")
        job_logger.setLevel(logging.INFO)
        job_logger.propagate = False
        job_logger.addHandler(log_handler)
        log_closed = False

        def close_job_log() -> None:
            nonlocal log_closed
            if log_closed:
                return
            try:
                job_logger.info("Spec Mapper job finished (job_id=%s)", job_id)
                log_handler.flush()
            finally:
                job_logger.removeHandler(log_handler)
                log_handler.close()
                logging.Logger.manager.loggerDict.pop(job_logger.name, None)
                log_closed = True

        try:
            job_logger.info("Spec Mapper job started (job_id=%s)", job_id)

            # Validate after the safe audit log is ready so fatal input errors
            # still leave a downloadable, non-sensitive job record.
            if not als_path.exists():
                raise FileNotFoundError(f"ALS2SDTM 文件不存在: {als_file}")
            if not template_path.exists():
                raise FileNotFoundError(f"模板文件不存在: {template_file}")
            job_logger.info("Validated session-owned input and approved template")

            from src.spec_mapper import SpecMapper

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
            job_logger.info("Workbook processing completed")

            if job_manager.is_cancelled(job_id):
                job_logger.info("Job cancelled before terminal publication")
                close_job_log()
                if session_id and log_path.exists():
                    session_manager.add_file(session_id, str(log_path))
                job_manager.update_job(
                    job_id,
                    state="cancelling",
                    message="Spec Mapper 正在安全终止",
                    output_log=str(log_path),
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

            # Full, safe structured issue list. The API payload is normally
            # capped for size while the COMPLETE list is persisted to a
            # downloadable JSON. If that persistence fails, fall back to
            # exposing the FULL list in the job payload instead — items beyond
            # the cap must never become invisible (A5), and the UI only renders
            # the download link when the file actually exists (output_issues).
            all_issues = _all_spec_issues(stats)
            issues_total = len(all_issues)
            payload_issues = all_issues[:_SPEC_ISSUE_CAP]
            issues_json_path: Path | None = None
            if all_issues:
                candidate = output_dir / f"{output_name}.issues.json"
                try:
                    candidate.write_text(json.dumps(all_issues, ensure_ascii=False, indent=2), encoding="utf-8")
                    issues_json_path = candidate
                except OSError as exc:
                    issues_json_path = None
                    payload_issues = all_issues
                    job_logger.warning(
                        "Could not persist the full issue list (%s); exposing all %d issues in the job payload",
                        type(exc).__name__,
                        issues_total,
                    )
                if session_id and issues_json_path and issues_json_path.exists():
                    session_manager.add_file(session_id, str(issues_json_path))

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
            if written < attempted or skipped > 0 or warn_count > 0 or err_count > 0:
                state = "completed_with_errors"
                message = (
                    f"⚠️ Spec 已生成但需人工复核：成功写入 {written}/{attempted} 项，"
                    f"跳过 {skipped}，警告 {warn_count}，错误 {err_count}"
                )
            else:
                state = "completed"
                message = f"✓ 完成！处理 {total_als_records} 条记录，成功写入 {written}/{attempted} 项操作"

            job_logger.info(
                "Write summary: attempted=%d written=%d skipped=%d warnings=%d errors=%d",
                attempted,
                written,
                skipped,
                warn_count,
                err_count,
            )
            # Flush and close the downloadable log before publishing a terminal
            # state; a polling client can download immediately after this update.
            close_job_log()
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
                spec_issues=payload_issues,
                spec_issues_total=issues_total,
                output_issues=str(issues_json_path) if issues_json_path else None,
            )
        except Exception as exc:
            job_logger.error("Spec Mapper job failed (%s)", type(exc).__name__)
            raise
        finally:
            close_job_log()

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
            safe_log_path = str(log_path) if log_path and log_path.exists() else None
            if session_id and safe_log_path:
                session_manager.add_file(session_id, safe_log_path)
            job_manager.update_job(
                job_id,
                state="failed",
                message=safe_message,
                output_log=safe_log_path,
            )


def start_spec_mapper_job(
    job_id: str,
    als_file: str,
    template_file: str,
    output_name: str,
    als_sheet: str = "Sheet1",
    highlight: bool = True,
    create_test_sheets: bool = True,
    session_id: str | None = None,
) -> bool:
    """启动 spec_mapper 后台任务."""

    def run_managed() -> None:
        try:
            _run_spec_mapper_job(
                job_id,
                als_file,
                template_file,
                output_name,
                als_sheet,
                highlight,
                create_test_sheets,
                session_id,
            )
        finally:
            if session_id:
                snapshot_parents = {Path(als_file).resolve().parent, Path(template_file).resolve().parent}
                for snapshot_parent in snapshot_parents:
                    _remove_session_snapshot_tree(session_id, snapshot_parent)
                    _remove_session_snapshot_tree(session_id, snapshot_parent.parent / ".rollback")
            # Keep cancellation non-terminal while the mapper is still saving
            # or closing its log. Session cleanup may delete artifacts only
            # after this finalizer removes the live worker registration.
            job_manager.finish_worker(
                job_id,
                threading.current_thread(),
                cancellation_message="Spec Mapper 任务已被用户终止",
            )

    thread = threading.Thread(
        target=run_managed,
        daemon=True,
    )
    return job_manager.start_worker(job_id, thread)
