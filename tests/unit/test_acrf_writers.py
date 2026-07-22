"""End-to-end extraction → writers → als2sdtm converter round-trip (integration)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.processors.acrf import (
    extract_acrf,
    write_als2sdtm_xlsx,
    write_processed_json,
    write_processed_xlsx,
)
from src.processors.acrf.writers import ALS2SDTM_SHEET_NAME
from src.processors.als_converter import convert_als2sdtm

pytestmark = pytest.mark.integration


def test_extract_writes_all_outputs_and_roundtrips(sample_acrf_pdf: Path, tmp_path: Path):
    result = extract_acrf(str(sample_acrf_pdf))
    assert result.records  # Cover skipped, two forms extracted

    json_path = tmp_path / "sample.json"
    xlsx_path = tmp_path / "sample.xlsx"
    als_path = tmp_path / "sample_ALS2SDTM.xlsx"
    write_processed_json(json_path, result.records)
    write_processed_xlsx(xlsx_path, result.records)
    write_als2sdtm_xlsx(als_path, result.records)

    # Processed JSON: exact 4-key schema, non-empty metadata_table.
    rows = json.loads(json_path.read_text(encoding="utf-8"))
    assert all(
        set(r) == {"metadata_table", "metadata_variable", "annotation_table", "annotation_variable"} for r in rows
    )
    assert all(r["metadata_table"] for r in rows)

    # Sibling xlsx keeps the num order column first (pipeline coverage pass).
    df_sib = pd.read_excel(xlsx_path)
    assert next(iter(df_sib.columns)) == "num"

    # Portable als2sdtm workbook: sheet named eCRF + converter-recognised headers.
    xl = pd.ExcelFile(als_path)
    assert xl.sheet_names == [ALS2SDTM_SHEET_NAME]
    df_als = pd.read_excel(als_path, sheet_name=ALS2SDTM_SHEET_NAME)
    assert {"表名", "表", "变量名", "变量", "SDTM_Domain", "SDTM_Variable"}.issubset(df_als.columns)

    # Round-trip: convert_als2sdtm re-ingests it with zero extra flags.
    outputs = convert_als2sdtm(str(als_path), str(tmp_path), output_name="roundtrip", output_format="json")
    rt = json.loads(Path(outputs["json"]).read_text(encoding="utf-8"))
    assert rt
    assert {
        "annotation_table",
        "metadata_table",
        "annotation_variable",
        "metadata_variable",
        "SDTM_Domain",
        "SDTM_Variable",
    } <= set(rt[0])
    assert rt[0]["annotation_table"] == "Demographics"
