# PR Review Hardening Design

## Scope

This follow-up addresses verified review findings on PR #17 without changing
the locked four-level cascade or expanding the original SDTM accuracy scope.
It covers QNAM collision safety, KB authority preservation, structured RAG
context rendering, deterministic evaluation gates, tests, and PR evidence.

It does not copy held-out labels into production assets, alter the 74
`KB_DISAGREE` records, or add new remote processing.

## Verified Findings

The current PR head reproduces three behavioral defects:

1. Lossy QNAM normalization maps distinct non-ASCII names to `COMMENT` and can
   truncate distinct long ASCII names to the same eight-character token.
2. Comment-like heuristics can reclassify a Level-1/2 KB mapping as SUPP and
   replace an authoritative composite KB expression with `QVAL`.
3. A retrieved RAG example can render `VARIABLE when CONDITION` even though
   the production prompt requires `sdtm_variable` to contain only a plain
   variable name.

The evaluation also lacks a machine-readable duplicate-QNAM counter.

The review request to set failed Level-4 results to a null cascade level is not
accepted. The existing design defines an LLM-path parse/error fallback as
Level 4, while only final coverage repair uses a null level. This semantic is
already documented and tested.

## Design

### Collision-resistant QNAM normalization

Keep an already-valid structured QNAM unchanged. When deriving a QNAM would
discard non-ASCII content or truncate a token longer than eight characters,
combine a readable ASCII prefix when available with a stable digest of the
full raw value. The result must:

- match `^[A-Z][A-Z0-9]{0,7}$`;
- avoid SUPPQUAL variables and standard variables for the parent domain;
- be stable for the same input; and
- distinguish the verified non-ASCII and long-name collision cases.

If no candidate is usable, derive a stable digest token from the raw CRF
variable rather than using a shared `COMMENT` fallback.

### Duplicate-QNAM detection

Add a batch MappingCritic error when distinct raw variables use the same
`(supp_dataset, supp_variable)` key. Repeated occurrences of the same raw
variable are not collisions.

Add `duplicate_supp_qnam` to the evaluation quality counters. Count additional
distinct raw-variable assignments beyond the first within each duplicate key,
and apply the same no-increase acceptance rule used by the other deterministic
quality counters.

### KB authority

Comment-like heuristics must not reclassify KB results. The SUPP output branch
must restore the original KB domain and SDTM variable snapshot, while retaining
the intended decomposition of simple structured KB expressions such as
`QVAL when QNAM=...`.

### Structured RAG rendering

Expose a small public helper that splits a legacy `when` expression into its
plain variable and condition clauses. RAG context rendering uses the helper to
show the target as a plain `DOMAIN.VARIABLE`, followed by separate `testcd`,
`supp_variable`, or generic condition annotations. It must never present the
legacy compound expression as the target variable.

### Audit documentation

Clarify that `_audit_mapping_result` both stamps cascade provenance and writes
the optional audit record. Do not rename or split the method in this follow-up.

## Testing

Tests are written and observed failing before production changes:

- non-ASCII QNAM values are legal, stable, and distinct;
- long ASCII names with a shared prefix do not collide;
- duplicate QNAM assignments are detected by MappingCritic and the evaluator;
- repeated occurrences of the same raw variable are not false positives;
- KB composite expressions survive comment-like metadata unchanged;
- RAG output contains a plain target and separate condition fields;
- existing Level-4 fallback provenance remains `4`.

After focused tests pass, run the required prompt, cascade, normalizer,
deterministic validator, MappingCritic, evaluator, Ruff, format, mypy, and
repository CI commands.

## Experiment and PR Update

Because structured RAG rendering changes the real Level-4 prompt, rerun the
Improved arm with the frozen model, input, KB, RAG, concurrency, and generation
configuration. Compare it with the frozen baseline using the updated quality
counter and existing paired-bootstrap gates.

If any acceptance gate fails, revert the failing behavioral candidate and keep
the failed report outside the repository. If all gates pass, commit and push
the focused changes to the existing PR branch, update the PR description with
the new tested SHA and evidence, reply in each inline thread, and resolve only
the threads whose findings are addressed or technically rejected.
