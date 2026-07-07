"""
Domain Inference Mixin
Provides methods for mapping tables/variables to SDTM domains.
"""

import logging
from typing import Any

from src.config.domain_semantic_map import (
    _DOMAIN_KEYWORDS_SORTED,
    infer_domain_from_table,
)
from src.processors.mixin_typing import SDTMProcessorHost

logger = logging.getLogger(__name__)


class DomainInferenceMixin:
    """Methods for inferring SDTM domains from table/variable metadata."""

    # ------------------------------------------------------------------
    # The following instance attributes are expected to be initialised by
    # the host class (SDTMProcessor.__init__):
    #   self.domain_name_lookup   : Dict[str, str]
    #   self.semantic_keyword_domain_map : Dict[str, str]
    #   self._semantic_kw_sorted  : List[Tuple[str, str]]
    #   self.CHINESE_TABLE_DOMAIN_MAP : Dict[str, str]
    #   self.debug                : bool
    #   self.domain_override_threshold : float
    # ------------------------------------------------------------------

    def _map_table_to_domain(self: SDTMProcessorHost, table_name: Any, source: str = "table") -> str | None:
        if not table_name:
            return None
        table_str = str(table_name).strip()
        if not table_str:
            return None

        lower = table_str.lower()
        upper = table_str.upper()

        if lower in self.domain_name_lookup:
            return self.domain_name_lookup[lower]
        normalized = lower.replace(" ", "")
        if normalized in self.domain_name_lookup:
            return self.domain_name_lookup[normalized]

        if source == "table":
            for keyword, code in self.semantic_keyword_domain_map.items():
                if keyword and keyword in lower:
                    return code

        normalized_lower = lower.replace("）", ")")

        tokens = [lower]
        for sep in ("/", "-", "—", "(", "（", ")", "）"):
            tokens.extend([token.strip() for token in normalized_lower.split(sep) if token.strip()])

        for token in tokens:
            if not token:
                continue
            if token in self.domain_name_lookup:
                return self.domain_name_lookup[token]

        semantic_code = self._match_semantic_domain(lower)
        if semantic_code:
            return semantic_code

        for candidate in self.CHINESE_TABLE_DOMAIN_MAP.values():
            if candidate and candidate in upper:
                return candidate

        for name, code in self.domain_name_lookup.items():
            if name and name in lower:
                return code

        return None

    def _match_semantic_domain(self: SDTMProcessorHost, text: str) -> str | None:
        """
        Return a domain code if the text matches any semantic keyword.
        Uses longest-match-first strategy to prevent short keywords from
        shadowing longer, more specific ones.
        """
        if not text:
            return None
        lowered = text.strip().lower()
        if not lowered:
            return None

        for keyword, code in self._semantic_kw_sorted:
            if keyword and keyword in lowered:
                return code

        for kw, domain in _DOMAIN_KEYWORDS_SORTED:
            if kw in lowered:
                return domain

        return None

    def _recommend_domain_from_annotation(self: SDTMProcessorHost, annotation_table: Any) -> str | None:
        """
        Determine the primary domain hint using annotation table semantics.
        Uses unified infer_domain_from_table from domain_semantic_map.
        """
        if not annotation_table:
            return None

        domain = infer_domain_from_table(str(annotation_table))
        if domain:
            return domain

        domain = self._map_table_to_domain(annotation_table, source="table")
        if domain:
            return domain

        return self._match_semantic_domain(annotation_table)

    @staticmethod
    def _normalize_domain_code(domain: Any) -> str | None:
        if not isinstance(domain, str):
            return None
        value = domain.strip().upper()
        if not value:
            return None
        if len(value) > 2:
            value = value[:2]
        if value.isalpha():
            return value
        return None

    def _infer_candidate_domains(
        self: SDTMProcessorHost,
        variable_data: dict[str, Any],
        kb_context: dict[str, Any],
    ) -> list[str]:
        """
        Infer candidate SDTM domains for prompt generation.

        OPTIMIZED: If a high-confidence domain hint exists, only return that domain
        to minimize prompt length.
        """
        primary_domain = self._recommend_domain_from_annotation(
            variable_data.get("annotation_table", "")
        ) or kb_context.get("domain_hint")

        if primary_domain:
            primary_domain = self._normalize_domain_code(primary_domain)
            if primary_domain:
                return [primary_domain]

        ordered: list[str] = []
        seen: set = set()

        def add_domain(candidate: str | None) -> None:
            if not candidate:
                return
            code = self._normalize_domain_code(candidate) or str(candidate).strip().upper()
            if not code:
                return
            if code not in seen:
                seen.add(code)
                ordered.append(code)

        add_domain(self._match_semantic_domain(variable_data.get("annotation_variable", "")))

        for key in ("annotation_table", "metadata_table"):
            add_domain(self._map_table_to_domain(variable_data.get(key, ""), source="table"))

        for suggestion in kb_context.get("suggestions", [])[:2]:
            add_domain(suggestion.get("domain"))

        # Direction 4: metadata_variable prefix as auxiliary domain signal
        # Only use as a tiebreaker when no other hint is available
        kb_hints = getattr(self, "kb_hints", None)
        if not ordered and kb_hints is not None:
            mv_domain = kb_hints.get_mv_prefix_domain(variable_data.get("metadata_variable", ""))
            add_domain(mv_domain)

        return ordered[:3]
