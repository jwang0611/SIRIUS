# No-leak held-out evaluation and regression gate

The release gate is an offline replay over externally supplied, authorized,
metadata-only ground truth. It does not call OpenRouter or any other model.
Real-model generation is a separate, non-default operation through
`scripts/run_sdtm_experiment.py --execute` and requires explicit approval.

## Data contract

A release manifest uses schema `sirius-heldout-manifest/v1`, sets
`distinct_studies_confirmed: true`, and references at least two files from
different, opaque studies. Do not commit those files or generated reports.
Each dataset entry must record:

- an opaque ID in the form `study-NNN` (never a study, sponsor, project, site,
  or protocol name);
- an approved `source_class`: `metadata_only_als`, `metadata_only_edc`, or
  `metadata_only_crf`;
- `deidentified: true` and `authorized_for_engineering: true` after the
  maintainer has verified both facts;
- the source schema version, normalized SHA-256, and row count;
- the colocated opaque file name (`study-NNN.json`) and a JSON list containing
  only opaque `EVAL-NNNN` IDs, the four input identity fields, `SDTM_Domain`,
  and `SDTM_Variable`.

Example shape (placeholder hashes are intentionally invalid):

```json
{
  "schema_version": "sirius-heldout-manifest/v1",
  "evaluation_profile": "release",
  "distinct_studies_confirmed": true,
  "datasets": [
    {
      "dataset_id": "study-001",
      "source_class": "metadata_only_als",
      "deidentified": true,
      "authorized_for_engineering": true,
      "schema_version": "als-metadata/v1",
      "file": "study-001.json",
      "sha256": "<reviewed normalized SHA-256>",
      "row_count": 100
    },
    {
      "dataset_id": "study-002",
      "source_class": "metadata_only_edc",
      "deidentified": true,
      "authorized_for_engineering": true,
      "schema_version": "als-metadata/v1",
      "file": "study-002.json",
      "sha256": "<reviewed normalized SHA-256>",
      "row_count": 100
    }
  ]
}
```

The validator rejects a single study, identifying manifest fields, unexpected
row fields, hash/count drift, duplicate input identities, exact overlap with
the supplied production/project KB roots, and matches to built-in semantic
table names or keywords. Leakage diagnostics contain input hashes, never raw
metadata values.

`data/evaluation/full_pipeline_heldout_v1.json` is intentionally retained only
as a characterization benchmark. Its manifest marks it ineligible because it
has one source workbook and 181 production-KB overlaps.

## Reviewed baseline

The baseline is a maintainer-reviewed JSON object using schema
`sirius-eval-baseline/v1`. It must bind the exact release manifest SHA-256 and
record the accepted metrics, per-metric maximum regression, quality counters,
FALLBACK / `*_PENDING` counts, and their permitted increases. The tool never
guesses, derives, or silently updates a baseline.

Required rate paths are `coverage`, `exact_rate`, `domain_rate`,
`supp.precision`, `supp.recall`, and `supp.f1`. Required count sections are
`quality_issues` and `outcome_counts`. Use zero tolerances unless a maintainer
reviews and documents a different policy.

## Offline replay

First validate the release inputs against all knowledge sources and create the
processor input. This must pass before authorizing a real-model run:

```bash
python scripts/run_offline_eval_gate.py \
  --dataset-manifest /external/eval/manifest.json \
  --validate-only \
  --benchmark-output /external/eval/benchmark.json \
  --project-knowledge-root /external/eval/project-kb \
  --report-json /external/eval/preflight.json \
  --report-markdown /external/eval/preflight.md
```

After generation, place the previously generated processor JSON and reviewed
baseline in the same external artifact directory. Then run the deterministic
replay:

```bash
python scripts/run_offline_eval_gate.py \
  --dataset-manifest /external/eval/manifest.json \
  --ai-output /external/eval/replay.json \
  --baseline /external/eval/baseline.json \
  --project-knowledge-root /external/eval/project-kb \
  --report-json /external/eval/report.json \
  --report-markdown /external/eval/report.md \
  --require-gate
```

Exit code `0` means the dataset, leakage check, and reviewed baseline all
passed. Exit code `1` means a data/leakage/regression gate failed. Exit code
`2` means an input could not be parsed or scored. JSON and Markdown report the
domain and exact-variable rates, SUPP precision/recall/F1, coverage,
FALLBACK / `*_PENDING`, MappingCritic errors, and source/domain strata.
The production structured KB and prompt examples are always checked;
`--project-knowledge-root` is mandatory and repeatable so a session/project KB
cannot be omitted accidentally.

The normal GitHub CI suite runs deterministic contract tests, including an
injected-overlap failure and a lowered-metric failure, without an external LLM.
Activating the actual release regression gate remains blocked until a
maintainer supplies the two authorized datasets and approves the first
baseline; that external evidence must not be fabricated from the existing KB.
