"""
Catalog Mixin
Standard SDTM catalog initialisation and domain-name lookups.
"""

import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd

from src.processors.mixin_typing import SDTMProcessorHost

logger = logging.getLogger(__name__)


class CatalogMixin:
    """Methods for loading / building the SDTM standard catalog."""

    # ------------------------------------------------------------------
    # Expected instance attributes (set by host __init__):
    #   self.rag_config           : Dict[str, Any]
    #   self.prompt_generator     : SDTMPromptGenerator
    #   self.standard_catalog     : Dict[str, Any]
    #   self.CHINESE_TABLE_DOMAIN_MAP : Dict[str, str]
    # ------------------------------------------------------------------

    def _initialize_standard_catalog(self: SDTMProcessorHost) -> dict[str, Any]:
        """Load standard SDTM catalog data (Chinese enhanced version)."""
        structured_base = Path(
            self.rag_config.get("structured_path")
            or self.rag_config.get("kb_path")
            or os.getenv("RAG_STRUCTURED_PATH")
            or os.getenv("RAG_KB_PATH")
            or "data/knowledge_base/structured"
        )

        def resolve_path(config_value: str | None, default_name: str) -> Path | None:
            if config_value:
                candidate = Path(config_value)
                if not candidate.is_absolute():
                    candidate = structured_base / candidate
            else:
                candidate = structured_base / default_name
            return candidate

        standard_cn_path = resolve_path(
            self.rag_config.get("standard_cn_file") or os.getenv("RAG_STANDARD_CN_FILE"),
            "sdtm_spec_enhanced.parquet",
        )

        catalog = self._build_standard_catalog(standard_cn_path)
        if catalog.get("domains"):
            self.prompt_generator.set_standard_catalog(catalog)
            if standard_cn_path:
                self.rag_config.setdefault("standard_cn_file", str(standard_cn_path))
            print(f"✅ Loaded SDTM standard catalog | domains: {len(catalog['domains'])}")
        else:
            print("ℹ️ SDTM standard catalog not loaded (missing files or empty datasets)")
        return catalog

    def _build_standard_catalog(
        self: SDTMProcessorHost,
        chinese_path: Path | None,
    ) -> dict[str, Any]:
        """Build SDTM standard catalog from Chinese enhanced specification file."""
        catalog: dict[str, Any] = {"domains": {}}
        domain_seen_cn: dict[str, set] = {}

        if chinese_path and chinese_path.exists():
            try:
                df_cn = pd.read_parquet(chinese_path)
            except Exception as exc:
                print(f"⚠️ Failed to load SDTM spec file {chinese_path}: {exc}")
            else:
                for row in df_cn.to_dict("records"):
                    domain = (row.get("domain") or "").strip().upper()
                    variable = (row.get("variable") or row.get("metadata_variable") or "").strip()
                    if not domain or not variable:
                        continue
                    domain_entry = catalog["domains"].setdefault(domain, {"variables_cn": []})
                    seen = domain_seen_cn.setdefault(domain, set())
                    if variable in seen:
                        continue
                    seen.add(variable)
                    chunk_text = row.get("chunk_text", "")
                    label_cn = row.get("label") or self._extract_label(chunk_text, "label")
                    domain_entry["variables_cn"].append(
                        {
                            "variable": variable,
                            "label_cn": label_cn,
                        }
                    )
        elif chinese_path:
            print(f"⚠️ SDTM spec file not found: {chinese_path}")

        return catalog

    @staticmethod
    def _extract_label(text: Any, key: str) -> str:
        if not isinstance(text, str):
            return ""
        prefix = f"{key.lower()}:"
        for line in text.splitlines():
            if line.lower().startswith(prefix):
                return line.split(":", 1)[1].strip()
        return ""

    def _build_domain_name_lookup(self: SDTMProcessorHost) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for name, code in self.CHINESE_TABLE_DOMAIN_MAP.items():
            lookup[name.lower()] = code
        if hasattr(self.prompt_generator, "domain_titles"):
            for domain, titles in self.prompt_generator.domain_titles.items():
                en_title = (titles or {}).get("en")
                cn_title = (titles or {}).get("cn")
                if en_title:
                    lookup[en_title.lower()] = domain
                if cn_title:
                    lookup[cn_title.lower()] = domain
        catalog_domains = self.standard_catalog.get("domains", {})
        for domain, data in catalog_domains.items():
            title_en = data.get("title_en")
            title_cn = data.get("title_cn")
            if title_en:
                lookup.setdefault(title_en.lower(), domain)
            if title_cn:
                lookup.setdefault(title_cn.lower(), domain)
        return lookup
