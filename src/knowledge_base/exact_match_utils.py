"""Pure helpers for exact-match key building used by the KB query layer."""

from __future__ import annotations

import re
from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Any

try:
    import pandas as pd
except ImportError:  # pragma: no cover - pandas is a hard dep in prod
    pd = None  # type: ignore[assignment]


EXACT_MATCH_COLUMNS: list[str] = [
    "metadata_variable",
    "annotation_variable",
    "metadata_table",
    "annotation_table",
]
NORMALIZED_SUFFIX: str = "__norm"
DEEP_NORM_SUFFIX: str = "__deep"

# Regex patterns compiled once
_RE_PARENS = re.compile(r"[（(][^)）]*[)）]")
_RE_SLASHES = re.compile(r"[/／\\]")
_RE_WHITESPACE_PUNCT = re.compile(r"[\s_\-—–·.。、,，;；:：]+")


def normalize_exact_match_value(value: Any) -> str:
    """Normalise ``value`` for case-insensitive exact matching.

    Returns an empty string for ``None``, NaN, or whitespace-only input.
    """
    if value is None:
        return ""
    if pd is not None:
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
    value_str = str(value).strip().lower()
    return value_str


def normalize_deep(value: Any) -> str:
    """Aggressive normalization for fuzzy matching.

    Handles common CRF naming variants:
    - Removes parenthesized content: "不良事件(AE)" -> "不良事件"
    - Removes slashes: "受试者/参与者" -> "受试者参与者"
    - Removes whitespace, underscores, dashes
    - Case-insensitive
    """
    if value is None:
        return ""
    if pd is not None:
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
    s = str(value).strip().lower()
    if not s:
        return ""
    s = _RE_PARENS.sub("", s)
    s = _RE_SLASHES.sub("", s)
    s = _RE_WHITESPACE_PUNCT.sub("", s)
    return s


def fuzzy_score(a: str, b: str) -> float:
    """Compute similarity between two already-deep-normalized strings.

    Uses containment check first (fast path), then SequenceMatcher.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        return len(shorter) / len(longer)
    return SequenceMatcher(None, a, b).ratio()


def build_exact_match_key(
    variable_data: dict[str, Any],
    *,
    columns: Sequence[str] = EXACT_MATCH_COLUMNS,
) -> str:
    """Build a stable ``|||`` delimited key from the exact-match columns.

    Returns an empty string whenever any required column is missing or
    normalises to blank, matching the historical KB contract.
    """
    normalized_values: list[str] = [normalize_exact_match_value(variable_data.get(col, "")) for col in columns]
    if not all(normalized_values):
        return ""
    return "|||".join(normalized_values)


def prepare_match_column(series: pd.Series) -> pd.Series:
    """Sanitise a Series for text-based matching.

    Converts categoricals back to ``object``, fills NaNs with "", strips
    strings, and coerces everything to ``str``.
    """
    if pd is None:  # pragma: no cover - pandas required in practice
        raise RuntimeError("pandas is required for prepare_match_column")
    if series is None:
        return pd.Series(dtype=str)

    if isinstance(series.dtype, pd.CategoricalDtype):
        series = series.astype("object")
    else:
        series = series.astype("object", copy=False)

    series = series.where(series.notna(), "")
    series = series.map(lambda v: v.strip() if isinstance(v, str) else str(v))
    return series.astype(str)


__all__ = [
    "DEEP_NORM_SUFFIX",
    "EXACT_MATCH_COLUMNS",
    "NORMALIZED_SUFFIX",
    "build_exact_match_key",
    "fuzzy_score",
    "normalize_deep",
    "normalize_exact_match_value",
    "prepare_match_column",
]
