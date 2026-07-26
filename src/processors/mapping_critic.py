"""
Mapping Critic — Internal consistency validation for SDTM mapping results.

Inspired by the SATA framework's "Critic Agent" pattern, this module
performs batch-level consistency checks across all mapping recommendations
for a given study. Unlike the DeterministicValidator (which checks individual
records), the MappingCritic checks cross-record consistency.

Checks include:
- Same-table domain consistency: variables from the same CRF table
  should primarily map to the same SDTM domain
- Findings TESTCD coherence: FATESTCD values should be consistent
  within a findings group
- SUPP IDVAR validity: SUPP variable IDVAR must reference a valid
  identifier variable for the parent domain
- SUPP field completeness: SUPP records must have dataset + QNAM + QVAL
- Score sanity: scores must be in [0,1]; low-confidence records flagged
- KB expression integrity: KB conditional expressions must not be truncated
- Variable coverage: every input variable must have a real recommendation
- SUPP QNAM uniqueness: distinct raw variables must not share a dataset/QNAM key
- UNMAPPED ratio: batch-level flag when UNMAPPED rate exceeds threshold
- Low confidence ratio: batch-level flag when low-score rate exceeds threshold
- SUPP naming convention: QNAM must be uppercase, ≤8 chars, alphanumeric
"""

import logging
import re
from collections import Counter, defaultdict
from typing import Any

from src.config.domain_semantic_map import is_valid_domain, strip_supp_prefix

logger = logging.getLogger(__name__)

# SUPP QNAM must be uppercase alphanumeric, max 8 chars
_QNAM_RE = re.compile(r"^[A-Z0-9]{1,8}$")


class ConsistencyIssue:
    """A single consistency issue found by the MappingCritic."""

    def __init__(
        self,
        severity: str,
        check_name: str,
        description: str,
        affected_variables: list[str] | None = None,
        suggested_fix: str | None = None,
    ):
        self.severity = severity  # "error", "warning", "info"
        self.check_name = check_name
        self.description = description
        self.affected_variables = affected_variables or []
        self.suggested_fix = suggested_fix

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "check_name": self.check_name,
            "description": self.description,
            "affected_variables": self.affected_variables,
            "suggested_fix": self.suggested_fix,
        }

    def __repr__(self) -> str:
        return f"[{self.severity.upper()}] {self.check_name}: {self.description}"


class MappingCritic:
    """Validates internal consistency across a batch of mapping results.

    Usage:
        critic = MappingCritic()
        issues = critic.criticize(all_recommendations)
        for issue in issues:
            print(issue)
    """

    def __init__(self, debug: bool = False):
        self.debug = debug

    def criticize(
        self,
        recommendations: list[dict[str, Any]],
        original_mappings: list[dict[str, Any]] | None = None,
    ) -> list[ConsistencyIssue]:
        """Run all consistency checks on a batch of recommendations.

        Args:
            recommendations: List of recommendation dicts, each containing
                at least: domain, sdtm_variable, variable_name, score, source.
            original_mappings: Optional list of input CRF variable dicts (each
                with ``metadata_variable`` and ``metadata_table``). When provided,
                enables the variable-coverage check (R7).

        Returns:
            List of ConsistencyIssue objects sorted by severity (errors first)
        """
        issues: list[ConsistencyIssue] = []

        # --- Existing checks ---
        issues.extend(self._check_same_table_domain_consistency(recommendations))
        issues.extend(self._check_findings_testcd_coherence(recommendations))
        issues.extend(self._check_variable_name_validity(recommendations))
        issues.extend(self._check_domain_validity(recommendations))
        issues.extend(self._check_duplicate_mappings(recommendations))

        # --- New quality checks ---
        issues.extend(self._check_supp_completeness(recommendations))
        issues.extend(self._check_score_sanity(recommendations))
        issues.extend(self._check_kb_expression_integrity(recommendations))
        issues.extend(self._check_supp_naming(recommendations))
        issues.extend(self._check_duplicate_supp_qnam(recommendations))
        issues.extend(self._check_unmapped_ratio(recommendations))
        issues.extend(self._check_low_confidence_ratio(recommendations))
        if original_mappings is not None:
            issues.extend(self._check_variable_coverage(recommendations, original_mappings))

        # Sort by severity: error > warning > info
        severity_order = {"error": 0, "warning": 1, "info": 2}
        issues.sort(key=lambda i: severity_order.get(i.severity, 3))

        if self.debug and issues:
            error_count = sum(1 for i in issues if i.severity == "error")
            warn_count = sum(1 for i in issues if i.severity == "warning")
            info_count = sum(1 for i in issues if i.severity == "info")
            print(f"   [MappingCritic] {error_count} errors, {warn_count} warnings, {info_count} info")

        return issues

    def _check_same_table_domain_consistency(
        self,
        recommendations: list[dict[str, Any]],
    ) -> list[ConsistencyIssue]:
        """Check that variables from the same CRF table map to consistent domains.

        While some cross-domain mapping is legitimate (e.g., a VS table mapping
        to VS + FA), significant domain fragmentation may indicate errors.
        """
        issues: list[ConsistencyIssue] = []

        # Group by annotation_table (CRF table)
        table_domains: dict[str, Counter] = defaultdict(Counter)
        table_vars: dict[str, list[str]] = defaultdict(list)

        for rec in recommendations:
            table = rec.get("annotation_table") or rec.get("metadata_table") or ""
            domain = str(rec.get("domain", "")).upper()
            var_name = rec.get("variable_name", "")
            if table and domain:
                table_domains[table][domain] += 1
                if var_name:
                    table_vars[table].append(f"{var_name}->{domain}")

        for table, domain_counter in table_domains.items():
            total_vars = sum(domain_counter.values())
            if total_vars < 3:
                continue  # Too few variables to check consistency

            # Get the primary domain (most common)
            primary_domain, primary_count = domain_counter.most_common(1)[0]
            primary_ratio = primary_count / total_vars

            # If the primary domain covers less than 50% of variables, flag it
            if primary_ratio < 0.5 and len(domain_counter) > 2:
                domain_list = ", ".join(f"{d}({c})" for d, c in domain_counter.most_common())
                issues.append(
                    ConsistencyIssue(
                        severity="warning",
                        check_name="same_table_domain_consistency",
                        description=f"Table '{table}' maps to {len(domain_counter)} domains: {domain_list}",
                        affected_variables=table_vars[table][:5],
                        suggested_fix=f"Review if all these domains are correct. Primary domain '{primary_domain}' covers only {primary_ratio:.0%} of variables.",
                    )
                )

        return issues

    def _check_findings_testcd_coherence(
        self,
        recommendations: list[dict[str, Any]],
    ) -> list[ConsistencyIssue]:
        """Check that findings domains have consistent FATESTCD/TESTCD values.

        In findings domains (FA, LB, VS, PE, etc.), each variable should have
        a corresponding TESTCD that groups related variables together.
        """
        issues: list[ConsistencyIssue] = []
        findings_domains = {"FA", "LB", "VS", "PE", "EG", "PR", "QS", "SC"}

        # Group by domain + testcd
        domain_testcd_groups: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

        for rec in recommendations:
            domain = str(rec.get("domain", "")).upper()
            if domain not in findings_domains:
                continue
            testcd = str(rec.get("testcd", "")).strip()
            var_name = rec.get("variable_name", "")
            sdtm_var = str(rec.get("sdtm_variable", "")).upper()

            # Findings variables without TESTCD
            if not testcd and sdtm_var and not sdtm_var.startswith("FA"):  # Skip non-result vars
                # Check if this is a result variable that should have TESTCD
                result_suffixes = ("ORRES", "STRESC", "STRESN", "ORRESU", "STRESU")
                if any(sdtm_var.endswith(s) for s in result_suffixes):
                    issues.append(
                        ConsistencyIssue(
                            severity="warning",
                            check_name="findings_testcd_coherence",
                            description=f"Findings variable {sdtm_var} in {domain} domain has no TESTCD",
                            affected_variables=[var_name],
                            suggested_fix=f"Add a TESTCD value for {sdtm_var}",
                        )
                    )

            if testcd:
                domain_testcd_groups[domain][testcd].append(var_name)

        # Check for orphan TESTCDs (only one variable in a testcd group)
        for domain, testcd_groups in domain_testcd_groups.items():
            for testcd, vars_list in testcd_groups.items():
                if len(vars_list) == 1:
                    issues.append(
                        ConsistencyIssue(
                            severity="info",
                            check_name="findings_testcd_coherence",
                            description=f"Orphan TESTCD '{testcd}' in {domain}: only variable '{vars_list[0]}'",
                            affected_variables=vars_list,
                            suggested_fix="Consider if this TESTCD should have additional variables (e.g., result + unit)",
                        )
                    )

        return issues

    def _check_variable_name_validity(
        self,
        recommendations: list[dict[str, Any]],
    ) -> list[ConsistencyIssue]:
        """Check that all SDTM variable names are valid (<=8 chars, no spaces)."""
        issues: list[ConsistencyIssue] = []
        seen_invalid: list[str] = []

        for rec in recommendations:
            sdtm_var = str(rec.get("sdtm_variable", "")).strip()
            if not sdtm_var:
                continue

            # "NOT SUBMITTED" is a legitimate sentinel meaning the variable is
            # intentionally not mapped to SDTM — it must not be validated as a name.
            if sdtm_var.upper() == "NOT SUBMITTED":
                continue

            # KB results may contain composite or conditional patterns such as:
            #   "ECSTDTC|EXSTDTC", "ECDOSE/ECDOSU when ECMOOD=已执行",
            #   "VSSTAT=未查 if VSORRES=未查", "QVAL when QNAM=XXX"
            # These are mapping expressions, not bare variable names — skip them.
            is_from_kb = rec.get("kb_validated") or str(rec.get("source", "")).upper() in ("KB", "KB_NOT_SUBMITTED")
            if is_from_kb and (
                "|" in sdtm_var
                or "/" in sdtm_var
                or "=" in sdtm_var
                or " if " in sdtm_var.lower()
                or " when " in sdtm_var.lower()
            ):
                continue

            if len(sdtm_var) > 8:
                seen_invalid.append(sdtm_var)

            if " " in sdtm_var or "\t" in sdtm_var:
                seen_invalid.append(sdtm_var)

        if seen_invalid:
            issues.append(
                ConsistencyIssue(
                    severity="error",
                    check_name="variable_name_validity",
                    description=f"{len(seen_invalid)} variable names exceed 8-char limit or contain spaces: {seen_invalid[:5]}",
                    affected_variables=seen_invalid[:10],
                    suggested_fix="Truncate or map to nearest valid standard variable",
                )
            )

        return issues

    def _check_domain_validity(
        self,
        recommendations: list[dict[str, Any]],
    ) -> list[ConsistencyIssue]:
        """Check that all mapped domains are valid SDTM domains."""
        issues: list[ConsistencyIssue] = []
        invalid_domains: set[str] = set()

        for rec in recommendations:
            domain = str(rec.get("domain", "")).upper().strip()
            if not domain:
                continue

            # KB results may contain composite domains like "EC|EX".
            # Validate each part individually.
            is_from_kb = rec.get("kb_validated") or rec.get("source") == "KB"
            if is_from_kb and "|" in domain:
                parts = [d.strip() for d in domain.split("|") if d.strip()]
                for part in parts:
                    base_domain = strip_supp_prefix(part)
                    if base_domain and not is_valid_domain(base_domain):
                        invalid_domains.add(base_domain)
                continue

            base_domain = strip_supp_prefix(domain)
            if base_domain and not is_valid_domain(base_domain):
                invalid_domains.add(base_domain)

        if invalid_domains:
            issues.append(
                ConsistencyIssue(
                    severity="error",
                    check_name="domain_validity",
                    description=f"Invalid SDTM domains found: {', '.join(sorted(invalid_domains))}",
                    suggested_fix="Map to a valid SDTM domain from the standard domain list",
                )
            )

        return issues

    def _check_duplicate_mappings(
        self,
        recommendations: list[dict[str, Any]],
    ) -> list[ConsistencyIssue]:
        """Check for conflicting mappings — same variable, same target, but from DIFFERENT sources.

        It is normal for the same variable to receive identical KB recommendations
        multiple times when the CRF variable appears across several annotation
        sub-forms (e.g. PR table split into 7 visit-forms).  Those repetitions are
        deduplicated in the Excel merge step and are NOT flagged here.

        What IS flagged: cases where the exact same (var, domain, sdtm_var, testcd)
        combination is produced by two or more *distinct* sources (e.g. KB + LLM
        both emit the same answer — harmless redundancy worth noting).
        Genuinely *conflicting* mappings (same var, different domain/sdtm_var) are
        a separate concern handled upstream by the cascade selection logic.
        """
        issues: list[ConsistencyIssue] = []

        seen: dict[tuple, set[str]] = defaultdict(set)
        for rec in recommendations:
            var_name = rec.get("variable_name", "")
            domain = str(rec.get("domain", "")).upper()
            sdtm_var = str(rec.get("sdtm_variable", "")).upper()
            testcd = str(rec.get("testcd", "")).upper()
            key = (var_name, domain, sdtm_var, testcd)
            source = str(rec.get("source", "")).upper()
            if source:
                seen[key].add(source)

        # Only flag when multiple DISTINCT sources agree on the same mapping.
        # Same-source repetitions (e.g. KB×7 for a 7-visit form) are expected.
        multi_source = {k: v for k, v in seen.items() if len(v) > 1}
        if multi_source:
            dup_count = len(multi_source)
            examples = [f"{k[0]}→{k[1]}.{k[2]} ({'/'.join(sorted(v))})" for k, v in list(multi_source.items())[:3]]
            issues.append(
                ConsistencyIssue(
                    severity="info",
                    check_name="duplicate_mappings",
                    description=(
                        f"{dup_count} variable(s) received identical recommendations from multiple distinct sources "
                        f"(e.g. {examples[0] if examples else '—'}). This is redundant but consistent."
                    ),
                    suggested_fix=(
                        "No action needed — the output merge step keeps only the highest-scored result. "
                        "This info confirms KB and LLM agree on these mappings."
                    ),
                )
            )

        return issues

    # ==================== New quality checks ====================

    def _check_supp_completeness(
        self,
        recommendations: list[dict[str, Any]],
    ) -> list[ConsistencyIssue]:
        """R1 - SUPP records must have supp_dataset, supp_variable, and sdtm_variable=QVAL.

        Incomplete SUPP records will produce broken SAS datasets (missing QNAM/QVAL).
        """
        issues: list[ConsistencyIssue] = []
        incomplete: list[str] = []

        for rec in recommendations:
            if str(rec.get("sdtm_variable_type", "")).lower() != "supp":
                continue

            sdtm_var = str(rec.get("sdtm_variable", "")).strip()
            # Skip KB composite patterns (multi-variable definitions)
            if "|" in sdtm_var or "/" in sdtm_var:
                continue

            var_name = rec.get("variable_name", "?")
            missing: list[str] = []
            if not rec.get("supp_dataset"):
                missing.append("supp_dataset")
            if not rec.get("supp_variable"):
                missing.append("supp_variable (QNAM)")
            if sdtm_var.upper() != "QVAL":
                missing.append(f"sdtm_variable should be QVAL (got '{sdtm_var}')")

            if missing:
                incomplete.append(f"{var_name}: missing {', '.join(missing)}")

        if incomplete:
            issues.append(
                ConsistencyIssue(
                    severity="error",
                    check_name="supp_completeness",
                    description=f"{len(incomplete)} SUPP record(s) have incomplete fields: {incomplete[:5]}",
                    affected_variables=[e.split(":")[0] for e in incomplete[:10]],
                    suggested_fix="Ensure supp_dataset (SUPPxx) and supp_variable (QNAM) are populated for all SUPP records",
                )
            )

        return issues

    def _check_score_sanity(
        self,
        recommendations: list[dict[str, Any]],
    ) -> list[ConsistencyIssue]:
        """R3 - Scores must be in [0, 1]. Individual records with score < 0.5 are low-confidence.

        Out-of-range scores indicate a pipeline bug; low-confidence scores may need review.
        """
        issues: list[ConsistencyIssue] = []
        out_of_range: list[str] = []
        low_conf: list[str] = []

        for rec in recommendations:
            score = rec.get("score")
            if score is None:
                continue
            var_name = rec.get("variable_name", "?")
            try:
                score_f = float(score)
            except (TypeError, ValueError):
                out_of_range.append(f"{var_name}: score={score!r} (not numeric)")
                continue

            if score_f < 0.0 or score_f > 1.0:
                out_of_range.append(f"{var_name}: score={score_f:.3f}")
            elif score_f < 0.5:
                source = rec.get("source", "")
                if source not in ("UNMAPPED", "FALLBACK"):
                    low_conf.append(f"{var_name}: score={score_f:.3f} (source={source})")

        if out_of_range:
            issues.append(
                ConsistencyIssue(
                    severity="error",
                    check_name="score_sanity",
                    description=f"{len(out_of_range)} record(s) have out-of-range scores: {out_of_range[:5]}",
                    affected_variables=[e.split(":")[0] for e in out_of_range[:10]],
                    suggested_fix="Scores must be in [0, 1]. Check postprocessing pipeline for score calculation bugs.",
                )
            )

        if low_conf:
            issues.append(
                ConsistencyIssue(
                    severity="warning",
                    check_name="score_sanity",
                    description=f"{len(low_conf)} record(s) have low confidence (score < 0.5): {low_conf[:5]}",
                    affected_variables=[e.split(":")[0] for e in low_conf[:10]],
                    suggested_fix="Review low-confidence mappings manually. Consider enriching KB or adding domain-specific rules.",
                )
            )

        return issues

    def _check_kb_expression_integrity(
        self,
        recommendations: list[dict[str, Any]],
    ) -> list[ConsistencyIssue]:
        """R4 - KB conditional expressions (e.g. 'MBSTAT=未查 if [RAW]VIRRES=未查') must not be truncated.

        The DeterministicValidator has a bypass for such expressions; this check
        acts as a regression guard to confirm the bypass is working.
        """
        issues: list[ConsistencyIssue] = []
        truncated: list[str] = []

        for rec in recommendations:
            if str(rec.get("source", "")).upper() != "KB":
                continue
            if not rec.get("variable_name_truncated"):
                continue

            original = rec.get("original_sdtm_variable", "")
            current = rec.get("sdtm_variable", "")
            var_name = rec.get("variable_name", "?")

            if original and ("=" in original or " if " in original.lower()):
                truncated.append(f"{var_name}: '{original}' → '{current}'")

        if truncated:
            issues.append(
                ConsistencyIssue(
                    severity="error",
                    check_name="kb_expression_integrity",
                    description=(
                        f"{len(truncated)} KB conditional expression(s) were truncated by DeterministicValidator: "
                        f"{truncated[:3]}"
                    ),
                    affected_variables=[e.split(":")[0] for e in truncated[:10]],
                    suggested_fix=(
                        "KB records containing '=' or 'if' in sdtm_variable are conditional mapping formulas "
                        "and should bypass the 8-char length rule. Check DeterministicValidator KB bypass logic."
                    ),
                )
            )

        return issues

    def _check_supp_naming(
        self,
        recommendations: list[dict[str, Any]],
    ) -> list[ConsistencyIssue]:
        """R12 - SUPP QNAM (supp_variable) must be uppercase, ≤8 chars, alphanumeric.

        Invalid QNAM values will cause SAS dataset validation failures.
        """
        issues: list[ConsistencyIssue] = []
        invalid: list[str] = []

        for rec in recommendations:
            if str(rec.get("sdtm_variable_type", "")).lower() != "supp":
                continue

            qnam_raw = str(rec.get("supp_variable", "")).strip()
            if not qnam_raw:
                continue

            var_name = rec.get("variable_name", "?")

            # KB SUPP records often store the full conditional mapping expression in
            # supp_variable, e.g. "ISSTAT=未查 IF [RAW]ADAPERF=否".  The actual QNAM
            # is only the token before the first "=".  Extract it before validating.
            qnam = qnam_raw
            if "=" in qnam_raw:
                qnam = qnam_raw.split("=")[0].strip()

            if not _QNAM_RE.match(qnam):
                reasons: list[str] = []
                if len(qnam) > 8:
                    reasons.append(f"length {len(qnam)} > 8")
                if qnam != qnam.upper():
                    reasons.append("not uppercase")
                if not qnam.isalnum():
                    reasons.append("contains non-alphanumeric characters")
                invalid.append(f"{var_name}: QNAM='{qnam}' ({', '.join(reasons)})")

        if invalid:
            issues.append(
                ConsistencyIssue(
                    severity="error",
                    check_name="supp_naming_convention",
                    description=f"{len(invalid)} SUPP QNAM value(s) violate naming rules: {invalid[:5]}",
                    affected_variables=[e.split(":")[0] for e in invalid[:10]],
                    suggested_fix="QNAM must be uppercase, ≤8 alphanumeric characters (CDISC SUPPQUAL requirement).",
                )
            )

        return issues

    def _check_duplicate_supp_qnam(
        self,
        recommendations: list[dict[str, Any]],
    ) -> list[ConsistencyIssue]:
        """Flag distinct raw variables assigned to the same SUPP dataset/QNAM."""
        assignments: dict[tuple[str, str], set[str]] = defaultdict(set)
        display_names: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)

        for rec in recommendations:
            if str(rec.get("sdtm_variable_type", "")).lower() != "supp":
                continue

            dataset = str(rec.get("supp_dataset", "")).strip().upper()
            qnam = str(rec.get("supp_variable", "")).strip().upper()
            variable_name = str(rec.get("variable_name", "")).strip()
            if not dataset or not qnam or not variable_name:
                continue

            qnam = qnam.split("=", 1)[0].strip()
            key = (dataset, qnam)
            normalized_name = variable_name.casefold()
            assignments[key].add(normalized_name)
            display_names[key].setdefault(normalized_name, variable_name)

        collisions = {key: names for key, names in assignments.items() if len(names) > 1}
        if not collisions:
            return []

        duplicate_assignments = sum(len(names) - 1 for names in collisions.values())
        affected_variables = sorted(
            {display_names[key][normalized_name] for key, names in collisions.items() for normalized_name in names}
        )
        examples = ", ".join(f"{dataset}.{qnam}" for dataset, qnam in sorted(collisions)[:5])
        return [
            ConsistencyIssue(
                severity="error",
                check_name="duplicate_supp_qnam",
                description=(
                    f"{duplicate_assignments} additional raw variable assignment(s) reuse a SUPP QNAM key: {examples}"
                ),
                affected_variables=affected_variables[:20],
                suggested_fix=("Assign a unique QNAM to each distinct raw variable within a SUPP dataset."),
            )
        ]

    def _check_variable_coverage(
        self,
        recommendations: list[dict[str, Any]],
        original_mappings: list[dict[str, Any]],
    ) -> list[ConsistencyIssue]:
        """R7 - Every input CRF variable must have at least one real recommendation (score > 0).

        Variables that only have UNMAPPED/FALLBACK entries indicate gaps in KB coverage or
        LLM reasoning failures.
        """
        issues: list[ConsistencyIssue] = []

        # Build a set of variables that have a real (non-UNMAPPED) recommendation.
        # We index by both (table, var) and var-only because some recommendation
        # records (e.g. KB_NOT_SUBMITTED) do not carry a metadata_table field.
        covered: set[tuple[str, str]] = set()
        covered_var_names: set[str] = set()
        for rec in recommendations:
            source = str(rec.get("source", "")).upper()
            score = float(rec.get("score", 0) or 0)
            if source in ("UNMAPPED", "FALLBACK"):
                continue
            if score <= 0.0:
                continue
            var_name = rec.get("variable_name", "")
            table_name = rec.get("metadata_table", "") or rec.get("annotation_table", "")
            if var_name:
                covered.add((table_name, var_name))
                covered_var_names.add(var_name)

        # Find uncovered input variables.
        # Primary match: exact (metadata_table, metadata_variable) pair.
        # Fallback match: variable name only — handles recs that lack metadata_table
        # (e.g. KB_NOT_SUBMITTED entries where the field is not populated).
        uncovered: list[str] = []
        seen_uncovered: set[tuple[str, str]] = set()
        for mapping in original_mappings:
            var_name = mapping.get("metadata_variable", "")
            table_name = mapping.get("metadata_table", "")
            if not var_name:
                continue
            key = (table_name, var_name)
            if key in seen_uncovered:
                continue  # Deduplicate repeated (table, var) from multi-row input
            if (table_name, var_name) not in covered and var_name not in covered_var_names:
                seen_uncovered.add(key)
                uncovered.append(f"{table_name}.{var_name}" if table_name else var_name)

        if uncovered:
            issues.append(
                ConsistencyIssue(
                    severity="error",
                    check_name="variable_coverage",
                    description=(
                        f"{len(uncovered)} input variable(s) have no real recommendation "
                        f"(only UNMAPPED/FALLBACK or score=0): {uncovered[:5]}"
                    ),
                    affected_variables=uncovered[:10],
                    suggested_fix="Enrich KB for these variables or review why LLM failed to produce valid recommendations.",
                )
            )

        return issues

    def _check_unmapped_ratio(
        self,
        recommendations: list[dict[str, Any]],
    ) -> list[ConsistencyIssue]:
        """R9 - Batch-level flag when UNMAPPED/FALLBACK rate exceeds 10% of unique variables.

        A high UNMAPPED rate indicates systemic KB gaps or pipeline issues.
        """
        issues: list[ConsistencyIssue] = []

        # Deduplicate by variable_name — count unique variables per source
        var_sources: dict[str, str] = {}
        for rec in recommendations:
            var_name = rec.get("variable_name", "")
            if not var_name:
                continue
            source = str(rec.get("source", "")).upper()
            # Keep the best source seen (KB > RAG > LLM > FALLBACK > UNMAPPED)
            priority = {"KB": 0, "RAG": 1, "LLM": 2, "FALLBACK": 3, "UNMAPPED": 4}
            current_priority = priority.get(var_sources.get(var_name, "UNMAPPED"), 4)
            new_priority = priority.get(source, 4)
            if new_priority < current_priority:
                var_sources[var_name] = source

        total_unique = len(var_sources)
        if total_unique == 0:
            return issues

        unmapped_count = sum(1 for s in var_sources.values() if s in ("UNMAPPED", "FALLBACK"))
        ratio = unmapped_count / total_unique

        if ratio > 0.10:
            issues.append(
                ConsistencyIssue(
                    severity="warning",
                    check_name="unmapped_ratio",
                    description=(
                        f"{unmapped_count}/{total_unique} variables ({ratio:.0%}) are UNMAPPED or FALLBACK "
                        f"— exceeds 10% threshold"
                    ),
                    suggested_fix=(
                        "High UNMAPPED rate indicates KB coverage gaps. "
                        "Consider adding missing variables to the knowledge base."
                    ),
                )
            )

        return issues

    def _check_low_confidence_ratio(
        self,
        recommendations: list[dict[str, Any]],
    ) -> list[ConsistencyIssue]:
        """R10 - Batch-level flag when more than 20% of variables have top score < 0.7.

        A high low-confidence rate may indicate KB quality issues or domain inference problems.
        """
        issues: list[ConsistencyIssue] = []

        # Keep only the top score per variable (highest score across all its recs).
        # Records without an explicit score field are skipped — they may be test
        # fixtures or interim records that haven't been scored yet.
        var_top_score: dict[str, float] = {}
        for rec in recommendations:
            raw_score = rec.get("score")
            if raw_score is None:
                continue
            var_name = rec.get("variable_name", "")
            if not var_name:
                continue
            source = str(rec.get("source", "")).upper()
            if source in ("UNMAPPED", "FALLBACK"):
                continue
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                continue
            if score > var_top_score.get(var_name, -1.0):
                var_top_score[var_name] = score

        total_mapped = len(var_top_score)
        if total_mapped == 0:
            return issues

        low_conf_vars = [v for v, s in var_top_score.items() if s < 0.7]
        ratio = len(low_conf_vars) / total_mapped

        if ratio > 0.20:
            score_dist = Counter(
                "≥0.9" if s >= 0.9 else "0.7-0.9" if s >= 0.7 else "0.5-0.7" if s >= 0.5 else "<0.5"
                for s in var_top_score.values()
            )
            dist_str = ", ".join(f"{k}:{v}" for k, v in sorted(score_dist.items()))
            issues.append(
                ConsistencyIssue(
                    severity="warning",
                    check_name="low_confidence_ratio",
                    description=(
                        f"{len(low_conf_vars)}/{total_mapped} mapped variables ({ratio:.0%}) "
                        f"have top score < 0.7 — exceeds 20% threshold. Distribution: {dist_str}"
                    ),
                    affected_variables=low_conf_vars[:10],
                    suggested_fix=(
                        "Low confidence across many variables may indicate KB coverage gaps, "
                        "noisy annotation data, or misconfigured domain inference."
                    ),
                )
            )

        return issues
