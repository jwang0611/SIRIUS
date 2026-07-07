"""ALS2SDTM Excel Converter 库

将 ALS2SDTM Excel 文件转换为 JSON 和/或 Parquet 格式。
用途：专门处理 ALS2SDTM_310.xlsx 等格式的文件。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_COLUMN_MAPPING: dict[str, str | list[str]] = {
    "annotation_table": ["表名", "表名称", "annotation_table"],
    "metadata_table": ["表", "metadata_table", "SASDatasetName"],
    "annotation_variable": ["变量名", "变量名/注释/内嵌表名", "annotation_variable", "ItemName"],
    "metadata_variable": ["变量", "metadata_variable", "SASFieldName"],
    "SDTM_Domain": ["SDTM_Domain", "SDTM domain", "SDTM 域", "SDTM域"],
    "SDTM_Variable": ["SDTM_Variable", "SDTM variable", "SDTM 变量", "SDTM变量"],
}


def excel_col_to_index(col: str) -> int:
    """将 Excel 列名（如 'A'、'B'、'AA'）转换为 0-based 索引。

    Args:
        col: Excel 列名字符串，大小写不敏感。

    Returns:
        列索引（0-based）。
    """
    col = col.upper()
    result = 0
    for char in col:
        result = result * 26 + (ord(char) - ord("A") + 1)
    return result - 1


def list_sheets(input_file: str) -> list[str]:
    """列出 Excel 中非空的 sheet，若均为空则返回全部 sheet 名。"""
    xl = pd.ExcelFile(input_file)
    non_empty: list[str] = []
    for name in xl.sheet_names:
        tmp_df = xl.parse(sheet_name=name)
        if not tmp_df.dropna(how="all").empty:
            non_empty.append(name)
    return non_empty if non_empty else xl.sheet_names


def _pick_column(
    df: pd.DataFrame,
    candidates: str | list[str],
    sheet_name: str,
) -> pd.Series:
    """在 DataFrame 中根据候选列名列表或 Excel 列字母挑选一列。

    匹配优先级：
    1. 精确匹配（区分大小写）
    2. 大小写不敏感匹配
    3. 若候选为纯字母，则视为 Excel 列字母，按索引取列

    Args:
        df: 目标 DataFrame。
        candidates: 候选列名字符串或字符串列表。
        sheet_name: 当前 sheet 名称，用于报错信息。

    Returns:
        匹配的 pandas Series。

    Raises:
        ValueError: 未找到任何匹配的列。
    """
    lower_column_map = {str(col).lower(): col for col in df.columns}
    candidate_list = candidates if isinstance(candidates, list) else [candidates]

    for cand in candidate_list:
        if not isinstance(cand, str):
            continue
        if cand in df.columns:
            return df[cand]
        cand_l = cand.lower()
        if cand_l in lower_column_map:
            return df[lower_column_map[cand_l]]
        if cand.isalpha():
            col_idx = excel_col_to_index(cand)
            if col_idx < df.shape[1]:
                return df.iloc[:, col_idx]

    raise ValueError(
        f"Columns {candidate_list} not found in sheet '{sheet_name}'. Available columns: {list(df.columns)[:20]} ..."
    )


def convert_als2sdtm(
    input_file: str,
    output_dir: str,
    output_name: str | None = None,
    sheet_name: str | None = "eCRF",
    column_mapping: dict[str, str | list[str]] | None = None,
    output_format: str = "both",
    verbose: bool = False,
) -> dict[str, str]:
    """转换 ALS2SDTM Excel 文件到 JSON 和/或 Parquet 格式。

    Args:
        input_file: 输入 Excel 文件路径。
        output_dir: 输出目录。
        output_name: 输出文件名（不含扩展名），默认使用输入文件名。
        sheet_name: Excel sheet 名称；若为 ``None`` 则自动检测第一个非空 sheet。
        column_mapping: 列映射配置（字段名 -> Excel 列名或列名列表）。
        output_format: 输出格式（``'json'``、``'parquet'``、``'both'``）。
        verbose: 是否输出详细信息。

    Returns:
        生成的文件路径字典，键为格式名称（``'json'`` / ``'parquet'``），值为绝对路径。
    """
    if column_mapping is None:
        column_mapping = DEFAULT_COLUMN_MAPPING

    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    if output_name is None:
        output_name = Path(input_file).stem

    if verbose:
        logger.info("Input file: %s", input_file)
        logger.info("Sheet name: %s", sheet_name or "AUTO")
        logger.info("Column mapping: %s", column_mapping)

    logger.info("Reading Excel file: %s", input_file)

    if sheet_name:
        df = pd.read_excel(input_file, sheet_name=sheet_name)
    else:
        sheets = list_sheets(input_file)
        if not sheets:
            raise ValueError(f"No sheets found in {input_file}")
        sheet_name = sheets[0]
        df = pd.read_excel(input_file, sheet_name=sheet_name)
        if verbose:
            logger.info("Auto-selected sheet: %s", sheet_name)

    if verbose:
        logger.info("Loaded DataFrame: %s rows x %s columns", df.shape[0], df.shape[1])
        logger.info("Original columns: %s ...", list(df.columns)[:10])

    selected_cols: dict[str, pd.Series] = {}
    for field_name, col_key in column_mapping.items():
        selected_cols[field_name] = _pick_column(df, col_key, sheet_name)

    if verbose:
        logger.info("Extracting columns: %s", list(column_mapping.values()))
        logger.info("Mapped fields: %s", list(selected_cols.keys()))

    selected_df = pd.DataFrame(selected_cols, columns=list(selected_cols.keys()))

    original_rows = len(selected_df)
    selected_df = selected_df.dropna(how="all")
    removed_rows = original_rows - len(selected_df)

    if verbose and removed_rows > 0:
        logger.info("Removed %s empty rows", removed_rows)

    logger.info("Processed DataFrame: %s rows x %s columns", len(selected_df), len(selected_df.columns))

    if verbose:
        logger.info("\nFirst 3 rows:\n%s", selected_df.head(3).to_string())

    output_files: dict[str, str] = {}

    if output_format in ("json", "both"):
        json_file = output_dir_path / f"{output_name}.json"
        selected_df.to_json(json_file, orient="records", indent=2, force_ascii=False)
        output_files["json"] = str(json_file)
        logger.info("JSON saved: %s", json_file)

    if output_format in ("parquet", "both"):
        parquet_file = output_dir_path / f"{output_name}.parquet"
        selected_df.to_parquet(parquet_file, index=False)
        output_files["parquet"] = str(parquet_file)
        logger.info("Parquet saved: %s", parquet_file)

    logger.info(
        "\nSummary:\n  - Total rows: %s\n  - Total columns: %s\n  - Column names: %s",
        len(selected_df),
        len(selected_df.columns),
        list(selected_df.columns),
    )

    return output_files


def batch_convert(
    input_dir: str,
    output_dir: str,
    pattern: str = "ALS2SDTM*.xlsx",
    **kwargs,
) -> list[dict[str, str | dict[str, str]]]:
    """批量转换目录下的 ALS2SDTM 文件。

    Args:
        input_dir: 输入目录。
        output_dir: 输出目录。
        pattern: 文件匹配模式。
        **kwargs: 传递给 ``convert_als2sdtm`` 的其他参数。

    Returns:
        所有转换结果列表，每项为 ``{"input": str, "outputs": dict[str, str]}``。
    """
    input_path = Path(input_dir)

    if not input_path.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    excel_files = list(input_path.glob(pattern))
    if not excel_files:
        logger.warning("No files matching '%s' found in %s", pattern, input_dir)
        return []

    logger.info("Found %s files to convert", len(excel_files))

    all_outputs: list[dict[str, str | dict[str, str]]] = []

    for i, excel_file in enumerate(excel_files, 1):
        logger.info("\n%s", "=" * 60)
        logger.info("[%s/%s] Converting: %s", i, len(excel_files), excel_file.name)
        logger.info("%s", "=" * 60)

        try:
            output_files = convert_als2sdtm(input_file=str(excel_file), output_dir=output_dir, **kwargs)
            all_outputs.append({"input": str(excel_file), "outputs": output_files})
        except Exception:
            logger.exception("Failed to convert %s", excel_file.name)

    return all_outputs
