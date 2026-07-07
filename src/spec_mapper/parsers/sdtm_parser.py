"""
SDTM Variable Parser

Unified parser for SDTM variable patterns with proper handling of:
- | separator (cross-domain mappings)
- / separator (multi-variable within same domain)
- Conditional patterns (when clauses)
- SUPP patterns (QVAL when QNAM=XXX)

Parsing priority:
1. Clean whitespace
2. Split by | (cross-domain)
3. Split by / (multi-variable)
4. Classify each variable (Simple/SUPP/Conditional)
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class MappingType(Enum):
    """Types of SDTM variable mappings."""

    SIMPLE = "simple"  # Direct 1:1 mapping
    SUPP = "supp"  # QVAL when QNAM=XXX
    CONDITIONAL = "conditional"  # XXORRES when XXTESTCD=YYY or XXTEST=YYY
    CONDITIONAL_OTHER = "conditional_other"  # ECDOSE when ECMOOD=已执行 (non-TEST/TESTCD)
    ASSIGNMENT = "assignment"  # Variable A = xxxx (fixed value assignment)


class ConditionType(Enum):
    """Types of conditional patterns for CONDITIONAL mappings."""

    TESTCD = "testcd"  # when XXTESTCD=YYY
    TEST = "test"  # when XXTEST=YYY
    OTHER = "other"  # when OTHER_VAR=VALUE (non-TEST/TESTCD)


@dataclass
class ParsedMapping:
    """Represents a single parsed SDTM variable mapping.

    Attributes:
        domain: Target SDTM domain (e.g., "DM", "CM")
        variable: Target SDTM variable name (e.g., "CMTRT", "QVAL")
        mapping_type: Type of mapping (Simple/SUPP/Conditional/Conditional_Other/Assignment)
        condition: Optional condition clause (e.g., 'when PRDYN="1"')
        original_variable: Original unparsed variable string
        qnam_value: For SUPP mappings, the QNAM value
        test_condition: For conditional mappings, the test condition (VAR, VALUE)
        condition_type: For conditional mappings, the type (TESTCD/TEST/OTHER)
        assignment_value: For ASSIGNMENT mappings, the value after "="
    """

    domain: str
    variable: str
    mapping_type: MappingType
    condition: str | None = None
    original_variable: str = ""
    qnam_value: str | None = None  # For SUPP: QNAM value
    test_condition: tuple[str, str] | None = None  # For Conditional: (COND_VAR, VALUE)
    condition_type: ConditionType | None = None  # For Conditional: TESTCD/TEST/OTHER
    assignment_value: str | None = None  # For ASSIGNMENT: value after "="

    def __post_init__(self):
        """Post-initialization processing."""
        # Extract QNAM value for SUPP mappings
        if self.mapping_type == MappingType.SUPP and self.condition:
            self.qnam_value = self._extract_qnam_value(self.condition)

        # Extract test condition for conditional mappings
        if self.mapping_type == MappingType.CONDITIONAL and self.condition:
            self.test_condition, self.condition_type = self._extract_test_condition_with_type(self.condition)

        # For CONDITIONAL_OTHER, also extract condition info
        if self.mapping_type == MappingType.CONDITIONAL_OTHER and self.condition:
            self.test_condition = self._extract_first_condition(self.condition)
            self.condition_type = ConditionType.OTHER

        # For ASSIGNMENT, the condition field holds the assignment value
        if self.mapping_type == MappingType.ASSIGNMENT and self.condition:
            self.assignment_value = self.condition
            self.condition = None

    @staticmethod
    def _extract_qnam_value(condition: str) -> str | None:
        """Extract QNAM value from condition.

        Examples:
            "when QNAM=SITENAM" -> "SITENAM"
            "when QNAM = SUBJINIT" -> "SUBJINIT"
        """
        match = re.search(r"QNAM\s*=\s*([^\s,]+)", condition, re.IGNORECASE)
        if match:
            return match.group(1).strip().strip("\"'")
        return None

    @staticmethod
    def _extract_test_condition_with_type(condition: str) -> tuple[tuple[str, str] | None, ConditionType | None]:
        """Extract test condition and determine condition type.

        Examples:
            "when FATESTCD=THDIA" -> (("FATESTCD", "THDIA"), ConditionType.TESTCD)
            "when MITEST=MSI and MINAM=本地实验室" -> (("MITEST", "MSI"), ConditionType.TEST)

        Returns:
            Tuple of (condition_tuple, condition_type)
        """
        # Match first condition variable=value
        match = re.search(r"when\s+(\w+)\s*=\s*([^\s]+?)(?:\s+and\s+|\s*$)", condition, re.IGNORECASE)
        if match:
            var = match.group(1).upper()
            val = match.group(2).strip().strip("\"'")

            # Determine condition type based on variable name
            if var.endswith("TESTCD"):
                return ((var, val), ConditionType.TESTCD)
            elif var.endswith("TEST"):
                return ((var, val), ConditionType.TEST)
            else:
                # This shouldn't happen for CONDITIONAL type, but handle gracefully
                return ((var, val), ConditionType.OTHER)
        return (None, None)

    @staticmethod
    def _extract_first_condition(condition: str) -> tuple[str, str] | None:
        """Extract first condition from a when clause (for OTHER type).

        Examples:
            "when ECMOOD=已执行" -> ("ECMOOD", "已执行")
            "when RSSCAT=病理评估" -> ("RSSCAT", "病理评估")
        """
        match = re.search(r"when\s+(\w+)\s*=\s*([^\s]+?)(?:\s+and\s+|\s*$)", condition, re.IGNORECASE)
        if match:
            var = match.group(1).upper()
            val = match.group(2).strip().strip("\"'")
            return (var, val)
        return None

    def get_transformation_suffix(self) -> str:
        """Get the suffix to append to transformation definition.

        Returns:
            Empty string for Simple and SUPP mappings.
            Condition clause for Conditional mappings only.

        Note:
            For SUPP mappings, the QNAM value is already captured in the
            SUPPRecord.variable_name field, so no suffix is needed.
        """
        if self.mapping_type == MappingType.SIMPLE:
            return ""
        elif self.mapping_type == MappingType.SUPP:
            # SUPP mappings don't need the "when QNAM=XXX" suffix
            # because QNAM is already in the SUPPRecord structure
            return ""
        elif self.condition:
            return f" {self.condition}"
        return ""


def clean_sdtm_variable(sdtm_variable: str) -> str:
    """Clean SDTM_Variable string.

    Removes:
    - Leading/trailing whitespace
    - Multiple consecutive spaces -> single space
    - Spaces around = sign

    Args:
        sdtm_variable: Raw SDTM variable string

    Returns:
        Cleaned string

    Examples:
        >>> clean_sdtm_variable("QVAL  when  QNAM= AOPR")
        'QVAL when QNAM=AOPR'
        >>> clean_sdtm_variable("CMTRT   when   PRDYN = \\"1\\"")
        'CMTRT when PRDYN="1"'
    """
    if not sdtm_variable:
        return sdtm_variable

    # Remove leading/trailing whitespace
    cleaned = sdtm_variable.strip()

    # Replace multiple consecutive spaces with single space
    cleaned = re.sub(r"\s+", " ", cleaned)

    # Remove spaces around = sign
    cleaned = re.sub(r"\s*=\s*", "=", cleaned)

    return cleaned


class SDTMVariableParser:
    """Parser for SDTM variable patterns.

    Handles complex patterns with proper precedence:
    1. | separator (cross-domain)
    2. / separator (multi-variable)
    3. Pattern classification (Simple/SUPP/Conditional)

    Examples:
        >>> parser = SDTMVariableParser()
        >>> mappings = parser.parse("DM|DS", "AAA/BBB|CCC/DDD")
        >>> # Returns: [DM.AAA, DM.BBB, DS.CCC, DS.DDD]
    """

    def parse(self, sdtm_domain: str, sdtm_variable: str) -> list[ParsedMapping]:
        """Parse SDTM domain and variable strings into individual mappings.

        Args:
            sdtm_domain: SDTM domain string (may contain |)
            sdtm_variable: SDTM variable string (may contain |, /)

        Returns:
            List of ParsedMapping objects

        Examples:
            >>> # Simple cross-domain
            >>> parse("CM|PR", "CMTRT|PRTRT")
            [ParsedMapping(domain="CM", variable="CMTRT", type=Simple),
             ParsedMapping(domain="PR", variable="PRTRT", type=Simple)]

            >>> # Multi-variable same domain
            >>> parse("AE", "AESDTH/AESLIFE/AESHOSP")
            [ParsedMapping(domain="AE", variable="AESDTH", type=Simple),
             ParsedMapping(domain="AE", variable="AESLIFE", type=Simple),
             ParsedMapping(domain="AE", variable="AESHOSP", type=Simple)]

            >>> # Cross-domain with conditions
            >>> parse("CM|PR", "CMTRT when PRDYN=\\"1\\"|PRTRT when PRDTN=\\"2\\"")
            [ParsedMapping(domain="CM", variable="CMTRT", type=Conditional,
                          condition='when PRDYN="1"'),
             ParsedMapping(domain="PR", variable="PRTRT", type=Conditional,
                          condition='when PRDTN="2"')]

            >>> # Multi-variable with SUPP
            >>> parse("AE", "AESDTH/AESLIFE/QVAL when QNAM=SAECAT")
            [ParsedMapping(domain="AE", variable="AESDTH", type=Simple),
             ParsedMapping(domain="AE", variable="AESLIFE", type=Simple),
             ParsedMapping(domain="AE", variable="QVAL", type=SUPP,
                          condition="when QNAM=SAECAT", qnam_value="SAECAT")]

            >>> # Cross-domain SUPP + Conditional
            >>> parse("DM|CM", "QVAL when QNAM=AOPR|CMDOSRGM when AOPR=\\"FOLFOX\\"")
            [ParsedMapping(domain="DM", variable="QVAL", type=SUPP,
                          condition="when QNAM=AOPR", qnam_value="AOPR"),
             ParsedMapping(domain="CM", variable="CMDOSRGM", type=Conditional,
                          condition='when AOPR="FOLFOX"')]
        """
        if not sdtm_domain or not sdtm_variable:
            logger.warning(f"Empty domain or variable: domain='{sdtm_domain}', variable='{sdtm_variable}'")
            return []

        # Clean input strings
        sdtm_domain = clean_sdtm_variable(sdtm_domain)
        sdtm_variable = clean_sdtm_variable(sdtm_variable)

        # Step 1: Split by | (cross-domain)
        if "|" in sdtm_variable:
            return self._parse_cross_domain(sdtm_domain, sdtm_variable)

        # Step 2: Single domain, check for / (multi-variable)
        if "/" in sdtm_variable:
            return self._parse_multi_variable(sdtm_domain, sdtm_variable)

        # Step 3: Single domain, single variable
        return self._parse_single_variable(sdtm_domain, sdtm_variable)

    def _parse_cross_domain(self, sdtm_domain: str, sdtm_variable: str) -> list[ParsedMapping]:
        """Parse cross-domain mapping (contains |).

        Args:
            sdtm_domain: Domain string (may contain |)
            sdtm_variable: Variable string (contains |)

        Returns:
            List of ParsedMapping objects
        """
        # Split both by |
        domain_parts = [d.strip() for d in sdtm_domain.split("|")]
        variable_parts = [v.strip() for v in sdtm_variable.split("|")]

        # Validate matching parts
        if len(domain_parts) != len(variable_parts):
            logger.warning(
                f"Cross-domain mismatch: {len(domain_parts)} domains vs "
                f"{len(variable_parts)} variable groups. "
                f"Domain: '{sdtm_domain}', Variable: '{sdtm_variable}'"
            )

            # Try to use single domain for all variable groups
            if len(domain_parts) == 1:
                domain_parts = domain_parts * len(variable_parts)
            else:
                # Pad with last domain
                while len(domain_parts) < len(variable_parts):
                    domain_parts.append(domain_parts[-1] if domain_parts else "")

        # Process each domain-variable pair
        all_mappings: list[ParsedMapping] = []

        for domain, var_group in zip(domain_parts, variable_parts, strict=False):
            if not domain or not var_group:
                continue

            domain = domain.upper()

            # Each var_group may contain / separator
            if "/" in var_group:
                mappings = self._parse_multi_variable(domain, var_group)
            else:
                mappings = self._parse_single_variable(domain, var_group)

            all_mappings.extend(mappings)

        logger.info(f"Parsed cross-domain: {len(all_mappings)} mappings from '{sdtm_domain}' / '{sdtm_variable}'")

        return all_mappings

    def _parse_multi_variable(self, domain: str, variable_group: str) -> list[ParsedMapping]:
        """Parse multi-variable mapping (contains /).

        Handles shared when/if conditions like "VSORRES/VSORRESU when VSTESTCD=WEIGHT"
        or "DSSTDTC/DSCAT=xxx if DSSTDTC不为空" where the condition applies to all variables.

        Also handles assignment-style patterns like "DSCAT=方案里程碑 / DSSCAT=随机化 when RANDYN=是"
        where each / segment is independent (no shared condition extraction).

        Args:
            domain: Single domain string
            variable_group: Variable string (contains /)

        Returns:
            List of ParsedMapping objects
        """
        domain = domain.upper()

        # Check if this is an assignment-style pattern (first segment starts with VAR=)
        # In this case, each / segment is independent and may have its own when/if clause
        if re.match(r"^[A-Za-z_]\w*=", variable_group):
            parts = [v.strip() for v in variable_group.split("/")]
            mappings: list[ParsedMapping] = []
            for part in parts:
                if part:
                    mappings.extend(self._parse_single_variable(domain, part))
            logger.debug(f"Parsed multi-assignment: {len(mappings)} mappings in domain '{domain}'")
            return mappings

        # Check if there's a shared when/if condition at the end
        # Pattern: "VAR1/VAR2/VAR3 when CONDITION" or "VAR1/VAR2 if CONDITION"
        shared_condition = None
        vars_part = variable_group

        # Find "when" or "if" keyword that appears after the last "/"
        cond_match = re.search(r"\s+(when|if)\s+", variable_group, re.IGNORECASE)
        if cond_match:
            last_slash_pos = variable_group.rfind("/")
            cond_pos = cond_match.start()

            if cond_pos > last_slash_pos:
                # The keyword is after the last '/', so it's a shared condition
                condition_start = cond_match.start()
                shared_condition = variable_group[condition_start:].strip()
                vars_part = variable_group[:condition_start].strip()

                logger.debug(f"Found shared condition '{shared_condition}' for variables: {vars_part}")

        # Split by /
        variable_parts = [v.strip() for v in vars_part.split("/")]

        mappings = []

        for var_str in variable_parts:
            if not var_str:
                continue

            # If there's a shared condition, append it to each variable
            # (skip if the variable already has its own when/if clause)
            if shared_condition and not re.search(r"\b(?:when|if)\b", var_str, re.IGNORECASE):
                var_str_with_condition = f"{var_str} {shared_condition}"
                var_mappings = self._parse_single_variable(domain, var_str_with_condition)
            else:
                # Parse each variable individually (it may have its own condition)
                var_mappings = self._parse_single_variable(domain, var_str)

            mappings.extend(var_mappings)

        logger.debug(f"Parsed multi-variable: {len(mappings)} mappings in domain '{domain}'")

        return mappings

    def _parse_single_variable(self, domain: str, variable_str: str) -> list[ParsedMapping]:
        """Parse single variable string and classify its type.

        Args:
            domain: Single domain string
            variable_str: Single variable string (may contain condition)

        Returns:
            List containing single ParsedMapping object
        """
        domain = domain.upper()
        variable_str = variable_str.strip()

        # Classify pattern type
        mapping_type, variable, condition = self._classify_pattern(variable_str)

        mapping = ParsedMapping(
            domain=domain,
            variable=variable,
            mapping_type=mapping_type,
            condition=condition,
            original_variable=variable_str,
        )

        logger.debug(
            f"Parsed single variable: {domain}.{variable} (type={mapping_type.value}, condition='{condition}')"
        )

        return [mapping]

    def _classify_pattern(self, variable_str: str) -> tuple[MappingType, str, str | None]:
        """Classify variable string pattern.

        Args:
            variable_str: Variable string (may contain condition)

        Returns:
            Tuple of (MappingType, variable_name, condition)

        Examples:
            >>> _classify_pattern("CMTRT")
            (MappingType.SIMPLE, "CMTRT", None)

            >>> _classify_pattern("QVAL when QNAM=SITENAM")
            (MappingType.SUPP, "QVAL", "when QNAM=SITENAM")

            >>> _classify_pattern("FAORRES when FATESTCD=THDIA")
            (MappingType.CONDITIONAL, "FAORRES", "when FATESTCD=THDIA")

            >>> _classify_pattern("FAORRES when FATEST=MSI")
            (MappingType.CONDITIONAL, "FAORRES", "when FATEST=MSI")

            >>> _classify_pattern("ECDOSE when ECMOOD=已执行")
            (MappingType.CONDITIONAL_OTHER, "ECDOSE", "when ECMOOD=已执行")

            >>> _classify_pattern("DSSTDTC if DSSTDTC不为空")
            (MappingType.CONDITIONAL_OTHER, "DSSTDTC", "if DSSTDTC不为空")

            >>> _classify_pattern("DSCAT=方案里程碑")
            (MappingType.ASSIGNMENT, "DSCAT", "方案里程碑")

            >>> _classify_pattern("DSSCAT=随机化 when RANDYN=是")
            (MappingType.ASSIGNMENT, "DSSCAT", "随机化 when RANDYN=是")
        """
        # Check for assignment pattern: "VARIABLE=value..." (variable directly followed by =)
        # This detects patterns like "DSCAT=方案里程碑" or "DSSCAT=随机化 when RANDYN=是"
        # Distinguished from "when VAR=val" conditions by the = being immediately after the variable name
        assignment_match = re.match(r"^([A-Za-z_]\w*)=(.+)$", variable_str)
        if assignment_match:
            variable = assignment_match.group(1).strip().upper()
            assign_expr = assignment_match.group(2).strip()
            return (MappingType.ASSIGNMENT, variable, assign_expr)

        # Check for "when" or "if" condition keyword
        cond_keyword_match = re.search(r"\s+(when|if)\s+", variable_str, re.IGNORECASE)
        if not cond_keyword_match:
            # Simple mapping (no condition keyword found)
            return (MappingType.SIMPLE, variable_str.upper(), None)

        # Split on the matched keyword
        variable = variable_str[: cond_keyword_match.start()].strip().upper()
        keyword = cond_keyword_match.group(1)  # preserve original "when" or "if"
        condition_text = variable_str[cond_keyword_match.end() :].strip()

        if not variable or not condition_text:
            logger.warning(f"Malformed condition clause in: {variable_str}")
            return (MappingType.SIMPLE, variable_str.upper(), None)

        condition = f"{keyword} {condition_text}"
        condition_content = condition_text.upper()

        # Check if it's a SUPP pattern (QVAL with QNAM)
        if variable == "QVAL" and "QNAM" in condition_content:
            return (MappingType.SUPP, variable, condition)

        # Check for TESTCD or TEST pattern
        # Extract the first condition variable (before =)
        cond_match = re.match(r"(\w+)\s*=", condition_content)
        if cond_match:
            cond_var = cond_match.group(1).upper()

            # Check if it's a TESTCD pattern (e.g., FATESTCD, MITEST CD)
            if cond_var.endswith("TESTCD"):
                return (MappingType.CONDITIONAL, variable, condition)

            # Check if it's a TEST pattern (e.g., FATEST, MITEST) but NOT TESTCD
            if cond_var.endswith("TEST"):
                return (MappingType.CONDITIONAL, variable, condition)

        # Otherwise it's a CONDITIONAL_OTHER (non-TEST/TESTCD condition)
        # e.g., ECDOSE when ECMOOD=已执行, RSSTAT when RSSCAT=病理评估
        return (MappingType.CONDITIONAL_OTHER, variable, condition)
