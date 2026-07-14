"""Typed application settings backed by ``pydantic-settings``.

This module centralises every ``os.getenv`` the project used to scatter
across processors, the web layer, the knowledge base, and the spec
mapper. All values are parsed once, validated eagerly, and exposed as
grouped sub-models so the rest of the code can depend on a single,
auditable object:

    from src.config.settings import get_settings
    settings = get_settings()
    if settings.cascade.enabled:
        ...

Environment variables keep their **historical names** so existing
``.env`` files continue to work; see ``env_template.txt`` for the full
list.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BOOL_TRUE = {"1", "true", "yes", "on", "y", "t"}
_DEFAULT_CORS_ORIGINS = ["http://127.0.0.1:8000", "http://localhost:8000"]


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in _BOOL_TRUE


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------
class AIProviderSettings(BaseModel):
    """Outbound LLM provider configuration."""

    default_provider: str = Field(default="openrouter")
    default_model: str = Field(default="google/gemini-3-flash-preview")
    default_language: str = Field(default="en")

    openrouter_api_key: str = Field(default="")
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1")
    max_retries: int = Field(default=2, ge=0, le=10)
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)

    @field_validator("default_language")
    @classmethod
    def _normalize_language(cls, value: str) -> str:
        v = (value or "en").strip().lower()
        return v if v in {"cn", "en"} else "en"


class CascadeSettings(BaseModel):
    """4-level cascade thresholds. Higher values → fewer short-circuits."""

    enabled: bool = True
    kb_min_confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    kb_high_conf: float = Field(default=0.85, ge=0.0, le=1.0)
    rag_high_conf: float = Field(default=0.7, ge=0.0, le=1.0)
    kb_domain_override_conf: float = Field(default=0.85, ge=0.0, le=1.0)


class RAGSettings(BaseModel):
    embed_model: str = "Qwen3-Embed"
    top_k_retrieval: int = Field(default=3, ge=1, le=50)
    kb_default_file: str = ""


class KnowledgeBaseSettings(BaseModel):
    path: Path = Path("data/knowledge_base")
    structured_path: Path = Path("data/knowledge_base/structured")


class RuntimeSettings(BaseModel):
    """Processing & parallelism controls."""

    enable_parallel: bool = True
    max_workers: int = Field(default=5, ge=1, le=32)
    batch_size: int = Field(default=10, ge=1, le=1000)
    rate_limit_rpm: int = Field(default=120, ge=1, le=10_000)
    # Full prompts/responses may contain clinical context. Keep persistence
    # opt-in even when masking is enabled.
    log_ai: bool = False
    save_ai_interactions: bool = False
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        v = (value or "INFO").upper()
        if v not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            return "INFO"
        return v


class SecuritySettings(BaseModel):
    """GxP-grade audit + PHI masking toggles."""

    audit_log_enabled: bool = True
    data_masking_enabled: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: list(_DEFAULT_CORS_ORIGINS))


class WebRateLimitSettings(BaseModel):
    """slowapi rate-limit strings applied to the FastAPI layer."""

    upload: str = "30/minute"
    ai_job: str = "10/minute"
    general: str = "60/minute"
    read: str = "120/minute"


class PathSettings(BaseModel):
    raw: Path = Path("data/raw")
    processed: Path = Path("data/processed")
    output: Path = Path("data/output")


class SpecMapperSettings(BaseModel):
    template_path: Path = Path("data/knowledge_base/documents")
    output_path: Path = Path("data/spec_output")
    als_output_path: Path = Path("data/output")
    als_default_sheet: str = "Sheet1"
    highlight_mappings: bool = True


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------
class Settings(BaseSettings):
    """Top-level settings aggregate.

    Pydantic-settings loads from, in order of precedence:
    1. Constructor kwargs
    2. Environment variables (case-insensitive)
    3. ``.env`` in the working directory (if present)
    4. Defaults defined on the sub-models
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    ai: AIProviderSettings = AIProviderSettings()
    cascade: CascadeSettings = CascadeSettings()
    rag: RAGSettings = RAGSettings()
    kb: KnowledgeBaseSettings = KnowledgeBaseSettings()
    runtime: RuntimeSettings = RuntimeSettings()
    security: SecuritySettings = SecuritySettings()
    web_rate_limits: WebRateLimitSettings = WebRateLimitSettings()
    paths: PathSettings = PathSettings()
    spec_mapper: SpecMapperSettings = SpecMapperSettings()

    # ------------------------------------------------------------------
    # Pydantic-settings cannot auto-map historical flat env names to
    # nested sub-models, so we hydrate them here.
    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, **overrides: Any) -> Settings:
        import os

        # The nested settings models are hydrated from flat historical names
        # below, so BaseSettings' env_file handling cannot populate them.
        # Load the working directory's .env before reading os.environ and keep
        # exported environment variables at the higher precedence.
        load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
        env = os.environ

        def g(key: str, default: str | None = None) -> str | None:
            val = env.get(key)
            if val is None or val == "":
                return default
            return val

        default_model = (
            g("DEFAULT_MODEL")
            or g("OPENROUTER_MODEL")  # Backward-compatible alias used by the legacy web path.
            or "google/gemini-3-flash-preview"
        )

        ai = AIProviderSettings(
            default_provider=g("DEFAULT_AI_PROVIDER", "openrouter") or "openrouter",
            default_model=default_model,
            default_language=g("DEFAULT_LANGUAGE", "en") or "en",
            openrouter_api_key=g("OPENROUTER_API_KEY", "") or "",
            openrouter_base_url=g("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            or "https://openrouter.ai/api/v1",
            max_retries=int(g("LLM_MAX_RETRIES", "2") or 2),
            timeout_seconds=float(g("LLM_TIMEOUT_SECONDS", "120") or 120),
        )

        cascade = CascadeSettings(
            enabled=_to_bool(g("CASCADE_ENABLED", "true"), True),
            kb_min_confidence=float(g("KB_MIN_CONFIDENCE", "0.8") or 0.8),
            kb_high_conf=float(g("CASCADE_KB_HIGH_CONF", "0.85") or 0.85),
            rag_high_conf=float(g("CASCADE_RAG_HIGH_CONF", "0.7") or 0.7),
            kb_domain_override_conf=float(g("KB_DOMAIN_OVERRIDE_CONF", "0.85") or 0.85),
        )

        rag = RAGSettings(
            embed_model=g("RAG_EMBED_MODEL", "Qwen3-Embed") or "Qwen3-Embed",
            top_k_retrieval=int(g("TOP_K_RETRIEVAL", "3") or 3),
            kb_default_file=g("RAG_KB_DEFAULT_FILE", "") or "",
        )

        kb = KnowledgeBaseSettings(
            path=Path(g("KNOWLEDGE_BASE_PATH", "data/knowledge_base") or "data/knowledge_base"),
            structured_path=Path(
                g("KNOWLEDGE_STRUCTURED_PATH", "data/knowledge_base/structured") or "data/knowledge_base/structured"
            ),
        )

        runtime = RuntimeSettings(
            enable_parallel=_to_bool(g("SDTM_ENABLE_PARALLEL", "true"), True),
            max_workers=int(g("SDTM_MAX_WORKERS", "5") or 5),
            batch_size=int(g("BATCH_SIZE", "10") or 10),
            rate_limit_rpm=int(g("RATE_LIMIT_RPM", "120") or 120),
            log_ai=_to_bool(g("SDTM_LOG_AI", "false"), False),
            save_ai_interactions=_to_bool(g("SAVE_AI_INTERACTIONS", "false"), False),
            log_level=g("LOG_LEVEL", "INFO") or "INFO",
        )

        security = SecuritySettings(
            audit_log_enabled=_to_bool(g("AUDIT_LOG_ENABLED", "true"), True),
            data_masking_enabled=_to_bool(g("DATA_MASKING_ENABLED", "true"), True),
            cors_origins=[
                origin.strip()
                for origin in (g("SIRIUS_CORS_ORIGINS", ",".join(_DEFAULT_CORS_ORIGINS)) or "").split(",")
                if origin.strip()
            ],
        )

        web_rate_limits = WebRateLimitSettings(
            upload=g("RATE_LIMIT_UPLOAD", "30/minute") or "30/minute",
            ai_job=g("RATE_LIMIT_AI_JOB", "10/minute") or "10/minute",
            general=g("RATE_LIMIT_GENERAL", "60/minute") or "60/minute",
            read=g("RATE_LIMIT_READ", "120/minute") or "120/minute",
        )

        paths = PathSettings(
            raw=Path(g("RAW_DATA_PATH", "data/raw") or "data/raw"),
            processed=Path(g("PROCESSED_DATA_PATH", "data/processed") or "data/processed"),
            output=Path(g("OUTPUT_PATH", "data/output") or "data/output"),
        )

        spec_mapper = SpecMapperSettings(
            template_path=Path(
                g("SPEC_TEMPLATE_PATH", "data/knowledge_base/documents") or "data/knowledge_base/documents"
            ),
            output_path=Path(g("SPEC_OUTPUT_PATH", "data/spec_output") or "data/spec_output"),
            als_output_path=Path(g("ALS_OUTPUT_PATH", "data/output") or "data/output"),
            als_default_sheet=g("ALS_DEFAULT_SHEET", "Sheet1") or "Sheet1",
            highlight_mappings=_to_bool(g("SPEC_HIGHLIGHT_MAPPINGS", "true"), True),
        )

        return cls(
            ai=ai,
            cascade=cascade,
            rag=rag,
            kb=kb,
            runtime=runtime,
            security=security,
            web_rate_limits=web_rate_limits,
            paths=paths,
            spec_mapper=spec_mapper,
            **overrides,
        )

    # ------------------------------------------------------------------
    def dump_summary(self) -> dict[str, Any]:
        """JSON-safe summary, API-key redacted — for audit logs."""
        data = self.model_dump(mode="json")
        ai = data.get("ai") or {}
        if ai.get("openrouter_api_key"):
            ai["openrouter_api_key"] = "<set>"
        return data

    # ------------------------------------------------------------------
    @field_validator("cascade")
    @classmethod
    def _check_thresholds(cls, value: CascadeSettings, _info: ValidationInfo) -> CascadeSettings:
        if value.kb_high_conf < value.kb_min_confidence:
            raise ValueError(
                "CASCADE_KB_HIGH_CONF must be >= KB_MIN_CONFIDENCE "
                f"(got {value.kb_high_conf} < {value.kb_min_confidence})"
            )
        return value


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide singleton. Tests should call ``reload_settings()``."""
    return Settings.from_env()


def reload_settings(**overrides: Any) -> Settings:
    """Clear the cache and recreate the ``Settings`` (useful in tests)."""
    get_settings.cache_clear()
    if overrides:
        # When overrides are provided we deliberately skip the cache so the
        # caller keeps full control over lifetime.
        return Settings.from_env(**overrides)
    return get_settings()


__all__ = [
    "AIProviderSettings",
    "CascadeSettings",
    "KnowledgeBaseSettings",
    "PathSettings",
    "RAGSettings",
    "RuntimeSettings",
    "SecuritySettings",
    "Settings",
    "SpecMapperSettings",
    "WebRateLimitSettings",
    "get_settings",
    "reload_settings",
]
