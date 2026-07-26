"""Pure helpers for normalising SDTM recommendations.

Historically this logic lived inside ``PostprocessMixin`` as methods that
relied on heavy ``self`` state (debug flag, deterministic validator,
semantic maps). That made the core algorithm hard to test and hard to
reuse from the new ``RecommendationOrchestrator``.

The functions here are **pure**: input → output, no I/O, no logging
side-effects. The mixin now delegates to them, and so does the new
:class:`RecommendationNormalizer` service.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

_MULTI_DOMAIN_SPLIT = re.compile(r"[|/;]")
_WHEN_CLAUSE_RE = re.compile(r"\s+when\s+", re.IGNORECASE)
_DOMAIN_TOKEN_SPLIT = re.compile(r"[|/,\s]+")
_QNAM_RE = re.compile(r"^[A-Z][A-Z0-9]{0,7}$")
_NON_QNAM_CHARS_RE = re.compile(r"[^A-Z0-9]")

QVARS: frozenset[str] = frozenset({"QNAM", "QLABEL", "QVAL", "QORIG", "QEVAL", "IDVAR", "IDVARVAL"})

STANDARD_SUFFIXES: frozenset[str] = frozenset(
    {
        "TRT",
        "DECOD",
        "STDTC",
        "ENDTC",
        "DOSE",
        "DOSU",
        "DOSFRM",
        "DOSFRQ",
        "ROUTE",
        "INDC",
        "CAT",
        "SCAT",
        "TERM",
        "PRESP",
        "OCCUR",
        "STAT",
        "LOC",
        "ORRES",
        "ORRESU",
        "STRESC",
        "STRESN",
        "STRESU",
        "DTC",
        "SEQ",
        "SPID",
        "GRPID",
        "REFID",
        "LNKID",
        "TESTCD",
        "TEST",
        "SER",
        "REL",
        "ACN",
        "OUT",
        "TOXGR",
        "ONGO",
        "BODSYS",
        "SOC",
    }
)

COMMENT_KEYWORDS: tuple[str, ...] = (
    "备注",
    "说明",
    "注释",
    "评论",
    "自由文本",
    "其他",
    "其他说明",
    "其他指定",
    "comment",
    "comments",
    "note",
    "notes",
    "remark",
    "remarks",
    "other specify",
    "other",
)


# ---------------------------------------------------------------------------
# Small predicates
# ---------------------------------------------------------------------------
def is_standard_variable_name(name: str | None) -> bool:
    """Return True when ``name`` looks like ``{domain}{SUFFIX}`` (e.g. ``AEOCCUR``)."""
    if not name or len(name) < 3:
        return False
    upper = name.upper()
    if len(upper) < 4 or not upper[:2].isalpha():
        return False
    suffix = upper[2:]
    return suffix in STANDARD_SUFFIXES or any(suffix.endswith(s) for s in STANDARD_SUFFIXES)


def is_comment_like_variable(variable_name: str) -> bool:
    vn = (variable_name or "").lower()
    return any(kw in vn for kw in COMMENT_KEYWORDS)


# ---------------------------------------------------------------------------
# Domain filtering
# ---------------------------------------------------------------------------
def filter_recs_by_domain(
    domain_recs: Sequence[dict[str, Any]],
    target_domain: str | None,
) -> list[dict[str, Any]]:
    """Keep only recommendations whose domain matches ``target_domain``."""
    if not target_domain:
        return list(domain_recs)

    target_upper = target_domain.strip().upper()
    kept: list[dict[str, Any]] = []
    for rec in domain_recs:
        domain_value = str(rec.get("domain", "")).upper()
        tokens: list[str] = []
        for token in _DOMAIN_TOKEN_SPLIT.split(domain_value):
            token = token.strip()
            if not token:
                continue
            tokens.append(token)
            if token.startswith("SUPP") and len(token) > 4:
                tokens.append(token[4:])
        if target_upper in tokens:
            kept.append(rec)
    return kept


# ---------------------------------------------------------------------------
# Compound-clause decomposition  (e.g. "FAORRES when FATESTCD=THDIAG")
# ---------------------------------------------------------------------------
def split_when_expression(value: object) -> tuple[str, list[str]]:
    """Split a legacy ``VARIABLE when CONDITION`` expression without data loss."""
    text = str(value or "").strip()
    if not text:
        return "", []
    parts = _WHEN_CLAUSE_RE.split(text)
    return parts[0].strip(), [part.strip() for part in parts[1:] if part.strip()]


def is_supp_mapping(rec: dict[str, Any]) -> bool:
    """Return whether a recommendation belongs to the SUPP mapping contract."""
    variable_type = str(rec.get("sdtm_variable_type", "") or "").strip().lower()
    plain_variable, _ = split_when_expression(rec.get("sdtm_variable"))
    return variable_type == "supp" or plain_variable.upper() == "QVAL"


def collect_supp_qnam_assignments(
    recommendations: Sequence[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, str]]:
    """Collect SUPP dataset/QNAM assignments using one shared audit scope.

    The nested mapping is ``normalised raw variable -> display raw variable``.
    Runtime MappingCritic and offline release gates both consume this helper,
    so an untyped ``QVAL`` record cannot be counted by only one of them.
    """
    assignments: dict[tuple[str, str], dict[str, str]] = {}
    for rec in recommendations:
        if not is_supp_mapping(rec):
            continue

        dataset = str(rec.get("supp_dataset", "") or "").strip().upper()
        qnam = str(rec.get("supp_variable", "") or "").strip().upper()
        variable_name = str(rec.get("metadata_variable") or rec.get("variable_name") or "").strip()
        if not dataset or not qnam or not variable_name:
            continue

        qnam = qnam.split("=", 1)[0].strip()
        display_names = assignments.setdefault((dataset, qnam), {})
        display_names.setdefault(variable_name.casefold(), variable_name)
    return assignments


def decompose_when_clause(rec: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``rec`` with any ``... when KEY=VAL`` clause expanded."""
    sdtm_var = rec.get("sdtm_variable")
    if not isinstance(sdtm_var, str):
        return dict(rec)

    variable, clauses = split_when_expression(sdtm_var)
    if not clauses:
        return dict(rec)

    out = dict(rec)
    out["sdtm_variable"] = variable
    for clause in clauses:
        if "=" not in clause:
            continue
        key, _, val = clause.partition("=")
        key_upper = key.strip().upper()
        val = val.strip()
        if key_upper.endswith("TESTCD") and not out.get("testcd"):
            out["testcd"] = val
        elif key_upper == "QNAM" and not out.get("supp_variable"):
            out["supp_variable"] = val
    return out


# ---------------------------------------------------------------------------
# Multi-domain resolution
# ---------------------------------------------------------------------------
def resolve_multi_domain(
    rec: dict[str, Any],
    is_valid_domain_fn: Callable[[str], bool],
) -> dict[str, Any]:
    """Pick a single domain/variable pair when the LLM returns ``AE|FA`` style output."""
    domain = str(rec.get("domain", "")).upper()
    sdtm_var = rec.get("sdtm_variable")

    if not _MULTI_DOMAIN_SPLIT.search(domain):
        return dict(rec)

    out = dict(rec)
    domain_parts = [d.strip().upper() for d in _MULTI_DOMAIN_SPLIT.split(domain) if d.strip()]
    original_multi_domain = domain

    chosen = domain_parts[0] if domain_parts else domain
    for candidate in domain_parts:
        if is_valid_domain_fn(candidate):
            chosen = candidate
            break

    if sdtm_var and _MULTI_DOMAIN_SPLIT.search(str(sdtm_var)):
        var_parts = [v.strip() for v in _MULTI_DOMAIN_SPLIT.split(str(sdtm_var)) if v.strip()]
        try:
            domain_idx = domain_parts.index(chosen)
        except ValueError:
            domain_idx = 0
        out["sdtm_variable"] = var_parts[domain_idx] if domain_idx < len(var_parts) else var_parts[0]

    out["domain"] = chosen
    out["original_multi_domain"] = original_multi_domain
    return out


# ---------------------------------------------------------------------------
# Variable-type classification
# ---------------------------------------------------------------------------
def classify_variable_type(
    rec: dict[str, Any],
    variable_name: str,
) -> str:
    """Return ``'supp'`` or ``'standard'`` following the same rules as the mixin."""
    var_type = str(rec.get("sdtm_variable_type", "")).lower()
    domain = str(rec.get("domain", "")).upper()
    sdtm_var = rec.get("sdtm_variable")
    supp_ds = rec.get("supp_dataset")
    supp_var = rec.get("supp_variable")

    if is_comment_like_variable(variable_name) and var_type != "supp":
        return "supp"

    if var_type not in ("standard", "supp"):
        if (
            supp_ds
            or supp_var
            or (isinstance(sdtm_var, str) and sdtm_var.upper() in QVARS)
            or domain.startswith("SUPP")
        ):
            return "supp"
        return "standard"

    if var_type == "standard" and (
        supp_var or (isinstance(sdtm_var, str) and sdtm_var.upper() in QVARS) or domain.startswith("SUPP")
    ):
        return "supp"

    return var_type


# ---------------------------------------------------------------------------
# SUPP contract normalization
# ---------------------------------------------------------------------------
def _stable_qnam_token(raw: object) -> str:
    """Return a legal QNAM token without silently discarding distinguishing text."""
    text = str(raw or "").strip().upper()
    if not text:
        return ""

    compact = _NON_QNAM_CHARS_RE.sub("", text)
    if compact and not compact[0].isalpha():
        compact = f"Q{compact}"

    loses_non_ascii = any(ord(char) > 127 and char.isalnum() for char in text)
    if compact and len(compact) <= 8 and not loses_non_ascii:
        return compact

    prefix = compact[:4] if compact and compact[0].isalpha() else "Q"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest().upper()
    return f"{prefix}{digest[: 8 - len(prefix)]}"


def normalize_supp_record(
    rec: dict[str, Any],
    *,
    variable_name: str,
) -> dict[str, Any]:
    """Return a SUPP recommendation that satisfies the structured contract."""
    from src.processors.deterministic_validator import SUPPQUAL_VARS, _get_domain_standard_vars

    out = dict(rec)
    domain = str(out.get("domain", "") or "").strip().upper()
    base_domain = domain[4:] if domain.startswith("SUPP") else domain
    standard_vars = _get_domain_standard_vars(base_domain)

    def candidate_token(raw: object) -> str:
        return _stable_qnam_token(raw)

    qnam = ""
    candidates = (
        (variable_name, out.get("supp_variable"), out.get("sdtm_variable"))
        if out.get("auto_corrected_to_supp")
        else (out.get("supp_variable"), variable_name, out.get("sdtm_variable"))
    )
    for raw in candidates:
        token = candidate_token(raw)
        if _QNAM_RE.fullmatch(token) and token not in SUPPQUAL_VARS and token not in standard_vars:
            qnam = token
            break

    fallback_token = candidate_token(variable_name)
    if fallback_token in SUPPQUAL_VARS or fallback_token in standard_vars:
        fallback_token = _stable_qnam_token(f"SUPP|{variable_name}")

    out["sdtm_variable"] = "QVAL"
    out["sdtm_variable_type"] = "supp"
    out["supp_variable"] = qnam or fallback_token or "COMMENT"
    if base_domain:
        out["supp_dataset"] = f"SUPP{base_domain}"
    elif not out.get("supp_dataset"):
        out["supp_dataset"] = "SUPPXX"
    return out


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
def dedupe_by_key(
    normalized: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep the highest-scoring rec per ``(domain, sdtm_variable, testcd, supp_variable)``."""
    seen: dict[tuple, int] = {}
    deduped: list[dict[str, Any]] = []
    for rec in normalized:
        key = (
            str(rec.get("domain", "")).upper(),
            str(rec.get("sdtm_variable", "")).upper(),
            str(rec.get("testcd", "")).upper(),
            str(rec.get("supp_variable", "")).upper(),
        )
        score = rec.get("score", 0)
        if key in seen:
            idx = seen[key]
            if score > deduped[idx].get("score", 0):
                deduped[idx] = dict(rec)
        else:
            seen[key] = len(deduped)
            deduped.append(dict(rec))
    return deduped


# ---------------------------------------------------------------------------
# Cleaned-record projection (the mixin's final step)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CleanedRecord:
    domain: str
    sdtm_variable: str
    sdtm_variable_type: str
    score: float
    source: str
    variable_name: str
    testcd: str = ""
    supp_dataset: str = ""
    supp_variable: str = ""


def to_cleaned_dict(rec: dict[str, Any], *, variable_name: str) -> dict[str, Any]:
    """Project ``rec`` to the minimal cleaned form used by downstream consumers."""
    var_type = str(rec.get("sdtm_variable_type", "")).lower()
    base: dict[str, Any] = {
        "domain": rec.get("domain", ""),
        "sdtm_variable": rec.get("sdtm_variable", ""),
        "sdtm_variable_type": rec.get("sdtm_variable_type", ""),
        "score": rec.get("score", 0.9),
        "testcd": rec.get("testcd", ""),
        "source": rec.get("source", ""),
        "variable_name": variable_name,
    }
    if var_type == "supp":
        base["supp_dataset"] = rec.get("supp_dataset", "")
        base["supp_variable"] = rec.get("supp_variable", "")
    return base


__all__ = [
    "COMMENT_KEYWORDS",
    "QVARS",
    "STANDARD_SUFFIXES",
    "CleanedRecord",
    "classify_variable_type",
    "collect_supp_qnam_assignments",
    "decompose_when_clause",
    "dedupe_by_key",
    "filter_recs_by_domain",
    "is_comment_like_variable",
    "is_standard_variable_name",
    "is_supp_mapping",
    "normalize_supp_record",
    "resolve_multi_domain",
    "split_when_expression",
    "to_cleaned_dict",
]
