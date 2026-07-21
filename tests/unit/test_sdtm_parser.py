"""Behavior contract for the ALS2SDTM variable-expression DSL."""

from __future__ import annotations

import pytest

from src.spec_mapper.parsers import ConditionType, MappingType, SDTMVariableParser, clean_sdtm_variable


@pytest.fixture
def parser() -> SDTMVariableParser:
    return SDTMVariableParser()


def test_clean_sdtm_variable_normalizes_condition_spacing() -> None:
    assert clean_sdtm_variable('  QVAL  when  QNAM = "SAECAT"  ') == 'QVAL when QNAM="SAECAT"'


def test_pipe_pairs_each_variable_group_with_its_domain(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("CM|PR", 'CMTRT when PRDYN="1"|PRTRT when PRDTN="2"')

    assert [(item.domain, item.variable) for item in mappings] == [("CM", "CMTRT"), ("PR", "PRTRT")]
    assert [item.mapping_type for item in mappings] == [
        MappingType.CONDITIONAL_OTHER,
        MappingType.CONDITIONAL_OTHER,
    ]
    assert [item.test_condition for item in mappings] == [("PRDYN", "1"), ("PRDTN", "2")]


@pytest.mark.parametrize("separator", ["/", "//"])
def test_slash_variants_expand_variables_and_share_when_condition(parser: SDTMVariableParser, separator: str) -> None:
    mappings = parser.parse("VS", f"VSORRES{separator}VSORRESU when VSTESTCD=WEIGHT")

    assert [item.variable for item in mappings] == ["VSORRES", "VSORRESU"]
    assert all(item.mapping_type == MappingType.CONDITIONAL for item in mappings)
    assert all(item.condition == "when VSTESTCD=WEIGHT" for item in mappings)
    assert all(item.test_condition == ("VSTESTCD", "WEIGHT") for item in mappings)
    assert all(item.condition_type == ConditionType.TESTCD for item in mappings)


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


def test_supp_expression_extracts_qnam(parser: SDTMVariableParser) -> None:
    [mapping] = parser.parse("AE", "QVAL when QNAM=SAECAT")

    assert mapping.mapping_type == MappingType.SUPP
    assert mapping.variable == "QVAL"
    assert mapping.qnam_value == "SAECAT"
    assert mapping.get_transformation_suffix() == ""


def test_assignment_segments_remain_independent(parser: SDTMVariableParser) -> None:
    mappings = parser.parse("DS", "DSCAT=方案里程碑/DSSCAT=随机化 when RANDYN=是")

    assert [item.variable for item in mappings] == ["DSCAT", "DSSCAT"]
    assert all(item.mapping_type == MappingType.ASSIGNMENT for item in mappings)
    assert [item.assignment_value for item in mappings] == ["方案里程碑", "随机化 when RANDYN=是"]


@pytest.mark.parametrize(
    ("domain", "variable"),
    [("", "AETERM"), ("AE", "")],
)
def test_empty_domain_or_variable_returns_no_mapping(parser: SDTMVariableParser, domain: str, variable: str) -> None:
    assert parser.parse(domain, variable) == []
