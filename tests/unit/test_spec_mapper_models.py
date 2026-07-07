"""Unit tests for spec_mapper dataclass models."""

from __future__ import annotations

import pytest

from src.spec_mapper.models.als_record import ALSRecord
from src.spec_mapper.models.codelist_record import CodelistRecord
from src.spec_mapper.models.conditional_record import ConditionalRecord
from src.spec_mapper.models.supp_record import SUPPRecord
from src.spec_mapper.models.template_record import CellUpdate, TemplateRecord


def _als(**overrides):
    defaults = {
        "table": "DM",
        "variable": "SUBJID",
        "variable_label": "受试者编号",
        "format": "TEXT",
        "sdtm_domain": "DM",
        "sdtm_variable": "USUBJID",
        "row_index": 5,
    }
    defaults.update(overrides)
    return ALSRecord(**defaults)


class TestALSRecord:
    def test_strips_whitespace(self):
        r = _als(
            table=" DM ",
            variable=" SUBJID ",
            variable_label=" 受试者 ",
            sdtm_domain=" DM ",
            sdtm_variable=" USUBJID ",
            format=" TEXT ",
        )
        assert r.table == "DM"
        assert r.variable == "SUBJID"
        assert r.variable_label == "受试者"
        assert r.sdtm_domain == "DM"
        assert r.sdtm_variable == "USUBJID"
        assert r.format == "TEXT"

    def test_handles_empty_strings(self):
        r = _als(table="", variable="", variable_label="", sdtm_domain="", sdtm_variable="", format=None)
        assert r.table == ""
        assert r.format is None

    def test_strips_optional_fields(self):
        r = _als(metadata_table=" M ", component_type=" 单选 ", codelist_name=" CL ")
        assert r.metadata_table == "M"
        assert r.component_type == "单选"
        assert r.codelist_name == "CL"

    def test_is_valid_true_for_complete_record(self):
        assert _als().is_valid() is True

    def test_is_valid_false_when_required_missing(self):
        assert _als(table="").is_valid() is False
        assert _als(variable="").is_valid() is False
        assert _als(sdtm_domain="").is_valid() is False
        assert _als(sdtm_variable="").is_valid() is False

    def test_get_raw_reference_uses_metadata_table(self):
        r = _als(metadata_table="DM_RAW")
        assert r.get_raw_reference() == "[RAW]DM_RAW.SUBJID"

    def test_get_raw_reference_falls_back_to_table(self):
        r = _als()
        assert r.get_raw_reference() == "[RAW]DM.SUBJID"

    def test_needs_codelist_hint_default_trigger(self):
        r = _als(component_type="单选", codelist_name="SEX")
        assert r.needs_codelist_hint() is True

    def test_needs_codelist_hint_custom_trigger(self):
        r = _als(component_type="多选", codelist_name="CL")
        assert r.needs_codelist_hint(trigger_component_type="多选") is True

    def test_needs_codelist_hint_false_when_empty(self):
        r = _als(component_type="单选", codelist_name="")
        assert r.needs_codelist_hint() is False
        r2 = _als(component_type="文本", codelist_name="SEX")
        assert r2.needs_codelist_hint() is False


class TestTemplateRecord:
    def test_strips_and_extracts_domain_from_2char_sheet(self):
        r = TemplateRecord(
            variable_name=" AETERM ",
            transformation_def=" [RAW] ",
            row_index=15,
            col_index=5,
            sheet_name=" AE ",
        )
        assert r.variable_name == "AETERM"
        assert r.transformation_def == "[RAW]"
        assert r.sheet_name == "AE"
        assert r.domain == "AE"

    def test_domain_not_autoextracted_when_set(self):
        r = TemplateRecord(
            variable_name="V",
            transformation_def="",
            row_index=1,
            col_index=1,
            sheet_name="LONG_NAME",
            domain="LB",
        )
        assert r.domain == "LB"

    def test_domain_none_for_multichar_sheet(self):
        r = TemplateRecord(
            variable_name="V",
            transformation_def="",
            row_index=1,
            col_index=1,
            sheet_name="SUPPDM",
        )
        assert r.domain is None

    def test_is_valid(self):
        r = TemplateRecord(variable_name="V", transformation_def="", row_index=1, col_index=1, sheet_name="DM")
        assert r.is_valid() is True

    def test_is_valid_false_for_missing_fields(self):
        assert not TemplateRecord(
            variable_name="",
            transformation_def="",
            row_index=1,
            col_index=1,
            sheet_name="DM",
        ).is_valid()
        assert not TemplateRecord(
            variable_name="V",
            transformation_def="",
            row_index=0,
            col_index=1,
            sheet_name="DM",
        ).is_valid()
        assert not TemplateRecord(
            variable_name="V",
            transformation_def="",
            row_index=1,
            col_index=0,
            sheet_name="DM",
        ).is_valid()
        assert not TemplateRecord(
            variable_name="V",
            transformation_def="",
            row_index=1,
            col_index=1,
            sheet_name="",
        ).is_valid()


class TestCellUpdate:
    def test_valid_cell_update(self):
        u = CellUpdate(sheet_name="DM", row=5, col=3, value="x")
        assert u.sheet_name == "DM"
        assert u.reason is None

    def test_rejects_non_positive_row_col(self):
        with pytest.raises(ValueError):
            CellUpdate(sheet_name="DM", row=0, col=1, value="x")
        with pytest.raises(ValueError):
            CellUpdate(sheet_name="DM", row=1, col=-1, value="x")


class TestCodelistRecord:
    def test_to_row_data(self):
        r = CodelistRecord(
            domain="EG",
            testcd_var="EGTESTCD",
            testcd_value="QT",
            source_variable="QT间期",
            metadata_variable="QTINT",
        )
        row = r.to_row_data()
        assert row[1] == "EG"
        assert row[2] == "EGTESTCD"
        assert row[5] == "Char"
        assert row[6] == "QT"
        assert row[9] == "QT间期"
        assert row[10] == "QTINT"

    def test_repr(self):
        r = CodelistRecord(
            domain="EG",
            testcd_var="EGTESTCD",
            testcd_value="QT",
            source_variable="s",
        )
        assert "EG" in repr(r)
        assert "QT" in repr(r)


class TestSUPPRecord:
    def test_to_row_data(self):
        r = SUPPRecord(
            sheet_name="DM",
            variable_name="SITENAM",
            variable_label="中心名称",
            transformation_def="[RAW]",
            insert_row=10,
        )
        row = r.to_row_data()
        assert row[1] == "SITENAM"
        assert row[2] == "中心名称"
        assert row[3] == "Char"
        assert row[4] == "200"
        assert row[6] == "CRF"
        assert row[8] == "[RAW]"
        assert row[9] == "SUPP"

    def test_repr(self):
        r = SUPPRecord(
            sheet_name="DM",
            variable_name="X",
            variable_label="l",
            transformation_def="",
            insert_row=1,
        )
        assert "DM" in repr(r)


class TestConditionalRecord:
    def test_repr(self):
        r = ConditionalRecord(
            sheet_name="FATEST",
            column_names=["CRF_TESTCD", "CRF_ORRES"],
            mappings=[("THDIA", "[RAW]TH.THDIA"), ("FDTC", "[RAW]TH.F")],
        )
        text = repr(r)
        assert "FATEST" in text
        assert "count=2" in text
