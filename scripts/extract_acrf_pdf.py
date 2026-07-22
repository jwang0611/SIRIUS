#!/usr/bin/env python
"""Extract form names and field labels from an aCRF/eCRF PDF into JSON.

Reads a bookmarked eCRF PDF, derives the form list from the PDF outline and the
field labels from each form's page text (deterministic by default; add
``--use-llm`` for an LLM-assisted cleanup pass), then writes:

* ``<output-dir>/<name>.json``            – 4-field records for the recommender
* ``<output-dir>/<name>.xlsx``            – sibling with a ``num`` order column
* ``<als2sdtm-dir>/<name>_ALS2SDTM.xlsx`` – portable als2sdtm-format workbook

The JSON feeds ``scripts/generate_sdtm_recommendations.py`` unchanged; the
als2sdtm workbook is re-ingestible by ``scripts/convert_als2sdtm.py``.
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings
from pathlib import Path

# Add the project root to Python path to enable src imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

warnings.filterwarnings("ignore", message="Workbook contains no default style")

from src.config.settings import get_settings  # noqa: E402
from src.infrastructure.data_masker import DataMasker  # noqa: E402
from src.processors.acrf import (  # noqa: E402
    AcrfConfig,
    extract_acrf,
    write_als2sdtm_xlsx,
    write_processed_json,
    write_processed_xlsx,
)

DEFAULT_INPUT_DIR = Path("data/raw")
DEFAULT_OUTPUT_DIR = Path("data/processed")
DEFAULT_ALS2SDTM_DIR = Path("data/output")
SUPPORTED_EXTENSIONS = {".pdf"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract form/field metadata from an aCRF/eCRF PDF into JSON + als2sdtm workbook."
    )
    parser.add_argument(
        "-i",
        "--input",
        default=str(DEFAULT_INPUT_DIR),
        help="PDF file or directory containing PDFs (default: data/raw).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for the recommender JSON + sibling xlsx (default: data/processed).",
    )
    parser.add_argument(
        "--output-name",
        help="Optional output basename (only valid when the input is a single PDF).",
    )
    parser.add_argument(
        "--als2sdtm-dir",
        default=str(DEFAULT_ALS2SDTM_DIR),
        help="Directory for the portable als2sdtm workbook (default: data/output).",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Enable the optional LLM-assisted field cleanup pass (requires API credentials).",
    )
    parser.add_argument("--model", help="Model for the LLM pass (default: DEFAULT_MODEL env).")
    parser.add_argument("--api-key", help="API key for the LLM pass (default: env).")
    parser.add_argument("--base-url", help="Base URL for the LLM pass (default: OPENROUTER_BASE_URL).")
    parser.add_argument(
        "--language",
        choices=["cn", "en"],
        help="Prompt language for the LLM pass (default: DEFAULT_LANGUAGE env).",
    )
    parser.add_argument(
        "--mdv-mode",
        choices=["label", "synthetic"],
        help="How to fill metadata_variable (default: from ACRF_MDV_MODE / label).",
    )
    return parser.parse_args()


def collect_pdfs(path: Path) -> list[Path]:
    if path.is_file():
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {path.suffix}")
        return [path]
    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")
    if not path.is_dir():
        raise ValueError(f"Input path must be a file or directory, got: {path}")
    pdfs = [p for p in sorted(path.iterdir()) if p.suffix.lower() in SUPPORTED_EXTENSIONS and p.is_file()]
    if not pdfs:
        raise FileNotFoundError(f"No PDF files found in {path}")
    return pdfs


def _build_client(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    """Construct and initialise an OpenRouter client for the LLM pass."""
    from src.clients.openrouter_client import OpenRouterClient

    settings = get_settings()
    base_url = args.base_url or settings.ai.openrouter_base_url
    client = OpenRouterClient(api_key=args.api_key, base_url=base_url)
    if not client.initialize():
        raise RuntimeError("Failed to initialise the AI client for --use-llm (check API credentials)")
    client.set_model(args.model or settings.ai.default_model)
    return client


def _resolve_config(args: argparse.Namespace) -> AcrfConfig:
    mode = args.mdv_mode or os.getenv("ACRF_MDV_MODE", "label")
    base = AcrfConfig()
    return AcrfConfig(
        skip_forms=base.skip_forms,
        min_fields=int(os.getenv("ACRF_MIN_FIELDS", str(base.min_fields))),
        max_fields=int(os.getenv("ACRF_MAX_FIELDS", str(base.max_fields))),
        header_footer_band=float(os.getenv("ACRF_HEADER_FOOTER_BAND", str(base.header_footer_band))),
        metadata_variable_mode="synthetic" if mode == "synthetic" else "label",
        use_llm=bool(args.use_llm),
    )


def main() -> None:
    args = parse_args()
    settings = get_settings()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    als2sdtm_dir = Path(args.als2sdtm_dir)
    cfg = _resolve_config(args)
    language = args.language or settings.ai.default_language

    pdfs = collect_pdfs(input_path)
    if args.output_name and len(pdfs) != 1:
        raise ValueError("--output-name can only be used with a single input file.")

    client = _build_client(args) if args.use_llm else None
    masker = DataMasker() if settings.security.data_masking_enabled else None

    success_count = 0
    for pdf in pdfs:
        base_name = Path(args.output_name).stem if args.output_name else pdf.stem
        try:
            result = extract_acrf(
                str(pdf),
                cfg=cfg,
                use_llm=bool(args.use_llm),
                client=client,
                masker=masker,
                language=language,
            )
            if not result.records:
                raise RuntimeError("no fields extracted from PDF")

            json_path = output_dir / f"{base_name}.json"
            xlsx_path = output_dir / f"{base_name}.xlsx"
            als_path = als2sdtm_dir / f"{base_name}_ALS2SDTM.xlsx"
            write_processed_json(json_path, result.records)
            write_processed_xlsx(xlsx_path, result.records)
            write_als2sdtm_xlsx(als_path, result.records)
        except Exception as exc:
            print(f"[ERROR] {pdf.name}: {exc}", file=sys.stderr)
            continue

        stats = result.stats
        print(
            f"[OK] {pdf.name}: {stats.get('forms_with_fields')}/{stats.get('forms_total')} forms, "
            f"{stats.get('fields_total')} fields"
            + (f", {stats.get('forms_via_llm')} via LLM" if stats.get("llm_enabled") else "")
        )
        for path in (json_path, xlsx_path, als_path):
            try:
                print(f"     -> {path.relative_to(Path.cwd())}")
            except ValueError:
                print(f"     -> {path}")
        success_count += 1

    if success_count == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
