# AGENTS.md

This file provides guidance to Codex when working with this repository.

## What This Is

`SIRIUS` is an intelligent SDTM mapping system for clinical data standards work. It turns CRF / EDC-derived ALS metadata into SDTM mapping recommendations, supports project-specific knowledge bases, and can generate filled SDTM Spec workbooks from reviewed ALS2SDTM files.

The main product surfaces are:

- Local Web UI: FastAPI backend in `app.py` and `src/web/`, with static frontend assets in `src/web/static/`.
- Mapping engine: `src/processors/`, `src/knowledge_base/`, `src/rag/`, `src/prompts/`, and `src/clients/`.
- Spec generation: `src/spec_mapper/` plus SDTM template workbooks under `data/knowledge_base/template_spec/`.
- Scripts: extraction, KB preprocessing, recommendation generation, prompt validation, and full Spec generation under `scripts/`.
- Tests: unit, characterization, snapshot, and smoke tests under `tests/`.

The system intentionally combines deterministic rules, project KB lookup, RAG context, and LLM inference. Do not treat LLM output as authoritative until it has passed normalization, deterministic validation, and batch consistency checks.

## Authoritative Documents

- `README.md`: user-facing behavior, workflows, API endpoints, roadmap, and release notes.
- `pyproject.toml`: Python version, pytest configuration, Ruff rules, coverage settings, and mypy scope.
- `env_template.txt`: supported environment variables and operational defaults.
- `src/spec_mapper/README.md`: ALS to SDTM Spec rules, workbook behavior, and mapping examples.
- `src/prompts/templates/`, `src/prompts/rules/`, `src/prompts/examples/`: prompt contract and rule data.
- `tests/`: executable behavior contract. Add or update tests with behavior changes.
- `AGENTS.md`: Codex guidance.
- `CLAUDE.md`: Claude Code guidance.

If these sources disagree with code or tests, call out the mismatch and update implementation, docs, and tests together when appropriate.

## Repository Structure

| Path | Purpose |
| --- | --- |
| `app.py` | FastAPI app entrypoint, static file mount, router registration, rate-limit setup |
| `src/clients/` | AI client interfaces and OpenRouter implementation |
| `src/config/settings.py` | Typed pydantic-settings configuration facade |
| `src/config/domain_semantic_map.py` | SDTM domain hints, domain variables, and semantic maps |
| `src/infrastructure/` | Audit logging, data masking, and shared logging infrastructure |
| `src/knowledge_base/` | KB loading, exact matching, ranking, and LLM-facing KB query interface |
| `src/rag/` | Chunking, embedding, vector storage, retrieval, and prompt augmentation |
| `src/processors/sdtm_processor.py` | Batch SDTM recommendation orchestration |
| `src/processors/cascade.py` | 4-level KB/RAG/LLM cascade decision engine |
| `src/processors/recommendation_orchestrator.py` | Per-variable cascade, LLM fallback, and normalization pipeline |
| `src/processors/deterministic_validator.py` | SDTM domain, variable, SUPP, and IG 3.4 validation rules |
| `src/processors/mapping_critic.py` | Cross-record consistency checks |
| `src/prompts/` | YAML prompt templates, rules, examples, loader, and prompt builder |
| `src/spec_mapper/` | ALS2SDTM to SDTM Spec Excel mapper |
| `src/web/routers/` | Upload, files, jobs, session, corrections, and spec mapper API routes |
| `src/web/security.py` | Upload validation, filename sanitization, safe paths, command runner, rate limits |
| `src/web/session_manager.py` | Per-session file, job, and KB isolation plus cleanup |
| `src/web/static/` | Browser UI assets |
| `scripts/` | CLI utilities for extraction, conversion, recommendations, prompt CI, and Spec generation |
| `data/knowledge_base/template_spec/` | SDTM IG 3.2 and IG 3.4 template workbooks |
| `tests/` | Pytest suite and snapshots |

## Locked Product Decisions

- Preserve the 4-level cascade shape: KB direct match -> KB high confidence -> RAG-enhanced path -> LLM fallback. Thresholds are configuration, but cascade level semantics and audit labels must remain stable.
- LLM use is allowed only through configured clients and should stay behind masking, rate limiting, normalization, deterministic validation, and audit logging.
- Clinical inputs can contain sensitive data. Do not add telemetry, unapproved external storage, new remote processors, or raw document logging. Keep API keys out of source, tests, fixtures, and generated outputs.
- Session files and session KB are isolated by `X-Session-ID`; preserve filename sanitization, safe path checks, upload size/type validation, and cleanup behavior.
- User/project corrections and uploaded ALS2SDTM examples are part of the learning loop. Do not bypass session KB precedence or silently merge unrelated projects.
- Prompt behavior is data-driven through YAML. When changing prompts, rules, examples, or placeholders, update validation and snapshots instead of hard-coding one-off prompt strings.
- SDTM variables must obey CDISC constraints: valid domain, variable name length <= 8 for standard variables, and SUPP mappings represented as `sdtm_variable="QVAL"` with QNAM carried in `supp_variable` or structured fields.
- Spec Mapper must preserve workbook formatting, formulas, hyperlinks, sheet structure, highlighted generated cells, SUPP insertion behavior, CODELIST merge/insert behavior, and IG 3.2 / IG 3.4 differences.
- Web UI paths should call the real backend jobs and artifacts. Avoid mock-only success paths in production UI code.
- Generated clinical outputs, session artifacts, logs, `.env`, virtualenvs, caches, screenshots, and bulky Excel/PDF artifacts should not be committed unless the maintainer explicitly requests them.

## Working Discipline

- Keep changes scoped to the requested behavior. Avoid broad refactors while touching mapping, prompt, Excel, or Web workflows.
- Prefer structured APIs and parsers: `pandas`, `openpyxl`, Pydantic models, PyMuPDF/Excel-specific libraries where applicable, and existing helper modules over ad hoc string manipulation.
- Use `src.config.settings.get_settings()` or the established settings path for new configuration. Avoid scattering new `os.getenv` calls unless working inside legacy code that already uses them.
- For Web routes, reuse `sanitize_filename`, `safe_path`, `validate_upload_file`, `save_upload`, `run_command`, `job_manager`, and `session_manager` patterns.
- Return clear 4xx errors for invalid user files or bad input. Do not leak internal exceptions, file paths, secrets, or raw clinical content in API responses.
- Tests should use fakes or fixtures for LLM clients. Do not require real OpenRouter calls for normal unit or characterization tests.
- When changing workbook generation, add focused tests around the affected sheet, columns, formulas, styles, or inserted rows.
- When changing prompts, run prompt CI and update prompt snapshots when the intended rendered prompt changes.
- Treat frontend copy and workflow states as product behavior. Keep UI promises aligned with backend APIs and job states.

## Commands

Use `python3` instead of `python` if that is the available interpreter.

```bash
# Install the locked development environment
uv sync --locked

# Run the local Web UI
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload

# Full test suite
python -m pytest -q

# Focused test groups
python -m pytest tests/unit -q
python -m pytest tests/characterization -q
python -m pytest tests/smoke_test.py -q

# Prompt YAML validation
python scripts/prompt_ci/validate_prompts.py

# Common focused tests
python -m pytest tests/unit/test_cascade.py -q
python -m pytest tests/unit/test_recommendation_orchestrator.py -q
python -m pytest tests/unit/test_mapping_critic.py -q
python -m pytest tests/unit/test_spec_mapper_v34.py -q
python -m pytest tests/unit/test_prompt_snapshot.py -q

# Lint and type checks
python -m ruff check .
python -m ruff format --check .
python -m mypy src

# Generate SDTM recommendations from processed JSON
python scripts/generate_sdtm_recommendations.py \
  --json-file data/processed/your_file.json \
  --model google/gemini-3-flash-preview \
  --language cn \
  --enable-rag

# Generate full SDTM Spec from reviewed ALS2SDTM
python scripts/generate_full_spec.py \
  --als-file data/output/als2sdtm.xlsx \
  --template-file data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx \
  --output data/spec_output/final_spec.xlsx

# Lightweight repo hygiene
git diff --check
git status --short
```

## Pull Request Review Behavior

When reviewing changes in this repository, prioritize findings in this order:

1. clinical data safety, secrets, PHI/PII exposure, or unexpected external transmission
2. SDTM correctness, deterministic validator regressions, and Spec workbook corruption
3. cascade / KB / RAG / LLM behavior changes that alter confidence, source labels, or auditability
4. session isolation, upload safety, rate limits, and path traversal risks
5. missing tests, prompt validation, or snapshot updates
6. user-facing Web UI and API contract drift
7. documentation drift

For Claude-authored PRs, Codex should review and comment by default. Make follow-up commits only when the maintainer explicitly asks. When asked to modify a PR, push focused commits to the same branch and report the validation run.
