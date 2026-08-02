# Evaluation datasets

`full_pipeline_heldout_v1.json` is a curated, metadata-only characterization
benchmark for the complete SIRIUS cascade. It is **not eligible for a release
gate**: it comes from one source workbook and deliberately includes production
KB overlaps. It contains three cohorts:

- `KB_AGREE` (107 rows): the four input fields and normalized SDTM mapping
  agree with the production KB. This is the clean deterministic KB-path
  regression cohort.
- `KB_DISAGREE` (74 rows): the four input fields occur in the production KB,
  but its normalized SDTM mapping disagrees with manual ground truth. These
  rows are diagnostic/adjudication candidates, not a clean KB regression gate.
- `AI_RECOMMENDATION`: the four input fields do not occur in the production KB,
  exercising the remaining cascade and AI recommendation paths.

Across all 181 production-KB overlaps, replaying the current KB mapping can
match at most 107 rows (59.1%) against the manual ground truth. Report
`KB_AGREE` and `KB_DISAGREE` separately; do not interpret the combined overlap
rate as a pipeline regression until the 74 disagreements are adjudicated.

This is an end-to-end operational benchmark, not a prompt-only generalization
benchmark. Report results by both actual cascade `Source` and
`evaluation_cohort`; do not describe the aggregate score as LLM-only accuracy.

The JSON contains only input metadata, manually reviewed SDTM ground truth, and
cohort labels. The source workbook, AI suggestions, scores, reviewer names,
comments, assignments, and timelines are intentionally excluded. The manifest
records the source hash, production-KB hash, curation counts, exclusions, and
normalizations needed to reproduce an audit.

## Reproducible full-pipeline A/B

Generate the complete 490-row processor input. Keep the generated benchmark
and all run artifacts outside the repository:

```powershell
$python = "C:\path\to\python.exe"
$codeRoot = (Resolve-Path ".").Path
$artifactRoot = "C:\path\to\external\sirius-ab-artifacts"

& $python scripts/eval_prompt_accuracy.py `
  --ground-truth data/evaluation/full_pipeline_heldout_v1.json `
  --gen-benchmark `
  --benchmark-output "$artifactRoot\benchmark-input.json"
```

Before authorizing external calls, run the pinned wrapper without `--execute`.
It validates the complete held-out input and prints a conservative request
estimate, but does not start the production generator or call a model:

```powershell
& $python scripts/run_sdtm_experiment.py `
  --run-label baseline `
  --code-root $codeRoot `
  --input "$artifactRoot\benchmark-input.json" `
  --heldout "$codeRoot\data\evaluation\full_pipeline_heldout_v1.json" `
  --kb-root "$codeRoot\data\knowledge_base" `
  --output-base "$artifactRoot\baseline" `
  --manifest "$artifactRoot\baseline.manifest.json"
```

The default experiment is pinned to
`google/gemini-3-flash-preview`, `temperature=0`, RAG enabled with top-k 3,
`openai/text-embedding-3-small`, and five workers. This fully qualified model
slug is required by the public OpenRouter embeddings API and passed a
100-item throughput preflight; the legacy `Qwen3-Embed` internal-gateway alias
is not used. `--kb-root` identifies the complete hashed KB tree; unless
overridden, the runner passes its `structured/` Parquet directory to the
production RAG chunker. The current held-out/KB combination has a conservative
per-run ceiling of 309 generation requests, five 100-item query-embedding
requests for all 490 rows, and 27 cold-cache KB-embedding requests covering
2,616 chunks in batches of at most 100. Both arms must use separate clean
worktrees, and execution is
rejected when `data/cache/rag_vectors/` already contains a vector cache. The
manifest records this `cold_isolated_worktree` policy. It also captures the
experiment-runner script hash, target
Git SHA and dirty flag, normalized input hashes, every JSON/Parquet KB hash,
prompt component versions and hashes, model/generation parameters, endpoint
origin, RAG settings, cascade thresholds, concurrency, output hashes, source
counts, and cascade-level counts. It never records credentials or endpoint
query/path details.

Only after the estimated call count, model, endpoint/provider, and price risk
have been reported and explicitly authorized should the same command be run
with `--execute`. Run the baseline from its clean detached baseline worktree
and the improved candidate from its clean candidate worktree. Apart from
`--run-label`, `--code-root`, `--output-base`, and `--manifest`, every
experiment setting must be identical:

```powershell
& $python scripts/run_sdtm_experiment.py `
  --run-label baseline `
  --code-root $baselineCodeRoot `
  --input "$artifactRoot\benchmark-input.json" `
  --heldout "$baselineCodeRoot\data\evaluation\full_pipeline_heldout_v1.json" `
  --kb-root "$baselineCodeRoot\data\knowledge_base" `
  --output-base "$artifactRoot\baseline" `
  --manifest "$artifactRoot\baseline.manifest.json" `
  --execute

& $python scripts/run_sdtm_experiment.py `
  --run-label improved `
  --code-root $improvedCodeRoot `
  --input "$artifactRoot\benchmark-input.json" `
  --heldout "$improvedCodeRoot\data\evaluation\full_pipeline_heldout_v1.json" `
  --kb-root "$improvedCodeRoot\data\knowledge_base" `
  --output-base "$artifactRoot\improved" `
  --manifest "$artifactRoot\improved.manifest.json" `
  --execute
```

Record the required offline commands and their real exit codes in
`required-tests.json`, with `all_passed` true only when every required group
passes. Then create and gate the machine-readable report:

```powershell
& $python scripts/eval_prompt_accuracy.py `
  --ground-truth data/evaluation/full_pipeline_heldout_v1.json `
  --baseline "$artifactRoot\baseline_google_gemini-3-flash-prev.json" `
  --improved "$artifactRoot\improved_google_gemini-3-flash-prev.json" `
  --baseline-manifest "$artifactRoot\baseline.manifest.json" `
  --improved-manifest "$artifactRoot\improved.manifest.json" `
  --required-tests-json "$artifactRoot\required-tests.json" `
  --report-json "$artifactRoot\ab-report.json" `
  --bootstrap-seed 20260726 `
  --bootstrap-replicates 10000 `
  --require-full-coverage `
  --require-acceptance
```

The A/B report retains the complete baseline and improved metrics, including
cohort, domain, source, cascade-level, special-scenario, and deterministic
quality slices. It performs a paired bootstrap on `AI_RECOMMENDATION` rows
aligned by `evaluation_id`. Before scoring acceptance, it also requires both
manifests to have `status="succeeded"`, clean Git SHAs, all prompt component
versions/hashes, pinned generation/RAG/cascade/concurrency settings, complete
KB hashes, and held-out/output hashes that match the exact files passed to the
evaluator. Exit codes are:

- `0`: the requested report was written and all requested gates passed;
- `1`: full coverage or an acceptance gate failed;
- `2`: a manifest/report input is malformed or the shared run configuration
  differs.

Acceptance requires 100% coverage, no `KB_AGREE` Exact Match regression, a
positive `AI_RECOMMENDATION` Exact Match delta with either at least +2
percentage points or a paired 95% CI lower bound above zero, no overall or AI
Domain Match regression, no increase in any deterministic/parse/MappingCritic
quality counter, identical shared run settings, and passing required tests.
The 74 `KB_DISAGREE` rows remain a diagnostic-only adjudication cohort: they
are reported separately and never form a clean release gate before expert
review.

For a single diagnostic output:

```powershell
& $python scripts/eval_prompt_accuracy.py `
  --ground-truth data/evaluation/full_pipeline_heldout_v1.json `
  --ai-output "$artifactRoot\result.json" `
  --require-full-coverage
```

The report includes GT size, evaluated rows, missing/extra outputs, and
coverage. Headline exact/domain rates use the complete GT denominator, so
missing outputs count as failures rather than disappearing from the score.
Without `--require-full-coverage`, incomplete output emits a warning; with the
flag, it exits non-zero for CI or other gating contexts.

The ground-truth path is intentionally explicit. Never copy this dataset into
the production KB or prompt examples without first retiring it as held-out and
creating a newly versioned benchmark.

The strict two-study, no-leak release procedure is documented in
`docs/evaluation-release-gate.md`. Do not relabel this characterization dataset
or use its results to create a release baseline.
