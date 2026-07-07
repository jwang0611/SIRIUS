"""Recommendation orchestrator (per-variable pipeline).

Coordinates the 4-level cascade, the LLM fallback, normalisation, and
the deterministic validator for a single variable.  This is the seam
that will eventually replace the per-variable inner loop inside
:class:`SDTMProcessor`.

The class is dependency-injected: callers wire in their own cascade
predictor, inference service, and normaliser.  Tests can supply fakes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from src.processors.cascade import CascadeDecision, CascadePredictor
from src.processors.llm_inference_service import LLMInferenceService
from src.processors.recommendation_normalizer import RecommendationNormalizer

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationResult:
    """Bundle of everything the orchestrator produced for one variable."""

    recommendations: list[dict[str, Any]] = field(default_factory=list)
    cascade_level: int = 4
    source: str = "LLM"
    confidence: float = 0.0
    short_circuited: bool = False
    processing_time_ms: float = 0.0
    rag_contexts: list[Any] = field(default_factory=list)
    rag_info: dict[str, Any] = field(default_factory=dict)
    llm_raw: list[dict[str, Any]] | None = None
    errors: list[str] = field(default_factory=list)


class RecommendationOrchestrator:
    """Coordinate cascade → LLM → normalise for a single variable."""

    def __init__(
        self,
        *,
        cascade: CascadePredictor,
        normalizer: RecommendationNormalizer,
        inference: LLMInferenceService | None = None,
    ) -> None:
        self.cascade = cascade
        self.normalizer = normalizer
        self.inference = inference

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def process_variable(
        self,
        *,
        table_name: str,
        variable_name: str,
        variable_data: dict[str, Any],
        kb_context: dict[str, Any] | None = None,
        rag_contexts: list[Any] | None = None,
        rag_info: dict[str, Any] | None = None,
        target_domain: str | None = None,
        llm_prompt: str | None = None,
    ) -> OrchestrationResult:
        """Process a single variable through cascade + optional LLM."""
        start = time.perf_counter()
        decision: CascadeDecision = self.cascade.decide(
            kb_context=kb_context or {},
            rag_contexts=rag_contexts,
            rag_info=rag_info,
        )

        # Level-3 short-circuits but yields no recommendations directly
        # (callers historically pipe ``rag_contexts`` into a RAG-augmented
        # LLM prompt). Treat that case as "continue to LLM" here so the
        # orchestrator only owns the simple Level 1/2 fast path.
        if decision.short_circuited and decision.recommendations:
            recs = self.normalizer.normalize(
                table_name=table_name,
                variable_name=variable_name,
                domain_recs=decision.recommendations,
                target_domain=target_domain,
                enforce_domain=self._should_enforce_domain_on_cascade(decision),
            )
            return OrchestrationResult(
                recommendations=recs,
                cascade_level=decision.level,
                source=decision.source,
                confidence=decision.confidence,
                short_circuited=True,
                processing_time_ms=(time.perf_counter() - start) * 1000.0,
                rag_contexts=list(decision.rag_contexts),
                rag_info=dict(decision.rag_info),
            )

        if self.inference is None or not llm_prompt:
            return OrchestrationResult(
                recommendations=[],
                cascade_level=4,
                source="LLM",
                confidence=decision.confidence,
                short_circuited=False,
                processing_time_ms=(time.perf_counter() - start) * 1000.0,
                rag_contexts=list(decision.rag_contexts),
                rag_info=dict(decision.rag_info),
                errors=["no_llm_prompt_or_service"],
            )

        try:
            raw = self.inference.infer(prompt=llm_prompt, variable_data=variable_data)
        except Exception as exc:
            logger.exception("LLM inference failed for %s.%s", table_name, variable_name)
            return OrchestrationResult(
                recommendations=[],
                cascade_level=4,
                source="LLM",
                confidence=decision.confidence,
                short_circuited=False,
                processing_time_ms=(time.perf_counter() - start) * 1000.0,
                rag_contexts=list(decision.rag_contexts),
                rag_info=dict(decision.rag_info),
                errors=[f"llm_error:{exc.__class__.__name__}"],
            )

        recs = self.normalizer.normalize(
            table_name=table_name,
            variable_name=variable_name,
            domain_recs=raw,
            target_domain=target_domain,
            enforce_domain=True,
        )
        for rec in recs:
            if not rec.get("source"):
                rec["source"] = "LLM"
            rec["cascade_level"] = 4

        return OrchestrationResult(
            recommendations=recs,
            cascade_level=4,
            source="LLM",
            confidence=self._infer_confidence(recs, decision.confidence),
            short_circuited=False,
            processing_time_ms=(time.perf_counter() - start) * 1000.0,
            rag_contexts=list(decision.rag_contexts),
            rag_info=dict(decision.rag_info),
            llm_raw=raw,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _should_enforce_domain_on_cascade(decision: CascadeDecision) -> bool:
        """KB Level-1 matches are authoritative and bypass target-domain
        enforcement. Everything else respects the caller's target."""
        return decision.level != 1

    @staticmethod
    def _infer_confidence(recs: list[dict[str, Any]], fallback: float) -> float:
        if not recs:
            return float(fallback)
        scores = [float(r.get("score", 0.0) or 0.0) for r in recs]
        return max(scores) if scores else float(fallback)
