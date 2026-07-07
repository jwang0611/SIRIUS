#!/usr/bin/env python
"""Preprocess SDTMIG Excel data into RAG-friendly structured chunks.

This script reads the SDTMIG Excel file (datasets + variables sheets)
and produces enriched natural-language snippets that can be loaded into the
RAG knowledge base.  Each chunk contains structured metadata so that the
downstream retriever can prioritise by domain/variable if needed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


def _clean(value: Any) -> str:
    if pd.isna(value) or value is None:
        return ""
    return str(value).strip()


def _format_list(value: Any) -> str:
    if pd.isna(value) or value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v).strip() for v in value if v)
    text = str(value).strip()
    return text.replace(";", "; ")


def build_variable_chunks(
    variables_df: pd.DataFrame,
    datasets_meta: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    for _, row in variables_df.iterrows():
        dataset = _clean(row.get("Dataset Name"))
        variable = _clean(row.get("Variable Name"))
        if not dataset or not variable:
            continue

        dataset_info = datasets_meta.get(dataset, {})
        lines: list[str] = []
        lines.append(f"Domain: {dataset}")
        if dataset_info.get("Dataset Label"):
            lines[-1] += f" — {dataset_info['Dataset Label']}"

        if dataset_info.get("Class"):
            lines.append(f"Class: {dataset_info['Class']}")
        if dataset_info.get("Structure"):
            lines.append(f"Structure: {dataset_info['Structure']}")

        lines.append(f"Variable: {variable}")
        label = _clean(row.get("Variable Label"))
        if label:
            lines.append(f"Label: {label}")
        if row.get("Role"):
            lines.append(f"Role: {_clean(row['Role'])}")
        if row.get("Core"):
            lines.append(f"Core Status: {_clean(row['Core'])}")
        if row.get("Type"):
            lines.append(f"Type: {_clean(row['Type'])}")

        codelist = _format_list(row.get("CDISC CT Codelist Code(s)"))
        if codelist:
            lines.append(f"Controlled Terminology: {codelist}")

        value_domain = _format_list(row.get("Described Value Domain(s)"))
        if value_domain:
            lines.append(f"Value Domains: {value_domain}")

        value_list = _format_list(row.get("Value List"))
        if value_list:
            lines.append(f"Enumerated Values: {value_list}")

        notes = _clean(row.get("CDISC Notes"))
        if notes:
            lines.append(f"Notes: {notes}")

        chunk_text = "\n".join(line for line in lines if line)

        metadata = {
            "domain": dataset,
            "variable": variable,
            "label": label,
            "role": _clean(row.get("Role")),
            "core": _clean(row.get("Core")),
            "class": dataset_info.get("Class", ""),
            "structure": dataset_info.get("Structure", ""),
            "source": "SDTMIG_v3.2_csv",
            "type": "sdtm_variable",
            "language": "en",
            "confidence_default": 0.6,
        }

        records.append(
            {
                "chunk_id": f"SDTMIG_VAR::{dataset}::{variable}",
                "chunk_text": chunk_text,
                "metadata": json.dumps(metadata, ensure_ascii=False),
                "domain": dataset,
                "variable": variable,
                "label": label,
                "role": metadata["role"],
                "core": metadata["core"],
                "class": metadata["class"],
                "structure": metadata["structure"],
                "confidence_default": metadata["confidence_default"],
                "language": metadata["language"],
                "source": metadata["source"],
            }
        )

    return records


def build_dataset_chunks(datasets_df: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for _, row in datasets_df.iterrows():
        domain = _clean(row.get("Dataset Name"))
        if not domain:
            continue

        lines: list[str] = [f"Domain Overview: {domain}"]
        if row.get("Dataset Label"):
            lines[-1] += f" — {_clean(row['Dataset Label'])}"

        if row.get("Class"):
            lines.append(f"Class: {_clean(row['Class'])}")
        if row.get("Structure"):
            lines.append(f"Structure: {_clean(row['Structure'])}")

        chunk_text = "\n".join(line for line in lines if line)

        metadata = {
            "domain": domain,
            "class": _clean(row.get("Class")),
            "structure": _clean(row.get("Structure")),
            "source": "SDTMIG_v3.2_Datasets",
            "type": "sdtm_domain_overview",
            "language": "en",
            "confidence_default": 0.55,
        }

        records.append(
            {
                "chunk_id": f"SDTMIG_DOMAIN::{domain}",
                "chunk_text": chunk_text,
                "metadata": json.dumps(metadata, ensure_ascii=False),
                "domain": domain,
                "variable": "",
                "role": "",
                "core": "",
                "class": metadata["class"],
                "structure": metadata["structure"],
                "confidence_default": metadata["confidence_default"],
                "language": metadata["language"],
                "source": metadata["source"],
            }
        )

    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert SDTMIG Excel file into structured RAG chunks.")
    parser.add_argument(
        "--input-excel",
        default="data/knowledge_base/documents/sdtm_v3.2.xlsx",
        help="Path to the SDTM v3.2 Excel file.",
    )
    parser.add_argument(
        "--variables-sheet",
        default="Variables",
        help="Name of the variables sheet.",
    )
    parser.add_argument(
        "--datasets-sheet",
        default="Datasets",
        help="Name of the datasets sheet.",
    )
    parser.add_argument(
        "--output",
        default="data/knowledge_base/structured/sdtmig_preprocessed.parquet",
        help="Path to the output parquet file (will be overwritten).",
    )
    parser.add_argument(
        "--output-json",
        default="data/knowledge_base/structured/sdtmig_preprocessed.json",
        help="Optional path to also write JSON records for inspection.",
    )
    args = parser.parse_args()

    excel_path = Path(args.input_excel)
    if not excel_path.exists():
        raise FileNotFoundError(f"SDTM Excel file not found: {excel_path}")

    print(f"📖 Reading Excel file: {excel_path}")

    # Read variables sheet
    print(f"  - Loading sheet: {args.variables_sheet}")
    variables_df = pd.read_excel(excel_path, sheet_name=args.variables_sheet)
    print(f"    ✓ Loaded {len(variables_df)} variables")

    # Read datasets sheet
    print(f"  - Loading sheet: {args.datasets_sheet}")
    datasets_df = pd.read_excel(excel_path, sheet_name=args.datasets_sheet)
    print(f"    ✓ Loaded {len(datasets_df)} datasets")

    dataset_meta = (
        datasets_df.set_index("Dataset Name")
        .apply(lambda col: col.map(lambda v: "" if pd.isna(v) else str(v).strip()))
        .to_dict("index")
    )

    records = []
    records.extend(build_dataset_chunks(datasets_df))
    records.extend(build_variable_chunks(variables_df, dataset_meta))

    output_df = pd.DataFrame(records)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_df.to_parquet(output_path, index=False)
    print(f"✅ Wrote {len(output_df)} SDTMIG chunks to {output_path}")

    if args.output_json:
        json_path = Path(args.output_json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        output_df.to_json(json_path, orient="records", force_ascii=False, indent=2)
        print(f"✅ Wrote JSON preview to {json_path}")


if __name__ == "__main__":
    main()
