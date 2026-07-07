#!/usr/bin/env python
"""
validate_prompts.py
-------------------
Static validation script for prompt YAML files.

Run from the project root:
    python scripts/prompt_ci/validate_prompts.py

Exits with code 0 on success, 1 on any failure.

Checks performed (7 types):
  1. Schema check       - all YAML files parse and have required fields + valid version
  2. Placeholder check  - every {placeholder} in the template has a fill argument in
                          SDTMPromptGenerator.generate_variable_prompt()
  3. Rule ID uniqueness - no duplicate rule IDs in rules.yaml
  4. Rule ref integrity - domain_rules and pattern_rules only reference existing IDs
  5. Example domain validity - all domains in examples exist in DOMAIN_RULES or
                                DOMAIN_VARIABLES
  6. Variable length    - SDTM output variables in examples are ≤8 characters
  7. Version format     - each file's 'version' matches semver X.Y.Z
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

# Reconfigure stdout to UTF-8 so the script is safe on Windows GBK terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
elif sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Ensure src/ is importable when run directly
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

import yaml  # noqa: E402 – after sys.path fix

_PROMPTS_DIR = _ROOT / "src" / "prompts"
_TEMPLATE_PATH = _PROMPTS_DIR / "templates" / "variable_mapping.yaml"
_RULES_PATH = _PROMPTS_DIR / "rules" / "sdtm_rules.yaml"
_EXAMPLES_PATH = _PROMPTS_DIR / "examples" / "pattern_examples.yaml"

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# Placeholders in the template that are filled by generate_variable_prompt()
_EXPECTED_FILL_ARGS = {
    "language_instruction",
    "sdtm_rules",
    "domain_variables_section",
    "pair_json",
    "sibling_variables_section",
    "table_context_section",
    "domain_specific_patterns",
    "table_name",
    "variable_name",
    "domain_examples_section",
}

# Maximum SDTM variable length (CDISC rule)
_MAX_VAR_LEN = 8

# Variable name pattern – must be pure alphanumeric (no spaces/keywords in output field)
_PURE_VAR_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,7}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping at top level in {path.name}")
    return data


def _err(msg: str) -> str:
    return f"  [FAIL] {msg}"


def _ok(msg: str) -> str:
    return f"  [OK]   {msg}"


# ---------------------------------------------------------------------------
# Check 7: Version format (called inside each schema check)
# ---------------------------------------------------------------------------


def _check_version(data: dict, filename: str) -> list[str]:
    errors: list[str] = []
    version = str(data.get("version", ""))
    if not _SEMVER_RE.match(version):
        errors.append(_err(f"[{filename}] 'version' must be semver X.Y.Z, got: {version!r}"))
    return errors


# ---------------------------------------------------------------------------
# Check 1: Schema
# ---------------------------------------------------------------------------


def check_schema() -> tuple[bool, list[str], dict, dict, dict]:
    """Load all YAML files, validate required keys. Returns (ok, messages, tpl, rules, exs)."""
    msgs: list[str] = []
    ok = True
    tpl = rules = exs = {}

    required: dict[str, list[str]] = {
        "variable_mapping.yaml": ["version", "template", "language_instructions"],
        "sdtm_rules.yaml": [
            "version",
            "rules",
            "core_rules",
            "pattern_rules",
            "domain_rules",
            "domain_types",
        ],
        "pattern_examples.yaml": ["version", "examples", "variable_pattern_keywords"],
    }

    for path in [_TEMPLATE_PATH, _RULES_PATH, _EXAMPLES_PATH]:
        try:
            data = _load(path)
        except Exception as exc:
            msgs.append(_err(f"[{path.name}] Load error: {exc}"))
            ok = False
            continue

        missing = [k for k in required.get(path.name, []) if k not in data]
        if missing:
            msgs.append(_err(f"[{path.name}] Missing required keys: {missing}"))
            ok = False

        version_errors = _check_version(data, path.name)
        if version_errors:
            msgs.extend(version_errors)
            ok = False

        if path == _TEMPLATE_PATH:
            tpl = data
        elif path == _RULES_PATH:
            rules = data
        else:
            exs = data

    if ok:
        msgs.append(_ok("Schema check passed for all 3 YAML files"))

    return ok, msgs, tpl, rules, exs


# ---------------------------------------------------------------------------
# Check 2: Placeholder consistency
# ---------------------------------------------------------------------------


def check_placeholders(tpl: dict) -> tuple[bool, list[str]]:
    template_str: str = tpl.get("template", "")
    found = set(_PLACEHOLDER_RE.findall(template_str))
    # Remove double-brace escapes (these are literal {{ }} in the output JSON example)
    # The JSON output line uses {{"key": "val"}} which YAML keeps as {{ }}, so placeholders
    # inside those are not real placeholders.  We rely on the regex only matching \w+ anyway.
    unexpected = found - _EXPECTED_FILL_ARGS
    msgs: list[str] = []
    ok = True

    if unexpected:
        msgs.append(_err(f"Unexpected template placeholders (no fill logic): {sorted(unexpected)}"))
        ok = False

    missing = _EXPECTED_FILL_ARGS - found
    if missing:
        msgs.append(_err(f"Expected placeholders missing from template: {sorted(missing)}"))
        ok = False

    if ok:
        msgs.append(_ok("All template placeholders are accounted for"))
    return ok, msgs


# ---------------------------------------------------------------------------
# Check 3: Rule ID uniqueness
# ---------------------------------------------------------------------------


def check_rule_id_uniqueness(rules: dict) -> tuple[bool, list[str]]:
    rule_ids = list(rules.get("rules", {}).keys())
    seen: set[str] = set()
    duplicates: list[str] = []
    for rid in rule_ids:
        if rid in seen:
            duplicates.append(rid)
        seen.add(rid)
    if duplicates:
        return False, [_err(f"Duplicate rule IDs: {duplicates}")]
    return True, [_ok(f"All {len(rule_ids)} rule IDs are unique")]


# ---------------------------------------------------------------------------
# Check 4: Rule reference integrity
# ---------------------------------------------------------------------------


def check_rule_ref_integrity(rules: dict) -> tuple[bool, list[str]]:
    known_ids: set[str] = set(rules.get("rules", {}).keys())
    msgs: list[str] = []
    ok = True

    def _check_refs(section_name: str, mapping: dict) -> None:
        nonlocal ok
        for key, id_list in mapping.items():
            for rid in id_list or []:
                if rid not in known_ids:
                    msgs.append(_err(f"[{section_name}] '{key}' references unknown rule ID: {rid}"))
                    ok = False

    _check_refs("domain_rules", rules.get("domain_rules", {}))
    _check_refs("pattern_rules", rules.get("pattern_rules", {}))
    _check_refs("core_rules", {"core": rules.get("core_rules", [])})

    if ok:
        msgs.append(_ok("All rule references point to existing IDs"))
    return ok, msgs


# ---------------------------------------------------------------------------
# Check 5: Example domain validity
# ---------------------------------------------------------------------------


def check_example_domains(exs: dict, rules: dict) -> tuple[bool, list[str]]:
    known_domains: set[str] = set(rules.get("domain_rules", {}).keys())
    # Also load DOMAIN_VARIABLES if available
    try:
        from src.config.domain_semantic_map import DOMAIN_VARIABLES

        known_domains |= set(DOMAIN_VARIABLES.keys())
    except Exception:
        pass  # config not importable in CI minimal env – skip expansion

    msgs: list[str] = []
    ok = True
    for pattern, examples in exs.get("examples", {}).items():
        for ex in examples:
            domain = str(ex.get("domain", "")).upper()
            if domain and domain not in known_domains:
                msgs.append(_err(f"[examples.{pattern}] domain '{domain}' not in known domains"))
                ok = False

    if ok:
        msgs.append(_ok("All example domains are valid"))
    return ok, msgs


# ---------------------------------------------------------------------------
# Check 6: Variable length
# ---------------------------------------------------------------------------


def check_variable_length(exs: dict) -> tuple[bool, list[str]]:
    msgs: list[str] = []
    ok = True
    for pattern, examples in exs.get("examples", {}).items():
        for ex in examples:
            output: str = str(ex.get("output", ""))
            # Skip conditional outputs like "FAORRES when FATESTCD=THDIA"
            if " " in output or "=" in output:
                continue
            if len(output) > _MAX_VAR_LEN:
                msgs.append(_err(f"[examples.{pattern}] output variable '{output}' exceeds {_MAX_VAR_LEN} chars"))
                ok = False
            # Also check qnam if present
            qnam: str = str(ex.get("qnam", ""))
            if qnam and len(qnam) > _MAX_VAR_LEN:
                msgs.append(_err(f"[examples.{pattern}] qnam '{qnam}' exceeds {_MAX_VAR_LEN} chars"))
                ok = False

    if ok:
        msgs.append(_ok("All example variable names are within 8-character limit"))
    return ok, msgs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 60)
    print("Prompt YAML Validation")
    print("=" * 60)

    all_ok = True

    # --- Check 1: Schema + Check 7 (version embedded inside) ---
    print("\n[1/6] Schema & version check")
    schema_ok, schema_msgs, tpl, rules, exs = check_schema()
    for m in schema_msgs:
        print(m)
    all_ok = all_ok and schema_ok

    if not schema_ok:
        # Cannot run further checks without valid data
        print("\nSchema errors prevent further validation. Fix them first.")
        return 1

    # --- Check 2: Placeholders ---
    print("\n[2/6] Placeholder consistency")
    ph_ok, ph_msgs = check_placeholders(tpl)
    for m in ph_msgs:
        print(m)
    all_ok = all_ok and ph_ok

    # --- Check 3: Rule ID uniqueness ---
    print("\n[3/6] Rule ID uniqueness")
    uniq_ok, uniq_msgs = check_rule_id_uniqueness(rules)
    for m in uniq_msgs:
        print(m)
    all_ok = all_ok and uniq_ok

    # --- Check 4: Rule reference integrity ---
    print("\n[4/6] Rule reference integrity")
    ref_ok, ref_msgs = check_rule_ref_integrity(rules)
    for m in ref_msgs:
        print(m)
    all_ok = all_ok and ref_ok

    # --- Check 5: Example domain validity ---
    print("\n[5/6] Example domain validity")
    dom_ok, dom_msgs = check_example_domains(exs, rules)
    for m in dom_msgs:
        print(m)
    all_ok = all_ok and dom_ok

    # --- Check 6: Variable length ---
    print("\n[6/6] Variable length (≤8 chars)")
    var_ok, var_msgs = check_variable_length(exs)
    for m in var_msgs:
        print(m)
    all_ok = all_ok and var_ok

    print("\n" + "=" * 60)
    if all_ok:
        print("All checks passed.")
        return 0
    else:
        print("One or more checks FAILED. See errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
