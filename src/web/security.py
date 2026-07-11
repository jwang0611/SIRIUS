"""Shared security utilities, constants, and rate limiter for the web layer."""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, UploadFile
from slowapi import Limiter
from slowapi.util import get_remote_address

# ==================== 通用常量 ====================

PYTHON_BIN = os.getenv("PYTHON_BIN", sys.executable or "python")

# ==================== API 速率限制配置 ====================


def _get_client_identifier(request: Request) -> str:
    """
    获取客户端标识符，用于速率限制。
    优先使用 Session ID，其次使用 IP 地址。
    """
    session_id = request.headers.get("X-Session-ID")
    if session_id:
        return f"session:{session_id}"
    return get_remote_address(request)


limiter = Limiter(key_func=_get_client_identifier)

RATE_LIMIT_UPLOAD = os.getenv("RATE_LIMIT_UPLOAD", "30/minute")
RATE_LIMIT_AI_JOB = os.getenv("RATE_LIMIT_AI_JOB", "10/minute")
RATE_LIMIT_GENERAL = os.getenv("RATE_LIMIT_GENERAL", "60/minute")
RATE_LIMIT_READ = os.getenv("RATE_LIMIT_READ", "120/minute")

# ==================== 文件上传安全配置 ====================

MAX_FILE_SIZE_MB = 50
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024

ALLOWED_EXTENSIONS = {
    "excel": {".xlsx", ".xls"},
    "json": {".json"},
}

ALLOWED_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/json",
    "application/octet-stream",
}

UPLOAD_CONFIG = {
    "raw": {
        "target": Path("data/raw"),
        "allowed_extensions": ALLOWED_EXTENSIONS["excel"],
        "command": lambda path: [
            PYTHON_BIN,
            "scripts/extract_ecrf_sheet.py",
            "--input",
            str(path),
        ],
    },
    "raw_taimei": {
        "target": Path("data/raw"),
        "allowed_extensions": ALLOWED_EXTENSIONS["excel"],
        "command": lambda path: [
            PYTHON_BIN,
            "scripts/extract_taimei_sheet.py",
            "--input",
            str(path),
        ],
    },
    "example": {
        "target": Path("data/knowledge_base/documents"),
        "allowed_extensions": ALLOWED_EXTENSIONS["excel"],
        "command": lambda path: [
            PYTHON_BIN,
            "scripts/convert_als2sdtm.py",
            "--input",
            str(path),
            "--output-dir",
            "data/knowledge_base/structured",
        ],
    },
    "standards": {
        "target": Path("data/knowledge_base/documents/standards"),
        "allowed_extensions": ALLOWED_EXTENSIONS["excel"],
        "command": lambda path: [
            PYTHON_BIN,
            "scripts/preprocess_sdtmig.py",
            "--input-excel",
            str(path),
        ],
    },
    "example_raw": {
        "target": Path("data/knowledge_base/documents"),
        "allowed_extensions": ALLOWED_EXTENSIONS["excel"],
        "command": None,
    },
    "als_example_raw": {
        "target": Path("data/knowledge_base/documents"),
        "allowed_extensions": ALLOWED_EXTENSIONS["excel"],
        "command": None,
    },
    "als_output": {
        "target": Path("data/output"),
        "allowed_extensions": ALLOWED_EXTENSIONS["excel"],
        "command": None,
    },
}

# ==================== 文件名安全配置 ====================

MAX_FILENAME_LENGTH = 200

SAFE_FILENAME_PATTERN = re.compile(
    r"^[\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\s\-\.\(\)\[\]+@#&,;=\'~]+$",
    re.UNICODE,
)

DANGEROUS_CHARS = [
    "\x00",
    "\x01",
    "\x02",
    "\x03",
    "\x04",
    "\x05",
    "\x06",
    "\x07",
    "\x08",
    "\x0b",
    "\x0c",
    "\x0e",
    "\x0f",
    "\x10",
    "\x11",
    "\x12",
    "\x13",
    "\x14",
    "\x15",
    "\x16",
    "\x17",
    "\x18",
    "\x19",
    "\x1a",
    "\x1b",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x1f",
    "\x7f",
    "<",
    ">",
    ":",
    '"',
    "|",
    "?",
    "*",
    "\t",
    "\n",
    "\r",
]


# ==================== 安全工具函数 ====================


def validate_upload_file(file: UploadFile, allowed_extensions: set[str]) -> None:
    """
    验证上传文件的大小和类型。

    Args:
        file: 上传的文件对象
        allowed_extensions: 允许的文件扩展名集合

    Raises:
        HTTPException: 如果文件不符合要求
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_extensions:
        allowed_list = ", ".join(sorted(allowed_extensions))
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {file_ext}。允许的类型: {allowed_list}",
        )

    content_type = file.content_type or ""
    if content_type and content_type not in ALLOWED_MIME_TYPES:
        print(f"[Upload] 警告: 非标准 MIME 类型 {content_type}，文件: {file.filename}")

    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大: {file_size / 1024 / 1024:.1f} MB。最大允许: {MAX_FILE_SIZE_MB} MB",
        )

    if file_size == 0:
        raise HTTPException(status_code=400, detail="文件为空")


def sanitize_filename(filename: str) -> str:
    """
    清理文件名，防止路径遍历攻击和特殊字符问题。
    只保留文件名部分，移除任何危险字符。

    Args:
        filename: 原始文件名

    Returns:
        清理后的安全文件名

    Raises:
        HTTPException: 如果文件名无效
    """
    if not filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    clean_name = unicodedata.normalize("NFC", filename)
    clean_name = clean_name.replace("\\", "/").split("/")[-1]

    for char in DANGEROUS_CHARS:
        clean_name = clean_name.replace(char, "")

    clean_name = clean_name.replace("..", "")
    clean_name = clean_name.strip()

    if len(clean_name) > MAX_FILENAME_LENGTH:
        name_part, ext = os.path.splitext(clean_name)
        max_name_len = MAX_FILENAME_LENGTH - len(ext)
        if max_name_len > 0:
            clean_name = name_part[:max_name_len] + ext
        else:
            raise HTTPException(
                status_code=400,
                detail=f"文件名过长，最大允许 {MAX_FILENAME_LENGTH} 个字符",
            )

    if not clean_name:
        raise HTTPException(status_code=400, detail="无效的文件名")

    if clean_name.startswith("."):
        raise HTTPException(status_code=400, detail="文件名不能以点开头")

    if not SAFE_FILENAME_PATTERN.match(clean_name):
        invalid_chars = set()
        for char in clean_name:
            if not re.match(
                r"[\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\s\-\.\(\)\[\]+@#&,;=\'~]",
                char,
                re.UNICODE,
            ):
                if char.isprintable():
                    invalid_chars.add(char)
                else:
                    invalid_chars.add(f"U+{ord(char):04X}")

        if invalid_chars:
            chars_display = ", ".join(sorted(invalid_chars)[:5])
            raise HTTPException(
                status_code=400,
                detail=f"文件名包含不允许的字符: {chars_display}",
            )

    return clean_name


def safe_path(base_dir: Path, file_id: str) -> Path:
    """
    安全地构建文件路径，防止路径遍历攻击。
    确保最终路径在 base_dir 目录内。

    Args:
        base_dir: 允许访问的基础目录
        file_id: 用户提供的文件标识符

    Returns:
        验证后的安全路径

    Raises:
        HTTPException: 如果路径不安全
    """
    if not file_id:
        raise HTTPException(status_code=400, detail="文件标识不能为空")

    clean_file_id = sanitize_filename(file_id)

    base_resolved = base_dir.resolve()
    full_path = (base_dir / clean_file_id).resolve()

    try:
        full_path.relative_to(base_resolved)
    except ValueError:
        raise HTTPException(status_code=400, detail="非法的文件路径") from None

    return full_path


def save_upload(file: UploadFile, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return destination


COMMAND_TIMEOUT_SECONDS = int(os.getenv("COMMAND_TIMEOUT_SECONDS", "300"))


def run_command(command: list[str], timeout: int | None = None) -> str:
    if not command:
        return ""
    effective_timeout = timeout if timeout is not None else COMMAND_TIMEOUT_SECONDS
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=effective_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"脚本执行超时 ({effective_timeout}s) | cmd: {' '.join(command)}") from exc

    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()

    if completed.returncode != 0:
        msg = stderr or stdout or "脚本执行失败"
        raise RuntimeError(f"{msg} | cmd: {' '.join(command)}")

    if stderr and "[ERROR]" in stderr:
        raise RuntimeError(f"{stderr} | cmd: {' '.join(command)}")

    return stdout


# ==================== LLM Endpoint 安全配置 ====================

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# 内置 provider 预设对应的可信 host（与前端 LLM_PROVIDERS 保持一致）
_BUILTIN_LLM_HOSTS = {"openrouter.ai", "api.openai.com", "api.deepseek.com"}

# 云元数据地址：即便被管理员加入 allowlist 也永不放行（最高危 SSRF 目标）
_METADATA_HOSTS = {"169.254.169.254", "fd00:ec2::254", "[fd00:ec2::254]"}


def _host_of(url: str) -> str:
    """提取 URL 的 host（小写、去端口）。"""
    return (urlsplit(url).hostname or "").lower()


def server_default_llm_host() -> str:
    """服务器自身配置的 LLM endpoint host（env 回退密钥只应发往此 host）。"""
    base = os.getenv("OPENROUTER_BASE_URL") or DEFAULT_OPENROUTER_BASE_URL
    return _host_of(base)


def allowed_llm_hosts() -> set[str]:
    """允许的 LLM endpoint host 集合：内置 provider ∪ 服务器默认 host ∪ 管理员配置。"""
    hosts = set(_BUILTIN_LLM_HOSTS)
    default_host = server_default_llm_host()
    if default_host:
        hosts.add(default_host)
    for raw in os.getenv("SIRIUS_LLM_ALLOWED_HOSTS", "").split(","):
        host = raw.strip().lower()
        if host:
            hosts.add(host)
    return hosts


def _is_metadata_host(host: str) -> bool:
    """判断 host 是否为云元数据地址（不受 allowlist 影响，一律拒绝）。"""
    if host in _METADATA_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    # link-local (169.254.0.0/16 / fe80::/10) 覆盖 AWS/GCP/Azure 元数据地址
    return ip.is_link_local


def validate_llm_base_url(url: str) -> str:
    """
    校验用户提供的 LLM Base URL 是否安全可用（防 SSRF）。

    仅接受 scheme 为 http(s)、无 userinfo、host 在 allowlist 内的 URL；
    云元数据地址一律拒绝。校验通过返回规范化后的 URL，否则抛 ValueError（→ 422）。
    """
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("base_url 必须以 http:// 或 https:// 开头")
    parts = urlsplit(url)
    # 拒绝带 userinfo 的 URL，避免凭据混入 URL 被日志打印
    if parts.username or parts.password:
        raise ValueError("base_url 不能包含用户名/密码")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("base_url 缺少主机名")
    if _is_metadata_host(host):
        raise ValueError("base_url 指向被禁止的元数据地址")
    if host not in allowed_llm_hosts():
        raise ValueError(
            "base_url 主机不在允许列表中；请使用内置 Provider，"
            "或联系管理员将该主机加入 SIRIUS_LLM_ALLOWED_HOSTS"
        )
    return url


def is_server_default_llm_endpoint(url: str | None) -> bool:
    """
    判断该 endpoint 是否为服务器自身配置的默认 endpoint。

    仅当 endpoint 缺省或 host 等于服务器默认 host 时才可安全使用 env 回退密钥；
    其余（含 allowlist 内的第三方 provider）必须由请求自带 token，避免服务器密钥外泄。
    """
    if not url:
        return True
    return _host_of(url) == server_default_llm_host()
