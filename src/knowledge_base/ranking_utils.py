"""Pure ranking / scoring helpers for the KB query layer."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None  # type: ignore[assignment]


_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
    }
)

_VALID_NAME_RE = re.compile(r"^[A-Z0-9_]+$")
_KEY_TERM_RE = re.compile(r"\b\w+\b")


DOMAIN_DESCRIPTIONS: dict[str, str] = {
    "AE": "Adverse Events - Records of adverse events experienced by study subjects",
    "DM": "Demographics - Basic demographic and administrative information about study subjects",
    "LB": "Laboratory - Laboratory test results and findings",
    "VS": "Vital Signs - Vital signs measurements",
    "CM": "Concomitant Medications - Medications taken by study subjects",
    "MH": "Medical History - Medical history of study subjects",
    "EX": "Exposure - Study drug exposure information",
    "DS": "Disposition - Subject disposition and completion status",
    "PR": "Procedures - Procedures performed on study subjects",
    "EG": "ECG - Electrocardiogram findings",
    "PE": "Physical Exam - Physical examination findings",
    "QS": "Questionnaires - Questionnaire and assessment data",
}


def extract_key_terms(query_text: str) -> list[str]:
    """Return lowercase key terms from ``query_text``, dropping stop words."""
    if not query_text:
        return []
    words = _KEY_TERM_RE.findall(query_text.lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) > 2]


def validate_naming_convention(variable_name: str, domain: str) -> float:
    """Score an SDTM variable name against the naming convention.

    Returns 0.0 when the name is longer than 8 characters (invalid).
    Awards 0.3 for length, 0.4 for correct domain prefix, and 0.3 for
    valid character set.
    """
    if not variable_name:
        return 0.0
    score = 0.0
    if len(variable_name) > 8:
        return 0.0
    score += 0.3
    if domain and variable_name.startswith(domain):
        score += 0.4
    if _VALID_NAME_RE.match(variable_name):
        score += 0.3
    return score


def calculate_confidence(suggestions: Sequence[dict[str, Any]]) -> float:
    """Compute overall confidence: max score + 0.1 if a strong runner-up exists."""
    if not suggestions:
        return 0.0
    try:
        max_score = max(float(s.get("score", 0.0) or 0.0) for s in suggestions)
    except (TypeError, ValueError):
        return 0.0
    if len(suggestions) > 1:
        second = float(suggestions[1].get("score", 0.0) or 0.0)
        if second > 0.3:
            max_score += 0.1
    return min(max_score, 1.0)


def rank_suggestions_from_records(
    records: Iterable[dict[str, Any]],
    *,
    query: str,
    context: str | None = None,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """Rank KB rows (as dicts) into suggestions.

    Mirrors the historical ``_rank_mapping_suggestions`` scoring:
    - 0.4 when query appears in ``Variable Name``
    - 0.3 when query appears in ``Variable Label``
    - 0.2 when context matches ``Role``
    - 0.1 when ``Core`` == "Req"
    """
    query_lower = (query or "").lower()
    context_lower = (context or "").lower()

    suggestions: list[dict[str, Any]] = []
    for row in records:
        score = 0.0
        var_name = str(row.get("Variable Name", ""))
        var_label = str(row.get("Variable Label", ""))
        role = str(row.get("Role", ""))
        core = row.get("Core", "")

        if query_lower and query_lower in var_name.lower():
            score += 0.4
        if query_lower and query_lower in var_label.lower():
            score += 0.3
        if context_lower and role and role.lower() in context_lower:
            score += 0.2
        if core == "Req":
            score += 0.1

        suggestions.append(
            {
                "domain": row.get("Dataset Name", ""),
                "sdtm_variable": var_name,
                "variable_name": var_name,
                "variable_label": var_label,
                "role": role,
                "core": core,
                "type": row.get("Type", ""),
                "score": score,
                "confidence": score,
                "notes": row.get("CDISC Notes", ""),
            }
        )

    suggestions.sort(key=lambda s: s["score"], reverse=True)
    return suggestions[:top_n]


def rank_suggestions(
    df: pd.DataFrame,
    *,
    query: str,
    context: str | None = None,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """DataFrame-flavoured wrapper around :func:`rank_suggestions_from_records`."""
    if pd is None:  # pragma: no cover
        raise RuntimeError("pandas is required for rank_suggestions")
    records = df.to_dict("records") if df is not None else []
    return rank_suggestions_from_records(records, query=query, context=context, top_n=top_n)


def get_domain_description(domain: str) -> str:
    """Return a human-readable description for a domain code."""
    if not domain:
        return "Unknown domain - Clinical data domain"
    return DOMAIN_DESCRIPTIONS.get(domain, f"Domain {domain} - Clinical data domain")


def find_similar_variable_names(target_var: str, candidate_names: Iterable[str], *, top_n: int = 5) -> list[str]:
    """Return candidate names that contain or are contained in ``target_var``."""
    if not target_var:
        return []
    target_lower = target_var.lower()
    similar: list[str] = []
    for name in candidate_names:
        name_str = str(name)
        name_lower = name_str.lower()
        if target_lower in name_lower or name_lower in target_lower:
            similar.append(name_str)
            if len(similar) >= top_n:
                break
    return similar


def generate_functional_query(var_info: Any) -> str:
    """Generate a natural-language mapping query from KB row metadata.

    Accepts any mapping-like object with ``.get(key, default)`` — plain
    dicts, pandas Series, and similar. Only ``Role`` and ``Variable Label``
    are read, so no full materialisation is required.
    """
    role = str(var_info.get("Role", ""))
    var_label = str(var_info.get("Variable Label", "")).lower()
    if role == "Identifier":
        return "Map subject identifier field"
    if role == "Topic":
        return f"Map the main topic field for {var_label}"
    if role == "Timing":
        return f"Map timing information for {var_label}"
    if role == "Qualifier":
        return f"Map qualifier information for {var_label}"
    return f"Map field for {var_label}"


__all__ = [
    "DOMAIN_DESCRIPTIONS",
    "calculate_confidence",
    "extract_key_terms",
    "find_similar_variable_names",
    "generate_functional_query",
    "get_domain_description",
    "rank_suggestions",
    "rank_suggestions_from_records",
    "validate_naming_convention",
]
