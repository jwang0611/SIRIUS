"""Semantic contracts shared by prompt YAML and the production prompt path."""

from __future__ import annotations

from src.prompts import sdtm_rules
from src.prompts.loader import load_rules


def test_runtime_rule_categories_are_exact_yaml_copies():
    yaml_rules = load_rules()

    assert sdtm_rules.CORE_RULES == yaml_rules["core_rules"]
    assert sdtm_rules.PATTERN_RULES == yaml_rules["pattern_rules"]
    assert sdtm_rules.DOMAIN_RULES == yaml_rules["domain_rules"]
    assert sdtm_rules.DOMAIN_TYPES == yaml_rules["domain_types"]
