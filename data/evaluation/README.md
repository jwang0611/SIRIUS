# Evaluation datasets

`full_pipeline_heldout_v1.json` is a curated, metadata-only benchmark for the
complete SIRIUS cascade. It deliberately contains both:

- `KB_OVERLAP`: the four input fields occur in the production KB, exercising
  deterministic KB paths.
- `AI_RECOMMENDATION`: the four input fields do not occur in the production KB,
  exercising the remaining cascade and AI recommendation paths.

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
  --ai-output data/output/result.json
```

The ground-truth path is intentionally explicit. Never copy this dataset into
the production KB or prompt examples without first retiring it as held-out and
creating a newly versioned benchmark.
