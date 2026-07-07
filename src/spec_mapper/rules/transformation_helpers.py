"""Pure transformation helpers for the spec_mapper rules engine.

Config-driven: callers pass in the relevant option sets rather than
reading from :class:`ConfigLoader`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

NOT_SUBMITTED = "NOT SUBMITTED"


def is_not_submitted(sdtm_variable: str | None) -> bool:
    """Return ``True`` when ``sdtm_variable`` encodes a NOT SUBMITTED marker."""
    if not sdtm_variable:
        return False
    return sdtm_variable.strip().upper() == NOT_SUBMITTED


def extract_supp_additional_condition(condition: str | None) -> str:
    """Return the trailing ``when ...`` clauses after ``QNAM=XXX`` in a SUPP condition.

    Examples
    --------
    >>> extract_supp_additional_condition("when QNAM=MIRESOTH when MITEST=MLH1")
    'when MITEST=MLH1'
    >>> extract_supp_additional_condition("when QNAM=XXX when A=1 and B=2")
    'when A=1 and B=2'
    >>> extract_supp_additional_condition("when QNAM=SITENAM")
    ''
    >>> extract_supp_additional_condition("")
    ''
    """
    if not condition:
        return ""

    parts = re.split(r"(?:^|\s+)when\s+", condition, flags=re.IGNORECASE)
    parts = [p for p in parts if p.strip()]

    if len(parts) < 2:
        return ""

    additional_parts = parts[1:]
    return "when " + " when ".join(additional_parts)


def get_variable_hints(
    target_variable: str | None,
    *,
    qnam_value: str | None = None,
    spid_grpid_patterns: Sequence[str] = ("SPID", "GRPID"),
    spid_grpid_note: str = "",
    dtc_patterns: Sequence[str] = ("DTC",),
    dtc_note: str = "",
) -> list[str]:
    """Return variable-specific hint notes for ``target_variable``/``qnam_value``.

    The SPID/GRPID note is returned when the variable ends with any of
    the SPID/GRPID patterns. The DTC note is returned when the variable
    or the QNAM value ends with any of the DTC patterns. Each hint is
    returned at most once, preserving insertion order.
    """
    hints: list[str] = []
    var_upper = (target_variable or "").upper()
    qnam_upper = (qnam_value or "").upper()

    if spid_grpid_note:
        for pattern in spid_grpid_patterns:
            if pattern and var_upper.endswith(pattern.upper()):
                hints.append(spid_grpid_note)
                break

    if dtc_note:
        for pattern in dtc_patterns:
            pu = (pattern or "").upper()
            if not pu:
                continue
            if var_upper.endswith(pu) or qnam_upper.endswith(pu):
                hints.append(dtc_note)
                break

    return hints


def append_hints_to_transformation(
    transformation: str,
    *,
    extra_hints: Iterable[str] | None = None,
    needs_codelist_hint: bool = False,
    codelist_note: str = "",
) -> str:
    """Append codelist + variable hints to ``transformation``.

    The returned string is a deduplicated, newline-joined sequence of
    hints appended to ``transformation``. If any hint is added, the
    transformation is first terminated with ``;`` to match the existing
    output format.
    """
    hints: list[str] = []

    if needs_codelist_hint and codelist_note:
        hints.append(codelist_note)

    if extra_hints:
        for hint in extra_hints:
            if hint and hint not in hints:
                hints.append(hint)

    if not hints:
        return transformation

    stripped = (transformation or "").rstrip()
    if stripped and not stripped.endswith(";"):
        stripped = stripped + ";"
    return stripped + "\n" + "\n".join(hints)


__all__ = [
    "NOT_SUBMITTED",
    "append_hints_to_transformation",
    "extract_supp_additional_condition",
    "get_variable_hints",
    "is_not_submitted",
]
