"""Unit tests for ``src.processors.als_converter``."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.processors.als_converter import (
    batch_convert,
    convert_als2sdtm,
    excel_col_to_index,
    list_sheets,
)

# Reduced mapping for test workbooks that do not include metadata columns.
_MINIMAL_MAPPING: dict[str, list[str]] = {
    "annotation_table": ["表名", "annotation_table"],
    "annotation_variable": ["变量名", "annotation_variable"],
    "SDTM_Domain": ["SDTM_Domain"],
    "SDTM_Variable": ["SDTM_Variable"],
}


@pytest.fixture
def sample_als_xlsx(tmp_path):
    """构造一个最小化的 ALS 风格 Excel 测试文件。"""
    file_path = tmp_path / "ALS_Test.xlsx"

    # Sheet1 – 非空，用于常规测试
    df1 = pd.DataFrame(
        {
            "表名": ["AE", "CM", "LB"],
            "变量名": ["不良反应名称", "药物名称", "检验项"],
            "SDTM_Domain": ["AE", "CM", "LB"],
            "SDTM_Variable": ["AETERM", "CMTRT", "LBTEST"],
        }
    )

    # Sheet2 – 完全为空，用于 list_sheets 过滤测试
    df2 = pd.DataFrame()

    # Sheet3 – 另一张非空表，使用中文替代列名
    df3 = pd.DataFrame(
        {
            "表名称": ["DM", "MH"],
            "变量名/注释/内嵌表名": ["受试者编号", "病史"],
            "SDTM 域": ["DM", "MH"],
            "SDTM 变量": ["USUBJID", "MHTERM"],
        }
    )

    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        df1.to_excel(writer, sheet_name="eCRF", index=False)
        df2.to_excel(writer, sheet_name="Empty", index=False)
        df3.to_excel(writer, sheet_name="Chinese", index=False)

    return str(file_path)


class TestExcelColToIndex:
    def test_single_letter(self):
        assert excel_col_to_index("A") == 0
        assert excel_col_to_index("Z") == 25

    def test_multi_letter(self):
        assert excel_col_to_index("AA") == 26
        assert excel_col_to_index("ab") == 27

    def test_lowercase(self):
        assert excel_col_to_index("a") == 0


class TestListSheets:
    def test_returns_only_non_empty_sheets(self, sample_als_xlsx):
        sheets = list_sheets(sample_als_xlsx)
        assert "eCRF" in sheets
        assert "Chinese" in sheets
        assert "Empty" not in sheets

    def test_returns_all_when_every_sheet_empty(self, tmp_path):
        file_path = tmp_path / "AllEmpty.xlsx"
        df = pd.DataFrame()
        with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="S1", index=False)
            df.to_excel(writer, sheet_name="S2", index=False)
        sheets = list_sheets(str(file_path))
        assert sheets == ["S1", "S2"]


class TestConvertAls2Sdtm:
    def test_writes_both_formats(self, tmp_path, sample_als_xlsx):
        output_dir = tmp_path / "out"
        result = convert_als2sdtm(
            input_file=sample_als_xlsx,
            output_dir=str(output_dir),
            sheet_name="eCRF",
            output_format="both",
            column_mapping=_MINIMAL_MAPPING,
        )
        assert "json" in result
        assert "parquet" in result
        assert Path(result["json"]).exists()
        assert Path(result["parquet"]).exists()

    def test_auto_detects_sheet_when_none(self, tmp_path, sample_als_xlsx):
        output_dir = tmp_path / "out"
        result = convert_als2sdtm(
            input_file=sample_als_xlsx,
            output_dir=str(output_dir),
            sheet_name=None,
            output_format="json",
            column_mapping=_MINIMAL_MAPPING,
        )
        assert "json" in result
        assert Path(result["json"]).exists()

    def test_drops_fully_empty_rows(self, tmp_path):
        file_path = tmp_path / "WithEmpty.xlsx"
        df = pd.DataFrame(
            {
                "表名": ["AE", None, "CM"],
                "变量名": ["术语", None, "药物"],
                "SDTM_Domain": ["AE", None, "CM"],
                "SDTM_Variable": ["AETERM", None, "CMTRT"],
            }
        )
        with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Sheet1", index=False)

        output_dir = tmp_path / "out"
        convert_als2sdtm(
            input_file=str(file_path),
            output_dir=str(output_dir),
            sheet_name="Sheet1",
            output_format="json",
            column_mapping=_MINIMAL_MAPPING,
        )

        df = pd.read_json(output_dir / "WithEmpty.json")
        assert len(df) == 2

    def test_raises_on_missing_columns(self, tmp_path):
        file_path = tmp_path / "MissingCols.xlsx"
        df = pd.DataFrame({"A": [1, 2]})
        with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Sheet1", index=False)

        output_dir = tmp_path / "out"
        with pytest.raises(ValueError, match="not found in sheet"):
            convert_als2sdtm(
                input_file=str(file_path),
                output_dir=str(output_dir),
                sheet_name="Sheet1",
                output_format="json",
            )

    def test_column_mapping_with_chinese_aliases(self, tmp_path, sample_als_xlsx):
        """测试使用中文别名列名映射（如 ``表名称`` -> ``annotation_table``）。"""
        output_dir = tmp_path / "out"
        chinese_mapping: dict[str, list[str]] = {
            "annotation_table": ["表名称"],
            "annotation_variable": ["变量名/注释/内嵌表名"],
            "SDTM_Domain": ["SDTM 域"],
            "SDTM_Variable": ["SDTM 变量"],
        }
        result = convert_als2sdtm(
            input_file=sample_als_xlsx,
            output_dir=str(output_dir),
            sheet_name="Chinese",
            output_format="json",
            column_mapping=chinese_mapping,
        )
        df = pd.read_json(result["json"])
        assert "annotation_table" in df.columns
        assert set(df["annotation_table"]) == {"DM", "MH"}


class TestBatchConvert:
    def test_batch_convert_processes_all_files(self, tmp_path):
        input_dir = tmp_path / "in"
        input_dir.mkdir()
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        for name in ["ALS2SDTM_A.xlsx", "ALS2SDTM_B.xlsx", "Other.xlsx"]:
            df = pd.DataFrame(
                {
                    "表名": ["AE"],
                    "变量名": ["术语"],
                    "SDTM_Domain": ["AE"],
                    "SDTM_Variable": ["AETERM"],
                }
            )
            fp = input_dir / name
            with pd.ExcelWriter(fp, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="Sheet1", index=False)

        results = batch_convert(
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            pattern="ALS2SDTM*.xlsx",
            sheet_name="Sheet1",
            output_format="json",
            column_mapping=_MINIMAL_MAPPING,
        )
        assert len(results) == 2
        for r in results:
            assert "json" in r["outputs"]
