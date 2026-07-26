# Auditable SDTM Recommendation Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and execute a reproducible Baseline vs Improved SDTM recommendation experiment whose focused prompt candidate is accepted only when every clinical, statistical, and deterministic-quality gate passes.

**Architecture:** Keep `SDTMProcessor` as the production path, add provenance at its existing return boundaries, and add two focused evaluation modules for manifests and A/B statistics. Commit the audit infrastructure before changing prompt content so the baseline and improved SHAs are distinct and the prompt candidate can be reverted independently.

**Tech Stack:** Python 3.11+, pytest, PyYAML, syrupy snapshots, standard-library JSON/hashlib/random/subprocess/statistics, existing SIRIUS `SDTMProcessor`, deterministic validator, and MappingCritic.

---

## File Map

**Create**

- `src/evaluation/__init__.py` — evaluation package marker.
- `src/evaluation/run_manifest.py` — file hashing, sanitized reproducibility manifests, and manifest comparison.
- `src/evaluation/ab_analysis.py` — scenario labels, output-quality counts, paired bootstrap, and acceptance gates.
- `scripts/run_sdtm_experiment.py` — one safe command builder for both production runs; estimate-only unless `--execute` is present.
- `tests/unit/test_sdtm_processor_provenance.py` — real processor-path provenance behavior.
- `tests/unit/test_run_manifest.py` — manifest and experiment-command contracts.
- `tests/unit/test_ab_analysis.py` — scenario, quality, bootstrap, and gate contracts.
- `tests/unit/test_prompt_semantics.py` — YAML single-source and structured prompt semantics.

**Modify**

- `src/processors/sdtm_processor.py` — stamp actual cascade level on successful, pre-filtered, and coverage-repair output.
- `src/processors/postprocess.py` — stamp Level 4 on LLM-path fallback output.
- `scripts/eval_prompt_accuracy.py` — preserve provenance, expose row outcomes, emit JSON reports, and enforce acceptance.
- `tests/unit/test_eval_prompt_accuracy.py` — CLI/report integration and complete slice accounting.
- `data/evaluation/README.md` — reproducible experiment and report usage.
- `src/prompts/sdtm_rules.py` — remove duplicate hard-coded category assignments.
- `src/prompts/rules/sdtm_rules.yaml` — structured FA rules and authoritative domain groups.
- `src/prompts/templates/variable_mapping.yaml` — structured FA hint and version bump.
- `src/prompts/examples/pattern_examples.yaml` — structured FA examples and version bump.
- `scripts/prompt_ci/validate_prompts.py` — semantic output-contract validation.
- `tests/unit/test_prompt_snapshot.py` and `tests/unit/__snapshots__/test_prompt_snapshot.ambr` — intended prompt output changes.

**Do not modify**

- `data/evaluation/full_pipeline_heldout_v1.json`
- production KB/RAG contents
- the original `main` checkout or its `desktop/build/` deletions
- `RecommendationOrchestrator` or `RecommendationNormalizer`

## Environment Used for This Worktree

PowerShell commands use:

```powershell
$siriusPython = 'C:\Users\chenkai.lv\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$taskArtifacts = 'C:\Users\chenkai.lv\.codex\visualizations\2026\07\26\019f9d09-4a04-7070-8e33-bb11c86c0c53\artifacts'
$pytestBase = 'C:\Users\chenkai.lv\.codex\visualizations\2026\07\26\019f9d09-4a04-7070-8e33-bb11c86c0c53\pytest-plan'
$pytestCache = 'C:\Users\chenkai.lv\.codex\visualizations\2026\07\26\019f9d09-4a04-7070-8e33-bb11c86c0c53\pytest-cache-plan'
```

The artifact directory is outside the repository and must never be staged.

### Task 1: Production cascade provenance

**Files:**

- Create: `tests/unit/test_sdtm_processor_provenance.py`
- Modify: `src/processors/sdtm_processor.py`
- Modify: `src/processors/postprocess.py`

- [ ] **Step 1: Write failing provenance tests**

Add tests with these concrete assertions:

```python
from src.processors.sdtm_processor import SDTMProcessor


def _bare_processor() -> SDTMProcessor:
    processor = object.__new__(SDTMProcessor)
    processor.audit_logger = None
    processor.debug = False
    return processor


def test_successful_result_gets_cascade_level_even_without_audit_logger():
    processor = _bare_processor()
    recs = [{"domain": "FA", "sdtm_variable": "FAORRES", "source": "RAG"}]
    processor._audit_mapping_result({}, recs, cascade_level=3)
    assert recs[0]["cascade_level"] == 3


def test_not_submitted_prefilter_has_level_zero():
    processor = _bare_processor()
    processor.kb_hints = type("Hints", (), {"is_not_submitted_table": lambda self, value: True})()
    recs = processor.process_variable_pair(
        "SUBJECT",
        {"metadata_variable": "CRFVER", "annotation_table": "Questionnaire"},
        [],
    )
    assert recs[0]["source"] == "KB_NOT_SUBMITTED"
    assert recs[0]["cascade_level"] == 0


def test_llm_fallback_has_level_four():
    processor = _bare_processor()
    recs = processor._build_fallback_recommendations(
        "AE", {"metadata_variable": "AETERM"}, "AE", "Failed to parse AI JSON"
    )
    assert recs[0]["source"] == "FALLBACK"
    assert recs[0]["cascade_level"] == 4
```

Also add a coverage-repair test that binds
`_recommend_domain_from_annotation` and `_map_table_to_domain` to deterministic
test lambdas, calls `_ensure_all_variables_covered`, and asserts the synthesized
record has `cascade_level is None`.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
& $siriusPython -m pytest tests/unit/test_sdtm_processor_provenance.py -q --basetemp=$pytestBase -o cache_dir=$pytestCache
```

Expected: failures for missing `cascade_level`, not import/setup errors.

- [ ] **Step 3: Implement minimal provenance stamping**

In `_audit_mapping_result`, stamp before the audit-logger early return:

```python
for rec in domain_recs:
    rec["cascade_level"] = cascade_level
if not self.audit_logger or not domain_recs:
    return
```

Add `"cascade_level": 0` to the NOT SUBMITTED pre-filter record in
`process_variable_pair`. Add `"cascade_level": 4` to
`PostprocessMixin._build_fallback_recommendations`. Add
`"cascade_level": None` to `_ensure_all_variables_covered` synthesized
`UNMAPPED` output.

- [ ] **Step 4: Run focused and cascade tests**

Run:

```powershell
& $siriusPython -m pytest tests/unit/test_sdtm_processor_provenance.py tests/unit/test_cascade.py tests/characterization/test_cascade_shortcircuit.py -q --basetemp=$pytestBase -o cache_dir=$pytestCache
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add src/processors/sdtm_processor.py src/processors/postprocess.py tests/unit/test_sdtm_processor_provenance.py
git commit -m "feat: expose production cascade provenance"
```

### Task 2: Reproducible manifest and safe experiment runner

**Files:**

- Create: `src/evaluation/__init__.py`
- Create: `src/evaluation/run_manifest.py`
- Create: `scripts/run_sdtm_experiment.py`
- Create: `tests/unit/test_run_manifest.py`

- [ ] **Step 1: Write failing hashing and sanitization tests**

Test these public APIs:

```python
from src.evaluation.run_manifest import (
    build_run_manifest,
    compare_shared_configuration,
    hash_file,
    sanitize_endpoint,
)


def test_hash_file_normalizes_text_line_endings(tmp_path):
    lf = tmp_path / "lf.json"
    crlf = tmp_path / "crlf.json"
    lf.write_bytes(b'{\n"x": 1\n}\n')
    crlf.write_bytes(b'{\r\n"x": 1\r\n}\r\n')
    assert hash_file(lf, normalize_text=True) == hash_file(crlf, normalize_text=True)


def test_endpoint_manifest_drops_credentials_and_path():
    assert sanitize_endpoint("https://user:secret@example.test/private/v1") == {
        "scheme": "https",
        "host": "example.test",
        "port": None,
    }


def test_shared_config_comparison_allows_only_run_identity_changes():
    baseline = {"configuration": {"model": "m", "temperature": 0}, "git": {"sha": "a"}}
    improved = {"configuration": {"model": "m", "temperature": 0}, "git": {"sha": "b"}}
    assert compare_shared_configuration(baseline, improved)["equal"] is True
```

Add a manifest test using a temporary code root and KB directory. Assert the
manifest includes input/held-out/KB/prompt hashes, model parameters, RAG
parameters, concurrency, Git identity supplied by the caller, and no
`api_key`, `prompt`, or `response` key at any nesting depth.

- [ ] **Step 2: Verify RED**

```powershell
& $siriusPython -m pytest tests/unit/test_run_manifest.py -q --basetemp=$pytestBase -o cache_dir=$pytestCache
```

Expected: import failure for the not-yet-created module.

- [ ] **Step 3: Implement `run_manifest.py`**

Implement:

```python
TEXT_HASH_SUFFIXES = {".json", ".yaml", ".yml", ".py", ".md"}


def hash_file(path: Path, *, normalize_text: bool = False) -> str:
    payload = path.read_bytes()
    if normalize_text:
        payload = payload.replace(b"\r\n", b"\n")
    return hashlib.sha256(payload).hexdigest()


def sanitize_endpoint(url: str) -> dict[str, str | int | None]:
    parsed = urlsplit(url)
    return {"scheme": parsed.scheme, "host": parsed.hostname or "", "port": parsed.port}
```

`build_run_manifest` accepts all configuration as explicit keyword arguments,
hashes the three prompt YAML files and every `.json`/`.parquet` KB file in
sorted relative-path order, records row counts without copying row content, and
returns a JSON-serializable dictionary. `compare_shared_configuration` compares
the complete `configuration`, held-out/input hashes, and KB hashes while
allowing only run label, timestamps, Git SHA, prompt fingerprints, and outputs
to differ.

- [ ] **Step 4: Write failing experiment-runner tests**

Import `build_parser`, `build_generator_command`, `build_pinned_environment`,
and `estimate_external_requests` from `scripts.run_sdtm_experiment`.

Assert:

- parser defaults to estimate-only and requires `--execute` for subprocess use;
- generated command includes `--temperature 0`, the exact model, KB/RAG
  options, `--parallel`, `--max-workers 5`, and never includes an API key;
- environment pins `KB_MIN_CONFIDENCE`, `CASCADE_KB_HIGH_CONF`,
  `CASCADE_RAG_HIGH_CONF`, `KB_DOMAIN_OVERRIDE_CONF`, `RAG_ENABLED`,
  `RAG_KB_DEFAULT_FILE`, `SDTM_ENABLE_PARALLEL`, `SDTM_MAX_WORKERS`, and
  `SDTM_LOG_AI=0`; and
- request estimate returns 309 maximum generation calls per run and 618 for
  the complete two-run experiment for the current held-out cohort counts.

- [ ] **Step 5: Verify runner tests RED**

```powershell
& $siriusPython -m pytest tests/unit/test_run_manifest.py -q --basetemp=$pytestBase -o cache_dir=$pytestCache
```

Expected: runner imports/functions missing.

- [ ] **Step 6: Implement the runner**

The runner must:

1. validate a clean `--code-root` Git worktree;
2. validate a 490-row input hash and held-out hash;
3. print the model and upper-bound generation/embedding request estimates;
4. exit successfully without network activity unless `--execute` is present;
5. build the production generator subprocess command from the same parsed
   values written to the manifest;
6. set the pinned environment without printing secrets;
7. write a `running` manifest before execution;
8. finalize it as `succeeded` or `failed`, including output file hashes and
   source/cascade counts; and
9. propagate the generator exit code.

Use `subprocess.run(command, cwd=code_root, env=environment, check=False)` with
an argument list, never `shell=True`.

- [ ] **Step 7: Verify and commit**

```powershell
& $siriusPython -m pytest tests/unit/test_run_manifest.py -q --basetemp=$pytestBase -o cache_dir=$pytestCache
& $siriusPython scripts/run_sdtm_experiment.py --help
git add src/evaluation scripts/run_sdtm_experiment.py tests/unit/test_run_manifest.py
git commit -m "feat: add reproducible SDTM run manifests"
```

### Task 3: Scenario and deterministic-quality analysis

**Files:**

- Create: `src/evaluation/ab_analysis.py`
- Create: `tests/unit/test_ab_analysis.py`
- Modify: `scripts/eval_prompt_accuracy.py`
- Modify: `tests/unit/test_eval_prompt_accuracy.py`

- [ ] **Step 1: Write failing scenario-label tests**

Use metadata-only synthetic references:

```python
@pytest.mark.parametrize(
    ("domain", "variable", "expected"),
    [
        ("FA", "QVAL when QNAM=FAOROTH when FATESTCD=THCLA", {"SUPP", "TESTCD"}),
        ("TU|TR", "TULOC|TRORRES when TRTESTCD=DIAMETER", {"MULTI_DOMAIN", "TESTCD"}),
        ("", "NOT SUBMITTED", {"NOT_SUBMITTED"}),
        ("CUSTOM", "CUSVAR", {"NON_STANDARD_DOMAIN"}),
    ],
)
def test_classify_scenarios(domain, variable, expected):
    assert classify_scenarios({"SDTM_Domain": domain, "SDTM_Variable": variable}) == expected
```

Add a standard single-domain case that returns an empty set.

- [ ] **Step 2: Write failing output-quality tests**

Construct processor rows for:

- a valid standard variable;
- `non_standard_variable=True`;
- a standard variable longer than eight characters;
- `sdtm_variable_type="supp"` without `QVAL`, `supp_dataset`, or valid QNAM;
- a fallback with `fallback_reason="Failed to parse AI JSON"`;
- source `UNMAPPED`;
- `cascade_level=None`; and
- duplicated global MappingCritic error dictionaries.

Assert exact counters:

```python
assert counts == {
    "deterministic_validation_errors": 1,
    "illegal_sdtm_variables": 1,
    "illegal_supp_qnam": 1,
    "parse_failures": 1,
    "unmapped_outputs": 1,
    "missing_cascade_provenance": 1,
    "mapping_critic_errors": 1,
}
```

- [ ] **Step 3: Verify RED**

```powershell
& $siriusPython -m pytest tests/unit/test_ab_analysis.py -q --basetemp=$pytestBase -o cache_dir=$pytestCache
```

Expected: module/functions missing.

- [ ] **Step 4: Implement scenario and quality helpers**

`classify_scenarios` tokenizes multi-domain expressions, checks every token
with `is_valid_domain`, and recognizes SUPP/TESTCD/NOT SUBMITTED from the
complete normalized reference expression.

`count_quality_issues(rows, consistency_issues)` validates only the final
deduplicated evaluated recommendation per key. It uses the existing standard
domain catalog and the token regex `^[A-Z][A-Z0-9]{0,7}$`. A valid SUPP record
requires:

```python
variable_type == "supp"
sdtm_variable == "QVAL"
supp_dataset.startswith("SUPP")
valid_token(supp_variable)
```

MappingCritic issue dictionaries are deduplicated by canonical JSON before
counting severity `error`.

- [ ] **Step 5: Preserve provenance and row outcomes in the evaluator**

Extend `load_ai_output` rows with:

```python
"cascade_level": drec.get("cascade_level"),
"supp_dataset": drec.get("supp_dataset", ""),
"supp_variable": drec.get("supp_variable", ""),
"testcd": drec.get("testcd", ""),
"fallback_reason": drec.get("fallback_reason", ""),
"validation_flags": {
    key: drec.get(key)
    for key in (
        "invalid_domain_corrected",
        "variable_name_corrected",
        "variable_name_truncated",
        "domain_prefix_mismatch",
        "non_standard_variable",
        "auto_corrected_to_supp",
    )
    if drec.get(key)
},
```

Extend `evaluate` with serializable `row_results`, `cascade_stats`, and
`scenario_stats`. Each row result includes `evaluation_id`, cohort, domain,
source, cascade level, scenarios, status, exact/domain booleans, and no raw
review metadata.

- [ ] **Step 6: Add evaluator integration assertions**

Update `tests/unit/test_eval_prompt_accuracy.py` to prove:

- all three cohorts have independent `gt_size`, coverage, exact, and domain
  values;
- source and cascade-level slices use actual processor metadata;
- SUPP, TESTCD, multi-domain, non-standard, and NOT SUBMITTED scenario slices
  are independent;
- missing rows remain failures in every applicable slice; and
- `KB_DISAGREE` is marked diagnostic in returned metadata.

- [ ] **Step 7: Run and commit**

```powershell
& $siriusPython -m pytest tests/unit/test_ab_analysis.py tests/unit/test_eval_prompt_accuracy.py tests/unit/test_full_pipeline_heldout.py -q --basetemp=$pytestBase -o cache_dir=$pytestCache
git add src/evaluation/ab_analysis.py scripts/eval_prompt_accuracy.py tests/unit/test_ab_analysis.py tests/unit/test_eval_prompt_accuracy.py
git commit -m "feat: audit SDTM evaluation scenarios and quality"
```

### Task 4: Paired bootstrap, machine report, and release gates

**Files:**

- Modify: `src/evaluation/ab_analysis.py`
- Modify: `scripts/eval_prompt_accuracy.py`
- Modify: `tests/unit/test_ab_analysis.py`
- Modify: `tests/unit/test_eval_prompt_accuracy.py`
- Modify: `data/evaluation/README.md`

- [ ] **Step 1: Write failing paired-bootstrap tests**

Use paired baseline/improved binary outcomes with known differences:

```python
baseline = [0, 0, 1, 1]
improved = [1, 0, 1, 1]
result = paired_bootstrap(baseline, improved, seed=20260726, replicates=10_000)
assert result["observed_delta"] == pytest.approx(0.25)
assert result == paired_bootstrap(baseline, improved, seed=20260726, replicates=10_000)
```

Assert unequal lengths raise `ValueError` and that pairs are selected by the
same evaluation key, not independently.

- [ ] **Step 2: Write table-driven acceptance-gate tests**

Create one passing comparison and one isolated failure for every gate:

- baseline or improved coverage below 1.0;
- KB_AGREE exact regression;
- AI exact delta non-positive;
- AI delta below 0.02 with CI lower bound not above zero;
- overall Domain Match regression;
- AI Domain Match regression;
- each quality counter increasing;
- shared manifest configuration mismatch; and
- required test status false.

Assert `KB_DISAGREE` exact regression alone does not reject the candidate.

- [ ] **Step 3: Verify RED**

```powershell
& $siriusPython -m pytest tests/unit/test_ab_analysis.py -q --basetemp=$pytestBase -o cache_dir=$pytestCache
```

Expected: paired/bootstrap/gate functions missing.

- [ ] **Step 4: Implement deterministic paired analysis**

Use `random.Random(seed)` and sample pair indices with replacement. Sort
replicate deltas and calculate the 2.5th and 97.5th percentile using a
deterministic nearest-rank helper. Return improved/worsened/unchanged counts,
observed delta, seed, replicate count, and CI.

`evaluate_acceptance` returns:

```python
{
    "decision": "ACCEPT" if all(gate["passed"] for gate in gates) else "ROLLBACK",
    "gates": [
        {"name": "...", "passed": True, "baseline": ..., "improved": ..., "detail": "..."},
    ],
}
```

- [ ] **Step 5: Add machine-report CLI**

Add parser arguments:

```text
--baseline-manifest PATH
--improved-manifest PATH
--report-json PATH
--bootstrap-seed 20260726
--bootstrap-replicates 10000
--required-tests-json PATH
--require-acceptance
```

In A/B mode, require both manifests when `--report-json` or
`--require-acceptance` is used. Write the report with schema version,
manifests, complete baseline/improved metrics, paired analysis, quality
counters, manifest comparison, test evidence, gates, and decision.

Exit codes:

- `0`: report created and all requested gates pass;
- `1`: coverage or acceptance gate failure;
- `2`: malformed/mismatched manifest or report input.

- [ ] **Step 6: Add CLI integration tests**

Tests invoke `main` through monkeypatched `sys.argv` and assert:

- report JSON is machine-readable and includes all cohorts/domains/scenarios;
- `--require-acceptance` exits 1 on rollback;
- manifest mismatch exits 2;
- no aggregate-only report can omit cohort metrics; and
- report parent directories are created without writing inside production data
  paths in tests.

- [ ] **Step 7: Update evaluation documentation**

Document the exact benchmark generation, estimate-only runner, authorized
baseline/improved commands, report command, exit codes, gate definitions, and
the diagnostic-only status of the 74 `KB_DISAGREE` rows. Correct the stale
`--input` examples to `--json-file`.

- [ ] **Step 8: Verify the audit baseline revision**

```powershell
& $siriusPython -m pytest tests/unit/test_run_manifest.py tests/unit/test_ab_analysis.py tests/unit/test_eval_prompt_accuracy.py tests/unit/test_full_pipeline_heldout.py tests/unit/test_sdtm_processor_provenance.py tests/unit/test_cascade.py tests/characterization/test_cascade_shortcircuit.py -q --basetemp=$pytestBase -o cache_dir=$pytestCache
& $siriusPython -m ruff check src/evaluation scripts/run_sdtm_experiment.py scripts/eval_prompt_accuracy.py src/processors/sdtm_processor.py src/processors/postprocess.py tests/unit/test_run_manifest.py tests/unit/test_ab_analysis.py tests/unit/test_eval_prompt_accuracy.py tests/unit/test_sdtm_processor_provenance.py
& $siriusPython -m mypy src/evaluation src/processors/sdtm_processor.py src/processors/postprocess.py
git diff --check
```

Expected: all commands pass except no claim is made about the previously
recorded unrelated full-suite Windows failures.

- [ ] **Step 9: Commit and record the audit baseline SHA**

```powershell
git add src/evaluation scripts/run_sdtm_experiment.py scripts/eval_prompt_accuracy.py src/processors/sdtm_processor.py src/processors/postprocess.py tests/unit data/evaluation/README.md
git commit -m "feat: gate SDTM recommendation experiments"
git rev-parse HEAD
```

Write the resulting SHA into the external baseline run manifest when the run is
authorized. Do not modify prompt YAML before this commit.

### Task 5: YAML as the prompt-rule single source

**Files:**

- Create: `tests/unit/test_prompt_semantics.py`
- Modify: `src/prompts/sdtm_rules.py`
- Modify: `src/prompts/rules/sdtm_rules.yaml`

- [ ] **Step 1: Write the failing single-source test**

```python
from src.prompts.loader import load_rules
from src.prompts import sdtm_rules


def test_runtime_rule_categories_are_exact_yaml_copies():
    yaml_rules = load_rules()
    assert sdtm_rules.CORE_RULES == yaml_rules["core_rules"]
    assert sdtm_rules.PATTERN_RULES == yaml_rules["pattern_rules"]
    assert sdtm_rules.DOMAIN_RULES == yaml_rules["domain_rules"]
    assert sdtm_rules.DOMAIN_TYPES == yaml_rules["domain_types"]
```

This must fail at the initial code because runtime `DOMAIN_TYPES` differs from
YAML.

- [ ] **Step 2: Verify RED**

```powershell
& $siriusPython -m pytest tests/unit/test_prompt_semantics.py::test_runtime_rule_categories_are_exact_yaml_copies -q --basetemp=$pytestBase -o cache_dir=$pytestCache
```

Expected: assertion difference for `DOMAIN_TYPES`.

- [ ] **Step 3: Remove duplicate assignments**

Delete the second `CORE_RULES`, `PATTERN_RULES`, `DOMAIN_RULES`, and
`DOMAIN_TYPES` blocks from `src/prompts/sdtm_rules.py`. Keep the typed values
loaded from `_data` as the only definitions.

Update YAML `domain_types.FINDINGS` to preserve current intended IG 3.4
findings coverage and include the previously omitted MO domain:

```yaml
FINDINGS: [LB, VS, EG, PE, QS, PC, PP, TR, RS, IS, MB, MO, FA, SC, OE, MK, GF, CP, XU]
```

Increment rules version from `1.0.0` to `1.1.0`.

- [ ] **Step 4: Verify GREEN**

```powershell
& $siriusPython -m pytest tests/unit/test_prompt_semantics.py -q --basetemp=$pytestBase -o cache_dir=$pytestCache
```

Expected: single-source test passes.

- [ ] **Step 5: Commit**

```powershell
git add src/prompts/sdtm_rules.py src/prompts/rules/sdtm_rules.yaml tests/unit/test_prompt_semantics.py
git commit -m "fix: make prompt rules YAML authoritative"
```

### Task 6: Structured FA prompt contract in the real processor path

**Files:**

- Modify: `src/prompts/rules/sdtm_rules.yaml`
- Modify: `src/prompts/templates/variable_mapping.yaml`
- Modify: `src/prompts/examples/pattern_examples.yaml`
- Modify: `scripts/prompt_ci/validate_prompts.py`
- Modify: `tests/unit/test_prompt_semantics.py`
- Modify: `tests/unit/test_prompt_snapshot.py`
- Modify: `tests/unit/__snapshots__/test_prompt_snapshot.ambr`

- [ ] **Step 1: Write failing YAML semantic tests**

Add tests that load all three YAML components and assert:

```python
assert all(" when " not in str(example["output"]).lower() for examples in data["examples"].values() for example in examples)
assert all("=" not in str(example["output"]) for examples in data["examples"].values() for example in examples)
```

For every `type: supp` example assert `output == "QVAL"`, `supp`, and `qnam`.
For every FA standard-result example assert `output == "FAORRES"` and
`testcd`. For the FA other example assert `supp == "SUPPFA"`,
`qnam == "FAOROTH"`, and a parent `testcd`.

- [ ] **Step 2: Write the failing rendered production-path test**

Construct `SDTMProcessor` with `object.__new__`, assign a real
`SDTMPromptGenerator("en")`, set `data_masker=None`, `kb_hints=None`,
`debug=False`, and bind `_infer_candidate_domains` to return `["FA"]`.
Call `_create_enhanced_prompt` with synthetic FA metadata and assert:

```python
assert "sdtm_variable** must be a plain variable name" in prompt
assert "FAORRES when" not in prompt
assert "QVAL when QNAM" not in prompt
assert '"sdtm_variable": "FAORRES"' in prompt
assert '"testcd": "THLOC"' in prompt
```

This proves the modified content is used by the actual production prompt
boundary, not an unused alternative implementation.

- [ ] **Step 3: Verify RED**

```powershell
& $siriusPython -m pytest tests/unit/test_prompt_semantics.py -q --basetemp=$pytestBase -o cache_dir=$pytestCache
```

Expected: failures showing embedded `when` clauses.

- [ ] **Step 4: Convert FA rules, hints, and examples**

Use structured language:

```yaml
RULE_FA_001: "FA domain: ... set sdtm_variable='FAORRES', testcd='XX', and sdtm_variable_type='standard'."
RULE_FA_002: "FA domain: ... set sdtm_variable='QVAL', supp_dataset='SUPPFA', supp_variable='FAOROTH', testcd='XX', and sdtm_variable_type='supp'."
RULE_FA_003: "FA domain: Date fields ... set sdtm_variable='FAORRES' and a date-related testcd. Do not use FADTC."
```

Set template version to `1.2.0` and examples version to `1.1.0`. Convert every
FA example composite `output` to `output: FAORRES` plus `testcd`. Give the FA
other example `testcd: THCLA`.

- [ ] **Step 5: Extend Prompt CI**

Add `check_structured_output_contract(examples, template, rules)` and show it
as check 7. It rejects spaces/equals/`when` in output values, incomplete SUPP
examples, missing FA TESTCD, and conflicting rendered guidance text. Remove the
old variable-length skip for composite expressions because composites are now
invalid.

- [ ] **Step 6: Verify semantics and observe snapshot RED**

```powershell
& $siriusPython scripts/prompt_ci/validate_prompts.py
& $siriusPython -m pytest tests/unit/test_prompt_semantics.py tests/unit/test_prompt_snapshot.py -q --basetemp=$pytestBase -o cache_dir=$pytestCache
```

Expected: Prompt CI and semantic tests pass; snapshots fail only for intended
rendered changes.

- [ ] **Step 7: Update and inspect snapshots**

```powershell
& $siriusPython -m pytest tests/unit/test_prompt_snapshot.py --snapshot-update --basetemp=$pytestBase -o cache_dir=$pytestCache
git diff -- tests/unit/__snapshots__/test_prompt_snapshot.ambr
```

Confirm the diff removes composite FA/SUPP guidance and preserves the rest of
the contract before continuing.

- [ ] **Step 8: Run complete required offline gate**

```powershell
& $siriusPython scripts/prompt_ci/validate_prompts.py
& $siriusPython -m pytest tests/unit/test_prompt_snapshot.py tests/unit/test_prompt_semantics.py tests/unit/test_cascade.py tests/characterization/test_cascade_shortcircuit.py tests/unit/test_normalizer.py tests/unit/test_recommendation_normalizer.py tests/characterization/test_normalizer_pipeline.py tests/unit/test_deterministic_validator.py tests/unit/test_mapping_critic.py tests/unit/test_eval_prompt_accuracy.py tests/unit/test_full_pipeline_heldout.py tests/unit/test_ab_analysis.py tests/unit/test_run_manifest.py tests/unit/test_sdtm_processor_provenance.py -q --basetemp=$pytestBase -o cache_dir=$pytestCache
& $siriusPython -m ruff check src/prompts src/evaluation src/processors/sdtm_processor.py src/processors/postprocess.py scripts/prompt_ci/validate_prompts.py scripts/eval_prompt_accuracy.py scripts/run_sdtm_experiment.py tests/unit/test_prompt_semantics.py tests/unit/test_ab_analysis.py tests/unit/test_run_manifest.py tests/unit/test_sdtm_processor_provenance.py
& $siriusPython -m mypy src/prompts src/evaluation src/processors/sdtm_processor.py src/processors/postprocess.py
git diff --check
```

Expected: all required commands pass.

- [ ] **Step 9: Commit the prompt candidate and record improved SHA**

```powershell
git add src/prompts scripts/prompt_ci/validate_prompts.py tests/unit/test_prompt_semantics.py tests/unit/test_prompt_snapshot.py tests/unit/__snapshots__/test_prompt_snapshot.ambr
git commit -m "fix: enforce structured SDTM prompt output"
git rev-parse HEAD
```

### Task 7: Prepare the external 490-row experiment without network calls

**Files:**

- No tracked files.
- External artifacts under `$taskArtifacts`.

- [ ] **Step 1: Generate the complete leak-free processor input**

```powershell
New-Item -ItemType Directory -Force -Path $taskArtifacts
& $siriusPython scripts/eval_prompt_accuracy.py --ground-truth data/evaluation/full_pipeline_heldout_v1.json --gen-benchmark --benchmark-output "$taskArtifacts\benchmark_input.json"
```

Expected: 490 rows and cohort counts 309/107/74. Verify no SDTM ground-truth
fields exist in the benchmark input.

- [ ] **Step 2: Identify audit baseline and improved SHAs**

Use `git log --oneline --decorate` and select:

- audit baseline: the Task 4 commit immediately before Task 5;
- improved: the Task 6 commit.

Create a detached baseline worktree under the task-owned worktree directory.
Do not use stash or alter the original checkout.

- [ ] **Step 3: Run estimate-only for both revisions**

Invoke `scripts/run_sdtm_experiment.py` twice without `--execute`, once with
each code root. Both estimates must show:

- model `google/gemini-3-flash-preview`;
- up to 309 generation calls per run;
- up to 309 query embedding calls per run when RAG reaches the AI cohort;
- up to 618 generation and 618 query embedding calls across A/B;
- temperature 0 and identical pinned parameters; and
- no network activity.

- [ ] **Step 4: Report cost risk and request explicit authorization**

Report the exact model, endpoint category, call upper bounds, unknown/private
gateway pricing risk, possible token variance, and artifact locations. Do not
add `--execute` until the user explicitly authorizes the real A/B.

### Task 8: Execute authorized A/B and enforce accept or rollback

**Files:**

- No generated outputs are tracked.
- Prompt candidate tracked files may be reverted if gates fail.

- [ ] **Step 1: Execute baseline after authorization**

Use the baseline worktree and the pinned runner with `--execute`. Write
`baseline.manifest.json` under `$taskArtifacts`. Require terminal status
`succeeded`, output hash present, and 490 flattened processor rows.

- [ ] **Step 2: Execute improved with the identical command**

Use the improved worktree. Change only run label, code root/Git SHA, prompt
fingerprints, output paths, and manifest path. Write
`improved.manifest.json`. Require 490 rows.

- [ ] **Step 3: Write required-test evidence**

Create external `required-tests.json` containing command strings, exit codes,
pass counts, and timestamps from Task 6. Do not claim the unrelated broad
pytest baseline failures are fixed.

- [ ] **Step 4: Generate and enforce the A/B report**

```powershell
$baselineManifest = Get-Content -Raw "$taskArtifacts\baseline.manifest.json" | ConvertFrom-Json
$improvedManifest = Get-Content -Raw "$taskArtifacts\improved.manifest.json" | ConvertFrom-Json
$baselineOutput = $baselineManifest.outputs.json.path
$improvedOutput = $improvedManifest.outputs.json.path
& $siriusPython scripts/eval_prompt_accuracy.py `
  --ground-truth data/evaluation/full_pipeline_heldout_v1.json `
  --baseline $baselineOutput `
  --improved $improvedOutput `
  --baseline-manifest "$taskArtifacts\baseline.manifest.json" `
  --improved-manifest "$taskArtifacts\improved.manifest.json" `
  --required-tests-json "$taskArtifacts\required-tests.json" `
  --report-json "$taskArtifacts\ab-report.json" `
  --bootstrap-seed 20260726 `
  --bootstrap-replicates 10000 `
  --require-full-coverage `
  --require-acceptance
```

Expected: exit 0 only for `decision: ACCEPT`.

- [ ] **Step 5: If the decision is ROLLBACK**

Preserve the external manifests and failed report. Revert only the Task 5–6
prompt-candidate commits with normal `git revert` commits; never use reset.
Rerun the required offline tests. Report the failed gate evidence and do not
claim improved accuracy.

If another focused prompt hypothesis is justified by aggregate diagnostic
patterns without copying held-out answers, start a new RED/GREEN prompt
candidate commit and repeat the improved run only after reporting the
additional call estimate and receiving renewed authorization.

- [ ] **Step 6: If the decision is ACCEPT**

Keep the prompt candidate. Run final `git status --short`, `git diff --check`,
the complete required offline gate, and inspect both manifests/report hashes.
Confirm no generated data, logs, keys, caches, prompt previews, or clinical
artifacts are staged.

- [ ] **Step 7: Final completion audit**

Map every objective requirement to one of:

- committed source/test/doc line;
- required-test command result;
- baseline/improved manifest field;
- report metric/gate; or
- explicit known limitation/manual adjudication note.

Only after every requirement has authoritative evidence may the persistent
goal be marked complete.
