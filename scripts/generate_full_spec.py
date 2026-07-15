#!/usr/bin/env python3
"""
End-to-End SDTM Spec Generator
================================

Generate complete SDTM Specification documents from CRF data.

This script provides two modes:
1. Full pipeline: CRF JSON → ALS (via RAG/AI) → SDTM Spec (via mapping)
2. Spec only: Existing ALS → SDTM Spec (skip ALS generation)

Examples:
    # Full pipeline
    python scripts/generate_full_spec.py \\
        --json-file data/processed/crf_data.json \\
        --template-file data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx \\
        --output data/spec_output/final_spec.xlsx

    # Use existing ALS
    python scripts/generate_full_spec.py \\
        --als-file data/output/existing_als.xlsx \\
        --template-file data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx \\
        --output data/spec_output/final_spec.xlsx
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.clients.openrouter_client import OpenRouterClient  # noqa: E402
from src.config.settings import get_settings  # noqa: E402
from src.models.sdtm_models import GenerationConfig, RateLimitConfig  # noqa: E402
from src.processors.sdtm_processor import SDTMProcessor  # noqa: E402
from src.spec_mapper import SpecMapper  # noqa: E402

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def generate_als_from_crf(
    json_file: str,
    output_file: str | None = None,
    provider: str = "openrouter",
    model: str | None = None,
    enable_kb: bool = True,
    language: str = "en",
) -> str:
    """
    Generate ALS file from CRF JSON data using RAG/AI.

    Args:
        json_file: Path to CRF JSON file
        output_file: Optional output file path (auto-generated if None)
        provider: AI provider (openrouter/openai/google)
        model: Model name to use
        enable_kb: Enable knowledge base integration
        language: Prompt language (en/cn)

    Returns:
        Path to generated ALS file
    """
    logger.info("=" * 80)
    logger.info("STEP 1: Generating ALS from CRF data (using RAG/AI)")
    logger.info("=" * 80)

    # Load environment variables
    load_dotenv()
    settings = get_settings()
    model = model or settings.ai.default_model

    # Resolve input file (relative to project root)
    json_path = Path(json_file)
    if not json_path.is_absolute():
        processed_data_path = os.getenv("PROCESSED_DATA_PATH", "data/processed")
        json_path = project_root / processed_data_path / json_file

    if not json_path.exists():
        raise FileNotFoundError(f"CRF JSON file not found: {json_path}")

    logger.info(f"Input CRF JSON: {json_path}")

    # Determine output file (relative to project root)
    if output_file is None:
        als_output_path = os.getenv("ALS_OUTPUT_PATH", "data/output")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = str(project_root / als_output_path / f"{json_path.stem}_als_{timestamp}.xlsx")

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Output ALS file: {output_path}")

    # Initialize AI client
    logger.info(f"Initializing {provider} client (model: {model})...")

    if provider == "openrouter":
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not set in environment")

        base_url = settings.ai.openrouter_base_url
        client = OpenRouterClient(api_key=api_key, base_url=base_url)
        client.set_model(model)
    else:
        raise NotImplementedError(f"Provider {provider} not yet supported in this script")

    if not client.initialize():
        raise RuntimeError("Failed to initialize AI client")

    logger.info("✓ AI client initialized")

    # Initialize SDTM processor
    logger.info("Initializing SDTM processor...")

    generation_config = GenerationConfig(max_output_tokens=2000, temperature=0.15, top_p=0.95, top_k=40)

    rate_limit_config = RateLimitConfig(requests_per_minute=60)

    processor = SDTMProcessor(
        client=client,
        model_name=model,
        generation_config=generation_config,
        rate_limit_config=rate_limit_config,
        debug=False,
        language=language,
        enable_knowledge_base=enable_kb,
    )

    logger.info("✓ SDTM processor initialized")

    # Process mappings
    logger.info("Processing CRF data to generate ALS...")

    import json

    with open(json_path, encoding="utf-8") as f:
        mappings = json.load(f)

    logger.info(f"Loaded {len(mappings)} CRF mapping(s)")

    # Generate recommendations
    recommendations = processor.process_mappings(
        mappings,
        dry_run=False,
        resume=False,
        output_file=str(output_path.with_suffix("")),  # Without extension
        input_file=str(json_path),  # 用于 eCRF sheet merge
    )

    # Save to Excel and JSON
    processor.save_recommendations(
        recommendations,
        str(output_path.with_suffix("")),  # Without extension
        original_mappings=mappings,
        input_file=str(json_path),  # 用于 eCRF sheet merge
    )

    # Find the generated Excel file (processor adds model name suffix)
    model_suffix = f"_{client.get_sanitized_model_name(model)}"
    final_output = output_path.parent / f"{output_path.stem}{model_suffix}.xlsx"

    if not final_output.exists():
        # Fallback to original name
        final_output = output_path

    logger.info(f"✓ ALS file generated: {final_output}")
    logger.info("")

    return str(final_output)


def map_als_to_spec(
    als_file: str,
    template_file: str,
    output_file: str,
    als_sheet: str | None = None,
    highlight: bool | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Map ALS file to SDTM Spec template.

    Args:
        als_file: Path to ALS Excel file
        template_file: Path to SDTM template Excel file
        output_file: Path to output Spec file
        als_sheet: Sheet name in ALS file
        highlight: Apply highlighting to new mappings
        dry_run: Preview mode without saving

    Returns:
        Statistics dictionary
    """
    logger.info("=" * 80)
    logger.info("STEP 2: Mapping ALS to SDTM Spec Template")
    logger.info("=" * 80)

    # Load environment variables (if not already loaded)
    load_dotenv()

    # Load environment variables for defaults
    if als_sheet is None:
        als_sheet = os.getenv("ALS_DEFAULT_SHEET", "Sheet1")
    if highlight is None:
        highlight = os.getenv("SPEC_HIGHLIGHT_MAPPINGS", "true").lower() == "true"

    als_path = Path(als_file)
    template_path = Path(template_file)
    output_path = Path(output_file)

    # Validate files
    if not als_path.exists():
        raise FileNotFoundError(f"ALS file not found: {als_path}")
    if not template_path.exists():
        raise FileNotFoundError(f"Template file not found: {template_path}")

    logger.info(f"ALS file: {als_path}")
    logger.info(f"Template file: {template_path}")
    logger.info(f"Output file: {output_path}")
    logger.info(f"ALS sheet: {als_sheet}")
    logger.info(f"Highlight mappings: {highlight}")

    # Initialize mapper
    logger.info("Initializing Spec Mapper...")
    mapper = SpecMapper(als_file=als_path, template_file=template_path, als_sheet=als_sheet, log_level="INFO")

    logger.info("✓ Spec Mapper initialized")

    # Process mapping
    logger.info("Processing ALS to Spec mapping...")
    stats = mapper.process(output_file=output_path, highlight=highlight, dry_run=dry_run)

    logger.info("")
    logger.info("=" * 80)
    logger.info("MAPPING STATISTICS")
    logger.info("=" * 80)
    logger.info(f"ALS Records:         {stats['als_records']}")
    logger.info(f"Template Records:    {stats['template_records']}")
    logger.info(f"Cell Updates:        {stats['updates']}")
    logger.info(f"SUPP Rows Inserted:  {stats['supp_records']}")
    logger.info(f"Conditional Groups:  {stats['conditional_records']}")

    if not dry_run:
        logger.info(f"\n✓ Final Spec saved to: {output_path}")
    else:
        logger.info("\n(DRY RUN - no files saved)")

    logger.info("=" * 80)
    logger.info("")

    return stats


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate complete SDTM Spec from CRF data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # End-to-end: CRF JSON → ALS → Spec
  python scripts/generate_full_spec.py \\
    --json-file data/processed/crf_data.json \\
    --template-file data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx \\
    --output data/spec_output/final_spec.xlsx

  # Skip ALS generation (use existing ALS file)
  python scripts/generate_full_spec.py \\
    --als-file data/output/existing_als.xlsx \\
    --template-file data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx \\
    --output data/spec_output/final_spec.xlsx

  # Dry run (preview without saving)
  python scripts/generate_full_spec.py \\
    --als-file data/output/als.xlsx \\
    --template-file data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx \\
    --output data/spec_output/result.xlsx \\
    --dry-run
        """,
    )

    # Input options (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--json-file", help="CRF JSON file (generate ALS first, then map to Spec)")
    input_group.add_argument("--als-file", help="Existing ALS Excel file (skip ALS generation, map directly to Spec)")

    # Required arguments
    parser.add_argument("--template-file", required=True, help="SDTM template Excel file")
    parser.add_argument("--output", required=True, help="Output Spec Excel file")

    # ALS generation options (only used if --json-file is specified)
    parser.add_argument(
        "--provider",
        default="openrouter",
        choices=["openrouter", "openai", "google"],
        help="AI provider (default: openrouter)",
    )
    parser.add_argument(
        "--model",
        default=get_settings().ai.default_model,
        help="AI model name (default: from DEFAULT_MODEL)",
    )
    parser.add_argument(
        "--enable-kb", action="store_true", default=True, help="Enable RAG knowledge base (default: True)"
    )
    parser.add_argument("--language", default="en", choices=["en", "cn"], help="Prompt language (default: en)")

    # Mapping options
    parser.add_argument(
        "--als-sheet", default=None, help='ALS sheet name (default: from ALS_DEFAULT_SHEET env or "Sheet1")'
    )
    parser.add_argument(
        "--highlight",
        action="store_true",
        default=None,
        help="Highlight new mappings with yellow background (default: from SPEC_HIGHLIGHT_MAPPINGS env or True)",
    )
    parser.add_argument("--no-highlight", action="store_false", dest="highlight", help="Disable highlighting")
    parser.add_argument("--dry-run", action="store_true", help="Preview mode without saving files")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )

    args = parser.parse_args()

    # Set logging level
    logging.getLogger().setLevel(getattr(logging, args.log_level.upper()))

    try:
        # Step 1: Generate or locate ALS file
        if args.json_file:
            als_file = generate_als_from_crf(
                json_file=args.json_file,
                output_file=None,  # Auto-generate
                provider=args.provider,
                model=args.model,
                enable_kb=args.enable_kb,
                language=args.language,
            )
        else:
            # Use existing ALS file
            als_file = args.als_file
            logger.info("=" * 80)
            logger.info("Using existing ALS file (skipping ALS generation)")
            logger.info("=" * 80)
            logger.info(f"ALS file: {als_file}")
            logger.info("")

        # Step 2: Map ALS to SDTM Spec
        map_als_to_spec(
            als_file=als_file,
            template_file=args.template_file,
            output_file=args.output,
            als_sheet=args.als_sheet,
            highlight=args.highlight,
            dry_run=args.dry_run,
        )

        # Final summary
        logger.info("=" * 80)
        logger.info("✅ COMPLETED SUCCESSFULLY")
        logger.info("=" * 80)
        if args.json_file:
            logger.info("Pipeline: CRF → ALS → Spec")
        else:
            logger.info("Pipeline: ALS → Spec")
        logger.info(f"Final output: {args.output if not args.dry_run else '(dry run)'}")
        logger.info("=" * 80)

        return 0

    except KeyboardInterrupt:
        logger.warning("\n\nOperation cancelled by user")
        return 1

    except Exception as e:
        logger.error(f"\n\n❌ Error: {e!s}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
