#!/usr/bin/env python

"""
Generate SDTM recommendations from mapped annotations using AI.
Supports Google Generative AI, OpenAI, and DeepSeek.
"""

import argparse
import json
import os
import sys
from typing import Any

# Add the project root to Python path to enable src imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from dotenv import load_dotenv  # noqa: E402

from src.clients.openrouter_client import OpenRouterClient  # noqa: E402
from src.config.settings import get_settings  # noqa: E402
from src.processors.sdtm_processor import SDTMProcessor  # noqa: E402


def load_json_mappings(json_file: str) -> dict[str, Any]:
    """
    Load JSON mappings from file.

    Args:
        json_file: Path to JSON file

    Returns:
        Dictionary containing the mappings
    """
    try:
        with open(json_file, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading JSON file: {e!s}")
        sys.exit(1)


def main():
    """Main entry point."""
    settings = get_settings()
    try:
        default_rag_top_k = int(os.getenv("RAG_TOP_K", "3"))
    except (TypeError, ValueError):
        default_rag_top_k = 3
    try:
        default_rag_min_score = float(os.getenv("RAG_MIN_SCORE", "0.4"))
    except (TypeError, ValueError):
        default_rag_min_score = 0.4
    try:
        default_rag_char_limit = int(os.getenv("RAG_CHAR_LIMIT", "1500"))
    except (TypeError, ValueError):
        default_rag_char_limit = 1500

    parser = argparse.ArgumentParser(description="Generate SDTM recommendations from mapped annotations.")

    # Required arguments
    parser.add_argument("--json-file", required=True, help="Path to JSON file containing mappings")

    # Get default paths from environment variables
    default_output_path = os.getenv("ALS_OUTPUT_PATH", "data/output")
    default_kb_path = os.getenv("KNOWLEDGE_STRUCTURED_PATH", "data/knowledge_base/structured")
    default_rate_limit = int(os.getenv("RATE_LIMIT_RPM", "120"))

    # Optional arguments
    parser.add_argument(
        "--output", help=f"Base name for output files (without extension) (default: {default_output_path}/als2sdtm)"
    )
    parser.add_argument("--api-key", help="API key (will use env variables if not provided)")

    parser.add_argument(
        "--provider",
        choices=["google", "openai", "deepseek", "openrouter"],
        default=settings.ai.default_provider,
        help="AI provider to use (default: from DEFAULT_AI_PROVIDER env or openrouter)",
    )
    parser.add_argument(
        "--model", help="Model to use for generation (provider-specific, default: from DEFAULT_MODEL env)"
    )
    parser.add_argument("--base-url", help="Base URL for custom endpoints (e.g., DeepSeek API)")
    parser.add_argument("--list-models", action="store_true", help="List available models and exit")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode with enhanced features")
    parser.add_argument(
        "--log-ai",
        action="store_true",
        help="Persist full prompts/responses for each AI call (also enabled by SDTM_LOG_AI=true in .env)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run without making API calls")
    parser.add_argument("--resume", action="store_true", help="Resume from previous run")
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=default_rate_limit,
        help=f"Rate limit in requests per minute (default: {default_rate_limit})",
    )
    parser.add_argument(
        "--max-output-tokens", type=int, default=2000, help="Maximum number of tokens to generate (default: 2000)"
    )
    parser.add_argument("--temperature", type=float, default=0.15, help="Sampling temperature (0.0-1.0, default: 0.15)")
    parser.add_argument(
        "--language",
        choices=["cn", "en"],
        default=os.getenv("DEFAULT_LANGUAGE", "cn"),
        help="Language for prompts (cn=Chinese, en=English, default: from DEFAULT_LANGUAGE env or en)",
    )
    parser.add_argument(
        "--kb-path",
        default=default_kb_path,
        help=f"Knowledge base directory used for RAG prompt augmentation (default: {default_kb_path})",
    )
    parser.add_argument(
        "--rag-top-k",
        type=int,
        default=default_rag_top_k,
        help="Number of RAG chunks to retrieve for prompt augmentation (default: 3)",
    )
    parser.add_argument(
        "--rag-embedding-model",
        default=os.getenv("RAG_EMBED_MODEL", "Qwen3-Embed"),
        help="Embedding model to use for RAG retrieval (default: Qwen3-Embed)",
    )
    parser.add_argument(
        "--rag-min-score",
        type=float,
        default=default_rag_min_score,
        help="Minimum cosine similarity score to keep a RAG chunk (default: 0.4)",
    )
    parser.add_argument(
        "--rag-char-limit",
        type=int,
        default=default_rag_char_limit,
        help="Maximum characters of RAG excerpts to inject into prompts (default: 1500)",
    )
    parser.add_argument(
        "--rag-verbose", action="store_true", help="Output detailed RAG retrieval logs for each variable"
    )
    parser.add_argument(
        "--kb-verbose", action="store_true", help="Output detailed KB vector matching logs for each variable"
    )
    parser.add_argument(
        "--force-rag", action="store_true", help="Force RAG retrieval even when KB confidence is high (for debugging)"
    )
    parser.add_argument(
        "--kb-extra-files",
        nargs="*",
        default=[],
        help="Additional KB files to merge with default (space-separated, e.g. --kb-extra-files file1.parquet file2.json)",
    )
    parser.add_argument(
        "--save-frequency",
        type=int,
        help="How often (in processed pairs) to flush progress to disk. Default adapts to dataset size.",
    )

    # Parallel processing options
    parser.add_argument(
        "--parallel",
        action="store_true",
        dest="enable_parallel",
        default=None,
        help="Enable parallel processing with thread pool (default: True, or from SDTM_ENABLE_PARALLEL env)",
    )
    parser.add_argument(
        "--no-parallel",
        action="store_false",
        dest="enable_parallel",
        help="Disable parallel processing, use sequential mode",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Maximum number of parallel workers (default: 5, or from SDTM_MAX_WORKERS env)",
    )

    args = parser.parse_args()

    # Load environment variables
    load_dotenv()

    try:
        # Get processed data path from environment
        processed_data_path = os.getenv("PROCESSED_DATA_PATH", "data/processed")

        # Handle input file path - add default input directory if needed
        json_file_path = args.json_file
        if not os.path.isabs(json_file_path) and not os.path.exists(json_file_path):
            # If it's not an absolute path and doesn't exist in current directory,
            # try looking in processed data directory (relative to project root)
            default_input_path = os.path.join(project_root, processed_data_path, json_file_path)
            if os.path.exists(default_input_path):
                json_file_path = default_input_path
                print(f"Using input file from default directory: {json_file_path}")
            else:
                # If file doesn't exist in default directory either, use original path
                # (will let the file loading function handle the error)
                json_file_path = args.json_file

        # Set default output file name if not provided
        if args.output:
            output_file = args.output
        else:
            # Create default output path in data/output/ directory (relative to project root)
            output_dir = os.path.join(project_root, default_output_path)
            os.makedirs(output_dir, exist_ok=True)
            base_name = os.path.splitext(os.path.basename(json_file_path))[0]
            output_file = os.path.join(output_dir, f"{base_name}_sdtm_recommendations")

        # Initialize the appropriate client and set provider-specific default models
        if args.provider == "openrouter":
            # For OpenRouter, use the provided base URL, env variable, or default
            base_url = args.base_url or settings.ai.openrouter_base_url
            client = OpenRouterClient(api_key=args.api_key, base_url=base_url)
            model_name = args.model or settings.ai.default_model
            print(f"Using OpenRouter model: {model_name} at {base_url}")

            # Ensure the model name is correctly set
            if hasattr(client, "set_model"):
                client.set_model(model_name)

        # Dry-run only renders local prompts and must not require credentials.
        # Listing models and real generation still require an initialized client.
        if not args.dry_run and not client.initialize():
            print("Failed to initialize AI client")
            sys.exit(1)

        # List models if requested
        if args.list_models:
            models = client.list_available_models()
            print("\nAvailable models:")
            for model in models:
                print(f"  - {model}")
            sys.exit(0)

        # Set the model if the client supports it
        if hasattr(client, "set_model"):
            client.set_model(model_name)

        # Create configuration objects with proper attributes
        class GenerationConfig:
            def __init__(self, max_output_tokens, temperature, top_p, top_k):
                self.max_output_tokens = max_output_tokens
                self.temperature = temperature
                self.top_p = top_p
                self.top_k = top_k

        class RateLimitConfig:
            def __init__(self, requests_per_minute):
                self.requests_per_minute = requests_per_minute

        # Configure generation and rate limiting
        generation_config = GenerationConfig(
            max_output_tokens=args.max_output_tokens, temperature=args.temperature, top_p=0.95, top_k=40
        )

        rate_limit_config = RateLimitConfig(args.rate_limit)

        # Create processor instance
        print("📝 Using SDTM Processor with Knowledge Base Integration")
        processor = SDTMProcessor(
            client=client,
            model_name=model_name,
            generation_config=generation_config,
            rate_limit_config=rate_limit_config,
            debug=args.debug,
            language=args.language,
            enable_knowledge_base=True,
            log_ai_interactions=args.log_ai if args.log_ai else None,  # None → use env var
            save_frequency=args.save_frequency,
            max_workers=args.max_workers,
            enable_parallel=args.enable_parallel,
            rag_config={
                "kb_path": args.kb_path,
                "extra_kb_files": args.kb_extra_files,
                "top_k": args.rag_top_k,
                "embedding_model": args.rag_embedding_model,
                "min_score": args.rag_min_score,
                "char_limit": args.rag_char_limit,
                "verbose": args.rag_verbose,
                "kb_verbose": args.kb_verbose,
                "force": args.force_rag,
            },
        )

        # Load mappings
        mappings = load_json_mappings(json_file_path)

        # Get model suffix for output file
        model_suffix = f"_{client.get_sanitized_model_name(model_name)}"

        # When resuming, always use the model-suffixed filename
        if args.resume:
            # Ensure output_file has the model suffix
            if model_suffix not in output_file:
                output_file_with_model = f"{output_file}{model_suffix}"
            else:
                output_file_with_model = output_file

            # Check if the model-suffixed file exists
            json_with_model = f"{output_file_with_model}.json"
            if os.path.exists(json_with_model):
                output_file = output_file_with_model
                print(f"Resuming with model-specific file: {json_with_model}")
            else:
                print(f"Warning: No existing file found at {json_with_model}")
                print(f"Will create a new file with model suffix: {model_suffix}")
                # Still use the model-suffixed filename for new files
                output_file = output_file_with_model

        # Process mappings
        if args.dry_run:
            # In dry run mode, just preview the prompts
            processor.process_mappings(
                mappings, dry_run=True, resume=args.resume, output_file=output_file, input_file=json_file_path
            )
            print("\nDry run completed successfully.")

            # Show what the output filename would be
            if model_suffix not in output_file:
                output_file_with_model = f"{output_file}{model_suffix}"
            else:
                output_file_with_model = output_file

            print("In a real run, output files would be:")
            print(f"  - {output_file_with_model}.json")
            print(f"  - {output_file_with_model}.xlsx")

            # Show enhanced features info
            if args.debug:
                print("\nEnhanced features enabled:")
                print(f"  - Language: {args.language}")
                print("  - Debug mode: Enabled")
                print("  - SUPP dataset support: Enabled")
                print("  - Three-step recommendation process: Enabled")

            sys.exit(0)

        # Generate recommendations and get the full output path with model name
        recommendations = processor.process_mappings(
            mappings,
            resume=args.resume,
            output_file=output_file,
            input_file=json_file_path,  # Pass input file path for eCRF sheet merge
        )

        # output_file now has the model name added from process_mappings
        processor.save_recommendations(
            recommendations,
            output_file,
            resume=args.resume,
            original_mappings=mappings,
            input_file=json_file_path,  # Pass input file path for eCRF sheet merge
        )

        # Make sure to include model name in the output file for display
        if model_suffix not in output_file:
            output_file = f"{output_file}{model_suffix}"

        # Print summary
        print("\nRecommendations generated successfully!")
        print("Results saved to:")
        json_output = f"{output_file}.json"
        excel_output = f"{output_file}.xlsx"
        print(f"  - {json_output}")
        print(f"  - {excel_output}")

        # Show enhanced features info
        if args.debug:
            print("\nEnhanced features used:")
            print(f"  - Language: {args.language}")
            print("  - Knowledge Base Integration: ON")
            print("  - SUPP dataset support: Enabled")
            print("  - Three-step recommendation process: Domain → Variable → SUPP")
            print("  - Multi-domain support: AE, DM, LB, VS, CM, PR, EX, QS, RS, TU, TR and other domains")

    except Exception as e:
        if args.debug:
            import traceback

            traceback.print_exc()
        else:
            print(f"Error: {e!s}")
        sys.exit(1)


if __name__ == "__main__":
    main()
