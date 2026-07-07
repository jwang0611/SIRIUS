"""Level-1 → Level-4 cascade prediction, decoupled from ``SDTMProcessor``.

The cascade is a deterministic function of:
- the KB context (what the KB/direct-match lookup returned for the
  variable),
- the RAG context (top score, retrieved snippets), and
- a handful of numeric thresholds (:class:`CascadeSettings`).

Moving the decision into its own class means we can unit-test Levels
1–3 without booting the full processor, parallel executor, LLM client,
or knowledge base.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from src.config.settings import CascadeSettings

CascadeSource = Literal["KB_DIRECT", "KB_HIGH", "RAG_HIGH", "LLM"]


@dataclass
class CascadeDecision:
    """Outcome of a cascade attempt for one variable.

    ``short_circuited=True`` means the caller should **not** invoke the LLM
    and should instead consume ``recommendations`` directly. When False, the
    caller falls back to Level 4 (LLM) using any ``rag_contexts`` already
    gathered to avoid a second retrieval.
    """

    level: Literal[1, 2, 3, 4]
    source: CascadeSource
    short_circuited: bool
    confidence: float = 0.0
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    rag_contexts: list[dict[str, Any]] = field(default_factory=list)
    rag_info: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


class CascadePredictor:
    """Pure decision engine for the 4-level cascade.

    It does **not** call the LLM or the KB. Callers pass already-retrieved
    KB and (optional) RAG data; the predictor replies with which level
    should win and whether to short-circuit.
    """

    def __init__(self, settings: CascadeSettings) -> None:
        self.settings = settings

    # ------------------------------------------------------------------
    def decide(
        self,
        *,
        kb_context: dict[str, Any],
        rag_contexts: Sequence[dict[str, Any]] | None = None,
        rag_info: dict[str, Any] | None = None,
    ) -> CascadeDecision:
        """Evaluate Levels 1-3; fall through to Level 4 when none apply."""
        kb_confidence = float(kb_context.get("confidence", 0.0) or 0.0)
        kb_source = kb_context.get("source")
        kb_suggestions: list[dict[str, Any]] = list(kb_context.get("suggestions", []) or [])

        # --- Level 1: KB eCRF direct match ---
        if kb_source == "eCRF_direct_match" and kb_confidence >= self.settings.kb_min_confidence and kb_suggestions:
            return CascadeDecision(
                level=1,
                source="KB_DIRECT",
                short_circuited=True,
                confidence=kb_confidence,
                recommendations=kb_suggestions,
                reason=f"KB direct match (conf={kb_confidence:.2f})",
            )

        # --- Level 2: KB high-confidence suggestions ---
        if kb_suggestions and kb_confidence >= self.settings.kb_high_conf:
            return CascadeDecision(
                level=2,
                source="KB_HIGH",
                short_circuited=True,
                confidence=kb_confidence,
                recommendations=kb_suggestions,
                reason=f"KB high-confidence (conf={kb_confidence:.2f})",
            )

        # --- Level 3: RAG-enhanced retrieval ---
        rag_contexts_list: list[dict[str, Any]] = list(rag_contexts or [])
        rag_info_dict: dict[str, Any] = dict(rag_info or {})
        rag_top_score = float(rag_info_dict.get("top_score", 0.0) or 0.0)

        if rag_contexts_list and rag_top_score >= self.settings.rag_high_conf:
            return CascadeDecision(
                level=3,
                source="RAG_HIGH",
                short_circuited=True,
                confidence=rag_top_score,
                recommendations=[],  # caller must map rag_contexts to recs
                rag_contexts=rag_contexts_list,
                rag_info=rag_info_dict,
                reason=f"RAG high-confidence (top={rag_top_score:.2f})",
            )

        # --- Level 4: fall through to LLM ---
        return CascadeDecision(
            level=4,
            source="LLM",
            short_circuited=False,
            confidence=max(kb_confidence, rag_top_score),
            rag_contexts=rag_contexts_list,
            rag_info=rag_info_dict,
            reason="No short-circuit threshold met",
        )

    # ------------------------------------------------------------------
    def should_enforce_domain(self, *, rag_top_score: float) -> bool:
        """Mirror the mixin rule: high RAG score → allow domain override."""
        return rag_top_score < self.settings.kb_domain_override_conf


__all__ = ["CascadeDecision", "CascadePredictor", "CascadeSource"]
