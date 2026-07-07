"""Unit tests for ``src.models.boundary`` Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models.boundary import (
    CascadeResult,
    DomainRecommendationRecord,
    MappingResult,
    TableRecommendationRecord,
    VariableInput,
)


class TestVariableInput:
    def test_minimal_construction(self):
        vi = VariableInput()
        assert vi.metadata_table == ""
        assert vi.SDTM_Domain is None

    def test_strips_whitespace(self):
        vi = VariableInput(metadata_variable="  AETERM  ")
        assert vi.metadata_variable == "AETERM"

    def test_extra_fields_preserved(self):
        vi = VariableInput(metadata_table="AE", custom_field="foo")
        dumped = vi.model_dump()
        assert dumped.get("custom_field") == "foo"


class TestDomainRecommendationRecord:
    def test_requires_domain_and_variable(self):
        with pytest.raises(ValidationError):
            DomainRecommendationRecord()

    def test_upper_cases_domain_and_variable(self):
        r = DomainRecommendationRecord(domain="ae", sdtm_variable="aeterm")
        assert r.domain == "AE"
        assert r.sdtm_variable == "AETERM"

    def test_score_range_validation(self):
        with pytest.raises(ValidationError):
            DomainRecommendationRecord(domain="AE", sdtm_variable="AETERM", score=1.5)
        with pytest.raises(ValidationError):
            DomainRecommendationRecord(domain="AE", sdtm_variable="AETERM", score=-0.1)

    def test_cascade_level_range(self):
        for level in (1, 2, 3, 4):
            DomainRecommendationRecord(domain="AE", sdtm_variable="X", cascade_level=level)
        with pytest.raises(ValidationError):
            DomainRecommendationRecord(domain="AE", sdtm_variable="X", cascade_level=5)

    def test_variable_type_enum(self):
        DomainRecommendationRecord(domain="AE", sdtm_variable="X", sdtm_variable_type="supplementary")
        with pytest.raises(ValidationError):
            DomainRecommendationRecord(domain="AE", sdtm_variable="X", sdtm_variable_type="garbage")

    def test_defaults(self):
        r = DomainRecommendationRecord(domain="AE", sdtm_variable="AETERM")
        assert r.source == "LLM"
        assert r.sdtm_variable_type == "standard"
        assert r.priority is None


class TestCascadeResult:
    def test_valid_minimal(self):
        c = CascadeResult(level=1, source="KB_DIRECT", confidence=0.9)
        assert c.short_circuited is False
        assert c.metadata == {}

    def test_level_must_be_1_to_4(self):
        with pytest.raises(ValidationError):
            CascadeResult(level=0, source="LLM", confidence=0.5)
        with pytest.raises(ValidationError):
            CascadeResult(level=5, source="LLM", confidence=0.5)

    def test_source_must_be_one_of_enum(self):
        with pytest.raises(ValidationError):
            CascadeResult(level=1, source="BOGUS", confidence=0.5)

    def test_forbids_extra(self):
        with pytest.raises(ValidationError):
            CascadeResult(level=1, source="LLM", confidence=0.5, unexpected="x")

    def test_confidence_range(self):
        with pytest.raises(ValidationError):
            CascadeResult(level=1, source="LLM", confidence=1.5)


class TestMappingResult:
    def test_composes_children(self):
        mr = MappingResult(
            variable_input=VariableInput(metadata_variable="AETERM"),
            recommendations=[DomainRecommendationRecord(domain="AE", sdtm_variable="AETERM", score=0.9)],
            cascade=CascadeResult(level=1, source="KB_DIRECT", confidence=0.95),
        )
        assert mr.recommendations[0].domain == "AE"
        assert mr.cascade.level == 1
        assert mr.errors == []

    def test_accepts_extra_fields(self):
        mr = MappingResult(
            variable_input=VariableInput(),
            recommendations=[],
            debug_flag=True,
        )
        dumped = mr.model_dump()
        assert "debug_flag" in dumped


class TestTableRecommendationRecord:
    def test_defaults(self):
        t = TableRecommendationRecord(table_name="AE")
        assert t.domain_recommendations == []
        assert t.original_mappings == []

    def test_round_trip(self):
        t = TableRecommendationRecord(
            table_name="AE",
            domain_recommendations=[DomainRecommendationRecord(domain="AE", sdtm_variable="AETERM", score=0.9)],
        )
        data = t.model_dump()
        restored = TableRecommendationRecord.model_validate(data)
        assert restored.table_name == "AE"
        assert len(restored.domain_recommendations) == 1
