"""Behavior contract for the ALS2SDTM variable-expression DSL.

These are characterization tests: they record what
``src/spec_mapper/parsers/sdtm_parser.py`` does today, so that any change to the
parser has to be a deliberate one. The parser is the single front door for every
ALS ``SDTM_Variable`` expression, and most of its error handling is silent
(warn-and-continue), so the failure paths are covered here on purpose.

Tests marked ``QUIRK`` in a comment lock in behavior that is arguably a defect
rather than an intended contract: dropped domains, spurious extra variables,
truncated assignment values, whitespace-only input escaping the empty guard, and
``None`` extractions on conditions the classifier accepted. If you deliberately
fix one of those, update the expectation -- do not read the red test as a
regression and revert the fix.
"""

from __future__ import annotations

import pytest

from src.spec_mapper.parsers import (
    ConditionType,
    MappingType,
    ParsedMapping,
    SDTMVariableParser,
    clean_sdtm_variable,
)


@pytest.fixture
def parser() -> SDTMVariableParser:
    return SDTMVariableParser()


def rows(mappings: list[ParsedMapping]) -> list[tuple[str, str, MappingType, str | None]]:
    """Compact (domain, variable, type, condition) view for multi-mapping assertions."""
    return [(item.domain, item.variable, item.mapping_type, item.condition) for item in mappings]


# ---------------------------------------------------------------------------
# clean_sdtm_variable
# ---------------------------------------------------------------------------


def test_clean_sdtm_variable_normalizes_condition_spacing() -> None:
    assert clean_sdtm_variable('  QVAL  when  QNAM = "SAECAT"  ') == 'QVAL when QNAM="SAECAT"'


def test_clean_sdtm_variable_returns_falsy_input_unchanged() -> None:
    assert clean_sdtm_variable("") == ""


def test_clean_sdtm_variable_collapses_tabs_and_newlines() -> None:
    assert clean_sdtm_variable("AETERM\twhen\nAESER = Y") == "AETERM when AESER=Y"


def test_clean_sdtm_variable_strips_spaces_around_every_equals() -> None:
    # The "=" rule is global, not limited to the first occurrence.
    assert clean_sdtm_variable("A = B = C") == "A=B=C"


# ---------------------------------------------------------------------------
# SIMPLE mappings
# ---------------------------------------------------------------------------


def test_simple_mapping_uppercases_domain_and_variable(parser: SDTMVariableParser) -> None:
    [mapping] = parser.parse("ae", "aeterm")

    assert (mapping.domain, mapping.variable, mapping.mapping_type) == ("AE", "AETERM", MappingType.SIMPLE)
    assert mapping.condition is None
    # original_variable keeps the author's casing; only the emitted variable is uppercased.
    assert mapping.original_variable == "aeterm"
    assert mapping.get_transformation_suffix() == ""


def test_multi_variable_without_condition_yields_simple_mappings(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("AE", "AESDTH/AESLIFE/AESHOSP")

    assert [item.variable for item in mappings] == ["AESDTH", "AESLIFE", "AESHOSP"]
    assert all(item.mapping_type == MappingType.SIMPLE and item.condition is None for item in mappings)


# ---------------------------------------------------------------------------
# empty / whitespace-only input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("domain", "variable"),
    [("", "AETERM"), ("AE", "")],
)
def test_empty_domain_or_variable_returns_no_mapping(parser: SDTMVariableParser, domain: str, variable: str) -> None:
    assert parser.parse(domain, variable) == []


def test_whitespace_only_domain_yields_empty_domain_mapping(parser: SDTMVariableParser) -> None:
    # QUIRK: the guard in parse() tests falsiness before cleaning, so "   " slips
    # through and produces a mapping with an empty domain instead of no mapping.
    [mapping] = parser.parse("   ", "AETERM")

    assert (mapping.domain, mapping.variable) == ("", "AETERM")


def test_whitespace_only_variable_yields_empty_variable_mapping(parser: SDTMVariableParser) -> None:
    # QUIRK: same guard gap on the variable side -- an empty SIMPLE variable, not [].
    [mapping] = parser.parse("AE", "   ")

    assert (mapping.domain, mapping.variable, mapping.mapping_type) == ("AE", "", MappingType.SIMPLE)


# ---------------------------------------------------------------------------
# | separator (cross-domain)
# ---------------------------------------------------------------------------


def test_pipe_pairs_each_variable_group_with_its_domain(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("CM|PR", 'CMTRT when PRDYN="1"|PRTRT when PRDTN="2"')

    assert [(item.domain, item.variable) for item in mappings] == [("CM", "CMTRT"), ("PR", "PRTRT")]
    assert [item.mapping_type for item in mappings] == [
        MappingType.CONDITIONAL_OTHER,
        MappingType.CONDITIONAL_OTHER,
    ]
    assert [item.test_condition for item in mappings] == [("PRDYN", "1"), ("PRDTN", "2")]


def test_pipe_in_domain_only_is_not_split(parser: SDTMVariableParser) -> None:
    # Cross-domain splitting is driven by the variable string; a lone "|" in the
    # domain is carried through verbatim.
    [mapping] = parser.parse("CM|PR", "CMTRT")

    assert mapping.domain == "CM|PR"


def test_single_domain_is_broadcast_to_every_pipe_group(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("AE", "AESDTH|QVAL when QNAM=SAECAT")

    assert [(item.domain, item.variable, item.mapping_type) for item in mappings] == [
        ("AE", "AESDTH", MappingType.SIMPLE),
        ("AE", "QVAL", MappingType.SUPP),
    ]


def test_extra_variable_groups_reuse_the_last_domain(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("CM|PR", "CMTRT|PRTRT|DSTERM")

    assert [(item.domain, item.variable) for item in mappings] == [
        ("CM", "CMTRT"),
        ("PR", "PRTRT"),
        ("PR", "DSTERM"),
    ]


def test_extra_domains_are_dropped(parser: SDTMVariableParser) -> None:
    # QUIRK: zip(..., strict=False) silently discards the surplus DS domain after
    # only logging a warning; the row never reaches the generated Spec.
    mappings = parser.parse("CM|PR|DS", "CMTRT|PRTRT")

    assert [(item.domain, item.variable) for item in mappings] == [("CM", "CMTRT"), ("PR", "PRTRT")]


def test_empty_pipe_segments_are_skipped(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("CM||PR", "CMTRT||PRTRT")

    assert [(item.domain, item.variable) for item in mappings] == [("CM", "CMTRT"), ("PR", "PRTRT")]


def test_trailing_pipe_group_is_skipped(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("CM|PR", "CMTRT|")

    assert [(item.domain, item.variable) for item in mappings] == [("CM", "CMTRT")]


def test_pipe_group_may_itself_contain_slash_and_shared_condition(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("VS|EG", "VSORRES/VSORRESU when VSTESTCD=WEIGHT|EGORRES when EGTESTCD=QT")

    assert [(item.domain, item.variable, item.condition) for item in mappings] == [
        ("VS", "VSORRES", "when VSTESTCD=WEIGHT"),
        ("VS", "VSORRESU", "when VSTESTCD=WEIGHT"),
        ("EG", "EGORRES", "when EGTESTCD=QT"),
    ]


# ---------------------------------------------------------------------------
# / and // separators (multi-variable)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("separator", ["/", "//"])
def test_slash_variants_expand_variables_and_share_when_condition(parser: SDTMVariableParser, separator: str) -> None:
    mappings = parser.parse("VS", f"VSORRES{separator}VSORRESU when VSTESTCD=WEIGHT")

    assert [item.variable for item in mappings] == ["VSORRES", "VSORRESU"]
    assert all(item.mapping_type == MappingType.CONDITIONAL for item in mappings)
    assert all(item.condition == "when VSTESTCD=WEIGHT" for item in mappings)
    assert all(item.test_condition == ("VSTESTCD", "WEIGHT") for item in mappings)
    assert all(item.condition_type == ConditionType.TESTCD for item in mappings)


def test_leading_double_slash_segments_are_skipped(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("VS", "//VSORRES/VSORRESU when VSTESTCD=WEIGHT")

    assert [item.variable for item in mappings] == ["VSORRES", "VSORRESU"]


def test_trailing_slash_before_shared_condition_is_skipped(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("VS", "VSORRES/VSORRESU/ when VSTESTCD=WEIGHT")

    assert [item.variable for item in mappings] == ["VSORRES", "VSORRESU"]
    assert all(item.condition == "when VSTESTCD=WEIGHT" for item in mappings)


def test_trailing_slash_without_condition_is_skipped(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("AE", "AESDTH/AESLIFE/")

    assert [item.variable for item in mappings] == ["AESDTH", "AESLIFE"]


def test_slash_only_input_yields_no_mappings(parser: SDTMVariableParser) -> None:
    assert parser.parse("AE", "///") == []


def test_shared_condition_is_appended_to_every_variable(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("AE", "AESDTH/AESLIFE/AESHOSP when AETESTCD=X")

    assert [item.variable for item in mappings] == ["AESDTH", "AESLIFE", "AESHOSP"]
    assert all(item.condition == "when AETESTCD=X" for item in mappings)
    # original_variable is reconstructed per variable, not copied from the raw group.
    assert [item.original_variable for item in mappings] == [
        "AESDTH when AETESTCD=X",
        "AESLIFE when AETESTCD=X",
        "AESHOSP when AETESTCD=X",
    ]


def test_condition_before_last_slash_is_not_shared(parser: SDTMVariableParser) -> None:
    # A when/if clause that starts before the last "/" belongs to its own segment.
    mappings = parser.parse("VS", "VSORRES when VSTESTCD=A/VSORRESU")

    assert rows(mappings) == [
        ("VS", "VSORRES", MappingType.CONDITIONAL, "when VSTESTCD=A"),
        ("VS", "VSORRESU", MappingType.SIMPLE, None),
    ]


def test_slash_inside_condition_value_splits_the_condition(parser: SDTMVariableParser) -> None:
    # QUIRK: a "/" inside the condition value defeats shared-condition detection,
    # drops the condition from VSORRES and emits a bogus VS.B variable.
    mappings = parser.parse("VS", "VSORRES/VSORRESU when VSTESTCD=A/B")

    assert rows(mappings) == [
        ("VS", "VSORRES", MappingType.SIMPLE, None),
        ("VS", "VSORRESU", MappingType.CONDITIONAL, "when VSTESTCD=A"),
        ("VS", "B", MappingType.SIMPLE, None),
    ]


def test_segment_with_own_condition_keeps_it_and_bare_segment_stays_simple(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("VS", "VSORRES when VSTESTCD=X/VSORRESU/VSSTAT when VSTESTCD=Y")

    assert rows(mappings) == [
        ("VS", "VSORRES", MappingType.CONDITIONAL, "when VSTESTCD=X"),
        ("VS", "VSORRESU", MappingType.SIMPLE, None),
        ("VS", "VSSTAT", MappingType.CONDITIONAL, "when VSTESTCD=Y"),
    ]


def test_shared_condition_applies_to_supp_segment_and_sibling(parser: SDTMVariableParser) -> None:
    # QUIRK: the shared-condition rule cannot tell a SUPP QNAM predicate apart from
    # a real condition, so the sibling AESDTH inherits "when QNAM=SAECAT" too.
    mappings = parser.parse("AE", "AESDTH/QVAL when QNAM=SAECAT")

    assert rows(mappings) == [
        ("AE", "AESDTH", MappingType.CONDITIONAL_OTHER, "when QNAM=SAECAT"),
        ("AE", "QVAL", MappingType.SUPP, "when QNAM=SAECAT"),
    ]
    assert mappings[1].qnam_value == "SAECAT"


# ---------------------------------------------------------------------------
# when / if keyword handling
# ---------------------------------------------------------------------------


def test_if_condition_is_extracted_for_each_variable(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("FA", "FAORRES/FAORRESU if FATEST=DIAMETER")

    assert [item.variable for item in mappings] == ["FAORRES", "FAORRESU"]
    assert all(item.condition == "if FATEST=DIAMETER" for item in mappings)
    assert all(item.test_condition == ("FATEST", "DIAMETER") for item in mappings)
    assert all(item.condition_type == ConditionType.TEST for item in mappings)


def test_if_testcd_condition_is_extracted(parser: SDTMVariableParser) -> None:
    [mapping] = parser.parse("FA", "FAORRES if FATESTCD=THDIA")

    assert mapping.mapping_type == MappingType.CONDITIONAL
    assert mapping.condition == "if FATESTCD=THDIA"
    assert mapping.test_condition == ("FATESTCD", "THDIA")
    assert mapping.condition_type == ConditionType.TESTCD


def test_if_condition_preserves_non_test_predicate(parser: SDTMVariableParser) -> None:
    [mapping] = parser.parse("DS", "DSDECOD if DSSCAT=DISPOSITION")

    assert mapping.mapping_type == MappingType.CONDITIONAL_OTHER
    assert mapping.test_condition == ("DSSCAT", "DISPOSITION")
    assert mapping.condition_type == ConditionType.OTHER
    assert mapping.get_transformation_suffix() == " if DSSCAT=DISPOSITION"


@pytest.mark.parametrize("keyword", ["WHEN", "If", "wHeN"])
def test_condition_keyword_is_case_insensitive_and_casing_preserved(parser: SDTMVariableParser, keyword: str) -> None:
    [mapping] = parser.parse("AE", f"AETERM {keyword} AESER=Y")

    assert mapping.mapping_type == MappingType.CONDITIONAL_OTHER
    # The author's original keyword casing survives into the emitted condition.
    assert mapping.condition == f"{keyword} AESER=Y"
    assert mapping.test_condition == ("AESER", "Y")
    assert mapping.get_transformation_suffix() == f" {keyword} AESER=Y"


def test_condition_keyword_at_start_falls_back_to_simple(parser: SDTMVariableParser) -> None:
    # The keyword regex requires whitespace before "when", so a leading keyword is
    # not a condition -- the whole string becomes the variable name.
    [mapping] = parser.parse("AE", "when AESER=Y")

    assert (mapping.mapping_type, mapping.variable) == (MappingType.SIMPLE, "WHEN AESER=Y")


def test_dangling_condition_keyword_falls_back_to_simple(parser: SDTMVariableParser) -> None:
    # Likewise the regex requires whitespace after the keyword.
    [mapping] = parser.parse("AE", "AETERM when")

    assert (mapping.mapping_type, mapping.variable) == (MappingType.SIMPLE, "AETERM WHEN")


# ---------------------------------------------------------------------------
# TEST / TESTCD / OTHER classification
# ---------------------------------------------------------------------------


def test_conditional_testcd_suffix_is_the_full_condition(parser: SDTMVariableParser) -> None:
    [mapping] = parser.parse("VS", "VSORRES when VSTESTCD=WEIGHT")

    assert mapping.get_transformation_suffix() == " when VSTESTCD=WEIGHT"


def test_and_chained_test_condition_keeps_only_first_predicate(parser: SDTMVariableParser) -> None:
    [mapping] = parser.parse("MI", "MIORRES when MITEST=MSI and MINAM=本地实验室")

    assert mapping.mapping_type == MappingType.CONDITIONAL
    # The full clause is preserved; only the extracted tuple is truncated.
    assert mapping.condition == "when MITEST=MSI and MINAM=本地实验室"
    assert mapping.test_condition == ("MITEST", "MSI")
    assert mapping.condition_type == ConditionType.TEST


def test_and_chained_other_condition_keeps_only_first_predicate(parser: SDTMVariableParser) -> None:
    [mapping] = parser.parse("EC", "ECDOSE when ECMOOD=已执行 and ECCAT=A")

    assert mapping.mapping_type == MappingType.CONDITIONAL_OTHER
    assert mapping.test_condition == ("ECMOOD", "已执行")
    assert mapping.condition_type == ConditionType.OTHER


def test_or_chained_condition_classifies_but_extracts_nothing(parser: SDTMVariableParser) -> None:
    # QUIRK: classifier and extractor disagree on or-chains. The row is CONDITIONAL
    # but carries no test_condition, so downstream TESTCD grouping never sees it.
    [mapping] = parser.parse("VS", "VSORRES when VSTESTCD=WEIGHT or VSTESTCD=HEIGHT")

    assert mapping.mapping_type == MappingType.CONDITIONAL
    assert mapping.test_condition is None
    assert mapping.condition_type is None


def test_nested_condition_keyword_makes_extractor_disagree_with_classifier(parser: SDTMVariableParser) -> None:
    # QUIRK (synthetic input): the classifier anchors on the first predicate while
    # the extractor searches for any when/if, so a nested keyword makes a
    # CONDITIONAL row report an OTHER condition type.
    [mapping] = parser.parse("VS", "VSORRES when VSTESTCD=A B and if AESER=Y")

    assert mapping.mapping_type == MappingType.CONDITIONAL
    assert mapping.test_condition == ("AESER", "Y")
    assert mapping.condition_type == ConditionType.OTHER


def test_chinese_predicate_without_equals_is_other_with_no_tuple(parser: SDTMVariableParser) -> None:
    [mapping] = parser.parse("DS", "DSSTDTC if DSSTDTC不为空")

    assert mapping.mapping_type == MappingType.CONDITIONAL_OTHER
    assert mapping.condition == "if DSSTDTC不为空"
    # No "=" in the predicate, so nothing can be extracted, but the type is still OTHER.
    assert mapping.test_condition is None
    assert mapping.condition_type == ConditionType.OTHER


def test_chinese_condition_value_is_preserved(parser: SDTMVariableParser) -> None:
    [mapping] = parser.parse("DS", "DSTERM when DSDECOD=知情同意书签署")

    assert mapping.test_condition == ("DSDECOD", "知情同意书签署")


def test_parenthesised_condition_is_other_with_no_tuple(parser: SDTMVariableParser) -> None:
    # QUIRK: parentheses defeat both the anchored classifier and the extractor, so a
    # TESTCD predicate silently degrades to CONDITIONAL_OTHER with no tuple.
    [mapping] = parser.parse("VS", "VSORRES when (VSTESTCD=WEIGHT)")

    assert mapping.mapping_type == MappingType.CONDITIONAL_OTHER
    assert mapping.test_condition is None


def test_quoted_condition_value_is_unquoted_in_tuple_but_kept_in_condition(parser: SDTMVariableParser) -> None:
    [mapping] = parser.parse("CM", 'CMTRT when PRDYN = "1"')

    assert mapping.condition == 'when PRDYN="1"'
    assert mapping.test_condition == ("PRDYN", "1")


# ---------------------------------------------------------------------------
# SUPP (QVAL when QNAM=XXX)
# ---------------------------------------------------------------------------


def test_supp_expression_extracts_qnam(parser: SDTMVariableParser) -> None:
    [mapping] = parser.parse("AE", "QVAL when QNAM=SAECAT")

    assert mapping.mapping_type == MappingType.SUPP
    assert mapping.variable == "QVAL"
    assert mapping.qnam_value == "SAECAT"
    assert mapping.get_transformation_suffix() == ""


def test_supp_accepts_if_keyword(parser: SDTMVariableParser) -> None:
    [mapping] = parser.parse("AE", "QVAL if QNAM=SAECAT")

    assert (mapping.mapping_type, mapping.qnam_value) == (MappingType.SUPP, "SAECAT")


def test_supp_detection_is_case_insensitive_but_value_casing_preserved(parser: SDTMVariableParser) -> None:
    [mapping] = parser.parse("AE", "qval when qnam=saecat")

    # The variable is uppercased, the QNAM value is not.
    assert (mapping.variable, mapping.mapping_type, mapping.qnam_value) == ("QVAL", MappingType.SUPP, "saecat")


def test_supp_quoted_qnam_value_is_unquoted(parser: SDTMVariableParser) -> None:
    [mapping] = parser.parse("AE", 'QVAL when QNAM = "SAECAT"')

    assert mapping.qnam_value == "SAECAT"


def test_supp_qnam_value_stops_at_comma(parser: SDTMVariableParser) -> None:
    [mapping] = parser.parse("AE", "QVAL when QNAM=SAECAT, other stuff")

    assert mapping.qnam_value == "SAECAT"


def test_supp_without_qnam_assignment_has_no_qnam_value(parser: SDTMVariableParser) -> None:
    # SUPP detection only needs "QNAM" as a substring of the condition, so a
    # non-assignment predicate still classifies as SUPP but yields no QNAM value.
    [mapping] = parser.parse("AE", "QVAL when QNAM is SAECAT")

    assert mapping.mapping_type == MappingType.SUPP
    assert mapping.qnam_value is None


def test_qnam_condition_on_non_qval_variable_is_not_supp(parser: SDTMVariableParser) -> None:
    # SUPP requires the variable to be exactly QVAL.
    [mapping] = parser.parse("AE", "AEVAL when QNAM=SAECAT")

    assert mapping.mapping_type == MappingType.CONDITIONAL_OTHER
    assert mapping.qnam_value is None
    assert mapping.test_condition == ("QNAM", "SAECAT")


# ---------------------------------------------------------------------------
# ASSIGNMENT (VARIABLE=value)
# ---------------------------------------------------------------------------


def test_assignment_segments_remain_independent(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("DS", "DSCAT=方案里程碑/DSSCAT=随机化 when RANDYN=是")

    assert [item.variable for item in mappings] == ["DSCAT", "DSSCAT"]
    assert all(item.mapping_type == MappingType.ASSIGNMENT for item in mappings)
    assert [item.assignment_value for item in mappings] == ["方案里程碑", "随机化 when RANDYN=是"]


def test_assignment_clears_condition_and_emits_no_suffix(parser: SDTMVariableParser) -> None:
    [mapping] = parser.parse("DS", "DSCAT=方案里程碑")

    assert mapping.mapping_type == MappingType.ASSIGNMENT
    # __post_init__ moves the parsed clause into assignment_value and nulls condition.
    assert mapping.assignment_value == "方案里程碑"
    assert mapping.condition is None
    assert mapping.get_transformation_suffix() == ""


def test_assignment_tolerates_spaces_around_equals(parser: SDTMVariableParser) -> None:
    # clean_sdtm_variable normalises first, so the anchored ^\w+= still matches.
    [mapping] = parser.parse("DS", "DSCAT = 方案里程碑")

    assert (mapping.mapping_type, mapping.assignment_value) == (MappingType.ASSIGNMENT, "方案里程碑")


def test_assignment_value_is_split_by_slash(parser: SDTMVariableParser) -> None:
    # QUIRK: an assignment value cannot contain "/" -- it is truncated and the
    # remainder becomes a spurious variable.
    mappings = parser.parse("DS", "DSCAT=A/B")

    assert rows(mappings) == [
        ("DS", "DSCAT", MappingType.ASSIGNMENT, None),
        ("DS", "B", MappingType.SIMPLE, None),
    ]
    assert mappings[0].assignment_value == "A"


def test_assignment_only_in_later_segment_uses_shared_condition_path(parser: SDTMVariableParser) -> None:
    # The independent-segment fast path only fires when the FIRST segment is an
    # assignment; otherwise the shared-condition path swallows the clause into the
    # assignment value.
    mappings = parser.parse("DS", "DSSTDTC/DSCAT=里程碑 if DSSTDTC不为空")

    assert rows(mappings) == [
        ("DS", "DSSTDTC", MappingType.CONDITIONAL_OTHER, "if DSSTDTC不为空"),
        ("DS", "DSCAT", MappingType.ASSIGNMENT, None),
    ]
    assert mappings[1].assignment_value == "里程碑 if DSSTDTC不为空"


def test_empty_segment_in_assignment_group_is_skipped(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("DS", "DSCAT=A//DSSCAT=B")

    assert [(item.variable, item.assignment_value) for item in mappings] == [("DSCAT", "A"), ("DSSCAT", "B")]


def test_identifier_starting_with_digit_is_not_an_assignment(parser: SDTMVariableParser) -> None:
    # ^[A-Za-z_]\w*= requires a letter or underscore first.
    [mapping] = parser.parse("DS", "1DSCAT=x")

    assert (mapping.mapping_type, mapping.variable) == (MappingType.SIMPLE, "1DSCAT=X")
