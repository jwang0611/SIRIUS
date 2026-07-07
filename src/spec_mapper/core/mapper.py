"""
Mapper Implementation using Unified Parser

Coordinate the mapping from ALS records to template records using
a unified SDTM variable parser for proper handling of complex patterns.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import cast

from ..models.als_record import ALSRecord
from ..models.codelist_record import CodelistRecord
from ..models.conditional_record import ConditionalRecord
from ..models.supp_record import SUPPRecord
from ..models.template_record import CellUpdate, TemplateRecord
from ..parsers import ConditionType, MappingType, ParsedMapping, SDTMVariableParser
from ..rules import transformation_helpers as _rules
from ..rules.transformation_helpers import NOT_SUBMITTED, is_not_submitted  # noqa: F401

logger = logging.getLogger(__name__)


@dataclass
class ParsedALSMapping:
    """Wrapper combining ALS record with its parsed mapping.

    Attributes:
        als_record: Original ALS record
        parsed: Parsed mapping result
    """

    als_record: ALSRecord
    parsed: ParsedMapping


class Mapper:
    """Coordinate the mapping from ALS records to template records.

    The Mapper uses a unified SDTM variable parser with:
    1. Proper precedence (| before /)
    2. Correct handling of mixed patterns (Simple/SUPP/Conditional)
    3. Proper transformation_def generation with conditions
    4. Support for conditional mappings in main domain sheets

    Examples:
        >>> mapper = Mapper(config)
        >>> updates, supp_records, cond_records = mapper.map(als_records, template_records)
    """

    def __init__(self, registry=None, config=None):
        """Initialize mapper with configuration.

        Args:
            registry: Deprecated, kept for compatibility but not used
            config: ConfigLoader instance for accessing configuration
        """
        from ..utils.config_loader import ConfigLoader

        self.config = config or ConfigLoader()
        self.parser = SDTMVariableParser()

        # Load special variable rules
        self.special_variables = self.config.get("special_variables", {})

        # Load output format configuration
        self.output_format = self.config.get_output_format()
        self.supp_merge_separator = self.output_format.get("supp_merge_separator", ";\n")

        # Load codelist hint configuration
        self.codelist_hint = self.config.get("codelist_hint", {})
        self.codelist_trigger_type = self.codelist_hint.get("trigger_component_type", "单选")
        self.codelist_note = self.codelist_hint.get("note", "注：请根据rawdata里变量对应的编码描述进行赋值")

        # Load variable-specific hints configuration
        self.variable_hints = self.config.get("variable_hints", {})
        # SPID/GRPID hint
        spid_grpid_config = self.variable_hints.get("spid_grpid", {})
        self.spid_grpid_patterns = spid_grpid_config.get("patterns", ["SPID", "GRPID"])
        self.spid_grpid_note = spid_grpid_config.get("note", "注：统一为z2.格式")
        # DTC time hint
        dtc_config = self.variable_hints.get("dtc", {})
        self.dtc_patterns = dtc_config.get("patterns", ["DTC"])
        self.dtc_note = dtc_config.get("note", "注：统一为ISO8601格式")
        # Assignment mapping transformation prefix (language-specific by template config)
        self.assignment_prefix = self.config.get("assignment_transformation_prefix", "赋值为")

        logger.info("Initialized Mapper with unified parser")

    def map(
        self, als_records: list[ALSRecord], template_records: list[TemplateRecord], create_test_sheets: bool = True
    ) -> tuple[list[CellUpdate], list[SUPPRecord], list[ConditionalRecord], list[CodelistRecord], list[dict]]:
        """Map ALS records to template records and generate updates.

        Args:
            als_records: List of ALSRecord from source file
            template_records: List of TemplateRecord from template file

        Returns:
            Tuple of (CellUpdate list, SUPPRecord list, ConditionalRecord list,
                      CodelistRecord list, unmatched_records list)
        """
        logger.info(f"Starting mapping: {len(als_records)} ALS records, {len(template_records)} template records")

        # Filter out NOT SUBMITTED records
        valid_als_records = self._filter_valid_records(als_records)

        # Parse all ALS records using new unified parser
        parsed_mappings = self._parse_als_records(valid_als_records)

        # Separate by mapping type
        simple_mappings = [m for m in parsed_mappings if m.parsed.mapping_type == MappingType.SIMPLE]
        supp_mappings = [m for m in parsed_mappings if m.parsed.mapping_type == MappingType.SUPP]
        conditional_mappings = [m for m in parsed_mappings if m.parsed.mapping_type == MappingType.CONDITIONAL]
        conditional_other_mappings = [
            m for m in parsed_mappings if m.parsed.mapping_type == MappingType.CONDITIONAL_OTHER
        ]
        assignment_mappings = [m for m in parsed_mappings if m.parsed.mapping_type == MappingType.ASSIGNMENT]

        logger.info(
            f"Parsed mappings: {len(simple_mappings)} simple, "
            f"{len(supp_mappings)} SUPP, {len(conditional_mappings)} conditional, "
            f"{len(conditional_other_mappings)} conditional_other, "
            f"{len(assignment_mappings)} assignment"
        )

        # Build template index
        template_index = self._build_template_index(template_records)

        # Separate simple mappings that target the same (domain, variable) as
        # conditional/conditional_other mappings. These must be merged into the
        # corresponding conditional aggregation so their transformations are not
        # overwritten by later passes.
        conditional_target_keys = {(m.parsed.domain, m.parsed.variable) for m in conditional_mappings}
        conditional_other_target_keys = {(m.parsed.domain, m.parsed.variable) for m in conditional_other_mappings}
        simple_overlapping_conditional: list[ParsedALSMapping] = []
        simple_overlapping_conditional_other: list[ParsedALSMapping] = []
        simple_non_overlapping: list[ParsedALSMapping] = []
        for m in simple_mappings:
            key = (m.parsed.domain, m.parsed.variable)
            if key in conditional_target_keys:
                simple_overlapping_conditional.append(m)
            elif key in conditional_other_target_keys:
                simple_overlapping_conditional_other.append(m)
            else:
                simple_non_overlapping.append(m)

        if simple_overlapping_conditional or simple_overlapping_conditional_other:
            logger.info(
                "Found %s simple mappings overlapping with conditional targets "
                "(%s conditional, %s conditional_other), merging to avoid overwrite",
                len(simple_overlapping_conditional) + len(simple_overlapping_conditional_other),
                len(simple_overlapping_conditional),
                len(simple_overlapping_conditional_other),
            )

        # Process each type
        updates: list[CellUpdate] = []

        # 1. Process simple mappings (non-overlapping only)
        simple_updates = self._process_simple_mappings(simple_non_overlapping, template_index)
        updates.extend(simple_updates)

        # 1.5. Process assignment mappings (Variable A = xxxx)
        assignment_updates = self._process_assignment_mappings(assignment_mappings, template_index)
        updates.extend(assignment_updates)

        # 2. Process conditional mappings (fills transformation_def in domain sheets)
        # Also includes simple overlapping mappings and collects TESTCD values for CODELIST sheet
        conditional_updates, codelist_records = self._process_conditional_mappings(
            conditional_mappings, template_index, simple_overlapping_conditional
        )
        updates.extend(conditional_updates)

        # 2.5. Process conditional_other mappings (directly append when condition)
        conditional_other_updates = self._process_conditional_other_mappings(
            conditional_other_mappings, template_index, simple_overlapping_conditional_other
        )
        updates.extend(conditional_other_updates)

        # 3. Process SUPP mappings (creates new rows)
        supp_records = self._process_supp_mappings(supp_mappings)

        # 4. Create conditional records for TEST sheets (if enabled)
        cond_records = self._create_conditional_records(conditional_mappings, create_test_sheets)

        # 5. Add cross-domain references
        cross_domain_updates = self._add_cross_domain_references(template_records, updates)
        updates.extend(cross_domain_updates)

        # 6. Collect unmatched records (simple/assignment that have no template match)
        unmatched_records = self._collect_unmatched_records(simple_mappings, assignment_mappings, template_index)

        logger.info(
            f"Mapping complete: {len(updates)} cell updates, "
            f"{len(supp_records)} SUPP rows, {len(cond_records)} conditional records, "
            f"{len(codelist_records)} codelist entries, {len(unmatched_records)} unmatched"
        )

        return updates, supp_records, cond_records, codelist_records, unmatched_records

    def _filter_valid_records(self, als_records: list[ALSRecord]) -> list[ALSRecord]:
        """Filter out invalid ALS records."""
        valid_records = []
        skipped_count = 0

        for als_rec in als_records:
            # Skip NOT SUBMITTED
            if is_not_submitted(als_rec.sdtm_variable):
                skipped_count += 1
                continue

            # Skip empty sdtm_variable or sdtm_domain
            if not als_rec.sdtm_variable or not als_rec.sdtm_domain:
                skipped_count += 1
                continue

            valid_records.append(als_rec)

        if skipped_count > 0:
            logger.info(f"Filtered out {skipped_count} invalid/NOT SUBMITTED records")

        return valid_records

    def _parse_als_records(self, als_records: list[ALSRecord]) -> list[ParsedALSMapping]:
        """Parse all ALS records using unified parser.

        Returns:
            List of ParsedALSMapping objects (ALS record + parsed mapping)
        """
        all_mappings: list[ParsedALSMapping] = []

        for als_rec in als_records:
            parsed_list = self.parser.parse(als_rec.sdtm_domain, als_rec.sdtm_variable)

            for parsed in parsed_list:
                all_mappings.append(ParsedALSMapping(als_record=als_rec, parsed=parsed))

                logger.debug(
                    f"Parsed: {parsed.domain}.{parsed.variable} "
                    f"(type={parsed.mapping_type.value}) <- {als_rec.get_raw_reference()}"
                )

        logger.info(f"Total parsed mappings: {len(all_mappings)} from {len(als_records)} ALS records")

        return all_mappings

    def _build_template_index(self, template_records: list[TemplateRecord]) -> dict[tuple[str, str], TemplateRecord]:
        """Build index of template records by (domain, variable).

        Returns:
            Dictionary mapping (domain, variable) to TemplateRecord
        """
        index: dict[tuple[str, str], TemplateRecord] = {}

        for template_rec in template_records:
            key = (template_rec.sheet_name, template_rec.variable_name)
            index[key] = template_rec

        logger.info(f"Built template index with {len(index)} variables")

        return index

    def _process_simple_mappings(
        self, mappings: list[ParsedALSMapping], template_index: dict[tuple[str, str], TemplateRecord]
    ) -> list[CellUpdate]:
        """Process simple mappings with aggregation for multiple sources.

        When multiple ALS records map to the same SDTM (domain, variable),
        their transformation definitions are aggregated with newlines.

        If any ALS record has component_type="单选" and codelist_name has value,
        a codelist hint note is appended at the end (only once per cell).

        Example:
            If TU.TULNKID comes from TU_TLB, TU_NTLB, TU_NL, RC tables,
            the output will be:
                [RAW]TU_TLB.TULNKID
                [RAW]TU_NTLB.TULNKID
                [RAW]TU_NL.TULNKID
                [RAW]RC.RCLNKID

        Returns:
            List of CellUpdate objects
        """
        updates: list[CellUpdate] = []

        # Group mappings by (domain, variable) to aggregate multiple sources
        simple_groups: dict[tuple[str, str], list[ParsedALSMapping]] = defaultdict(list)

        for mapping in mappings:
            key = (mapping.parsed.domain, mapping.parsed.variable)
            simple_groups[key].append(mapping)

        # Process each group
        for (domain, variable), group_mappings in simple_groups.items():
            template_key = (domain, variable)
            template_rec = template_index.get(template_key)

            if not template_rec:
                logger.debug(f"No template match for {domain}.{variable}")
                continue

            # Check if any record needs codelist hint
            needs_codelist_hint = any(
                m.als_record.needs_codelist_hint(self.codelist_trigger_type) for m in group_mappings
            )

            if len(group_mappings) == 1:
                # Single source - no aggregation needed
                mapping = group_mappings[0]
                transformation = self._generate_transformation(mapping.als_record)
                reason = "Simple mapping"
            else:
                # Multiple sources - aggregate with semicolon separator
                transformations = []
                for mapping in group_mappings:
                    trans = self._generate_transformation(mapping.als_record)
                    transformations.append(trans)

                # Remove duplicates while preserving order
                seen = set()
                unique_transformations = []
                for trans in transformations:
                    if trans not in seen:
                        seen.add(trans)
                        unique_transformations.append(trans)

                # Join with semicolon and newline as separator
                transformation = ";\n".join(unique_transformations)
                reason = f"Simple mapping (aggregated from {len(unique_transformations)} sources)"

                logger.info(
                    f"Aggregated simple mapping: {domain}.{variable} "
                    f"from {len(group_mappings)} ALS records -> "
                    f"{len(unique_transformations)} unique sources"
                )

            # Append all applicable hints (codelist, SPID/GRPID, DTC)
            transformation = self._append_hints_to_transformation(
                transformation, target_variable=variable, needs_codelist_hint=needs_codelist_hint
            )

            update = CellUpdate(
                sheet_name=template_rec.sheet_name,
                row=template_rec.row_index,
                col=template_rec.col_index,
                value=transformation,
                reason=reason,
            )

            updates.append(update)

            logger.debug(
                f"Simple mapping: {template_rec.sheet_name}.{template_rec.variable_name} "
                f"= {transformation[:50]}{'...' if len(transformation) > 50 else ''}"
            )

        logger.info(f"Processed {len(simple_groups)} simple mapping groups into {len(updates)} updates")

        return updates

    def _process_assignment_mappings(
        self, mappings: list[ParsedALSMapping], template_index: dict[tuple[str, str], TemplateRecord]
    ) -> list[CellUpdate]:
        """Process assignment mappings (Variable A = xxxx format).

        For patterns like "DSCAT = 方案里程碑", the transformation definition
        uses the configured assignment prefix (e.g. '赋值为"方案里程碑"' or
        'equals to "方案里程碑"') and source (F column) is set to "指定".

        For patterns with conditions like "DSSCAT = 随机化 when RANDYN = 是",
        the transformation uses the configured prefix with the condition suffix.

        Multiple assignments to the same variable are separated with ";".

        Returns:
            List of CellUpdate objects (includes both H column and F column updates)
        """

        updates: list[CellUpdate] = []

        # Group by (domain, variable) to aggregate multiple assignments
        assignment_groups: dict[tuple[str, str], list[ParsedALSMapping]] = defaultdict(list)

        for mapping in mappings:
            key = (mapping.parsed.domain, mapping.parsed.variable)
            assignment_groups[key].append(mapping)

        for (domain, variable), group_mappings in assignment_groups.items():
            template_key = (domain, variable)
            template_rec = template_index.get(template_key)

            if not template_rec:
                logger.debug(f"No template match for assignment {domain}.{variable}")
                continue

            # Build transformation definitions
            transformation_parts = []
            for mapping in group_mappings:
                expr = mapping.parsed.assignment_value or ""
                als_rec = mapping.als_record
                table_name = als_rec.metadata_table or als_rec.table or None
                formatted = self._format_assignment_expression(expr, metadata_table=table_name)
                if formatted and formatted not in transformation_parts:
                    transformation_parts.append(formatted)

            if len(transformation_parts) == 1:
                transformation = transformation_parts[0]
            else:
                transformation = ";\n".join(transformation_parts)

            # H column (transformation definition)
            update = CellUpdate(
                sheet_name=template_rec.sheet_name,
                row=template_rec.row_index,
                col=template_rec.col_index,
                value=transformation,
                reason="Assignment mapping",
            )
            updates.append(update)

            # F column (source = "指定", column 6)
            source_update = CellUpdate(
                sheet_name=template_rec.sheet_name,
                row=template_rec.row_index,
                col=6,
                value="指定",
                reason="Assignment mapping source",
            )
            updates.append(source_update)

            logger.debug(f"Assignment mapping: {domain}.{variable} = {transformation}")

        logger.info(f"Processed {len(assignment_groups)} assignment mapping groups into {len(updates)} updates")

        return updates

    def _format_assignment_expression(self, expr: str, metadata_table: str | None = None) -> str:
        """Format an assignment expression into transformation definition.

        Parses "value" or "value when/if CONDITION_VAR=condition_value" and formats as:
        - '<prefix>"value"'
        - '<prefix>"value" when [RAW]TABLE.CONDITION_VAR = condition_value'
        - '<prefix>"value" if [RAW]TABLE.CONDITION_VAR = condition_value'

        If value is already quoted (e.g. "方案里程碑"), no extra quotes are added.

        Args:
            expr: The assignment expression (everything after "=" in the original)
            metadata_table: Table name to insert after [RAW] prefix in conditions

        Returns:
            Formatted transformation definition string
        """
        import re

        if not expr:
            return ""

        # For English prefixes like "equals to", insert a separating space
        # before the quoted value; Chinese "赋值为" stays compact.
        prefix = self.assignment_prefix
        value_sep = " " if prefix and prefix[-1].isalnum() else ""

        # Check if expression contains a "when" or "if" condition clause
        cond_match = re.search(r"\s+(when|if)\s+", expr, re.IGNORECASE)
        if cond_match:
            value_part = expr[: cond_match.start()].strip()
            keyword = cond_match.group(1)  # "when" or "if"
            condition_part = expr[cond_match.end() :].strip()

            # Format the condition: prefix condition variables with [RAW]TABLE., add spaces around =
            formatted_condition = self._format_assignment_condition(condition_part, metadata_table)
            quoted_value = self._quote_if_needed(value_part)
            return f"{prefix}{value_sep}{quoted_value} {keyword} {formatted_condition}"
        else:
            quoted_value = self._quote_if_needed(expr)
            return f"{prefix}{value_sep}{quoted_value}"

    @staticmethod
    def _quote_if_needed(value: str) -> str:
        """Wrap value in quotes only if not already quoted."""
        if value.startswith('"') and value.endswith('"'):
            return value
        if value.startswith("\u201c") and value.endswith("\u201d"):
            return value
        return f'"{value}"'

    def _format_assignment_condition(self, condition: str, metadata_table: str | None = None) -> str:
        """Insert metadata_table name into existing [RAW] references.

        Only touches ``[RAW]VAR`` patterns — everything else is left as-is.

        - ``[RAW]PEPERF``       → ``[RAW]PE.PEPERF``   (table inserted)
        - ``[RAW]PE.PEPERF``    → ``[RAW]PE.PEPERF``   (already qualified)
        - ``PETESTCD=PEALL``    → ``PETESTCD=PEALL``    (no [RAW], untouched)

        Args:
            condition: Condition string (e.g. "[RAW]PEPERF=否 when PETESTCD=PEALL")
            metadata_table: Table name to insert after [RAW] (e.g. "PE")

        Returns:
            Condition with table-qualified [RAW] references
        """
        if not metadata_table:
            return condition

        import re

        def insert_table(match):
            var_part = match.group(1)  # "VAR" or "TABLE.VAR"
            if "." in var_part:
                return match.group(0)
            return f"[RAW]{metadata_table}.{var_part}"

        return re.sub(r"\[RAW\]([\w.]+)", insert_table, condition)

    def _process_conditional_mappings(
        self,
        mappings: list[ParsedALSMapping],
        template_index: dict[tuple[str, str], TemplateRecord],
        simple_overlapping: list[ParsedALSMapping] | None = None,
    ) -> tuple[list[CellUpdate], list[CodelistRecord]]:
        """Process conditional mappings with aggregation (fills transformation_def in domain sheets).

        For patterns like "FAORRES when FATESTCD=THDIA" and "FAORRES when FATEST=MSI",
        aggregates all conditions with TESTCD numbered first, then TEST continues numbering:
        - FA.FATESTCD: transformation_def = "1.THDIA;\n2.THFDTC;\n..."
        - FA.FATEST:   transformation_def = "6.MSI;\n7.MMR;\n..." (continues from TESTCD)
        - FA.FAORRES:  transformation_def = "1.[RAW]TH.THDIA when FATESTCD=THDIA\n..."

        When the same TESTCD/TEST value comes from multiple sources (different tables),
        the sources are aggregated under the same numbered entry.

        Simple (no-condition) mappings for the same (domain, variable) are merged
        into the aggregated ORRES output so they are not lost.

        Also collects TESTCD values for CODELIST sheet.

        Args:
            mappings: List of conditional mapping records
            template_index: Template index for looking up target cells
            simple_overlapping: Simple mappings that share a (domain, variable)
                with these conditional mappings. Their transformations are
                prepended to the aggregated ORRES output.

        Returns:
            Tuple of (CellUpdate list, CodelistRecord list)
        """
        updates: list[CellUpdate] = []
        codelist_records: list[CodelistRecord] = []

        # Three-level grouping:
        # Level 1: Group by (domain, target_variable)
        # Level 2: Group by condition_type (TESTCD or TEST)
        # Level 3: Group by condition_value to aggregate multiple sources
        # Structure: {(domain, target_var): {ConditionType: {cond_value: [(transformation, full_condition)]}}}
        aggregated: dict[
            tuple[str, str],  # (domain, target_var)
            dict[ConditionType, dict[str, list[tuple[str, str]]]],  # {cond_type: {value: [(trans, full_cond)]}}
        ] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

        # Group simple overlapping mappings by (domain, target_var)
        simple_overlap_groups: dict[tuple[str, str], list[ParsedALSMapping]] = defaultdict(list)
        if simple_overlapping:
            for m in simple_overlapping:
                key = (m.parsed.domain, m.parsed.variable)
                simple_overlap_groups[key].append(m)

        # Track which (domain, target_var) needs codelist hint
        needs_codelist_hint_map: dict[tuple[str, str], bool] = defaultdict(bool)

        # Check codelist hints from simple overlapping mappings
        for key, group in simple_overlap_groups.items():
            if any(m.als_record.needs_codelist_hint(self.codelist_trigger_type) for m in group):
                needs_codelist_hint_map[key] = True

        # Track TESTCD values with their source variable names for CODELIST
        # Structure: {(domain, testcd_var, testcd_value): (source_variable, metadata_variable)}
        testcd_source_map: dict[tuple[str, str, str], tuple[str, str]] = {}

        for mapping in mappings:
            domain = mapping.parsed.domain
            target_var = mapping.parsed.variable  # e.g., "FAORRES"

            # Get condition info
            test_condition = mapping.parsed.test_condition
            condition_type = mapping.parsed.condition_type
            full_condition = mapping.parsed.condition  # e.g., "when FATESTCD=THDIA"

            if not test_condition or not condition_type:
                logger.warning(f"Conditional mapping without test_condition: {mapping.parsed.original_variable}")
                continue

            cond_var, cond_value = test_condition  # e.g., ("FATESTCD", "THDIA")

            # Generate transformation definition
            transformation = self._generate_transformation(mapping.als_record)

            # Add to aggregation groups
            key = (domain, target_var)
            aggregated[key][condition_type][cond_value].append((transformation, full_condition or ""))

            # Check if this record needs codelist hint
            if mapping.als_record.needs_codelist_hint(self.codelist_trigger_type):
                needs_codelist_hint_map[key] = True

            # Collect TESTCD values for CODELIST sheet
            if condition_type == ConditionType.TESTCD:
                testcd_source_key = (domain, cond_var, cond_value)
                if testcd_source_key not in testcd_source_map:
                    testcd_source_map[testcd_source_key] = (
                        mapping.als_record.variable_label,
                        mapping.als_record.variable,
                    )

        # Now generate aggregated CellUpdates for each group
        for (domain, target_var), type_groups in aggregated.items():
            if not type_groups:
                continue

            # Get domain prefix (e.g., "FA" from "FAORRES")
            domain_prefix = domain[:2] if len(domain) >= 2 else domain

            # Process TESTCD first, then TEST with continued numbering
            testcd_groups = type_groups.get(ConditionType.TESTCD, {})
            test_groups = type_groups.get(ConditionType.TEST, {})

            # Sort values for consistent output
            sorted_testcd_values = sorted(testcd_groups.keys())
            sorted_test_values = sorted(test_groups.keys())

            # Calculate starting index for TEST (continues after TESTCD)
            testcd_count = len(sorted_testcd_values)
            testcd_count + 1

            # Generate TESTCD variable content (e.g., "THDIA;\nTHFDTC;")
            # Note: Numbers removed per requirements - just use separator
            if sorted_testcd_values:
                testcd_var_name = domain_prefix + "TESTCD"
                testcd_lines = [f"{val};" for val in sorted_testcd_values]
                testcd_aggregated = "\n".join(testcd_lines)

                # Update TESTCD variable
                testcd_template_key = (domain, testcd_var_name)
                testcd_template = template_index.get(testcd_template_key)
                if testcd_template:
                    update = CellUpdate(
                        sheet_name=testcd_template.sheet_name,
                        row=testcd_template.row_index,
                        col=testcd_template.col_index,
                        value=testcd_aggregated,
                        reason="Conditional aggregated TESTCD",
                    )
                    updates.append(update)
                    logger.debug(f"Aggregated TESTCD: {domain}.{testcd_var_name} = {len(sorted_testcd_values)} values")

            # Generate TEST variable content (e.g., "MSI;\nMMR;")
            # Note: Numbers removed per requirements - just use separator
            if sorted_test_values:
                test_var_name = domain_prefix + "TEST"
                test_lines = [f"{val};" for val in sorted_test_values]
                test_aggregated = "\n".join(test_lines)

                # Update TEST variable
                test_key = (domain, test_var_name)
                test_template = template_index.get(test_key)
                if test_template:
                    update = CellUpdate(
                        sheet_name=test_template.sheet_name,
                        row=test_template.row_index,
                        col=test_template.col_index,
                        value=test_aggregated,
                        reason="Conditional aggregated TEST",
                    )
                    updates.append(update)
                    logger.debug(f"Aggregated TEST: {domain}.{test_var_name} = {len(sorted_test_values)} values")

            # Generate target variable content (e.g., FAORRES)
            # Combine unconditional + TESTCD + TEST entries (no numbering)
            orres_lines = []

            # First add simple (unconditional) entries for the same target variable
            simple_entries = simple_overlap_groups.pop((domain, target_var), [])
            if simple_entries:
                seen_trans: set[str] = set()
                for m in simple_entries:
                    trans = self._generate_transformation(m.als_record)
                    if trans not in seen_trans:
                        seen_trans.add(trans)
                        orres_lines.append(f"{trans};")
                logger.debug(
                    f"Merged {len(simple_entries)} simple entries into conditional ORRES for {domain}.{target_var}"
                )

            # Then add TESTCD entries (no numbering, just separator)
            for cond_val in sorted_testcd_values:
                trans_cond_list = testcd_groups[cond_val]
                # Deduplicate transformations
                seen_trans = set()
                unique_entries = []
                for trans, full_cond in trans_cond_list:
                    if trans not in seen_trans:
                        seen_trans.add(trans)
                        unique_entries.append((trans, full_cond))

                # Add all unique entries with semicolon separator
                for trans, full_cond in unique_entries:
                    orres_lines.append(f"{trans} {full_cond};")

            # Then add TEST entries (no numbering, just separator)
            for cond_val in sorted_test_values:
                trans_cond_list = test_groups[cond_val]
                # Deduplicate transformations
                seen_trans = set()
                unique_entries = []
                for trans, full_cond in trans_cond_list:
                    if trans not in seen_trans:
                        seen_trans.add(trans)
                        unique_entries.append((trans, full_cond))

                # Add all unique entries with semicolon separator
                for trans, full_cond in unique_entries:
                    orres_lines.append(f"{trans} {full_cond};")

            orres_aggregated = "\n".join(orres_lines)

            # Append all applicable hints (codelist, SPID/GRPID, DTC)
            target_key = (domain, target_var)
            orres_aggregated = self._append_hints_to_transformation(
                orres_aggregated,
                target_variable=target_var,
                needs_codelist_hint=needs_codelist_hint_map.get(target_key, False),
            )

            # Update target variable (e.g., FA.FAORRES)
            target_template = template_index.get(target_key)
            if target_template:
                update = CellUpdate(
                    sheet_name=target_template.sheet_name,
                    row=target_template.row_index,
                    col=target_template.col_index,
                    value=orres_aggregated,
                    reason="Conditional aggregated ORRES",
                )
                updates.append(update)
                logger.debug(
                    f"Aggregated ORRES: {domain}.{target_var} = "
                    f"{len(sorted_testcd_values) + len(sorted_test_values)} values"
                )

        # Handle any remaining simple overlapping entries whose conditional
        # counterparts were all filtered out (e.g. missing test_condition).
        for (domain, target_var), remaining_entries in simple_overlap_groups.items():
            if not remaining_entries:
                continue
            seen_trans: set[str] = set()
            unique_trans: list[str] = []
            for m in remaining_entries:
                trans = self._generate_transformation(m.als_record)
                if trans not in seen_trans:
                    seen_trans.add(trans)
                    unique_trans.append(trans)

            needs_codelist = needs_codelist_hint_map.get((domain, target_var), False)
            transformation = ";\n".join(unique_trans)
            transformation = self._append_hints_to_transformation(
                transformation, target_variable=target_var, needs_codelist_hint=needs_codelist
            )
            target_key = (domain, target_var)
            target_template = template_index.get(target_key)
            if target_template:
                update = CellUpdate(
                    sheet_name=target_template.sheet_name,
                    row=target_template.row_index,
                    col=target_template.col_index,
                    value=transformation,
                    reason="Simple mapping (merged from conditional overlap)",
                )
                updates.append(update)
                logger.debug(f"Fallback simple ORRES: {domain}.{target_var} = {transformation[:50]}...")

        # Create CodelistRecord objects from collected TESTCD values
        for (domain, testcd_var, testcd_value), (source_variable, metadata_variable) in testcd_source_map.items():
            codelist_records.append(
                CodelistRecord(
                    domain=domain,
                    testcd_var=testcd_var,
                    testcd_value=testcd_value,
                    source_variable=source_variable,
                    metadata_variable=metadata_variable,
                )
            )

        # Sort codelist records by domain, then by testcd_value
        codelist_records.sort(key=lambda r: (r.domain, r.testcd_value))

        logger.info(
            f"Processed {len(mappings)} conditional mappings into "
            f"{len(updates)} aggregated cell updates, {len(codelist_records)} codelist entries"
        )

        return updates, codelist_records

    def _process_supp_mappings(self, mappings: list[ParsedALSMapping]) -> list[SUPPRecord]:
        """Process SUPP mappings (creates new rows to insert).

        Groups by (domain, QNAM) and merges multiple sources if needed.
        Supports additional conditions like "when QNAM=XXX when MITEST=MLH1".

        Returns:
            List of SUPPRecord objects
        """
        # Group by (domain, qnam_value)
        supp_groups: dict[tuple[str, str], list[ParsedALSMapping]] = defaultdict(list)

        for mapping in mappings:
            qnam = mapping.parsed.qnam_value
            if not qnam:
                logger.warning("SUPP mapping missing qnam_value, skipping: %s", mapping.parsed.original_variable)
                continue
            key = (mapping.parsed.domain, qnam)
            supp_groups[key].append(mapping)

        # Create SUPP records
        supp_records: list[SUPPRecord] = []

        for (domain, qnam_value), group_mappings in supp_groups.items():
            # Check if any record needs codelist hint
            needs_codelist_hint = any(
                m.als_record.needs_codelist_hint(self.codelist_trigger_type) for m in group_mappings
            )

            if len(group_mappings) == 1:
                # Single source
                mapping = group_mappings[0]
                als_rec = mapping.als_record

                # Generate base transformation
                transformation = self._generate_transformation(als_rec)

                # Extract and append additional condition if exists
                # e.g., "when QNAM=MIRESOTH when MITEST=MLH1" -> append "when MITEST=MLH1"
                additional_condition = self._extract_supp_additional_condition(mapping.parsed.condition)
                if additional_condition:
                    transformation = f"{transformation} {additional_condition}"
                    logger.debug(f"SUPP with additional condition: {transformation}")

                # Append all applicable hints (codelist, SPID/GRPID, DTC)
                # For SUPP, check qnam_value for DTC pattern
                transformation = self._append_hints_to_transformation(
                    transformation,
                    target_variable=qnam_value,  # Use QNAM value for SUPP
                    qnam_value=qnam_value,
                    needs_codelist_hint=needs_codelist_hint,
                )

                supp_record = SUPPRecord(
                    sheet_name=domain,
                    variable_name=qnam_value,
                    variable_label=als_rec.variable_label or qnam_value,
                    transformation_def=transformation,
                    insert_row=0,  # Placeholder
                    type_value="Char",
                    length="200",
                    source="CRF",
                    variable_type="SUPP",
                    source_order=als_rec.row_index,
                )

                supp_records.append(supp_record)

                logger.debug(f"SUPP record: {domain}.{qnam_value} = {transformation}")
            else:
                # Multiple sources - merge with deduplication
                transformations = []
                for mapping in group_mappings:
                    trans = self._generate_transformation(mapping.als_record)

                    # Extract and append additional condition if exists
                    additional_condition = self._extract_supp_additional_condition(mapping.parsed.condition)
                    if additional_condition:
                        trans = f"{trans} {additional_condition}"

                    transformations.append(trans)

                # Remove duplicates while preserving order
                seen = set()
                unique_transformations = []
                for trans in transformations:
                    if trans not in seen:
                        seen.add(trans)
                        unique_transformations.append(trans)

                combined_transformation = self.supp_merge_separator.join(unique_transformations)

                # Append all applicable hints (codelist, SPID/GRPID, DTC)
                combined_transformation = self._append_hints_to_transformation(
                    combined_transformation,
                    target_variable=qnam_value,  # Use QNAM value for SUPP
                    qnam_value=qnam_value,
                    needs_codelist_hint=needs_codelist_hint,
                )

                first_mapping = group_mappings[0]
                als_rec = first_mapping.als_record

                supp_record = SUPPRecord(
                    sheet_name=domain,
                    variable_name=qnam_value,
                    variable_label=als_rec.variable_label or qnam_value,
                    transformation_def=combined_transformation,
                    insert_row=0,  # Placeholder
                    type_value="Char",
                    length="200",
                    source="CRF",
                    variable_type="SUPP",
                    source_order=min(m.als_record.row_index for m in group_mappings),
                )

                supp_records.append(supp_record)

                logger.info(f"SUPP record merged: {domain}.{qnam_value} from {len(group_mappings)} sources")

        logger.info(f"Created {len(supp_records)} SUPP records")

        return supp_records

    def _process_conditional_other_mappings(
        self,
        mappings: list[ParsedALSMapping],
        template_index: dict[tuple[str, str], TemplateRecord],
        simple_overlapping: list[ParsedALSMapping] | None = None,
    ) -> list[CellUpdate]:
        """Process conditional_other mappings (non-TEST/TESTCD conditions).

        For patterns like "ECDOSE when ECMOOD=已执行", the transformation is:
        "[RAW]Table.Variable when ECMOOD=已执行"

        No numbering is applied - the when condition is directly appended.
        Multiple sources with the same condition are aggregated with newlines.

        Returns:
            List of CellUpdate objects
        """
        updates: list[CellUpdate] = []

        # Group by (domain, target_variable)
        # Value: List of (transformation, full_condition, als_record)
        aggregated: dict[tuple[str, str], list[tuple[str, str, ALSRecord]]] = defaultdict(list)

        for mapping in mappings:
            domain = mapping.parsed.domain
            target_var = mapping.parsed.variable  # e.g., "ECDOSE"
            full_condition = mapping.parsed.condition  # e.g., "when ECMOOD=已执行"

            # Generate transformation definition
            transformation = self._generate_transformation(mapping.als_record)

            # Add to aggregation groups (include als_record for codelist check)
            key = (domain, target_var)
            aggregated[key].append((transformation, full_condition or "", mapping.als_record))

        # Simple (unconditional) mappings that overlap with conditional_other
        # targets should be merged in instead of being overwritten.
        simple_overlap_groups: dict[tuple[str, str], list[ParsedALSMapping]] = defaultdict(list)
        if simple_overlapping:
            for m in simple_overlapping:
                simple_overlap_groups[(m.parsed.domain, m.parsed.variable)].append(m)

        # Generate CellUpdates
        for (domain, target_var), entries in aggregated.items():
            if not entries:
                continue

            # Check if any record needs codelist hint
            needs_codelist_hint = any(
                als_rec.needs_codelist_hint(self.codelist_trigger_type) for _, _, als_rec in entries
            )
            simple_entries = simple_overlap_groups.pop((domain, target_var), [])
            if simple_entries:
                needs_codelist_hint = needs_codelist_hint or any(
                    m.als_record.needs_codelist_hint(self.codelist_trigger_type) for m in simple_entries
                )

            # Deduplicate while preserving order.
            seen = set()
            unique_entries = []

            # Add unconditional simple entries first (no condition suffix)
            for simple_mapping in simple_entries:
                trans = self._generate_transformation(simple_mapping.als_record)
                entry_key = (trans, "")
                if entry_key not in seen:
                    seen.add(entry_key)
                    unique_entries.append((trans, ""))

            # Then add conditional_other entries
            for trans, cond, _ in entries:
                entry_key = (trans, cond)
                if entry_key not in seen:
                    seen.add(entry_key)
                    unique_entries.append((trans, cond))

            # Format: "[RAW]Table.Variable when CONDITION;"
            # Add semicolon separator for multiple sources
            if len(unique_entries) > 1:
                lines = [f"{f'{trans} {cond}'.strip()};" for trans, cond in unique_entries]
                combined_value = "\n".join(lines)
            else:
                # Single entry - no semicolon needed
                trans, cond = unique_entries[0]
                combined_value = f"{trans} {cond}".strip()

            # Append all applicable hints (codelist, SPID/GRPID, DTC)
            combined_value = self._append_hints_to_transformation(
                combined_value, target_variable=target_var, needs_codelist_hint=needs_codelist_hint
            )

            # Update target variable
            target_key = (domain, target_var)
            target_template = template_index.get(target_key)
            if target_template:
                update = CellUpdate(
                    sheet_name=target_template.sheet_name,
                    row=target_template.row_index,
                    col=target_template.col_index,
                    value=combined_value,
                    reason="Conditional other mapping",
                )
                updates.append(update)
                logger.debug(f"Conditional other: {domain}.{target_var} = {combined_value[:50]}...")
            else:
                logger.debug(f"No template match for conditional_other: {domain}.{target_var}")

        # Handle rare fallback case: simple overlap exists but all corresponding
        # conditional_other mappings were filtered out.
        for (domain, target_var), remaining_entries in simple_overlap_groups.items():
            if not remaining_entries:
                continue

            target_key = (domain, target_var)
            target_template = template_index.get(target_key)
            if not target_template:
                continue

            needs_codelist_hint = any(
                m.als_record.needs_codelist_hint(self.codelist_trigger_type) for m in remaining_entries
            )

            seen_trans: set[str] = set()
            unique_trans: list[str] = []
            for m in remaining_entries:
                trans = self._generate_transformation(m.als_record)
                if trans not in seen_trans:
                    seen_trans.add(trans)
                    unique_trans.append(trans)

            combined_value = ";\n".join(unique_trans)
            combined_value = self._append_hints_to_transformation(
                combined_value, target_variable=target_var, needs_codelist_hint=needs_codelist_hint
            )
            updates.append(
                CellUpdate(
                    sheet_name=target_template.sheet_name,
                    row=target_template.row_index,
                    col=target_template.col_index,
                    value=combined_value,
                    reason="Simple mapping (merged from conditional_other overlap)",
                )
            )

        logger.info(f"Processed {len(mappings)} conditional_other mappings into {len(updates)} cell updates")

        return updates

    def _create_conditional_records(
        self, mappings: list[ParsedALSMapping], create_test_sheets: bool = True
    ) -> list[ConditionalRecord]:
        """Create conditional records for TEST sheets (if enabled).

        This creates XXTEST sheets with separate handling for TESTCD and TEST:
        - For TESTCD conditions: sheet XXTEST with CRF_TESTCD and CRF_ORRES columns
        - For TEST conditions: sheet XXTEST with CRF_TEST and CRF_ORRES columns

        Each mapping is inserted as a separate row (one row per condition).

        Args:
            mappings: List of conditional mapping records
            create_test_sheets: Whether to create TEST sheets (default: True)

        Returns:
            List of ConditionalRecord objects
        """
        if not create_test_sheets:
            logger.info("Conditional TEST sheet creation disabled")
            return []

        # Group by (domain_prefix, condition_type)
        # Structure: {(domain_prefix, cond_type): [mappings]}
        domain_type_groups: dict[tuple[str, ConditionType], list[ParsedALSMapping]] = defaultdict(list)

        for mapping in mappings:
            domain_prefix = mapping.parsed.domain[:2]  # e.g., "FA" from "FA"
            cond_type = mapping.parsed.condition_type
            if cond_type and cond_type in (ConditionType.TESTCD, ConditionType.TEST):
                domain_type_groups[(domain_prefix, cond_type)].append(mapping)

        # Create conditional records
        cond_records: list[ConditionalRecord] = []

        for (domain_prefix, cond_type), group_mappings in domain_type_groups.items():
            sheet_name = domain_prefix + "TEST"

            # Determine column name based on condition type
            if cond_type == ConditionType.TESTCD:
                cond_col_name = "CRF_TESTCD"
            else:  # ConditionType.TEST
                cond_col_name = "CRF_TEST"

            # Group by condition value to aggregate multiple sources
            value_to_trans: dict[str, list[str]] = defaultdict(list)
            for mapping in group_mappings:
                cond_value = mapping.parsed.test_condition[1] if mapping.parsed.test_condition else ""
                orres_trans = self._generate_transformation(mapping.als_record)
                value_to_trans[cond_value].append(orres_trans)

            # Create rows with aggregated transformations
            rows = []
            for cond_value in sorted(value_to_trans.keys()):
                trans_list = value_to_trans[cond_value]
                # Deduplicate transformations
                unique_trans = list(dict.fromkeys(trans_list))
                # Join multiple sources with newline
                combined_trans = "\n".join(unique_trans)
                rows.append((cond_value, combined_trans))

            # Create conditional record with appropriate column names
            cond_record = ConditionalRecord(
                sheet_name=sheet_name,
                column_names=[cond_col_name, "CRF_ORRES"],
                mappings=cast(list[tuple[str, ...]], rows),
            )

            cond_records.append(cond_record)

            logger.info(f"Conditional record for {sheet_name} ({cond_type.value}): {len(rows)} rows")

        return cond_records

    def _collect_unmatched_records(
        self,
        simple_mappings: list[ParsedALSMapping],
        assignment_mappings: list[ParsedALSMapping],
        template_index: dict[tuple[str, str], TemplateRecord],
    ) -> list[dict]:
        """Collect simple/assignment mappings that have no matching template record.

        These represent SDTM variables that exist in ALS2SDTM but are not present
        in the template. They need to be inserted as new rows.

        Returns:
            List of dicts with keys: domain, variable, transformation, source, als_record
        """
        unmatched: list[dict] = []
        seen: set[tuple[str, str]] = set()

        for mapping in simple_mappings:
            key = (mapping.parsed.domain, mapping.parsed.variable)
            if key in template_index or key in seen:
                continue
            seen.add(key)
            unmatched.append(
                {
                    "domain": mapping.parsed.domain,
                    "variable": mapping.parsed.variable,
                    "transformation": mapping.als_record.get_raw_reference(),
                    "source": "CRF",
                    "variable_label": mapping.als_record.variable_label or mapping.parsed.variable,
                }
            )

        for mapping in assignment_mappings:
            key = (mapping.parsed.domain, mapping.parsed.variable)
            if key in template_index or key in seen:
                continue
            seen.add(key)
            expr = mapping.parsed.assignment_value or ""
            transformation = self._format_assignment_expression(expr)
            unmatched.append(
                {
                    "domain": mapping.parsed.domain,
                    "variable": mapping.parsed.variable,
                    "transformation": transformation,
                    "source": "指定",
                    "variable_label": mapping.als_record.variable_label or mapping.parsed.variable,
                }
            )

        if unmatched:
            logger.info(f"Collected {len(unmatched)} unmatched records (not in template)")

        return unmatched

    def _generate_transformation(self, als_record: ALSRecord) -> str:
        """Generate transformation definition for an ALS record.

        Args:
            als_record: ALS record

        Returns:
            Transformation definition string
        """
        # Return raw reference directly (no ISO 8601 wrapper)
        # Date format hint is now handled via variable_hints.dtc note
        return als_record.get_raw_reference()

    def _extract_supp_additional_condition(self, condition: str | None) -> str:
        """Extract additional condition from SUPP pattern (delegates to rules helper)."""
        return _rules.extract_supp_additional_condition(condition)

    def _get_variable_hints(self, target_variable: str, qnam_value: str | None = None) -> list[str]:
        """Get variable-specific hint notes (delegates to rules helper)."""
        return _rules.get_variable_hints(
            target_variable,
            qnam_value=qnam_value,
            spid_grpid_patterns=self.spid_grpid_patterns,
            spid_grpid_note=self.spid_grpid_note,
            dtc_patterns=self.dtc_patterns,
            dtc_note=self.dtc_note,
        )

    def _append_hints_to_transformation(
        self,
        transformation: str,
        target_variable: str,
        qnam_value: str | None = None,
        needs_codelist_hint: bool = False,
    ) -> str:
        """Append codelist + variable hints (delegates to rules helper)."""
        extra = self._get_variable_hints(target_variable, qnam_value)
        return _rules.append_hints_to_transformation(
            transformation,
            extra_hints=extra,
            needs_codelist_hint=needs_codelist_hint,
            codelist_note=self.codelist_note,
        )

    def _add_cross_domain_references(
        self, template_records: list[TemplateRecord], existing_updates: list[CellUpdate]
    ) -> list[CellUpdate]:
        """Add cross-domain references for special variables (e.g., STUDYID).

        Returns:
            List of new CellUpdate objects
        """
        cross_domain_rules = self.special_variables.get("cross_domain_references", {})
        if not cross_domain_rules:
            return []

        # Build set of sheets with existing updates
        sheets_with_updates = {update.sheet_name for update in existing_updates}

        updates: list[CellUpdate] = []

        for template_rec in template_records:
            var_name = template_rec.variable_name

            if var_name not in cross_domain_rules:
                continue

            rule = cross_domain_rules[var_name]
            source_domain = rule.get("source_domain", "")

            # Skip if this is the source domain
            if template_rec.sheet_name == source_domain:
                continue

            # Only add if sheet has other mappings
            if template_rec.sheet_name not in sheets_with_updates:
                continue

            reference_format = rule.get("reference_format", "")

            update = CellUpdate(
                sheet_name=template_rec.sheet_name,
                row=template_rec.row_index,
                col=template_rec.col_index,
                value=reference_format,
                reason="Cross-domain reference",
            )

            updates.append(update)

            logger.debug(f"Cross-domain reference: {template_rec.sheet_name}.{var_name} -> {reference_format}")

        logger.info(f"Added {len(updates)} cross-domain references")

        return updates
