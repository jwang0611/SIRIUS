#!/usr/bin/env python3
"""ALS2SDTM Excel Converter - 统一转换脚本（CLI wrapper）

Convert ALS2SDTM Excel files to JSON and/or Parquet format.
本脚本为薄层 CLI 封装，核心逻辑由 ``src.processors.als_converter`` 提供。
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.processors.als_converter import (  # noqa: E402  – import after path manipulation
    batch_convert,
    convert_als2sdtm,
    list_sheets,
)

# Load environment variables
load_dotenv()


def main():
    parser = argparse.ArgumentParser(
        description="Convert ALS2SDTM Excel files to JSON and/or Parquet format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 转换单个文件为JSON和Parquet
  python scripts/convert_als2sdtm.py \\
    --input data/knowledge_base/documents/ALS2SDTM_310.xlsx \\
    --output-dir data/knowledge_base/structured

  # 只输出JSON
  python scripts/convert_als2sdtm.py \\
    --input data/knowledge_base/documents/ALS2SDTM_310.xlsx \\
    --output-dir data/knowledge_base/structured \\
    --format json

  # 批量转换目录下所有ALS2SDTM文件
  python scripts/convert_als2sdtm.py \\
    --batch \\
    --input-dir data/knowledge_base/documents \\
    --output-dir data/knowledge_base/structured

  # 自定义列映射
  python scripts/convert_als2sdtm.py \\
    --input data/knowledge_base/documents/ALS2SDTM_310.xlsx \\
    --output-dir data/knowledge_base/structured \\
    --columns annotation_table=B metadata_table=C annotation_variable=E
        """,
    )

    # 输入参数
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--input", help="Input Excel file path")
    input_group.add_argument("--batch", action="store_true", help="Batch convert all files in input-dir")

    # Get default paths from environment variables
    default_input_dir = os.getenv("KNOWLEDGE_BASE_PATH", "data/knowledge_base")
    default_output_dir = os.getenv("KNOWLEDGE_STRUCTURED_PATH", "data/knowledge_base/structured")

    parser.add_argument(
        "--input-dir",
        default=default_input_dir,
        help=f"Input directory (for batch mode or relative input path) (default: {default_input_dir})",
    )

    # 输出参数
    parser.add_argument(
        "--output-dir",
        default=default_output_dir,
        help=f"Output directory for converted files (default: {default_output_dir})",
    )
    parser.add_argument("--output-name", help="Output filename (without extension)")
    parser.add_argument(
        "--format", choices=["json", "parquet", "both"], default="both", help="Output format (default: both)"
    )

    # Excel参数
    parser.add_argument("--sheet", default=None, help="Excel sheet name; if omitted, auto-detect first non-empty sheet")
    parser.add_argument(
        "--columns", nargs="+", help="Custom column mapping (e.g., annotation_table=B metadata_table=C)"
    )
    parser.add_argument("--list-sheets", action="store_true", help="List non-empty sheets and exit (no conversion)")

    # 其他参数
    parser.add_argument(
        "--pattern", default="ALS2SDTM*.xlsx", help="File pattern for batch mode (default: ALS2SDTM*.xlsx)"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # 解析自定义列映射
    column_mapping = None
    if args.columns:
        column_mapping = {}
        for col_spec in args.columns:
            if "=" not in col_spec:
                print(f"⚠️  Invalid column spec: {col_spec} (should be field=column)")
                continue
            field, excel_col = col_spec.split("=", 1)
            column_mapping[field] = excel_col

        if args.verbose:
            print(f"Custom column mapping: {column_mapping}")

    try:
        # 处理输出目录（相对于项目根目录）
        output_dir = args.output_dir
        if not os.path.isabs(output_dir):
            output_dir = os.path.join(project_root, output_dir)

        # 仅列出 sheet，退出
        if args.list_sheets:
            if not args.input:
                print("[ERROR] --list-sheets requires --input")
                return 1
            sheets = list_sheets(args.input)
            print(json.dumps(sheets, ensure_ascii=False))
            return 0

        if args.batch:
            # 处理输入目录（相对于项目根目录）
            input_dir = args.input_dir
            if not os.path.isabs(input_dir):
                input_dir = os.path.join(project_root, input_dir)

            # 批量转换
            results = batch_convert(
                input_dir=input_dir,
                output_dir=output_dir,
                pattern=args.pattern,
                sheet_name=args.sheet,
                column_mapping=column_mapping,
                output_format=args.format,
                verbose=args.verbose,
            )

            print(f"\n{'=' * 60}")
            print(f"[INFO] Batch conversion completed: {len(results)} files")
            print(f"{'=' * 60}")

        else:
            # 单文件转换
            input_file = args.input
            if not os.path.isabs(input_file):
                # 如果是相对路径，先检查文件是否在当前目录存在
                if not os.path.exists(input_file):
                    # 如果不存在，尝试在 input_dir 中查找（相对于项目根目录）
                    input_file = os.path.join(project_root, args.input_dir, input_file)

            convert_als2sdtm(
                input_file=input_file,
                output_dir=output_dir,
                output_name=args.output_name,
                sheet_name=args.sheet,
                column_mapping=column_mapping,
                output_format=args.format,
                verbose=args.verbose,
            )

            print("\n[INFO] Conversion completed successfully!")

    except Exception as e:
        print(f"\n[ERROR] Conversion failed: {e!s}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
