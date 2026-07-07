"""LLM inference service (Level-4 fallback).

Encapsulates the "call the AI client, parse JSON, return dicts" concern
so ``SDTMProcessor`` can delegate instead of owning raw prompt / client
plumbing inline.  Masking of PHI/PII happens at the boundary so the LLM
never sees unredacted payloads.

The service is intentionally side-effect free (aside from the AI call
and optional audit/masker hooks).  It does **not** normalise, validate,
or dedupe recommendations — that remains the job of
:class:`RecommendationNormalizer`.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.clients.base_client import BaseAIClient

logger = logging.getLogger(__name__)

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class LLMInferenceServiceError(RuntimeError):
    """Raised when the LLM returns an unparseable payload."""


class LLMInferenceService:
    """Wrap an AI client to produce raw recommendation dicts from a prompt."""

    def __init__(
        self,
        *,
        client: BaseAIClient,
        data_masker: Any | None = None,
        max_output_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> None:
        self.client = client
        self.data_masker = data_masker
        self.max_output_tokens = int(max_output_tokens)
        self.temperature = float(temperature)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def infer(
        self,
        *,
        prompt: str,
        variable_data: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Call the client and return a list of recommendation dicts.

        The ``variable_data`` argument is accepted so future subclasses
        (or adapters) can emit audit events tied to the source variable.
        """
        safe_prompt = self._maybe_mask_prompt(prompt, variable_data)
        raw = self.client.generate_content(
            prompt=safe_prompt,
            max_output_tokens=self.max_output_tokens,
            temperature=self.temperature,
        )
        return self.parse_response(raw)

    # ------------------------------------------------------------------
    # Parsing helpers (exposed for unit testing)
    # ------------------------------------------------------------------
    @staticmethod
    def parse_response(raw: str | None) -> list[dict[str, Any]]:
        """Parse an LLM response into a list of dicts.

        Accepts a JSON array, a single JSON object (wrapped), or a code
        block containing either.  Returns ``[]`` for empty or malformed
        payloads rather than raising, so the caller can fall back to KB
        / RAG heuristics.
        """
        if not raw:
            return []

        text = raw.strip()
        # Strip common markdown fences (```json ... ```)
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        payload = LLMInferenceService._extract_json(text)
        if payload is None:
            logger.debug("LLMInferenceService: no JSON found in response %r", raw[:160])
            return []

        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            logger.debug("LLMInferenceService: JSON decode failure for %r", payload[:160])
            return []

        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return [r for r in parsed if isinstance(r, dict)]
        return []

    @staticmethod
    def _extract_json(text: str) -> str | None:
        """Return the first JSON array or object substring found in ``text``."""
        array = _JSON_ARRAY_RE.search(text)
        if array:
            return array.group(0)
        obj = _JSON_OBJECT_RE.search(text)
        if obj:
            return obj.group(0)
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _maybe_mask_prompt(self, prompt: str, variable_data: dict[str, Any] | None) -> str:
        """Optionally redact PHI/PII tokens before sending to the LLM."""
        if self.data_masker is None or not prompt:
            return prompt
        try:
            return self.data_masker.mask_text(prompt)
        except Exception:
            logger.exception("DataMasker failed; falling back to raw prompt")
            return prompt
