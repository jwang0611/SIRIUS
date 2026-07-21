"""Mapper-level regression tests for conditional ALS2SDTM expressions."""

from __future__ import annotations

from src.spec_mapper.core.mapper import Mapper
from src.spec_mapper.models import ALSRecord, CodelistRecord, ConditionalRecord, TemplateRecord


def test_if_testcd_mapping_reaches_aggregated_updates_and_codelist() -> None:
    als_record = ALSRecord(
        table="TH",
        variable="THDIA",
        variable_label="Tumor diameter",
        format="TEXT",
        sdtm_domain="FA",
        sdtm_variable="FAORRES if FATESTCD=THDIA",
        row_index=0,
    )
    template_records = [
        TemplateRecord(
            variable_name="FATESTCD",
            transformation_def="",
            row_index=14,
            col_index=8,
            sheet_name="FA",
        ),
        TemplateRecord(
            variable_name="FAORRES",
            transformation_def="",
            row_index=15,
            col_index=8,
            sheet_name="FA",
        ),
    ]

    updates, supp_records, conditional_records, codelist_records, unmatched_records = Mapper().map(
        [als_record], template_records
    )

    updates_by_reason = {update.reason: update for update in updates}
    assert updates_by_reason["Conditional aggregated TESTCD"].value == "THDIA;"
    assert updates_by_reason["Conditional aggregated ORRES"].value == "[RAW]TH.THDIA if FATESTCD=THDIA;"
    assert conditional_records == [
        ConditionalRecord(
            sheet_name="FATEST",
            column_names=["CRF_TESTCD", "CRF_ORRES"],
            mappings=[("THDIA", "[RAW]TH.THDIA")],
        )
    ]
    assert codelist_records == [
        CodelistRecord(
            domain="FA",
            testcd_var="FATESTCD",
            testcd_value="THDIA",
            source_variable="Tumor diameter",
            metadata_variable="THDIA",
        )
    ]
    assert supp_records == []
    assert unmatched_records == []
