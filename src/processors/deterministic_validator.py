"""
Deterministic validation rules for SDTM mapping recommendations.

These rules enforce CDISC constraints regardless of LLM output quality.
Following the ClinAgent principle: "Deterministic Where It Matters" —
variable naming, domain validity, and format rules must be enforced
by code, not by relying on LLM compliance.
"""

import logging
import re
from difflib import SequenceMatcher
from typing import Any

from src.config.domain_semantic_map import (
    DOMAIN_VARIABLES,
    is_valid_domain,
    strip_supp_prefix,
)

logger = logging.getLogger(__name__)

# Variables that belong to SUPPLEMENTAL QUALIFIER datasets (SUPPXX).
# These are cross-context variables validated within SUPPXX, not domain-specific standard vars.
# When encountered in _compute_ig34_check, validation is skipped (not Fail).
SUPPQUAL_VARS: frozenset[str] = frozenset(
    {
        "RDOMAIN",
        "IDVAR",
        "IDVARVAL",
        "QNAM",
        "QLABEL",
        "QVAL",
        "QORIG",
        "QEVAL",
    }
)

# Cross-domain variable prefixes that are valid across multiple domains
CROSS_DOMAIN_PREFIXES = {
    "STUDYID",
    "DOMAIN",
    "USUBJID",
    "SUBJID",
    "RFSTDTC",
    "RFENDTC",
    "RFXSTDTC",
    "RFXENDTC",
    "RFICDTC",
    "RFPENDTC",
    "RFSTRF",
    "RFXSTRF",
    "EPOCH",
    "VISIT",
    "VISITNUM",
    "VISITDY",
}

# Maximum SDTM variable name length (CDISC constraint)
MAX_VARIABLE_NAME_LENGTH = 8

# Sentinel value indicating "variable not mapped" — must be skipped by validation
_NOT_SUBMITTED = "NOT SUBMITTED"

# Compound value separators: "|" = multi-domain, "/" = multi-variable, ";" = legacy
_MULTI_VALUE_RE = re.compile(r"[|/;]")

# Matches a valid SDTM variable name token: 1-8 uppercase ASCII letters/digits
_VAR_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9]{0,7}$")

# Prefix-only variant used to extract a variable token from the beginning of a
# longer expression (e.g. "EGSTAT=未查 IF ...").  No end anchor so anything
# after the token (=, space, Chinese chars, …) is ignored.
_VAR_TOKEN_PREFIX_RE = re.compile(r"^([A-Z][A-Z0-9]{0,7})")


def _extract_variable_names(sdtm_variable: str) -> list[str]:
    """Extract clean SDTM variable name tokens from a (possibly compound) sdtm_variable string.

    Handles the following real-world formats found in output:
      - Simple:            "AETERM"                               -> ["AETERM"]
      - Multi-var (/):     "SVSTDTC/SVENDTC"                     -> ["SVSTDTC", "SVENDTC"]
      - Multi-domain (|):  "DSSTDTC|RFICDTC"                     -> ["DSSTDTC", "RFICDTC"]
      - Assignment (=):    "VSSTAT=未"                            -> ["VSSTAT"]
      - Conditional (if):  "MBSTAT=未查 if [RAW]VIRRES=未查"     -> ["MBSTAT"]
      - Conditional (when):"FAORRES when FATESTCD=XX"            -> ["FAORRES"]
      - NOT SUBMITTED:     returns []

    Returns a list of uppercase variable name strings (≤8 chars, ASCII letters/digits).
    """
    s = sdtm_variable.strip()
    if not s or s.upper() == _NOT_SUBMITTED:
        return []

    # Strip conditional tail: "... if ..." or "... when ..."
    for kw in (" if ", " IF ", " when ", " WHEN "):
        if kw in s:
            s = s.split(kw)[0].strip()
            break

    # Split on multi-domain / multi-variable separators
    if "|" in s:
        parts = [p.strip() for p in s.split("|")]
    elif "/" in s:
        parts = [p.strip() for p in s.split("/")]
    else:
        parts = [s]

    result: list[str] = []
    for part in parts:
        # Take the token before the first "=" (assignment expressions like "VSSTAT=未")
        token = part.split("=")[0].strip().upper() if "=" in part else part.strip().upper()
        # Only accept well-formed SDTM variable names
        if token and _VAR_TOKEN_RE.match(token):
            result.append(token)

    return result


def _extract_qnam_from_supp_variable(supp_variable: str) -> str | None:
    """Extract the QNAM token from a ``supp_variable`` field value.

    The ``supp_variable`` field stores the QNAM **directly** — it IS the QNAM
    (optionally followed by a condition).  Examples::

        "EGSTAT=未查 IF [RAW]EGPERF=否"  -> "EGSTAT"
        "TUCYC"                          -> "TUCYC"
        "ADACOMPT"                       -> "ADACOMPT"
        "123INVALID"                     -> None

    Use :func:`_extract_qnam_from_expression` when parsing the QNAM from a
    composite ``sdtm_variable`` string like ``"QVAL when QNAM=EGSTAT=未查..."``.
    """
    s = supp_variable.strip().upper()
    m = _VAR_TOKEN_PREFIX_RE.match(s)
    return m.group(1) if m else None


def _extract_qnam_from_expression(sdtm_variable: str) -> str | None:
    """Extract the QNAM token from a 'QVAL when QNAM=XXX...' SUPP expression.

    Returns the extracted QNAM as an uppercase string (valid SDTM token format),
    or None if the pattern is not found / the token is not a valid variable name.

    Examples::
        "QVAL when QNAM=EGSTAT=未查 IF [RAW]EGPERF=否"  -> "EGSTAT"
        "QVAL when QNAM=SITENAM"                         -> "SITENAM"
        "QVAL when QNAM=ADACOMPT"                        -> "ADACOMPT"
        "AETERM"                                         -> None
        "QVAL when QNAM=123INVALID"                      -> None
    """
    s = sdtm_variable.upper()
    idx = s.find("QNAM=")
    if idx == -1:
        return None
    after = s[idx + 5 :]  # text that follows "QNAM=", e.g. "EGSTAT=未查 IF ..."
    # Use prefix-only regex — the token ends at the first non-alphanumeric char
    m = _VAR_TOKEN_PREFIX_RE.match(after)
    return m.group(1) if m else None


def _get_domain_standard_vars(domain: str) -> set[str]:
    """Get the set of standard variable names for a domain."""
    base_domain = strip_supp_prefix(domain)

    info = DOMAIN_VARIABLES.get(base_domain, {})
    variables = info.get("variables", [])
    if not variables:
        return set()

    if isinstance(variables[0], dict):
        return {v["variable"].upper() for v in variables if v.get("variable")}
    return {str(v).upper() for v in variables if v}


def find_closest_valid_variable(
    candidate: str,
    domain: str,
    standard_vars: set[str] | None = None,
    min_similarity: float = 0.6,
) -> str | None:
    """Find the closest valid standard variable for a candidate name.

    Instead of simple truncation (which can produce incorrect variable names
    like truncating "AETERMDESC" to "AETERMDE" instead of the correct "AETERM"),
    this function searches the domain's standard variable list for the most
    similar valid variable name.

    Args:
        candidate: The candidate variable name (may be > 8 chars)
        domain: The SDTM domain code (e.g., "AE", "LB")
        standard_vars: Pre-loaded set of standard vars (optional, loaded if not provided)
        min_similarity: Minimum similarity ratio (0-1) to accept a match

    Returns:
        The closest valid variable name, or None if no close match found.
    """
    if not candidate or not domain:
        return None

    candidate_upper = candidate.upper().strip()

    # If already valid length and in standard vars, return as-is
    if len(candidate_upper) <= MAX_VARIABLE_NAME_LENGTH:
        vars_set = standard_vars or _get_domain_standard_vars(domain)
        if candidate_upper in vars_set:
            return candidate_upper

    vars_set = standard_vars or _get_domain_standard_vars(domain)
    if not vars_set:
        return None

    # Strategy 1: Exact prefix match (most common LLM error)
    # LLM often appends suffixes like "DESC", "NAME", "CODE" to valid vars
    common_suffixes = ["DESC", "NAME", "CODE", "TEXT", "VALUE", "LABEL", "NUM", "IND"]
    for suffix in common_suffixes:
        if candidate_upper.endswith(suffix):
            stripped = candidate_upper[: -len(suffix)]
            if len(stripped) <= MAX_VARIABLE_NAME_LENGTH and stripped in vars_set:
                return stripped

    # Strategy 2: Similarity-based matching
    best_match: str | None = None
    best_ratio: float = 0.0

    for var in vars_set:
        # Only consider vars with the same domain prefix (2-char prefix)
        if domain[:2] and not var.startswith(domain[:2]) and var not in CROSS_DOMAIN_PREFIXES:
            continue

        ratio = SequenceMatcher(None, candidate_upper, var).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = var

    if best_match and best_ratio >= min_similarity:
        return best_match

    return None


def validate_variable_domain_consistency(
    domain: str,
    sdtm_variable: str,
) -> tuple[bool, str | None]:
    """Check if a variable name's prefix is consistent with its domain.

    SDTM convention: most domain-specific variables start with the 2-char
    domain code (e.g., AE variables start with "AE", LB variables with "LB").

    Returns:
        (is_consistent, issue_description)
    """
    if not domain or not sdtm_variable:
        return True, None

    domain_upper = domain.upper().strip()
    var_upper = sdtm_variable.upper().strip()

    # Skip cross-domain variables
    if var_upper in CROSS_DOMAIN_PREFIXES:
        return True, None

    # SUPP domains: base domain prefix check
    base_domain = strip_supp_prefix(domain_upper)

    if base_domain and len(var_upper) >= 2:
        var_prefix = var_upper[:2]
        if var_prefix != base_domain[:2]:
            # Some legitimate cross-domain prefixes exist
            legitimate_cross = {
                ("DM", "RF"),
                ("DM", "VS"),
                ("DM", "CM"),
                ("SC", "SC"),  # SC domain vars start with SC
            }
            if (base_domain, var_prefix) not in legitimate_cross:
                return False, f"Variable '{sdtm_variable}' prefix '{var_prefix}' doesn't match domain '{domain}'"

    return True, None


class DeterministicValidator:
    """Applies CDISC rules deterministically to mapping recommendations.

    This validator enforces constraints that LLMs may occasionally violate,
    following the principle that validation rules must be deterministic
    rather than relying on stochastic LLM compliance.
    """

    def __init__(self, debug: bool = False):
        self.debug = debug
        self._domain_vars_cache: dict[str, set[str]] = {}

    def _get_vars(self, domain: str) -> set[str]:
        if domain not in self._domain_vars_cache:
            self._domain_vars_cache[domain] = _get_domain_standard_vars(domain)
        return self._domain_vars_cache[domain]

    def validate_and_correct(
        self,
        rec: dict[str, Any],
        variable_name: str,
        domain_hint: str | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Validate and correct a single recommendation.

        Args:
            rec: The recommendation dict (will be copied, not mutated)
            variable_name: Original CRF variable name
            domain_hint: Inferred domain for fallback

        Returns:
            (corrected_rec, issues_list)
        """
        rec = dict(rec)  # Don't mutate original
        issues: list[str] = []

        domain = str(rec.get("domain", "")).upper().strip()
        sdtm_var = str(rec.get("sdtm_variable", "")).strip()

        if sdtm_var.upper() == _NOT_SUBMITTED:
            return rec, issues

        # KB records may carry compound values ("AE|FA", "AETERM/FAORRES");
        # these are intentional multi-mapping markers, not malformed names.
        if _MULTI_VALUE_RE.search(sdtm_var) or _MULTI_VALUE_RE.search(domain):
            return rec, issues

        # KB-sourced records often contain conditional expressions
        # (e.g. "MBSTAT=未查 if [RAW]VIRRES=未查") that are complete mapping
        # formulas, not simple variable names. Skip length/prefix validation
        # for these to preserve the original KB mapping intact.
        is_from_kb = str(rec.get("source", "")).upper() == "KB" or rec.get("kb_validated")
        if is_from_kb and ("=" in sdtm_var or " if " in sdtm_var.lower()):
            return rec, issues

        # 1. Domain validity
        base_domain = strip_supp_prefix(domain)
        if base_domain and not is_valid_domain(base_domain):
            if domain_hint and is_valid_domain(domain_hint):
                rec["domain"] = domain_hint
                rec["original_domain"] = domain
                rec["invalid_domain_corrected"] = True
                rec["score"] = min(rec.get("score", 0.5) * 0.7, 0.6)
                domain = domain_hint
                issues.append(f"invalid_domain_corrected: {rec.get('original_domain')} → {domain}")
            else:
                issues.append(f"invalid_domain: {domain} (no valid fallback)")

        # 2. Variable name length
        if sdtm_var and len(sdtm_var) > MAX_VARIABLE_NAME_LENGTH:
            standard_vars = self._get_vars(domain)
            closest = find_closest_valid_variable(sdtm_var, domain, standard_vars)

            if closest:
                original = sdtm_var
                rec["sdtm_variable"] = closest
                rec["original_sdtm_variable"] = original
                rec["variable_name_corrected"] = True
                issues.append(f"variable_name_corrected: {original} → {closest}")
            else:
                # No close match found — keep the truncated version as last resort
                truncated = sdtm_var[:MAX_VARIABLE_NAME_LENGTH]
                rec["sdtm_variable"] = truncated
                rec["original_sdtm_variable"] = sdtm_var
                rec["variable_name_truncated"] = True
                rec["score"] = min(rec.get("score", 0.5) * 0.6, 0.5)
                issues.append(f"variable_name_truncated: {sdtm_var} → {truncated} (no close match)")

        # 3. Variable-domain prefix consistency
        sdtm_var = str(rec.get("sdtm_variable", "")).strip()
        is_supp = str(rec.get("sdtm_variable_type", "")).lower() == "supp"
        if sdtm_var and domain and not is_supp:
            is_consistent, desc = validate_variable_domain_consistency(domain, sdtm_var)
            if not is_consistent:
                # Don't auto-correct prefix mismatches — just flag them
                rec["domain_prefix_mismatch"] = True
                rec["prefix_mismatch_detail"] = desc
                issues.append(f"domain_prefix_mismatch: {desc}")

        # 4. Standard variable validation against SDTM IG 3.4
        # Uses _extract_variable_names() to handle compound formats before lookup.
        sdtm_var = str(rec.get("sdtm_variable", "")).strip()
        if rec.get("sdtm_variable_type") == "standard" and sdtm_var and domain:
            extracted = _extract_variable_names(sdtm_var)
            if extracted:
                # Domains may be multi-domain "DS|DM"; pair each extracted var with its domain
                domains_list = [d.strip().upper() for d in domain.split("|") if d.strip()]
                failed_vars: list[str] = []
                for i, var_token in enumerate(extracted):
                    if var_token in CROSS_DOMAIN_PREFIXES:
                        continue
                    target_domain = domains_list[i] if i < len(domains_list) else domains_list[0]
                    base_target = strip_supp_prefix(target_domain)
                    standard_vars = self._get_vars(base_target)
                    # Only flag when the domain has a known variable list (i.e., IG 3.4 coverage)
                    if standard_vars and var_token not in standard_vars:
                        failed_vars.append(var_token)
                if failed_vars:
                    rec["non_standard_variable"] = True
                    rec["non_standard_variable_detail"] = failed_vars
                    issues.append(f"non_standard_variable: {failed_vars} not in IG 3.4 standard vars for {domain}")

        if self.debug and issues:
            print(f"   [DetValidator] {variable_name}: {issues}")

        return rec, issues
