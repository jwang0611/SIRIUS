"""Recommendation normaliser service.

Wraps the pure helpers in :mod:`src.processors.normalizer` behind a
class-shaped API that mirrors the methods previously exposed on
``PostprocessMixin``.  The class owns a debug flag and (optionally) a
``DeterministicValidator`` so callers can inject alternatives in tests.

This service is intentionally thin: the heavy lifting lives in the pure
normaliser module.  It exists so downstream code can depend on a stable
interface without importing mixin methods.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from src.config.domain_semantic_map import is_valid_domain
from src.processors import normalizer
from src.processors.deterministic_validator import DeterministicValidator

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RECOMMENDATIONS = 5


class RecommendationNormalizer:
    """Normalise, validate, and deduplicate domain recommendations.

    Parameters
    ----------
    debug:
        When ``True`` the helper emits diagnostic logger calls that match
        the historical output of ``PostprocessMixin``.
    validator:
        Optional :class:`DeterministicValidator`.  A new instance is
        created when omitted.
    domain_override_threshold:
        Score above which ``target_domain`` enforcement is skipped.  This
        mirrors the historical behaviour where very high-confidence
        predictions may keep a non-target domain.
    is_valid_domain_fn:
        Injected predicate used by multi-domain resolution.  Defaults to
        :func:`src.config.domain_semantic_map.is_valid_domain`.
    max_recommendations:
        Hard cap applied to the output length after deduplication.
    """

    def __init__(
        self,
        *,
        debug: bool = False,
        validator: DeterministicValidator | None = None,
        domain_override_threshold: float = 0.85,
        is_valid_domain_fn: Callable[[str], bool] | None = None,
        max_recommendations: int = _DEFAULT_MAX_RECOMMENDATIONS,
    ) -> None:
        self.debug = bool(debug)
        self.validator = validator or DeterministicValidator(debug=debug)
        self.domain_override_threshold = max(0.0, min(float(domain_override_threshold), 1.0))
        self._is_valid_domain = is_valid_domain_fn or is_valid_domain
        self.max_recommendations = max(1, int(max_recommendations))

    # ------------------------------------------------------------------
    # Public pipeline
    # ------------------------------------------------------------------
    def normalize(
        self,
        *,
        table_name: str,
        variable_name: str,
        domain_recs: list[dict[str, Any]],
        target_domain: str | None = None,
        enforce_domain: bool = True,
    ) -> list[dict[str, Any]]:
        """Run the full normalisation pipeline on ``domain_recs``.

        The pipeline stages are (in order):

        1. Optional target-domain filtering.
        2. "when" clause decomposition.
        3. Multi-domain resolution.
        4. Variable-type classification (standard vs supplementary).
        5. Deterministic validation / domain-prefix checks.
        6. Dedup + score-based pruning + final projection.
        """
        if not domain_recs:
            return []

        stage1 = self._filter_by_domain(
            domain_recs=domain_recs,
            target_domain=target_domain,
            enforce_domain=enforce_domain,
            table_name=table_name,
            variable_name=variable_name,
        )
        stage2 = [normalizer.decompose_when_clause(r) for r in stage1]
        stage3 = [normalizer.resolve_multi_domain(r, self._is_valid_domain) for r in stage2]

        validated: list[dict[str, Any]] = []
        for rec in stage3:
            rec["sdtm_variable_type"] = normalizer.classify_variable_type(rec, variable_name)
            if rec["sdtm_variable_type"] == "supp":
                rec = normalizer.normalize_supp_record(rec, variable_name=variable_name)
            fixed, _issues = self.validator.validate_and_correct(
                rec,
                variable_name=variable_name,
                domain_hint=target_domain,
            )
            validated.append(fixed)

        deduped = normalizer.dedupe_by_key(validated)
        deduped.sort(key=lambda r: float(r.get("score", 0.0) or 0.0), reverse=True)
        if len(deduped) > self.max_recommendations:
            deduped = deduped[: self.max_recommendations]

        return [normalizer.to_cleaned_dict(r, variable_name=variable_name) for r in deduped]

    # ------------------------------------------------------------------
    # Target-domain filtering with SUPP alias support
    # ------------------------------------------------------------------
    def _filter_by_domain(
        self,
        *,
        domain_recs: list[dict[str, Any]],
        target_domain: str | None,
        enforce_domain: bool,
        table_name: str,
        variable_name: str,
    ) -> list[dict[str, Any]]:
        target_upper = (
            target_domain.strip().upper() if isinstance(target_domain, str) and target_domain.strip() else None
        )
        if not target_upper or not enforce_domain:
            if not enforce_domain and self.debug:
                logger.debug(
                    "DomainOverride: %s.%s allowing non-target domain",
                    table_name,
                    variable_name,
                )
            return list(domain_recs)

        filtered: list[dict[str, Any]] = []
        for rec in domain_recs:
            domain_value = str(rec.get("domain", "")).upper()
            tokens = [t.strip() for t in re.split(r"[|/,\s]+", domain_value) if t.strip()]
            expanded = set(tokens)
            for tok in tokens:
                if tok.startswith("SUPP") and len(tok) > 4:
                    expanded.add(tok[4:])
            if target_upper in expanded:
                filtered.append(rec)

        if not filtered and self.debug:
            logger.debug(
                "DomainFilter: %s.%s no recommendations matched %s",
                table_name,
                variable_name,
                target_upper,
            )
        return filtered

    # ------------------------------------------------------------------
    # Convenience wrappers used by other code paths
    # ------------------------------------------------------------------
    @staticmethod
    def filter_by_domain(recs: list[dict[str, Any]], target_domain: str | None) -> list[dict[str, Any]]:
        """Expose the pure filter helper for callers that don't want the full pipeline."""
        return normalizer.filter_recs_by_domain(recs, target_domain)
