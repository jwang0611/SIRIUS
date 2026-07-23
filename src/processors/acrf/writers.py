"""Serialise extracted records to the recommender JSON, its sibling xlsx, and a
portable als2sdtm-format workbook.

The als2sdtm workbook uses a sheet named ``eCRF`` and the primary header aliases
from :data:`als_converter.DEFAULT_COLUMN_MAPPING`, so ``convert_als2sdtm`` re-
ingests it with zero extra flags. Headers are derived from that constant (not a
hard-coded copy) to keep the export↔converter contract in one place.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.processors.acrf.models import AcrfRecord
from src.processors.acrf.records import records_to_json_rows
from src.processors.als_converter import DEFAULT_COLUMN_MAPPING

ALS2SDTM_SHEET_NAME = "eCRF"

PROCESSED_JSON_COLUMNS = [
    "metadata_table",
    "metadata_variable",
    "annotation_table",
    "annotation_variable",
]
PROCESSED_XLSX_COLUMNS = ["num", *PROCESSED_JSON_COLUMNS]


def _primary_header(field: str) -> str:
    value = DEFAULT_COLUMN_MAPPING[field]
    return value[0] if isinstance(value, list) else value


# (workbook header, record attribute or None for a blank output column), ordered
# eCRF-input-side first then the SDTM output side.
ALS2SDTM_COLUMNS: list[tuple[str, str | None]] = [
    (_primary_header("annotation_table"), "annotation_table"),
    (_primary_header("metadata_table"), "metadata_table"),
    (_primary_header("annotation_variable"), "annotation_variable"),
    (_primary_header("metadata_variable"), "metadata_variable"),
    (_primary_header("SDTM_Domain"), None),
    (_primary_header("SDTM_Variable"), None),
]


def write_processed_json(path: Path, records: list[AcrfRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = records_to_json_rows(records)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


# Leading characters Excel/openpyxl may interpret as a formula. Field labels
# come straight from an uploaded PDF, so they are an untrusted injection vector.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _neutralize_formula(value: str) -> str:
    """Force a value to text so it can never be evaluated as a formula.

    Values beginning with a formula trigger are prefixed with a single quote,
    which makes openpyxl store an inline string (``data_type='s'``) rather than
    a formula and stops Excel from evaluating it on open.
    """
    if value[:1] in _FORMULA_TRIGGERS:
        return "'" + value
    return value


def write_processed_xlsx(path: Path, records: list[AcrfRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        [
            {
                "num": r.num,
                "metadata_table": _neutralize_formula(r.metadata_table),
                "metadata_variable": _neutralize_formula(r.metadata_variable),
                "annotation_table": _neutralize_formula(r.annotation_table),
                "annotation_variable": _neutralize_formula(r.annotation_variable),
            }
            for r in records
        ],
        columns=PROCESSED_XLSX_COLUMNS,
    )
    df.to_excel(path, index=False, engine="openpyxl")


def write_als2sdtm_xlsx(path: Path, records: list[AcrfRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in records:
        row = {}
        for header, attr in ALS2SDTM_COLUMNS:
            row[header] = _neutralize_formula(getattr(r, attr)) if attr else ""
        rows.append(row)
    columns = [header for header, _ in ALS2SDTM_COLUMNS]
    df = pd.DataFrame(rows, columns=columns)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=ALS2SDTM_SHEET_NAME)
