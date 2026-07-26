"""Pure scenario, quality, statistical, and gate helpers for SDTM A/B runs."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from src.config.domain_semantic_map import is_valid_domain, strip_supp_prefix
from src.processors.deterministic_validator import SUPPQUAL_VARS, _get_domain_standard_vars

SCENARIO_NON_STANDARD_DOMAIN = "NON_STANDARD_DOMAIN"
SCENARIO_MULTI_DOMAIN = "MULTI_DOMAIN"
SCENARIO_SUPP = "SUPP"
SCENARIO_TESTCD = "TESTCD"
SCENARIO_NOT_SUBMITTED = "NOT_SUBMITTED"

QUALITY_COUNTER_NAMES = (
    "deterministic_validation_errors",
    "illegal_sdtm_variables",
    "illegal_supp_qnam",
    "parse_failures",
    "unmapped_outputs",
    "missing_cascade_provenance",
    "mapping_critic_errors",
)

_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9]{0,7}$")
_DOMAIN_SPLIT_RE = re.compile(r"[|/;]")
_VARIABLE_SPLIT_RE = re.compile(r"[|/;]")
_VALIDATION_FLAGS = (
    "invalid_domain_corrected",
    "variable_name_corrected",
    "variable_name_truncated",
    "domain_prefix_mismatch",
    "non_standard_variable",
    "auto_corrected_to_supp",
)


def classify_scenarios(reference: dict[str, Any]) -> set[str]:
    """Derive diagnostic labels from one metadata-only ground-truth row."""
    domain = str(reference.get("SDTM_Domain", "") or "").strip().upper()
    variable = str(reference.get("SDTM_Variable", "") or "").strip().upper()
    scenarios: set[str] = set()

    if variable == "NOT SUBMITTED":
        scenarios.add(SCENARIO_NOT_SUBMITTED)
        return scenarios

    domain_tokens = [token.strip() for token in _DOMAIN_SPLIT_RE.split(domain) if token.strip()]
    if len(domain_tokens) > 1:
        scenarios.add(SCENARIO_MULTI_DOMAIN)
    if any(not is_valid_domain(strip_supp_prefix(token)) for token in domain_tokens):
        scenarios.add(SCENARIO_NON_STANDARD_DOMAIN)

    if domain.startswith("SUPP") or ("QVAL" in variable and "QNAM=" in variable):
        scenarios.add(SCENARIO_SUPP)
    if "TESTCD=" in variable:
        scenarios.add(SCENARIO_TESTCD)
    return scenarios


def _raw_variable(row: dict[str, Any]) -> str:
    return str(row.get("sdtm_variable") or row.get("ai_variable") or "").strip().upper()


def _row_validation_error(row: dict[str, Any]) -> bool:
    nested = row.get("validation_flags")
    if isinstance(nested, dict) and any(bool(nested.get(flag)) for flag in _VALIDATION_FLAGS):
        return True
    return any(bool(row.get(flag)) for flag in _VALIDATION_FLAGS)


def _standard_variable_is_illegal(row: dict[str, Any]) -> bool:
    source = str(row.get("source", "") or "").upper()
    if source in {"FALLBACK", "UNMAPPED"}:
        return False

    variable = _raw_variable(row)
    if not variable or variable == "NOT SUBMITTED":
        return False
    variable_type = str(row.get("sdtm_variable_type", "") or "standard").lower()
    if variable_type == "supp" or variable == "QVAL":
        return False

    variable = re.split(r"\s+(?:WHEN|IF)\s+", variable, maxsplit=1)[0]
    tokens = [token.strip().split("=", 1)[0] for token in _VARIABLE_SPLIT_RE.split(variable) if token.strip()]
    if not tokens or any(not _TOKEN_RE.fullmatch(token) for token in tokens):
        return True

    domain = str(row.get("domain") or row.get("ai_domain") or "").strip().upper()
    domains = [token.strip() for token in _DOMAIN_SPLIT_RE.split(domain) if token.strip()]
    for index, token in enumerate(tokens):
        target_domain = domains[index] if index < len(domains) else (domains[0] if domains else "")
        standard_vars = _get_domain_standard_vars(strip_supp_prefix(target_domain))
        if standard_vars and token not in standard_vars:
            return True
    return False


def _supp_structure_is_illegal(row: dict[str, Any]) -> bool:
    variable = _raw_variable(row)
    variable_type = str(row.get("sdtm_variable_type", "") or "").lower()
    is_supp = variable_type == "supp" or variable == "QVAL"
    if not is_supp:
        return False

    dataset = str(row.get("supp_dataset", "") or "").strip().upper()
    qnam = str(row.get("supp_variable", "") or "").strip().upper()
    domain = str(row.get("domain") or row.get("ai_domain") or "").strip().upper()
    base_domain = strip_supp_prefix(domain)

    if variable != "QVAL":
        return True
    if not dataset.startswith("SUPP") or (base_domain and dataset != f"SUPP{base_domain}"):
        return True
    if not _TOKEN_RE.fullmatch(qnam) or qnam in SUPPQUAL_VARS:
        return True
    standard_vars = _get_domain_standard_vars(base_domain)
    return bool(standard_vars and qnam in standard_vars)


def count_quality_issues(
    rows: list[dict[str, Any]],
    *,
    consistency_issues: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Count deterministic output issues once per evaluated recommendation."""
    counts: Counter[str] = Counter()
    for row in rows:
        if _row_validation_error(row):
            counts["deterministic_validation_errors"] += 1
        if _standard_variable_is_illegal(row):
            counts["illegal_sdtm_variables"] += 1
        if _supp_structure_is_illegal(row):
            counts["illegal_supp_qnam"] += 1

        source = str(row.get("source", "") or "").upper()
        reason = str(row.get("fallback_reason", "") or "").lower()
        if source == "FALLBACK" and ("parse" in reason or "json" in reason):
            counts["parse_failures"] += 1
        if source == "UNMAPPED":
            counts["unmapped_outputs"] += 1
        if row.get("cascade_level") is None:
            counts["missing_cascade_provenance"] += 1

    unique_critic_errors = {
        json.dumps(issue, ensure_ascii=False, sort_keys=True)
        for issue in consistency_issues or []
        if str(issue.get("severity", "")).lower() == "error"
    }
    counts["mapping_critic_errors"] = len(unique_critic_errors)
    return {name: counts[name] for name in QUALITY_COUNTER_NAMES}
