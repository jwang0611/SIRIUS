"""Shared typing for SDTM processor mixins (attributes live on SDTMProcessor)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from src.models.sdtm_models import GenerationConfig


class SDTMProcessorHost(Protocol):
    """Structural type for mixin methods; satisfied by SDTMProcessor."""

    rag_config: dict[str, Any]
    prompt_generator: Any
    CHINESE_TABLE_DOMAIN_MAP: dict[str, str]
    standard_catalog: dict[str, Any]
    domain_name_lookup: dict[str, str]
    semantic_keyword_domain_map: dict[str, str]
    _semantic_kw_sorted: list[tuple[str, str]]
    debug: bool
    domain_override_threshold: float
    generation_config: GenerationConfig

    def _recommend_domain_from_annotation(self, annotation_table: Any) -> str | None: ...

    def _map_table_to_domain(self, table_name: Any, source: str = "table") -> str | None: ...

    def _match_semantic_domain(self, text: str) -> str | None: ...

    def _normalize_domain_code(self, domain: Any) -> str | None: ...

    def _build_standard_catalog(self, chinese_path: Path | None) -> dict[str, Any]: ...

    def _extract_label(self, text: Any, key: str) -> str: ...

    def _infer_testcd(self, variable_name: str, annotation_variable: str, domain: str) -> str: ...
