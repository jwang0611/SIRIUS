# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in this repository.

## What This Is

`SIRIUS` is a Python 3.11 clinical data standards application for intelligent CRF / ALS to SDTM mapping. It provides:

- a FastAPI + static Web UI workflow for upload, preprocessing, AI recommendation, review artifacts, and Spec generation
- a 4-level recommendation cascade: KB direct match, KB high confidence, RAG-enhanced context, and LLM fallback
- deterministic post-processing for SDTM domain validity, variable validity, SUPP handling, IG 3.4 checks, and batch consistency
- session-specific project KB ingestion from ALS2SDTM examples and reviewed outputs
- GxP-oriented JSONL audit logging and PHI/PII masking before LLM calls
- an Excel Spec Mapper that fills SDTM IG 3.2 / IG 3.4 templates while preserving workbook behavior

The product promise is not "LLM decides the mapping." The promise is a reviewable, auditable assistant for clinical programmers and standards reviewers, with deterministic checks around every AI recommendation.

## Authoritative Sources

- `README.md` is the user-facing contract for workflows, API endpoints, behavior, and release notes.
- `pyproject.toml` defines Python version, test discovery, lint rules, and type-checking scope.
- `env_template.txt` lists supported runtime configuration.
- `src/spec_mapper/README.md` documents Excel Spec mapping behavior and workbook rules.
- Prompt YAML under `src/prompts/` is the source of truth for prompt text, rules, and examples.
- `tests/` is the executable behavior contract.

When changing user-visible behavior, update code, tests, and docs together.

## Repository Structure

| Path | Content |
| --- | --- |
| `app.py` | FastAPI entrypoint, middleware, static assets, router registration |
| `src/clients/` | Base AI client and OpenRouter client |
| `src/config/` | Typed settings and SDTM domain semantic maps |
| `src/infrastructure/` | Audit logger, data masker, logging helpers |
| `src/knowledge_base/` | KB loading, exact matching, ranking, query interface |
| `src/rag/` | Embeddings, vector store, retrieval, chunking, prompt augmentation |
| `src/processors/` | SDTM recommendation pipeline, cascade, normalizer, validator, critic, IO helpers |
| `src/models/` | SDTM and boundary models |
| `src/prompts/` | YAML prompt templates, rules, examples, loader, prompt generator |
| `src/spec_mapper/` | ALS2SDTM to SDTM Spec workbook mapper |
| `src/web/` | Web routes, security helpers, job/session management, static frontend |
| `scripts/` | CLI utilities for extraction, KB conversion, recommendation generation, prompt CI, Spec generation |
| `data/knowledge_base/template_spec/` | SDTM template workbooks |
| `tests/` | Unit, characterization, snapshot, and smoke tests |

## Locked Product Decisions

Follow these unless the maintainer explicitly changes direction:

1. Keep the 4-level cascade semantics stable. If thresholds change, source labels, audit `cascade_level`, and tests must still make sense.
2. Keep deterministic validation after LLM output. Do not skip normalizer, domain inference, `DeterministicValidator`, or `MappingCritic` paths for convenience.
3. Treat clinical content as sensitive. Do not add telemetry, new remote services, persistent external storage, raw prompt logging, or broad file retention without explicit approval.
4. Preserve session isolation. User uploads, session KB, generated files, and jobs must stay scoped by session where the current design expects it.
5. Keep Web UI behavior aligned with real backend tasks. Production UI should not report success from mock data or simulated artifacts.
6. Keep prompt content in YAML where the project has moved it. Avoid reintroducing large hard-coded prompt blocks in Python.
7. Preserve Spec Mapper workbook behavior: styles, formulas, hyperlinks, inserted rows, highlighted generated cells, SUPP rows, CODELIST updates, and IG 3.2 / IG 3.4 differences.
8. Do not commit `.env`, secrets, generated clinical outputs, session files, audit logs, temporary workbooks, caches, virtualenvs, or build artifacts.

## Development Commands

```bash
# Install the locked development environment
uv sync --locked

# Start local Web UI
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload

# Full tests
python -m pytest -q

# Focused tests
python -m pytest tests/unit -q
python -m pytest tests/characterization -q
python -m pytest tests/unit/test_spec_mapper_v34.py -q
python -m pytest tests/unit/test_prompt_snapshot.py -q

# Prompt validation
python scripts/prompt_ci/validate_prompts.py

# Lint / format / typing
python -m ruff check .
python -m ruff format --check .
python -m mypy src

# Manual recommendation run
python scripts/generate_sdtm_recommendations.py \
  --json-file data/processed/your_file.json \
  --model google/gemini-3-flash-preview \
  --language cn \
  --enable-rag

# Manual Spec generation
python scripts/generate_full_spec.py \
  --als-file data/output/als2sdtm.xlsx \
  --template-file data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx \
  --output data/spec_output/final_spec.xlsx
```

Use `python3` if `python` is not available.

## Implementation Guidance

- Prefer existing modules and dependency-injected boundaries before adding new abstractions. The current pipeline already has `CascadePredictor`, `RecommendationOrchestrator`, `RecommendationNormalizer`, `LLMInferenceService`, and Pydantic boundary models.
- Put new runtime configuration in `src/config/settings.py` and document it in `env_template.txt`.
- Use fake AI clients or deterministic fixtures in tests. Normal tests should not require live OpenRouter credentials.
- For Web routes, reuse `sanitize_filename`, `safe_path`, `validate_upload_file`, `save_upload`, `run_command`, `job_manager`, and `session_manager`.
- Return helpful 4xx errors for invalid workbooks, JSON, filenames, or missing files. Avoid leaking stack traces, absolute server paths, secrets, or raw clinical content.
- If you touch prompts, run `python scripts/prompt_ci/validate_prompts.py` and update snapshot tests when the rendered prompt intentionally changes.
- If you touch Spec Mapper logic, test the affected workbook sheet and verify formulas, styles, inserted rows, hyperlinks, and IG version-specific behavior.
- If you touch cascade, KB, RAG, normalization, or validation, add focused tests for source labels, confidence, short-circuit behavior, and audit-relevant fields.
- If you touch frontend workflow text or states, make sure the backend actually supports what the UI promises.

## Claude / Codex Collaboration

Claude may draft implementation changes and PRs. Codex may review those changes and leave comments. When responding to review feedback:

- Fix actionable, in-scope comments first.
- Keep follow-up commits focused and on the same PR branch unless the maintainer says otherwise.
- Report what changed and which validation commands ran.
- Ask before expanding scope beyond the requested product behavior, especially around LLM providers, clinical data retention, Web security, and Excel template semantics.
