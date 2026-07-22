# Evaluation datasets

`full_pipeline_heldout_v1.json` is a curated, metadata-only benchmark for the
complete SIRIUS cascade. It deliberately contains both:

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

Generate the complete processor input:

```bash
python scripts/eval_prompt_accuracy.py \
  --ground-truth data/evaluation/full_pipeline_heldout_v1.json \
  --gen-benchmark
```

Evaluate an output:

```bash
python scripts/eval_prompt_accuracy.py \
  --ground-truth data/evaluation/full_pipeline_heldout_v1.json \
  --ai-output data/output/result.json \
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
