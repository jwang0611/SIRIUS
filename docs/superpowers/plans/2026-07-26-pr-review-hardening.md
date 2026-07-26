# PR Review Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the verified PR #17 review findings while preserving KB authority, the structured prompt contract, cascade semantics, and the accepted A/B gates.

**Architecture:** Keep single-record normalization deterministic and side-effect free, then detect cross-record QNAM conflicts in MappingCritic and the evaluator. Reuse a public legacy-expression splitter so RAG examples and production prompt instructions share one representation. Validate each behavior test-first, then rerun the frozen Improved arm because the RAG prompt changes.

**Tech Stack:** Python 3.11, pytest, Ruff, mypy, YAML prompt CI, OpenRouter, paired-bootstrap A/B evaluator.

---

## File Structure

- `src/processors/normalizer.py`: stable QNAM derivation and public legacy `when` splitter.
- `src/processors/postprocess.py`: production-path KB snapshot protection.
- `src/processors/mapping_critic.py`: batch duplicate-QNAM error.
- `src/rag/prompt_augmenter.py`: structured rendering of retrieved conditions.
- `src/evaluation/ab_analysis.py`: machine-readable duplicate-QNAM counter and gate.
- `src/processors/sdtm_processor.py`: audit-method documentation only.
- `tests/unit/test_normalizer.py`: QNAM and expression-splitter contracts.
- `tests/unit/test_postprocess_mixin.py`: KB authority regression.
- `tests/unit/test_mapping_critic.py`: duplicate-QNAM batch behavior.
- `tests/unit/test_rag_prompt_augmenter.py`: RAG output contract.
- `tests/unit/test_ab_analysis.py`: counter and acceptance behavior.
- `docs/superpowers/specs/2026-07-26-pr-review-hardening-design.md`: approved design.

### Task 1: Collision-resistant QNAM derivation

**Files:**

- Modify: `tests/unit/test_normalizer.py`
- Modify: `src/processors/normalizer.py`

- [x] **Step 1: Write failing normalization tests**

Add `normalize_supp_record` to the imports and add:

```python
def test_non_ascii_supp_qnams_are_legal_stable_and_distinct():
    first = normalize_supp_record({"domain": "CM"}, variable_name="备注")
    repeated = normalize_supp_record({"domain": "CM"}, variable_name="备注")
    second = normalize_supp_record({"domain": "CM"}, variable_name="其他说明")

    assert first["supp_variable"] == repeated["supp_variable"]
    assert first["supp_variable"] != second["supp_variable"]
    assert re.fullmatch(r"[A-Z][A-Z0-9]{0,7}", first["supp_variable"])
    assert re.fullmatch(r"[A-Z][A-Z0-9]{0,7}", second["supp_variable"])


def test_long_supp_qnams_with_shared_prefix_do_not_collide():
    first = normalize_supp_record(
        {"domain": "AE", "auto_corrected_to_supp": True},
        variable_name="ADVERSEEVENTDETAIL1",
    )
    second = normalize_supp_record(
        {"domain": "AE", "auto_corrected_to_supp": True},
        variable_name="ADVERSEEVENTDETAIL2",
    )

    assert first["supp_variable"] != second["supp_variable"]
    assert len(first["supp_variable"]) <= 8
    assert len(second["supp_variable"]) <= 8
```

Also import `re`.

- [x] **Step 2: Run the tests and verify RED**

Run:

```powershell
python -m pytest `
  tests/unit/test_normalizer.py::test_non_ascii_supp_qnams_are_legal_stable_and_distinct `
  tests/unit/test_normalizer.py::test_long_supp_qnams_with_shared_prefix_do_not_collide -q
```

Expected: both tests fail because the current implementation returns shared
`COMMENT` and shared eight-character prefixes.

- [x] **Step 3: Implement stable lossy-token handling**

Add `import hashlib` and:

```python
def _stable_qnam_token(raw: object) -> str:
    text = str(raw or "").strip().upper()
    if not text:
        return ""

    compact = _NON_QNAM_CHARS_RE.sub("", text)
    if compact and not compact[0].isalpha():
        compact = f"Q{compact}"

    loses_non_ascii = any(ord(char) > 127 and char.isalnum() for char in text)
    if compact and len(compact) <= 8 and not loses_non_ascii:
        return compact

    prefix = compact[:4] if compact and compact[0].isalpha() else "Q"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest().upper()
    return f"{prefix}{digest[: 8 - len(prefix)]}"
```

Replace the nested truncation implementation with:

```python
def candidate_token(raw: object) -> str:
    return _stable_qnam_token(raw)
```

Use `candidate_token(variable_name) or "COMMENT"` for the final fallback.

- [x] **Step 4: Run focused normalization tests**

Run:

```powershell
python -m pytest tests/unit/test_normalizer.py tests/unit/test_postprocess_mixin.py -q
```

Expected: PASS, except the old `AEXCUSTO` assertion may fail because lossy
truncation is no longer the contract. Replace it with validity, stability, and
`!= "AEXCUSTO"` assertions, then rerun to PASS.

- [x] **Step 5: Commit**

```powershell
git add src/processors/normalizer.py tests/unit/test_normalizer.py tests/unit/test_postprocess_mixin.py
git commit -m "fix: prevent lossy SUPP QNAM collisions"
```

### Task 2: Preserve authoritative KB mappings

**Files:**

- Modify: `tests/unit/test_postprocess_mixin.py`
- Modify: `src/processors/postprocess.py`

- [x] **Step 1: Write the failing KB regression test**

```python
def test_comment_like_kb_composite_mapping_remains_authoritative():
    host = _Host()
    recs = [
        {
            "domain": "EC|EX",
            "sdtm_variable": "ECSTDTC when ECMOOD=X|EXSTDTC",
            "score": 0.99,
            "source": "KB",
            "kb_validated": True,
        }
    ]

    out = host._normalize_domain_recs(
        table_name="t",
        variable_name="备注",
        domain_recs=recs,
        target_domain=None,
        enforce_domain=False,
    )

    assert out[0]["domain"] == "EC|EX"
    assert out[0]["sdtm_variable"] == "ECSTDTC when ECMOOD=X|EXSTDTC"
    assert out[0]["sdtm_variable_type"] == "standard"
```

- [x] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m pytest tests/unit/test_postprocess_mixin.py::TestNormalizeDomainRecsBasic::test_comment_like_kb_composite_mapping_remains_authoritative -q
```

Expected: FAIL with `sdtm_variable == "QVAL"`.

- [x] **Step 3: Guard KB classification and restore snapshots**

Change the comment heuristic to:

```python
if not is_from_kb and is_comment_like and var_type != "supp":
```

In the SUPP cleaned record, use:

```python
"domain": original_kb_domain if original_kb_domain else rec.get("domain", ""),
"sdtm_variable": (
    original_kb_sdtm_var
    if original_kb_sdtm_var
    else rec.get("sdtm_variable", "")
),
```

Keep the existing update of `original_kb_sdtm_var` after simple `when`
decomposition so structured KB SUPP expressions still become `QVAL` plus
`supp_variable`.

- [x] **Step 4: Run postprocess and KB characterization tests**

Run:

```powershell
python -m pytest `
  tests/unit/test_postprocess_mixin.py `
  tests/characterization/test_normalizer_pipeline.py `
  tests/characterization/test_cascade_shortcircuit.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```powershell
git add src/processors/postprocess.py tests/unit/test_postprocess_mixin.py
git commit -m "fix: preserve KB mappings during SUPP normalization"
```

### Task 3: Align RAG examples with the structured prompt contract

**Files:**

- Modify: `tests/unit/test_normalizer.py`
- Modify: `tests/unit/test_rag_prompt_augmenter.py`
- Modify: `src/processors/normalizer.py`
- Modify: `src/rag/prompt_augmenter.py`

- [x] **Step 1: Write failing splitter and renderer tests**

Import `split_when_expression` in `test_normalizer.py` and add:

```python
def test_split_when_expression_preserves_all_condition_clauses():
    variable, clauses = split_when_expression(
        "QVAL when QNAM=FAOROTH when FATESTCD=THCLA"
    )

    assert variable == "QVAL"
    assert clauses == ["QNAM=FAOROTH", "FATESTCD=THCLA"]
```

Change the RAG test assertions to:

```python
assert "**XX.XXVAR**" in rendered
assert "condition=XXFLAG=Y" in rendered
assert "XXVAR when XXFLAG=Y" not in rendered
assert "Synthetic Table/RAWVAR" in rendered
```

- [x] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/unit/test_normalizer.py tests/unit/test_rag_prompt_augmenter.py -q
```

Expected: collection fails because `split_when_expression` does not exist, or
the renderer assertion fails before implementation.

- [x] **Step 3: Add and reuse the public splitter**

Add:

```python
def split_when_expression(value: object) -> tuple[str, list[str]]:
    text = str(value or "").strip()
    if not text:
        return "", []
    parts = _WHEN_CLAUSE_RE.split(text)
    return parts[0].strip(), [part.strip() for part in parts[1:] if part.strip()]
```

Refactor `decompose_when_clause` to call the helper, and export it through
`__all__`.

In `prompt_augmenter.py`, import the helper. Before building a mapping line:

```python
plain_var, conditions = split_when_expression(sdtm_var)
qualifiers: list[str] = []
for condition in conditions:
    key, separator, value = condition.partition("=")
    key_upper = key.strip().upper()
    value = value.strip()
    if separator and key_upper.endswith("TESTCD"):
        qualifiers.append(f"testcd={value}")
    elif separator and key_upper == "QNAM":
        qualifiers.append(f"supp_variable={value}")
    else:
        qualifiers.append(f"condition={condition}")
qualifier_text = f"; {', '.join(qualifiers)}" if qualifiers else ""
mapping_line = (
    f"{i}. [Score: {ctx.effective_score:.2f}] "
    f"{source_table}/{source_var} → **{sdtm_domain}.{plain_var}**"
    f"{qualifier_text} ({source_type})"
)
```

- [x] **Step 4: Run RAG, prompt semantics, and snapshots**

Run:

```powershell
python -m pytest `
  tests/unit/test_normalizer.py `
  tests/unit/test_rag_prompt_augmenter.py `
  tests/unit/test_sdtm_processor_provenance.py `
  tests/unit/test_prompt_semantics.py `
  tests/unit/test_prompt_snapshot.py -q
```

Expected: PASS with six prompt snapshots.

- [x] **Step 5: Commit**

```powershell
git add src/processors/normalizer.py src/rag/prompt_augmenter.py tests/unit/test_normalizer.py tests/unit/test_rag_prompt_augmenter.py
git commit -m "fix: render RAG mapping conditions structurally"
```

### Task 4: Detect and gate duplicate SUPP QNAM assignments

**Files:**

- Modify: `tests/unit/test_mapping_critic.py`
- Modify: `tests/unit/test_ab_analysis.py`
- Modify: `src/processors/mapping_critic.py`
- Modify: `src/evaluation/ab_analysis.py`

- [x] **Step 1: Write failing MappingCritic tests**

```python
class TestDuplicateSuppQnam:
    def test_distinct_raw_variables_with_same_supp_key_are_errors(self, critic):
        recs = [
            {
                "variable_name": "COMMENT_A",
                "sdtm_variable_type": "supp",
                "sdtm_variable": "QVAL",
                "supp_dataset": "SUPPCM",
                "supp_variable": "COMMENT",
            },
            {
                "variable_name": "COMMENT_B",
                "sdtm_variable_type": "supp",
                "sdtm_variable": "QVAL",
                "supp_dataset": "SUPPCM",
                "supp_variable": "COMMENT",
            },
        ]

        issues = critic._check_duplicate_supp_qnam(recs)

        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert issues[0].check_name == "duplicate_supp_qnam"

    def test_repeated_same_raw_variable_is_not_a_collision(self, critic):
        rec = {
            "variable_name": "COMMENT_A",
            "sdtm_variable_type": "supp",
            "sdtm_variable": "QVAL",
            "supp_dataset": "SUPPCM",
            "supp_variable": "COMMENT",
        }

        assert critic._check_duplicate_supp_qnam([rec, dict(rec)]) == []
```

- [x] **Step 2: Write failing evaluator counter and gate tests**

Add two legal SUPP rows with distinct `metadata_variable` values and the same
`SUPPCM.COMMENT`, then assert:

```python
counts = count_quality_issues(rows, consistency_issues=[])
assert counts["duplicate_supp_qnam"] == 1
```

Add `"duplicate_supp_qnam": 0` to expected quality dictionaries and add it to
the parameter list in `test_acceptance_rejects_any_quality_counter_increase`.

- [x] **Step 3: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/unit/test_mapping_critic.py tests/unit/test_ab_analysis.py -q
```

Expected: missing-method failure and missing counter assertion failure.

- [x] **Step 4: Implement the batch critic**

Add:

```python
def _check_duplicate_supp_qnam(
    self,
    recommendations: list[dict[str, Any]],
) -> list[ConsistencyIssue]:
    assignments: dict[tuple[str, str], set[str]] = defaultdict(set)
    display_names: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)

    for rec in recommendations:
        if str(rec.get("sdtm_variable_type", "")).lower() != "supp":
            continue
        dataset = str(rec.get("supp_dataset", "")).strip().upper()
        qnam = str(rec.get("supp_variable", "")).strip().upper()
        variable_name = str(rec.get("variable_name", "")).strip()
        if not dataset or not qnam or not variable_name:
            continue
        key = (dataset, qnam)
        normalized_name = variable_name.casefold()
        assignments[key].add(normalized_name)
        display_names[key].setdefault(normalized_name, variable_name)

    collisions = {
        key: raw_variables
        for key, raw_variables in assignments.items()
        if len(raw_variables) > 1
    }
    if not collisions:
        return []

    duplicate_assignments = sum(
        len(raw_variables) - 1 for raw_variables in collisions.values()
    )
    affected_variables = sorted(
        {
            display_names[key][raw_variable]
            for key, raw_variables in collisions.items()
            for raw_variable in raw_variables
        }
    )
    examples = ", ".join(
        f"{dataset}.{qnam}" for dataset, qnam in sorted(collisions)[:5]
    )
    return [
        ConsistencyIssue(
            severity="error",
            check_name="duplicate_supp_qnam",
            description=(
                f"{duplicate_assignments} additional raw variable assignment(s) "
                f"reuse a SUPP QNAM key: {examples}"
            ),
            affected_variables=affected_variables[:20],
            suggested_fix=(
                "Assign a unique QNAM to each distinct raw variable within a SUPP dataset."
            ),
        )
    ]
```

Call `self._check_duplicate_supp_qnam(recommendations)` from `criticize`
immediately after `_check_supp_naming`.

- [x] **Step 5: Implement the evaluator counter**

Add `"duplicate_supp_qnam"` to `QUALITY_COUNTER_NAMES`, then add:

```python
def _count_duplicate_supp_qnam(rows: list[dict[str, Any]]) -> int:
    assignments: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        variable_type = str(row.get("sdtm_variable_type", "")).lower()
        if variable_type != "supp" and _raw_variable(row) != "QVAL":
            continue
        dataset = str(row.get("supp_dataset", "")).strip().upper()
        qnam = str(row.get("supp_variable", "")).strip().upper()
        variable_name = str(
            row.get("metadata_variable") or row.get("variable_name") or ""
        ).strip()
        if dataset and qnam and variable_name:
            assignments[(dataset, qnam)].add(variable_name.casefold())
    return sum(
        len(raw_variables) - 1
        for raw_variables in assignments.values()
        if len(raw_variables) > 1
    )
```

Import `defaultdict` alongside `Counter`, then assign:

```python
counts["duplicate_supp_qnam"] = _count_duplicate_supp_qnam(rows)
```

before returning the ordered counter dictionary.

- [x] **Step 6: Run critic and evaluator tests**

Run:

```powershell
python -m pytest `
  tests/unit/test_mapping_critic.py `
  tests/unit/test_ab_analysis.py `
  tests/unit/test_eval_prompt_accuracy.py -q
```

Expected: PASS.

- [x] **Step 7: Commit**

```powershell
git add src/processors/mapping_critic.py src/evaluation/ab_analysis.py tests/unit/test_mapping_critic.py tests/unit/test_ab_analysis.py
git commit -m "feat: gate duplicate SUPP QNAM assignments"
```

### Task 5: Clarify provenance documentation and run offline gates

**Files:**

- Modify: `src/processors/sdtm_processor.py`
- Modify: `docs/superpowers/plans/2026-07-26-pr-review-hardening.md`

- [x] **Step 1: Clarify the audit method contract**

Change the docstring to:

```python
"""Stamp cascade provenance and write the audit record when enabled."""
```

Do not change Level-4 fallback behavior.

- [x] **Step 2: Run the required focused gate**

Run:

```powershell
python -m pytest `
  tests/unit/test_prompt_snapshot.py `
  tests/unit/test_prompt_semantics.py `
  tests/unit/test_cascade.py `
  tests/characterization/test_cascade_shortcircuit.py `
  tests/unit/test_normalizer.py `
  tests/unit/test_recommendation_normalizer.py `
  tests/unit/test_postprocess_mixin.py `
  tests/characterization/test_normalizer_pipeline.py `
  tests/unit/test_deterministic_validator.py `
  tests/unit/test_mapping_critic.py `
  tests/unit/test_eval_prompt_accuracy.py `
  tests/unit/test_full_pipeline_heldout.py `
  tests/unit/test_ab_analysis.py `
  tests/unit/test_run_manifest.py `
  tests/unit/test_sdtm_processor_provenance.py `
  tests/unit/test_rag_embeddings.py `
  tests/unit/test_rag_prompt_augmenter.py `
  tests/unit/test_external_call_retries.py `
  tests/unit/test_recommendation_orchestrator.py -q
```

Expected: all tests and six snapshots pass.

- [x] **Step 3: Run static and prompt gates**

```powershell
python scripts/prompt_ci/validate_prompts.py
python -m ruff check .
python -m ruff format --check .
python -m mypy src
git diff --check
```

Expected: every command exits 0.

- [x] **Step 4: Commit**

```powershell
git add src/processors/sdtm_processor.py docs/superpowers/plans/2026-07-26-pr-review-hardening.md
git commit -m "docs: clarify cascade provenance handling"
```

### Task 6: Run the frozen Improved arm and update PR #17

**Files:**

- External only: `artifacts/improved-v7-*`, `artifacts/ab-report.v7.json`,
  `artifacts/required-tests-v7.json`
- Update: PR #17 body and four inline review threads

- [x] **Step 1: Ensure the candidate revision is clean**

```powershell
git status --short
git rev-parse HEAD
```

Expected: clean status and a fixed candidate SHA.

- [x] **Step 2: Execute the frozen Improved arm**

Run:

```powershell
$reviewArtifacts = 'C:\Users\chenkai.lv\.codex\visualizations\2026\07\26\019f9d09-4a04-7070-8e33-bb11c86c0c53\artifacts'
$reviewWorktree = 'C:\Users\chenkai.lv\.codex\visualizations\2026\07\26\019f9d09-4a04-7070-8e33-bb11c86c0c53\worktrees\sirius-sdtm-eval'

python scripts/run_sdtm_experiment.py `
  --run-label improved-v7 `
  --code-root $reviewWorktree `
  --input "$reviewArtifacts\benchmark-input.json" `
  --heldout "$reviewWorktree\data\evaluation\full_pipeline_heldout_v1.json" `
  --kb-root "$reviewWorktree\data\knowledge_base" `
  --rag-kb-path "$reviewWorktree\data\knowledge_base\structured" `
  --output-base "$reviewArtifacts\improved-v7-output" `
  --manifest "$reviewArtifacts\improved-v7.manifest.json" `
  --execute `
  --provider openrouter `
  --model google/gemini-3-flash-preview `
  --temperature 0 `
  --top-p 0.95 `
  --top-k 40 `
  --max-output-tokens 2000 `
  --language cn `
  --rate-limit 120 `
  --rag-top-k 3 `
  --rag-embedding-model openai/text-embedding-3-small `
  --rag-min-score 0.4 `
  --rag-char-limit 1500 `
  --parallel `
  --max-workers 5 `
  --kb-min-confidence 0.8 `
  --cascade-kb-high-conf 0.85 `
  --cascade-rag-high-conf 0.7 `
  --domain-override-confidence 0.85
```

Write all outputs and the manifest outside the repository under the artifacts
directory. Expected generation-call count is approximately 244.

- [x] **Step 3: Evaluate with the updated gates**

```powershell
$reviewArtifacts = 'C:\Users\chenkai.lv\.codex\visualizations\2026\07\26\019f9d09-4a04-7070-8e33-bb11c86c0c53\artifacts'

python scripts/eval_prompt_accuracy.py `
  --ground-truth data/evaluation/full_pipeline_heldout_v1.json `
  --baseline "$reviewArtifacts\baseline-output_google_gemini-3-flash-preview.json" `
  --improved "$reviewArtifacts\improved-v7-output_google_gemini-3-flash-preview.json" `
  --baseline-manifest "$reviewArtifacts\baseline.manifest.json" `
  --improved-manifest "$reviewArtifacts\improved-v7.manifest.json" `
  --required-tests-json "$reviewArtifacts\required-tests-v7.json" `
  --report-json "$reviewArtifacts\ab-report.v7.json" `
  --bootstrap-seed 20260726 `
  --bootstrap-replicates 10000 `
  --require-full-coverage `
  --require-acceptance
```

Expected: 100% coverage and `ACCEPT`. If any gate fails, retain the failed
external report, revert the failing behavioral candidate, and do not claim an
accuracy improvement.

Observed for `improved-v7`: coverage and accuracy gates passed, but the release
decision was `ROLLBACK` because one malformed model response increased parse
and MappingCritic errors, while a Ruff-only runner hash change was incorrectly
treated as a frozen-configuration mismatch. The failed reports are retained as
`ab-report.v7.json` and `ab-report.failed-v7-comparable.json`.

### Task 6A: Remediate the failed v7 quality gates

- [x] Treat runner hash drift as explicit audit-only evidence while continuing
  to gate model, input, held-out, KB, RAG, concurrency, generation, and cascade
  configuration equality.
- [x] Recompute MappingCritic issues for both output arms with the same current
  critic instead of comparing embedded issues produced by different revisions.
- [x] Add one rate-limited production-path retry after malformed JSON, retaining
  the Level-4 fallback after the second malformed response.
- [x] Add regression tests for audit-only runner drift, comparable critic
  recomputation, retry success, and bounded retry fallback.
- [x] Run the required focused tests and static gates before fixing the v8 SHA.
- [ ] Commit a clean v8 candidate, rerun the frozen Improved arm, and require
  all machine gates to accept it before updating the PR.

- [ ] **Step 4: Update the PR branch and evidence**

Push the accepted candidate to `codex/sdtm-eval-optimization`. Update the PR
body with the new tested SHA, current head, duplicate-QNAM counter, refreshed
metrics, domain-types note, and the classification of the six existing illegal
variables as KB-sourced pending curation.

- [ ] **Step 5: Reply to and resolve review threads**

Reply in each inline thread:

1. QNAM: describe stable derivation, critic detection, evaluator counter, and tests.
2. KB snapshot: describe the KB heuristic guard and snapshot restoration.
3. RAG: describe plain-variable rendering and separated conditions.
4. Level 4: cite the locked design semantics and retained test.

Resolve all four threads after the replies are posted.

- [ ] **Step 6: Verify remote state**

Read PR #17 comments, review threads, head SHA, and check runs. Expected:

- the pushed SHA is the PR head;
- all four threads are resolved;
- the repository CI check completes successfully.
