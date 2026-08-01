"""Unexpected ExcelReader failures must abort instead of yielding partial data."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from openpyxl import Workbook

from src.spec_mapper.core.excel_reader import ExcelReader

REPO_ROOT = Path(__file__).resolve().parents[2]
V32_TEMPLATE = REPO_ROOT / "data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx"


def _write_als(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["表", "变量", "变量名", "metadata_table", "SDTM_Domain", "SDTM_Variable"])
    ws.append(["SUBJECT", "STUDYID", "Study identifier", "SUBJECT", "DM", "STUDYID"])
    wb.save(path)
    wb.close()


def test_unexpected_als_row_error_propagates(tmp_path: Path, monkeypatch, caplog) -> None:
    als = tmp_path / "als.xlsx"
    _write_als(als)
    reader = ExcelReader(als)

    def fail(_value):
        raise RuntimeError("PHI_SENTINEL_SHOULD_NOT_BE_LOGGED")

    monkeypatch.setattr(reader, "_safe_str", fail)
    with pytest.raises(RuntimeError, match="PHI_SENTINEL"):
        reader.read_als_records("Sheet1")
    assert "PHI_SENTINEL_SHOULD_NOT_BE_LOGGED" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_unexpected_template_sheet_error_propagates(tmp_path: Path, monkeypatch) -> None:
    source = V32_TEMPLATE
    assert source.is_file(), f"required repo template missing: {source}"
    template = tmp_path / "template.xlsx"
    shutil.copy2(source, template)
    reader = ExcelReader(template)

    def fail(*_args, **_kwargs):
        raise RuntimeError("unexpected template reader failure")

    monkeypatch.setattr(reader, "_read_single_template_sheet_from_workbook", fail)
    with pytest.raises(RuntimeError, match="unexpected template reader failure"):
        reader.read_template_records(["DM"])


def test_unexpected_template_row_error_propagates(tmp_path: Path, monkeypatch) -> None:
    """A row conversion bug must abort instead of returning a partial template."""
    source = V32_TEMPLATE
    assert source.is_file(), f"required repo template missing: {source}"
    template = tmp_path / "template.xlsx"
    shutil.copy2(source, template)
    reader = ExcelReader(template)

    def fail(_value):
        raise RuntimeError("unexpected template row failure")

    monkeypatch.setattr(reader, "_safe_str", fail)
    with pytest.raises(RuntimeError, match="unexpected template row failure"):
        reader.read_template_records(["DM"])
