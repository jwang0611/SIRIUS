"""Optional LLM-assisted field extraction (off by default).

The deterministic layer supplies the page text; the LLM cleans up wrapping,
strips options, and recovers grid/repeating-table fields that layout heuristics
miss. Every returned label is validated deterministically — dropped unless it
appears (whitespace-insensitively) in the original page text, so the model can
merge wrapped labels but cannot hallucinate new ones — then re-run through the
same stoplist/normalisation as the deterministic path.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

from src.processors.acrf.fields import _looks_like_field, clean_label, norm

logger = logging.getLogger(__name__)

_PROMPTS_PATH = Path(__file__).with_name("prompts.yaml")
_WS_ALL_RE = re.compile(r"\s+")
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


@lru_cache(maxsize=1)
def _load_prompts() -> dict:
    return yaml.safe_load(_PROMPTS_PATH.read_text(encoding="utf-8")) or {}


def build_prompt(form_name: str, page_text: str, language: str = "cn") -> str:
    templates = _load_prompts().get("field_extraction", {})
    template = templates.get(language) or templates.get("cn") or ""
    return template.format(form_name=form_name, page_text=page_text)


def _squash(text: str) -> str:
    """NFC-normalise and remove all whitespace (for wrap-tolerant matching)."""
    return _WS_ALL_RE.sub("", unicodedata.normalize("NFC", text or ""))


def parse_label_list(raw_response: str) -> list[str]:
    """Parse a JSON string array out of a model response (tolerant of fences)."""
    if not raw_response:
        return []
    match = _JSON_ARRAY_RE.search(raw_response)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def extract_fields_llm(
    page_text: str,
    *,
    form_name: str,
    client: object,
    masker: object | None = None,
    language: str = "cn",
    max_output_tokens: int = 1200,
) -> list[str]:
    """Return validated field labels for one form's page text via the LLM.

    ``client`` must expose ``generate_content(prompt, ...) -> str`` (the project
    :class:`BaseAIClient` contract). ``masker`` (if given) redacts PHI/PII before
    the text leaves the process; validation uses the original text.
    """
    if not page_text.strip():
        return []

    sent_text = page_text
    if masker is not None and hasattr(masker, "mask_text"):
        sent_text = masker.mask_text(page_text)

    prompt = build_prompt(form_name, sent_text, language)
    try:
        response = client.generate_content(prompt, max_output_tokens=max_output_tokens, temperature=0.0)  # type: ignore[attr-defined]
    except Exception as exc:
        logger.warning("aCRF LLM extraction failed for form %r: %s", form_name, exc)
        return []

    labels = parse_label_list(response)

    # Deterministic validation: anti-hallucination (must appear in the ORIGINAL
    # page text, whitespace-insensitively) + the shared stoplist/cleaning.
    haystack = _squash(page_text)
    out: list[str] = []
    seen: set[str] = set()
    for label in labels:
        if _squash(label) not in haystack:
            continue
        cleaned = clean_label(label)
        if not _looks_like_field(cleaned):
            continue
        key = norm(cleaned)
        if key and key not in seen:
            seen.add(key)
            out.append(cleaned)
    return out
