"""
KB-Derived Hints for LLM Prompt Enrichment
===========================================

Analyses the structured KB (ALS2SDTM_TEST.json or any compatible JSON KB) at
load-time and exposes five types of deterministic prompt hints that reduce
LLM guessing work:

1. **TESTCD pre-filling**   – metadata_variable → TESTCD (99 % stable)
2. **Domain examples**      – domain → [(annotation_variable, SDTM_Variable)]
3. **Disambiguation**       – annotation_variable → {domain → SDTM_Variable}
4. **NOT_SUBMITTED filter** – annotation_table names that are always skipped
5. **MV prefix → domain**   – metadata_variable prefix as auxiliary domain signal
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NOT_SUBMITTED = "NOT SUBMITTED"


def _extract_testcd(sdtm_variable: str) -> str | None:
    """Extract TESTCD / QNAM value from a SDTM_Variable expression."""
    m = re.search(r"(?:TESTCD|QNAM)=(\w+)", sdtm_variable, re.IGNORECASE)
    return m.group(1) if m else None


def _is_simple(sdtm_variable: str) -> bool:
    """True for plain variable names with no conditional expression."""
    return bool(sdtm_variable) and not any(c in sdtm_variable for c in ("=", "|", "/", " "))


class KBDerivedHints:
    """
    Pre-computes prompt enrichment data from a KB record list.

    Parameters
    ----------
    kb_records : list[dict]
        Raw records loaded from the structured KB JSON file.
    min_stable_ratio : float
        Fraction of records that must agree on a value for it to be
        considered "stable" (default 0.80).
    """

    def __init__(
        self,
        kb_records: list[dict[str, Any]],
        min_stable_ratio: float = 0.80,
    ) -> None:
        self._records = kb_records
        self._min_ratio = min_stable_ratio
        self._build()

    # ------------------------------------------------------------------
    # Build phase (called once at init)
    # ------------------------------------------------------------------

    def _build(self) -> None:
        records = self._records
        ratio = self._min_ratio

        # ── 1. metadata_variable → TESTCD ────────────────────────────
        mv_testcd_counts: dict[str, Counter] = defaultdict(Counter)
        mv_domain_counts: dict[str, Counter] = defaultdict(Counter)
        for r in records:
            mv = (r.get("metadata_variable") or "").strip()
            sv = (r.get("SDTM_Variable") or "").strip()
            dom = (r.get("SDTM_Domain") or "").strip().split("|")[0]
            if not mv or sv == _NOT_SUBMITTED:
                continue
            tc = _extract_testcd(sv)
            if tc:
                mv_testcd_counts[mv][tc] += 1
            if dom:
                mv_domain_counts[mv][dom] += 1

        self._mv_testcd: dict[str, str] = {}
        for mv, cnt in mv_testcd_counts.items():
            top_tc, top_n = cnt.most_common(1)[0]
            total = sum(cnt.values())
            if top_n / total >= ratio:
                self._mv_testcd[mv] = top_tc

        # ── 2. metadata_variable prefix (2-chars) → domain ───────────
        mv_prefix_domain: dict[str, Counter] = defaultdict(Counter)
        for mv, cnt in mv_domain_counts.items():
            prefix = mv[:2].upper() if len(mv) >= 2 else mv.upper()
            top_dom, top_n = cnt.most_common(1)[0]
            total = sum(cnt.values())
            if top_n / total >= ratio:
                mv_prefix_domain[prefix][top_dom] += top_n

        self._mv_prefix_domain: dict[str, str] = {}
        for prefix, dom_cnt in mv_prefix_domain.items():
            # Only use prefix→domain if the prefix is unambiguous
            top_dom, top_n = dom_cnt.most_common(1)[0]
            total = sum(dom_cnt.values())
            if top_n / total >= 0.90:  # stricter threshold for prefix inference
                self._mv_prefix_domain[prefix] = top_dom

        # ── 3. annotation_variable disambiguation ─────────────────────
        # Maps: annotation_variable → { domain → [sdtm_variable, ...] }
        av_dom_sv: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
        for r in records:
            av = (r.get("annotation_variable") or "").strip()
            sv = (r.get("SDTM_Variable") or "").strip()
            dom = (r.get("SDTM_Domain") or "").strip().split("|")[0]
            if not av or not sv or not dom or sv == _NOT_SUBMITTED:
                continue
            av_dom_sv[av][dom][sv] += 1

        self._disambiguation: dict[str, dict[str, str]] = {}
        for av, dom_map in av_dom_sv.items():
            # Only include annotation_variables that appear in multiple domains
            if len(dom_map) > 1:
                resolved: dict[str, str] = {}
                for dom, sv_cnt in dom_map.items():
                    top_sv, top_n = sv_cnt.most_common(1)[0]
                    total = sum(sv_cnt.values())
                    if top_n / total >= ratio:
                        resolved[dom] = top_sv
                if resolved:
                    self._disambiguation[av] = resolved

        # ── 4. Domain examples (diverse, representative) ──────────────
        # For each domain, keep up to 12 unique (annotation_variable, SDTM_Variable) pairs
        dom_examples: dict[str, list[tuple[str, str]]] = defaultdict(list)
        seen_per_domain: dict[str, set[str]] = defaultdict(set)
        for r in records:
            av = (r.get("annotation_variable") or "").strip()
            sv = (r.get("SDTM_Variable") or "").strip()
            dom = (r.get("SDTM_Domain") or "").strip().split("|")[0]
            if not av or not sv or not dom or sv == _NOT_SUBMITTED:
                continue
            key = f"{av}||{sv}"
            if key not in seen_per_domain[dom] and len(dom_examples[dom]) < 12:
                seen_per_domain[dom].add(key)
                dom_examples[dom].append((av, sv))
        self._domain_examples: dict[str, list[tuple[str, str]]] = dict(dom_examples)

        # ── 5. NOT_SUBMITTED annotation_tables ───────────────────────
        tbl_not_sub: dict[str, Counter] = defaultdict(Counter)
        for r in records:
            tbl = (r.get("annotation_table") or "").strip()
            sv = (r.get("SDTM_Variable") or "").strip()
            if tbl:
                tbl_not_sub[tbl]["not_sub" if sv == _NOT_SUBMITTED else "mapped"] += 1

        self._not_submitted_tables: set[str] = set()
        for tbl, cnt in tbl_not_sub.items():
            total = sum(cnt.values())
            if cnt.get("not_sub", 0) / total >= 0.90:  # ≥90% NOT_SUBMITTED
                self._not_submitted_tables.add(tbl.lower())

        logger.info(
            "KBDerivedHints built: %d mv→testcd | %d mv_prefix→domain | "
            "%d ambiguous_av | %d domain_example_sets | %d not_submitted_tables",
            len(self._mv_testcd),
            len(self._mv_prefix_domain),
            len(self._disambiguation),
            len(self._domain_examples),
            len(self._not_submitted_tables),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    # ── Direction 1: TESTCD pre-filling ──────────────────────────────

    def get_testcd_hint(self, metadata_variable: str) -> str | None:
        """
        Return the stable TESTCD for *metadata_variable*, or None.

        Example
        -------
        >>> hints.get_testcd_hint("AIMS0101")
        'AIMS0101'
        """
        return self._mv_testcd.get((metadata_variable or "").strip())

    # ── Direction 2: Domain examples ─────────────────────────────────

    def get_domain_examples(
        self,
        domain: str,
        n: int = 5,
        annotation_variable: str = "",
    ) -> list[tuple[str, str]]:
        """
        Return up to *n* (annotation_variable, SDTM_Variable) pairs for *domain*.

        Pairs that share the same annotation_variable as the current one are
        ranked first (most relevant context).

        Returns
        -------
        list of (annotation_variable, sdtm_variable)
        """
        examples = self._domain_examples.get(domain.upper(), [])
        if not examples:
            return []
        av_lower = (annotation_variable or "").strip().lower()

        def score(pair: tuple[str, str]) -> int:
            return 1 if pair[0].lower() == av_lower else 0

        ranked = sorted(examples, key=score, reverse=True)
        return ranked[:n]

    # ── Direction 3: Disambiguation ───────────────────────────────────

    def get_disambiguation_hint(
        self,
        annotation_variable: str,
        domain: str,
    ) -> str | None:
        """
        If *annotation_variable* maps to different SDTM variables across domains
        and *domain* is known, return the correct SDTM_Variable for that domain.

        Example
        -------
        >>> hints.get_disambiguation_hint("检查日期", "LB")
        'LBDTC'
        """
        av = (annotation_variable or "").strip()
        dom = (domain or "").strip().upper()
        if not av or not dom:
            return None
        dom_map = self._disambiguation.get(av)
        if dom_map:
            return dom_map.get(dom)
        return None

    # ── Direction 4: MV prefix → domain ──────────────────────────────

    def get_mv_prefix_domain(self, metadata_variable: str) -> str | None:
        """
        Return the likely SDTM domain inferred from *metadata_variable*'s
        2-character prefix, or None if no stable mapping exists.

        Example
        -------
        >>> hints.get_mv_prefix_domain("AETERM")
        'AE'
        """
        mv = (metadata_variable or "").strip()
        if len(mv) < 2:
            return None
        prefix = mv[:2].upper()
        return self._mv_prefix_domain.get(prefix)

    # ── Direction 6: NOT_SUBMITTED filter ────────────────────────────

    def is_not_submitted_table(self, annotation_table: str) -> bool:
        """
        Return True if ≥90 % of KB records for this table have NOT_SUBMITTED,
        meaning the whole table is typically not mapped to SDTM.

        Example
        -------
        >>> hints.is_not_submitted_table("既往及现病史问询页")
        True
        """
        return (annotation_table or "").strip().lower() in self._not_submitted_tables

    # ------------------------------------------------------------------
    # Prompt section builder (convenience)
    # ------------------------------------------------------------------

    def build_prompt_section(
        self,
        *,
        metadata_variable: str = "",
        annotation_variable: str = "",
        annotation_table: str = "",
        domain: str = "",
        n_examples: int = 4,
    ) -> str:
        """
        Build a compact **KB-Derived Hints** section for injection into the
        LLM prompt.  Returns an empty string if no hints are available.
        """
        lines: list[str] = []

        # TESTCD
        testcd = self.get_testcd_hint(metadata_variable)
        if testcd:
            lines.append(
                f"- **TESTCD pre-fill (KB, high-confidence):** "
                f"`{metadata_variable}` → TESTCD/QNAM = `{testcd}`. "
                f"Use this value in conditional expressions."
            )

        # Disambiguation
        if annotation_variable and domain:
            sv = self.get_disambiguation_hint(annotation_variable, domain)
            if sv:
                lines.append(
                    f"- **Disambiguation:** `{annotation_variable}` in domain `{domain}` "
                    f"→ `{sv}` (context-specific, verified from KB)."
                )

        # Domain examples
        if domain:
            examples = self.get_domain_examples(domain, n=n_examples, annotation_variable=annotation_variable)
            if examples:
                ex_strs = [f"`{av}` → `{sv}`" for av, sv in examples]
                lines.append(f"- **{domain} domain examples (from KB):** " + ", ".join(ex_strs))

        if not lines:
            return ""

        return "\n\n**KB-Derived Hints (high priority):**\n" + "\n".join(lines)

    # ------------------------------------------------------------------
    # Class-level factory
    # ------------------------------------------------------------------

    @classmethod
    def from_json_file(cls, path: str | Path, **kwargs: Any) -> KBDerivedHints:
        """Load KB from a JSON file and return a KBDerivedHints instance."""
        records = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(records, **kwargs)

    @classmethod
    def from_records(
        cls,
        records: list[dict[str, Any]],
        **kwargs: Any,
    ) -> KBDerivedHints:
        """Create from an already-loaded list of dicts."""
        return cls(records, **kwargs)
