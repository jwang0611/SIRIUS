#!/usr/bin/env python
"""Extract eCRF metadata from Taimei system Excel files into JSON.

Logic:
1) Read two sheets:
   - "Forms":    keep FormOID & SASDatasetName
   - "FormItem": keep FormOID, FormName, SASFieldName, ItemName & DataFormat

2) Merge on FormOID (inner join, preserving FormItem row order).
   Add a 1-based sequence number column "num".

3) Output JSON with the unified field names expected by the mapping pipeline:
   metadata_table    ← SASDatasetName
   metadata_variable ← SASFieldName
   annotation_table  ← FormName
   annotation_variable ← ItemName

Note: taimei.xlsx files contain AutoFilter XML that is incompatible with
openpyxl. This script therefore uses the `python-calamine` engine
(``pip install python-calamine``) which reads the raw cell values directly.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore", message="Workbook contains no default style")

DEFAULT_INPUT_DIR = Path("data/raw")
DEFAULT_OUTPUT_DIR = Path("data/processed")

FORMS_SHEET = "Forms"
FORMITEM_SHEET = "FormItem"

FORMS_KEEP = ["FormOID", "SASDatasetName"]

FORMITEM_KEEP = ["FormOID", "FormName", "SASFieldName", "ItemName", "DataFormat"]

SUPPORTED_EXTENSIONS = {".xls", ".xlsx", ".xlsm"}

# calamine reads .xlsx/.xlsm; fall back to xlrd for legacy .xls
_ENGINE_MAP: dict[str, str] = {
    ".xlsx": "calamine",
    ".xlsm": "calamine",
    ".xls": "xlrd",
}


def _read_sheet(workbook_path: Path, sheet_name: str) -> pd.DataFrame:
    """Read a single sheet, choosing the appropriate engine."""
    engine = _ENGINE_MAP.get(workbook_path.suffix.lower(), "calamine")
    return pd.read_excel(workbook_path, sheet_name=sheet_name, dtype=str, engine=engine)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract and merge eCRF metadata from Taimei 'Forms' and 'FormItem' sheets and store as JSON files."
        )
    )
    parser.add_argument(
        "-i",
        "--input",
        default=str(DEFAULT_INPUT_DIR),
        help="Excel file or directory containing Excel workbooks (default: data/raw).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory to store generated JSON files (default: data/processed).",
    )
    parser.add_argument(
        "--output-name",
        help="Optional JSON filename (only valid when the input is a single Excel file).",
    )
    return parser.parse_args()


def collect_workbooks(path: Path) -> list[Path]:
    if path.is_file():
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {path.suffix}")
        return [path]

    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")
    if not path.is_dir():
        raise ValueError(f"Input path must be a file or directory, got: {path}")

    workbooks = [
        item for item in sorted(path.iterdir()) if item.suffix.lower() in SUPPORTED_EXTENSIONS and item.is_file()
    ]
    if not workbooks:
        raise FileNotFoundError(f"No Excel files found in {path}")
    return workbooks


def ensure_columns(df: pd.DataFrame, required: list[str], sheet_name: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"Sheet '{sheet_name}' missing required columns: {', '.join(missing)}")


def extract_forms(workbook_path: Path) -> pd.DataFrame:
    """Extract FormOID and SASDatasetName from the Forms sheet."""
    df = _read_sheet(workbook_path, FORMS_SHEET)
    df.columns = [str(c).strip() for c in df.columns]
    ensure_columns(df, FORMS_KEEP, FORMS_SHEET)

    result = df[FORMS_KEEP].copy()
    for col in result.columns:
        result[col] = result[col].fillna("").astype(str).str.strip()
    return result


def extract_formitem(workbook_path: Path) -> pd.DataFrame:
    """Extract FormOID, FormName, SASFieldName, ItemName, DataFormat from FormItem sheet."""
    df = _read_sheet(workbook_path, FORMITEM_SHEET)
    df.columns = [str(c).strip() for c in df.columns]
    ensure_columns(df, FORMITEM_KEEP, FORMITEM_SHEET)

    result = df[FORMITEM_KEEP].copy()
    for col in result.columns:
        result[col] = result[col].fillna("").astype(str).str.strip()
    return result


def extract_and_merge(workbook_path: Path) -> pd.DataFrame:
    """Merge Forms and FormItem on FormOID, preserving FormItem row order.

    Returns a DataFrame with columns:
        num, metadata_table, metadata_variable, annotation_table, annotation_variable, DataFormat
    """
    forms_df = extract_forms(workbook_path)
    formitem_df = extract_formitem(workbook_path)

    merged = formitem_df.merge(forms_df, on="FormOID", how="left")

    # Preserve FormItem original order, add 1-based sequence number
    merged.insert(0, "num", range(1, len(merged) + 1))

    merged = merged.rename(
        columns={
            "SASDatasetName": "metadata_table",
            "SASFieldName": "metadata_variable",
            "FormName": "annotation_table",
            "ItemName": "annotation_variable",
        }
    )

    return merged


def resolve_output_paths(workbook: Path, output_dir: Path, override_name: str | None) -> tuple[Path, Path]:
    base_name = Path(override_name).stem if override_name else workbook.stem
    return output_dir / f"{base_name}.json", output_dir / f"{base_name}.xlsx"


def write_json(path: Path, records: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def write_excel(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(path, index=False, engine="openpyxl")


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    override_output_name = args.output_name

    workbooks = collect_workbooks(input_path)
    if override_output_name and len(workbooks) != 1:
        raise ValueError("--output-name can only be used with a single input file.")

    success_count = 0
    error_messages: list[str] = []

    for workbook in workbooks:
        try:
            final_df = extract_and_merge(workbook)
        except Exception as exc:
            error_msg = f"[ERROR] {workbook.name}: {exc}"
            print(error_msg, file=sys.stderr)
            error_messages.append(str(exc))
            continue

        json_path, excel_path = resolve_output_paths(workbook, output_dir, override_output_name)

        json_cols = ["metadata_table", "metadata_variable", "annotation_table", "annotation_variable"]
        records = final_df[json_cols].to_dict(orient="records")
        write_json(json_path, records)

        excel_cols = [
            "num",
            "metadata_table",
            "metadata_variable",
            "annotation_table",
            "annotation_variable",
            "DataFormat",
        ]
        write_excel(excel_path, final_df[excel_cols])

        try:
            json_display = json_path.relative_to(Path.cwd())
            excel_display = excel_path.relative_to(Path.cwd())
        except ValueError:
            json_display = json_path
            excel_display = excel_path

        print(f"[OK] {workbook.name}: {len(final_df)} rows merged")
        print(f"     -> {json_display}")
        print(f"     -> {excel_display}")
        success_count += 1

    if success_count == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
