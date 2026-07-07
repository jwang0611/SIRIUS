"""
Prompt YAML Loader
==================
Loads and validates prompt configuration from YAML files under src/prompts/.

File layout:
    src/prompts/templates/variable_mapping.yaml  - main prompt template + language instructions
    src/prompts/rules/sdtm_rules.yaml            - rules dict + category mappings
    src/prompts/examples/pattern_examples.yaml   - few-shot examples + keyword mappings

Public API (called by sdtm_prompts_simple.py and sdtm_rules.py):
    load_template()  -> dict
    load_rules()     -> dict
    load_examples()  -> dict
    get_version(name) -> str   # "template" | "rules" | "examples"
"""

from __future__ import annotations

import logging
import re
from functools import cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Resolve paths relative to this file (src/prompts/)
_PROMPTS_DIR = Path(__file__).parent
_TEMPLATE_PATH = _PROMPTS_DIR / "templates" / "variable_mapping.yaml"
_RULES_PATH = _PROMPTS_DIR / "rules" / "sdtm_rules.yaml"
_EXAMPLES_PATH = _PROMPTS_DIR / "examples" / "pattern_examples.yaml"

# Required top-level keys for each file
_REQUIRED_KEYS: dict[str, list[str]] = {
    "template": ["version", "template", "language_instructions"],
    "rules": ["version", "rules", "core_rules", "pattern_rules", "domain_rules", "domain_types"],
    "examples": ["version", "examples", "variable_pattern_keywords"],
}

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _load_yaml(path: Path) -> dict:
    """Load a YAML file and return its contents as a dict."""
    if not path.exists():
        raise FileNotFoundError(f"Prompt YAML file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping at top level in {path}")
    return data


def _validate_schema(data: dict, name: str, path: Path) -> None:
    """Validate that required top-level keys are present."""
    required = _REQUIRED_KEYS.get(name, [])
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"[{path.name}] Missing required keys: {missing}")

    version = data.get("version", "")
    if not _SEMVER_RE.match(str(version)):
        raise ValueError(f"[{path.name}] 'version' must be semver (e.g. '1.0.0'), got: {version!r}")


@cache
def load_template() -> dict:
    """
    Load and return the variable mapping template YAML.

    Returns
    -------
    dict with keys: version, template, language_instructions,
                    domain_specific_patterns, oncology_domains
    """
    data = _load_yaml(_TEMPLATE_PATH)
    _validate_schema(data, "template", _TEMPLATE_PATH)
    logger.debug(f"Loaded prompt template v{data['version']} from {_TEMPLATE_PATH.name}")
    return data


@cache
def load_rules() -> dict:
    """
    Load and return the SDTM rules YAML.

    Returns
    -------
    dict with keys: version, rules, core_rules, pattern_rules, domain_rules, domain_types
    """
    data = _load_yaml(_RULES_PATH)
    _validate_schema(data, "rules", _RULES_PATH)
    logger.debug(f"Loaded SDTM rules v{data['version']} from {_RULES_PATH.name}")
    return data


@cache
def load_examples() -> dict:
    """
    Load and return the pattern examples YAML.

    Returns
    -------
    dict with keys: version, examples, variable_pattern_keywords, domain_pattern_triggers
    """
    data = _load_yaml(_EXAMPLES_PATH)
    _validate_schema(data, "examples", _EXAMPLES_PATH)
    logger.debug(f"Loaded pattern examples v{data['version']} from {_EXAMPLES_PATH.name}")
    return data


def get_version(name: str) -> str:
    """
    Return the version string for the given prompt component.

    Parameters
    ----------
    name : str
        One of "template", "rules", "examples"
    """
    loaders = {"template": load_template, "rules": load_rules, "examples": load_examples}
    if name not in loaders:
        raise ValueError(f"Unknown prompt component: {name!r}. Choose from {list(loaders)}")
    return str(loaders[name]()["version"])


def clear_cache() -> None:
    """Clear the lru_cache for all loaders (useful in tests or after hot-reload)."""
    load_template.cache_clear()
    load_rules.cache_clear()
    load_examples.cache_clear()
