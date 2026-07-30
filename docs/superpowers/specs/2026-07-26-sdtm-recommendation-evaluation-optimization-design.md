# Auditable SDTM Recommendation Optimization Design

## Purpose

Complete one focused, auditable, and reversible optimization cycle for SIRIUS
SDTM Domain + Variable recommendations. The cycle must improve the real
`SDTMProcessor` production path, measure the complete held-out set, and make an
evidence-based accept or rollback decision without leaking held-out answers
into production knowledge sources.

## Fixed Scope

The implementation will:

1. make prompt YAML the single source of truth;
2. make the prompt output contract structurally consistent;
3. remove the conflicting FA `when` expression guidance;
4. expose production cascade provenance in processor output;
5. create reproducible run manifests and machine-readable A/B reports;
6. apply explicit paired statistical and deterministic-quality gates; and
7. retain a failed experiment report while reverting a rejected prompt
   candidate.

It will not migrate `SDTMProcessor` to the currently unused
`RecommendationOrchestrator` or `RecommendationNormalizer`, change the
four-level cascade semantics, adjudicate or write back the 74
`KB_DISAGREE` records, or refactor unrelated Web, Spec Mapper, desktop, or
clinical-data workflows.

## Repository and Isolation

Work occurs in the isolated branch `codex/sdtm-eval-optimization`, created from
`ebb860b1ce1e62c63e4c7844bf09b3eb104dd998`. The original `main` checkout has
six pre-existing user deletions under `desktop/build/`; they remain untouched
and are never staged by this work.

The implementation is split into two auditable revisions:

- **Audit baseline revision:** evaluation/reporting/manifest support and
  production provenance fields only. It must not change prompt content or
  recommendation decisions.
- **Prompt candidate revision:** YAML single-source repair and structured
  prompt-contract changes, with their tests and snapshots.

The baseline run uses the audit baseline revision. The improved run uses the
prompt candidate revision. If the candidate fails a gate, only the prompt
candidate revision is reverted; the audit infrastructure and external failed
experiment report remain.

## Reproducible Run Contract

Both runs consume
`data/evaluation/full_pipeline_heldout_v1.json`, converted to the same complete
490-row processor input. The input and held-out files are hashed. The baseline
and improved commands must be identical except for Git SHA and prompt
component versions.

The intended shared configuration is:

- provider: `openrouter`;
- model: `google/gemini-3-flash-preview`;
- temperature: `0`;
- top-p: `0.95`;
- top-k: `40`;
- maximum output tokens: `2000`;
- language: `cn`;
- KB root: `data/knowledge_base/structured`;
- production KB:
  `ALS2SDTM_Mapping_Template_v1.0.json` and its recorded SHA-256;
- RAG top-k: `3`;
- RAG embedding model: `Qwen3-Embed`;
- RAG minimum score: `0.4`;
- RAG character limit: `1500`;
- force RAG: disabled;
- parallel execution: enabled;
- maximum workers: `5`; and
- resume: disabled.

The actual endpoint host is recorded without credentials. No real generation
or embedding request may start until the operator explicitly authorizes the
estimated request count, chosen model, and cost risk. If a different model or
shared parameter is authorized, the same replacement value must be used for
both runs and recorded in both manifests.

Each run manifest records:

- schema version and run label;
- start/end timestamps and terminal status;
- Git SHA and dirty-state flag;
- held-out/input file paths, row counts, and SHA-256 hashes;
- production KB files and SHA-256 hashes;
- provider, model, sanitized endpoint host, and all generation parameters;
- RAG and concurrency parameters;
- template/rules/examples versions and file hashes;
- output artifact path and hash;
- processor output row count;
- source and cascade-level counts; and
- aggregate token usage when the client exposes it.

The manifest contains no API key, raw prompt/response, review metadata, or
additional clinical content.

## Production Provenance

`SDTMProcessor` remains the production implementation. It directly creates
`SDTMPromptGenerator` and calls `generate_variable_prompt` in the Level 4
path; tests must exercise that concrete path.

Every successfully processed recommendation receives its actual
`cascade_level` before being returned:

- `1`: direct production-KB match;
- `2`: high-confidence KB match;
- `3`: high-confidence RAG path;
- `4`: LLM fallback path, including an LLM-path parse/error fallback; and
- `0`: deterministic pre-cascade NOT SUBMITTED short-circuit.

The meanings of Levels 1–4 and their existing `source` labels do not change.
An output synthesized only by final coverage repair is not assigned a false
cascade level; it keeps source `UNMAPPED`, has `cascade_level: null`, and is
counted as an audit and deterministic-quality issue.

Production normalization remains defensive: legacy model or KB expressions
containing `when` may still be decomposed into structured fields. The new
prompt no longer instructs the model to emit those expressions.

## Prompt Contract

`src/prompts/sdtm_rules.py` loads `RULES`, `CORE_RULES`, `PATTERN_RULES`,
`DOMAIN_RULES`, and `DOMAIN_TYPES` exactly once from
`src/prompts/rules/sdtm_rules.yaml`. The later duplicate Python assignments are
removed.

The rendered prompt has one output contract:

- `sdtm_variable` is a plain variable token such as `FAORRES`, `QVAL`, or
  `AETERM`;
- findings conditions use `testcd`;
- supplementary QNAM uses `supp_variable`;
- supplementary dataset uses `supp_dataset`;
- supplementary values use `sdtm_variable: "QVAL"` and
  `sdtm_variable_type: "supp"`; and
- no rule, FA hint, or example embeds `when`, `TESTCD=`, or `QNAM=` in an
  `sdtm_variable` value.

FA examples retain the same clinical intent but are represented as structured
fields. A standard FA result is `FAORRES` plus `testcd`. An FA "other" field is
`QVAL`, `supp_dataset: "SUPPFA"`, `supp_variable: "FAOROTH"`, and the parent
`testcd`. Prompt YAML semantic versions are incremented for every changed
component.

Prompt CI is extended to reject:

- composite `sdtm_variable` example values;
- `when`, `QNAM=`, or `TESTCD=` embedded in example output values;
- missing `testcd` on examples categorized as TESTCD findings;
- incomplete SUPP examples; and
- rendered FA prompts whose guidance contradicts the structured contract.

## Evaluation Model

The evaluator continues to identify rows by the complete four-field key:
`annotation_table`, `metadata_table`, `annotation_variable`, and
`metadata_variable`. Deduplication must not hide missing rows. Headline rates
use the complete ground-truth denominator, and both runs must have 100%
coverage.

Every ground-truth row receives zero or more diagnostic scenario labels:

- `NON_STANDARD_DOMAIN`;
- `MULTI_DOMAIN`;
- `SUPP`;
- `TESTCD`; and
- `NOT_SUBMITTED`.

Labels are derived inside the evaluator only. No held-out output is copied to a
prompt, KB, RAG document, or production rule.

Metrics are reported by:

- evaluation cohort (`KB_AGREE`, `KB_DISAGREE`, `AI_RECOMMENDATION`);
- normalized ground-truth domain;
- actual `source`;
- actual `cascade_level`; and
- diagnostic scenario.

For each slice the report includes ground-truth size, evaluated count,
coverage, exact matches, exact rate, domain matches, and domain rate.
`KB_DISAGREE` is clearly marked diagnostic and is excluded from clean release
gates.

The evaluator also counts:

- deterministic validator error flags;
- illegal standard SDTM variables;
- illegal or incomplete SUPP/QNAM structures;
- parse fallbacks;
- `UNMAPPED` outputs;
- missing cascade provenance; and
- MappingCritic errors.

## Paired Comparison and Decision

The machine-readable JSON report includes both manifests, both complete metric
trees, row-level paired outcomes, improved/worsened/unchanged counts, paired
bootstrap results, every gate result, and the final `ACCEPT` or `ROLLBACK`
decision.

For the 309-row `AI_RECOMMENDATION` cohort, exact-match improvement is computed
per identical row key. A paired bootstrap resamples row pairs with replacement
using seed `20260726` and 10,000 replicates. The report records the observed
percentage-point delta and percentile 95% confidence interval.

The candidate is accepted only when all conditions hold:

1. both runs have 100% coverage;
2. `KB_AGREE` improved exact matches are not below baseline;
3. `AI_RECOMMENDATION` exact delta is positive and either at least 2.0
   percentage points or has a 95% CI lower bound greater than zero;
4. overall and `AI_RECOMMENDATION` Domain Match do not decline;
5. validator errors, illegal variables, illegal SUPP/QNAM structures, parse
   failures, `UNMAPPED`, missing provenance, and MappingCritic errors do not
   increase; and
6. Prompt CI, prompt snapshots, cascade, production normalizer,
   deterministic validator, MappingCritic, and evaluation tests pass.

Every failed condition is included in the report. A rejected candidate is
reverted and must not be described as an accuracy improvement.

## Artifacts and Data Safety

Generated benchmark input, processor JSON/XLSX, prompts, logs, run manifests,
and A/B reports are written under the task-owned artifact directory outside
the repository. They are not committed. Reports use evaluation IDs and
normalized mapping outcomes; they do not add source-workbook review details,
reviewer identities, API keys, prompts/responses, or raw PHI/PII.

The repository commits contain only source code, YAML prompt data, tests,
snapshots, and necessary documentation.

## Test Strategy

Implementation follows red-green-refactor. Focused tests prove:

- YAML category changes are reflected at runtime with no Python overwrite;
- FA rules, hints, examples, and rendered prompts use the structured contract;
- the production `SDTMProcessor` Level 1–4 and NOT SUBMITTED paths emit
  provenance;
- manifests pin all reproducibility inputs without secrets;
- every held-out row receives the correct cohort and scenario accounting;
- evaluator issue counts detect malformed production output;
- paired bootstrap is deterministic and paired by full key;
- `KB_DISAGREE` does not affect the clean release decision;
- every acceptance and rollback branch is machine testable; and
- prompt snapshots capture the intended rendered changes.

The acceptance test set is the Prompt CI plus the prompt snapshot, cascade,
normalizer, deterministic validator, MappingCritic, held-out integrity, and
evaluation unit tests named in the goal. `git diff --check`, Ruff on changed
Python files, and the focused mypy scope are also run.

At the initial SHA, Prompt CI passed and the focused target suite passed
159 tests. A broad `pytest -q` additionally produced 695 passes, three
pre-existing Windows/path or subprocess-decoding failures, and seven errors
from CLI-style functions in `tests/smoke_test.py` being collected as pytest
fixtures. These unrelated baseline issues are recorded but are not repaired
by this focused change.
