"""
test_prompt_snapshot.py
-----------------------
Snapshot tests for the SDTM prompt generator.

Uses syrupy to capture the rendered prompt output for fixed inputs.
Any change to the YAML template/rules/examples will cause these tests
to fail — a developer must then run:

    pytest --snapshot-update tests/unit/test_prompt_snapshot.py

to review and accept the new output.

Test cases cover:
  1. Simple variable mapping (AE domain, term pattern)
  2. SUPP variable case (AE comment → SUPPAE)
  3. FA domain case (complex FATESTCD pattern)
  4. Multi-domain case (AE + CM, date variable)
  5. No domain hints (fallback / default rules)
"""

from __future__ import annotations

import re

from syrupy.assertion import SnapshotAssertion

from src.prompts.loader import clear_cache
from src.prompts.sdtm_prompts_simple import SDTMPromptGenerator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_generator(language: str = "cn") -> SDTMPromptGenerator:
    """Return a fresh SDTMPromptGenerator with caches cleared."""
    clear_cache()
    return SDTMPromptGenerator(language=language)


def _gen_prompt(
    table: str,
    var_name: str,
    annotation_variable: str,
    annotation_table: str,
    domain_hints: list[str] | None,
    language: str = "cn",
) -> str:
    g = _make_generator(language)
    variable_data = {
        "metadata_variable": var_name,
        "annotation_variable": annotation_variable,
        "annotation_table": annotation_table,
        "type": "text",
    }
    all_vars = [{"metadata_variable": var_name, "annotation_variable": annotation_variable}]
    return g.generate_variable_prompt(
        table_name=table,
        variable_name=var_name,
        variable_data=variable_data,
        all_table_variables=all_vars,
        domain_hints=domain_hints,
    )


def _stable_prompt_for_snapshot(prompt: str) -> str:
    """Normalize env-variant blocks so snapshots stay cross-platform.

    The "Available SDTM Domains and Variables" section is derived from external
    KB files and can differ across CI/runtime environments while template/rules/
    examples remain unchanged. Strip only that block before snapshot compare.
    """
    # Normalize line endings first for cross-platform consistency.
    normalized = prompt.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(
        r"\*\*Available SDTM Domains and Variables:\*\*[\s\S]*?(?=\n\*\*Input \(Current Variable\):\*\*)",
        "",
        normalized,
    )
    return normalized


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestPromptSnapshots:
    """Snapshot tests – any YAML change that alters rendered output will fail here."""

    def test_simple_ae_term(self, snapshot: SnapshotAssertion) -> None:
        """Case 1: Simple standard variable in AE domain."""
        prompt = _gen_prompt(
            table="不良事件表",
            var_name="AETERM",
            annotation_variable="不良事件名称",
            annotation_table="不良事件",
            domain_hints=["AE"],
        )
        assert _stable_prompt_for_snapshot(prompt) == snapshot

    def test_supp_ae_comment(self, snapshot: SnapshotAssertion) -> None:
        """Case 2: SUPP variable (备注 → SUPPAE.AECOM)."""
        prompt = _gen_prompt(
            table="不良事件表",
            var_name="AECOM",
            annotation_variable="不良事件备注",
            annotation_table="不良事件",
            domain_hints=["AE"],
        )
        assert _stable_prompt_for_snapshot(prompt) == snapshot

    def test_fa_domain(self, snapshot: SnapshotAssertion) -> None:
        """Case 3: FA domain with FATESTCD pattern."""
        prompt = _gen_prompt(
            table="肿瘤病史表",
            var_name="THLOC",
            annotation_variable="当前疾病部位",
            annotation_table="肿瘤病史",
            domain_hints=["FA"],
        )
        assert _stable_prompt_for_snapshot(prompt) == snapshot

    def test_multi_domain_date(self, snapshot: SnapshotAssertion) -> None:
        """Case 4: Multi-domain (AE + CM), date variable."""
        prompt = _gen_prompt(
            table="不良事件表",
            var_name="AESTDTC",
            annotation_variable="不良事件开始日期",
            annotation_table="不良事件",
            domain_hints=["AE", "CM"],
        )
        assert _stable_prompt_for_snapshot(prompt) == snapshot

    def test_no_domain_hints(self, snapshot: SnapshotAssertion) -> None:
        """Case 5: No domain hints – uses default rules only."""
        prompt = _gen_prompt(
            table="受试者信息",
            var_name="SUBJID",
            annotation_variable="受试者编号",
            annotation_table="基本信息",
            domain_hints=None,
        )
        assert _stable_prompt_for_snapshot(prompt) == snapshot

    def test_english_language(self, snapshot: SnapshotAssertion) -> None:
        """Case 6: English language instruction variant."""
        prompt = _gen_prompt(
            table="Adverse Events",
            var_name="AETERM",
            annotation_variable="Adverse event term",
            annotation_table="AE",
            domain_hints=["AE"],
            language="en",
        )
        assert _stable_prompt_for_snapshot(prompt) == snapshot


# ---------------------------------------------------------------------------
# Loader version tracking
# ---------------------------------------------------------------------------


class TestPromptVersions:
    """Ensure all YAML components expose their versions."""

    def test_template_version_is_semver(self) -> None:
        import re

        from src.prompts.loader import get_version

        version = get_version("template")
        assert re.match(r"^\d+\.\d+\.\d+$", version), f"Bad version: {version}"

    def test_rules_version_is_semver(self) -> None:
        import re

        from src.prompts.loader import get_version

        version = get_version("rules")
        assert re.match(r"^\d+\.\d+\.\d+$", version), f"Bad version: {version}"

    def test_examples_version_is_semver(self) -> None:
        import re

        from src.prompts.loader import get_version

        version = get_version("examples")
        assert re.match(r"^\d+\.\d+\.\d+$", version), f"Bad version: {version}"
