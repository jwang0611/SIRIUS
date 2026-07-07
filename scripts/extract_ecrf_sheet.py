#!/usr/bin/env python
"""Extract eCRF metadata from raw Excel files into JSON.

Logic:
1) Process 3 sheets separately:
   - "eCRF界面": Filter 类型="变量" & 隐藏="否", keep 表→table, 变量→metadata_variable, 隐藏→hide
   - "内嵌设计": Keep 表名→table_total_annotated, 表→table_total, 内嵌表名→table_annotated, 内嵌表→table
   - "eCRF": Keep 编号→num, 表名→table_annotated, 表→table, 变量名→annotation_variable, 变量→metadata_variable

2) Merge steps:
   a. Merge "内嵌设计" & "eCRF" on (table_annotated, table), keep "内嵌设计" records only
      Rename: table_total_annotated→annotation_table, table_total→metadata_table
   b. Merge "eCRF界面" & "eCRF" on (table, metadata_variable), keep "eCRF界面" records only
   c. Union a & b, sort by num, generate final dataset
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

# Suppress openpyxl default style warnings
warnings.filterwarnings("ignore", message="Workbook contains no default style")


DEFAULT_INPUT_DIR = Path("data/raw")
DEFAULT_OUTPUT_DIR = Path("data/processed")

# Sheet names
ECRF_UI_SHEET_NAME = "eCRF界面"
EMBEDDED_DESIGN_SHEET_NAME = "内嵌设计"
ECRF_SHEET_NAME = "eCRF"

# eCRF界面 sheet configuration
ECRF_UI_TYPE_COLUMN = "类型"
ECRF_UI_TYPE_VALUE = "变量"
ECRF_UI_HIDE_COLUMN = "隐藏"
ECRF_UI_HIDE_VALUE = "否"
ECRF_UI_COLUMN_MAPPING = {
    "表": "table",
    "变量": "metadata_variable",
    "隐藏": "hide",
}

# 内嵌设计 sheet configuration
EMBEDDED_DESIGN_COLUMN_MAPPING = {
    "表名": "table_total_annotated",
    "表": "table_total",
    "内嵌表名": "table_annotated",
    "内嵌表": "table",
}

# eCRF sheet configuration
ECRF_COLUMN_MAPPING = {
    "编号": "num",
    "表名": "table_annotated",
    "表": "table",
    "变量名": "annotation_variable",
    "变量": "metadata_variable",
}

SUPPORTED_EXTENSIONS = {".xls", ".xlsx", ".xlsm"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract and merge eCRF metadata from 'eCRF界面', '内嵌设计', and 'eCRF' sheets, and store as JSON files."
        )
    )
    parser.add_argument(
        "-i",
        "--input",
        default=str(DEFAULT_INPUT_DIR),
        help="Excel file or directory that contains Excel workbooks (default: data/raw).",
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

    workbooks: list[Path] = []
    for item in sorted(path.iterdir()):
        if item.suffix.lower() in SUPPORTED_EXTENSIONS and item.is_file():
            workbooks.append(item)

    if not workbooks:
        raise FileNotFoundError(f"No Excel files found in {path}")
    return workbooks


def ensure_columns(df: pd.DataFrame, required_columns: list[str], sheet_name: str) -> None:
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        raise KeyError(f"Sheet '{sheet_name}' missing required columns: {', '.join(missing)}")


def extract_ecrf_ui(workbook_path: Path) -> pd.DataFrame:
    """
    Extract from eCRF界面 sheet.
    Filter: 类型="变量" AND 隐藏="否"
    Keep: 表→table, 变量→metadata_variable, 隐藏→hide
    """
    df = pd.read_excel(workbook_path, sheet_name=ECRF_UI_SHEET_NAME, dtype=str)
    df.columns = [str(col).strip() for col in df.columns]

    required_cols = [*list(ECRF_UI_COLUMN_MAPPING.keys()), ECRF_UI_TYPE_COLUMN]
    ensure_columns(df, required_cols, ECRF_UI_SHEET_NAME)

    # Filter: 类型="变量" AND 隐藏="否"
    type_mask = df[ECRF_UI_TYPE_COLUMN].astype(str).str.strip().eq(ECRF_UI_TYPE_VALUE)
    hide_mask = df[ECRF_UI_HIDE_COLUMN].astype(str).str.strip().eq(ECRF_UI_HIDE_VALUE)
    filtered_df = df.loc[type_mask & hide_mask, list(ECRF_UI_COLUMN_MAPPING.keys())].copy()

    # Rename columns
    filtered_df = filtered_df.rename(columns=ECRF_UI_COLUMN_MAPPING)

    # Strip whitespace
    for col in filtered_df.columns:
        filtered_df[col] = filtered_df[col].fillna("").astype(str).str.strip()

    return filtered_df


def extract_embedded_design(workbook_path: Path) -> pd.DataFrame:
    """
    Extract from 内嵌设计 sheet.
    Keep: 表名→table_total_annotated, 表→table_total, 内嵌表名→table_annotated, 内嵌表→table
    """
    df = pd.read_excel(workbook_path, sheet_name=EMBEDDED_DESIGN_SHEET_NAME, dtype=str)
    df.columns = [str(col).strip() for col in df.columns]

    required_cols = list(EMBEDDED_DESIGN_COLUMN_MAPPING.keys())
    ensure_columns(df, required_cols, EMBEDDED_DESIGN_SHEET_NAME)

    # Select and rename columns
    result_df = df[list(EMBEDDED_DESIGN_COLUMN_MAPPING.keys())].copy()
    result_df = result_df.rename(columns=EMBEDDED_DESIGN_COLUMN_MAPPING)

    # Strip whitespace
    for col in result_df.columns:
        result_df[col] = result_df[col].fillna("").astype(str).str.strip()

    return result_df


def extract_ecrf(workbook_path: Path) -> pd.DataFrame:
    """
    Extract from eCRF sheet.
    Keep: 编号→num, 表名→table_annotated, 表→table, 变量名→annotation_variable, 变量→metadata_variable
    """
    df = pd.read_excel(workbook_path, sheet_name=ECRF_SHEET_NAME, dtype=str)
    df.columns = [str(col).strip() for col in df.columns]

    required_cols = list(ECRF_COLUMN_MAPPING.keys())
    ensure_columns(df, required_cols, ECRF_SHEET_NAME)

    # Select and rename columns
    result_df = df[list(ECRF_COLUMN_MAPPING.keys())].copy()
    result_df = result_df.rename(columns=ECRF_COLUMN_MAPPING)

    # Strip whitespace
    for col in result_df.columns:
        result_df[col] = result_df[col].fillna("").astype(str).str.strip()

    return result_df


def merge_embedded_with_ecrf(embedded_df: pd.DataFrame, ecrf_df: pd.DataFrame) -> pd.DataFrame:
    """
    Step 2a: Merge 内嵌设计 & eCRF on (table_annotated, table).
    Keep only records from 内嵌设计.
    Rename: table_total_annotated→annotation_table, table_total→metadata_table
    """
    # Merge on table_annotated & table, keep only 内嵌设计 records (inner join)
    merged = embedded_df.merge(ecrf_df, on=["table_annotated", "table"], how="inner")

    # Rename columns for final output
    merged = merged.rename(
        columns={
            "table_total_annotated": "annotation_table",
            "table_total": "metadata_table",
        }
    )

    # Select final columns
    final_cols = ["num", "metadata_table", "metadata_variable", "annotation_table", "annotation_variable"]
    return merged[final_cols]


def merge_ecrf_ui_with_ecrf(ecrf_ui_df: pd.DataFrame, ecrf_df: pd.DataFrame) -> pd.DataFrame:
    """
    Step 2b: Merge eCRF界面 & eCRF on (table, metadata_variable).
    Keep only records from eCRF界面.
    """
    # Merge on table & metadata_variable, keep only eCRF界面 records (inner join)
    merged = ecrf_ui_df.merge(ecrf_df, on=["table", "metadata_variable"], how="inner")

    # Rename table→metadata_table, table_annotated→annotation_table
    merged = merged.rename(
        columns={
            "table": "metadata_table",
            "table_annotated": "annotation_table",
        }
    )

    # Select final columns
    final_cols = ["num", "metadata_table", "metadata_variable", "annotation_table", "annotation_variable"]
    return merged[final_cols]


def extract_and_merge(workbook_path: Path) -> tuple[pd.DataFrame, int, int]:
    """
    Main extraction logic combining all three sheets.

    Returns:
        - DataFrame with columns: num, metadata_table, metadata_variable, annotation_table, annotation_variable
        - Count from embedded merge (step a)
        - Count from ecrf_ui merge (step b)
    """
    # Step 1: Extract from all three sheets
    ecrf_ui_df = extract_ecrf_ui(workbook_path)
    embedded_df = extract_embedded_design(workbook_path)
    ecrf_df = extract_ecrf(workbook_path)

    # Step 2a: Merge 内嵌设计 & eCRF
    merged_embedded = merge_embedded_with_ecrf(embedded_df, ecrf_df)

    # Step 2b: Merge eCRF界面 & eCRF
    merged_ecrf_ui = merge_ecrf_ui_with_ecrf(ecrf_ui_df, ecrf_df)

    # Step 2c: Union and sort by num
    final_df = pd.concat([merged_embedded, merged_ecrf_ui], ignore_index=True)

    # Convert num to numeric for proper sorting
    final_df["num"] = pd.to_numeric(final_df["num"], errors="coerce")
    final_df = final_df.sort_values("num").reset_index(drop=True)

    return final_df, len(merged_embedded), len(merged_ecrf_ui)


def resolve_output_paths(workbook: Path, output_dir: Path, override_name: str | None) -> tuple[Path, Path]:
    """
    Resolve output paths for both JSON and Excel files.

    Returns:
        - JSON file path
        - Excel file path
    """
    if override_name:
        base_name = Path(override_name).stem
    else:
        base_name = workbook.stem

    json_path = output_dir / f"{base_name}.json"
    excel_path = output_dir / f"{base_name}.xlsx"
    return json_path, excel_path


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
            final_df, embedded_count, ecrf_ui_count = extract_and_merge(workbook)
        except Exception as exc:  # pragma: no cover - CLI feedback
            error_msg = f"[ERROR] {workbook.name}: {exc}"
            print(error_msg, file=sys.stderr)
            error_messages.append(str(exc))
            continue

        json_path, excel_path = resolve_output_paths(workbook, output_dir, override_output_name)

        # Write JSON (without num column)
        json_cols = ["metadata_table", "metadata_variable", "annotation_table", "annotation_variable"]
        records = final_df[json_cols].to_dict(orient="records")
        write_json(json_path, records)

        # Write Excel (with num column)
        excel_cols = ["num", "metadata_table", "metadata_variable", "annotation_table", "annotation_variable"]
        write_excel(excel_path, final_df[excel_cols])

        try:
            json_display = json_path.relative_to(Path.cwd())
            excel_display = excel_path.relative_to(Path.cwd())
        except ValueError:
            json_display = json_path
            excel_display = excel_path

        final_count = len(final_df)
        print(
            f"[OK] {workbook.name}: 内嵌设计 merge={embedded_count}, "
            f"eCRF界面 merge={ecrf_ui_count}, total={final_count} rows"
        )
        print(f"     -> {json_display}")
        print(f"     -> {excel_display}")
        success_count += 1

    # If processing a single file and it failed, exit with error code
    if len(workbooks) == 1 and success_count == 0:
        sys.exit(1)
    # If processing multiple files and all failed, exit with error code
    elif len(workbooks) > 1 and success_count == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
