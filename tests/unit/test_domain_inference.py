"""Unit tests for :class:`DomainInferenceMixin`.

The mixin expects a host class to supply several instance attributes.
We build a minimal host and exercise the mixin methods directly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.config.domain_semantic_map import (
    _DOMAIN_KEYWORDS_SORTED,
    ANNOTATION_KEYWORD_DOMAIN_MAP,
    CHINESE_TABLE_DOMAIN_MAP,
)
from src.processors.domain_inference import DomainInferenceMixin


class _FakePromptGenerator:
    def __init__(self) -> None:
        self.domain_titles: Dict[str, Dict[str, str]] = {}


class _Host(DomainInferenceMixin):
    def __init__(
        self,
        *,
        semantic_keyword_domain_map: Optional[Dict[str, str]] = None,
    ) -> None:
        self.CHINESE_TABLE_DOMAIN_MAP = CHINESE_TABLE_DOMAIN_MAP
        self.semantic_keyword_domain_map = (
            semantic_keyword_domain_map
            if semantic_keyword_domain_map is not None
            else dict(ANNOTATION_KEYWORD_DOMAIN_MAP)
        )
        # longest-first for longest-match behaviour
        self._semantic_kw_sorted: List[Tuple[str, str]] = sorted(
            self.semantic_keyword_domain_map.items(),
            key=lambda kv: len(kv[0]),
            reverse=True,
        )
        self.domain_name_lookup = {
            "adverse event": "AE",
            "adverse events": "AE",
            "demographics": "DM",
            "vital signs": "VS",
        }
        self.debug = False
        self.domain_override_threshold = 0.85
        self.prompt_generator = _FakePromptGenerator()
        self.standard_catalog: Dict[str, Any] = {"domains": {}}


class TestMapTableToDomain:
    def test_empty_returns_none(self):
        host = _Host()
        assert host._map_table_to_domain("") is None
        assert host._map_table_to_domain(None) is None
        assert host._map_table_to_domain("   ") is None

    def test_direct_lookup_hit(self):
        host = _Host()
        assert host._map_table_to_domain("Adverse Events") == "AE"

    def test_case_insensitive(self):
        host = _Host()
        assert host._map_table_to_domain("DEMOGRAPHICS") == "DM"

    def test_normalized_no_spaces(self):
        host = _Host(semantic_keyword_domain_map={})
        host.domain_name_lookup["vitalsigns"] = "VS"
        assert host._map_table_to_domain("Vital Signs") == "VS"

    def test_semantic_keyword_match(self):
        host = _Host(
            semantic_keyword_domain_map={"reaction": "AE"},
        )
        host.domain_name_lookup = {}  # force keyword path
        assert host._map_table_to_domain("drug reaction log") == "AE"

    def test_no_match_returns_none(self):
        host = _Host(semantic_keyword_domain_map={})
        host.domain_name_lookup = {}
        # avoid fallback to _DOMAIN_KEYWORDS_SORTED finding anything
        assert host._map_table_to_domain("qqqzzz") is None


class TestMatchSemanticDomain:
    def test_empty(self):
        host = _Host()
        assert host._match_semantic_domain("") is None
        assert host._match_semantic_domain("   ") is None

    def test_longest_match_wins(self):
        host = _Host(
            semantic_keyword_domain_map={
                "event": "XX",
                "adverse event": "AE",
            }
        )
        # The sorted list places "adverse event" first (longest match)
        assert host._match_semantic_domain("serious adverse event") == "AE"

    def test_fallback_to_domain_keywords(self):
        host = _Host(semantic_keyword_domain_map={})
        # _DOMAIN_KEYWORDS_SORTED should contain at least one AE keyword
        found_any = any(
            host._match_semantic_domain(f"xx {kw} yy") == domain for kw, domain in _DOMAIN_KEYWORDS_SORTED[:5]
        )
        assert found_any

    def test_no_keyword_hit(self):
        host = _Host(semantic_keyword_domain_map={})
        assert host._match_semantic_domain("completely-unrelated-zzzqqq") is None


class TestNormalizeDomainCode:
    def test_valid_two_letter(self):
        assert DomainInferenceMixin._normalize_domain_code("AE") == "AE"
        assert DomainInferenceMixin._normalize_domain_code("dm") == "DM"

    def test_trims_long(self):
        assert DomainInferenceMixin._normalize_domain_code("AEXX") == "AE"

    def test_non_alpha_rejected(self):
        assert DomainInferenceMixin._normalize_domain_code("A1") is None
        assert DomainInferenceMixin._normalize_domain_code("12") is None

    def test_empty_or_non_string(self):
        assert DomainInferenceMixin._normalize_domain_code("") is None
        assert DomainInferenceMixin._normalize_domain_code(None) is None
        assert DomainInferenceMixin._normalize_domain_code(123) is None


class TestInferCandidateDomains:
    def test_annotation_table_hint_wins(self):
        host = _Host()
        out = host._infer_candidate_domains(
            variable_data={"annotation_table": "Adverse Events"},
            kb_context={"domain_hint": "DM"},
        )
        assert out == ["AE"]

    def test_kb_hint_used_when_no_annotation(self):
        host = _Host(semantic_keyword_domain_map={})
        host.domain_name_lookup = {}
        out = host._infer_candidate_domains(
            variable_data={},
            kb_context={"domain_hint": "LB"},
        )
        assert out == ["LB"]

    def test_no_hint_gathers_candidates(self):
        """When no single primary hint: gather from multiple sources.

        We must make ``_recommend_domain_from_annotation`` return None for
        the annotation_table so we don't short-circuit. We do that by
        using a string that doesn't match any keyword map, then stub out
        ``_recommend_domain_from_annotation`` via monkeypatch-like override.
        """
        host = _Host(semantic_keyword_domain_map={})
        host.domain_name_lookup = {
            "foo zzzqqq": "CM",
            "bar zzzqqq": "EX",
        }
        # Force the early primary_domain path to miss.
        host._recommend_domain_from_annotation = lambda _t: None  # type: ignore[method-assign]

        out = host._infer_candidate_domains(
            variable_data={
                "annotation_variable": "zz",
                "annotation_table": "foo zzzqqq",
                "metadata_table": "bar zzzqqq",
            },
            kb_context={"suggestions": [{"domain": "LB"}]},
        )
        assert "CM" in out
        assert "EX" in out
        assert len(out) <= 3

    def test_deduplicates_candidates(self):
        host = _Host(semantic_keyword_domain_map={})
        host.domain_name_lookup = {"x zzqq": "AE", "y zzqq": "AE"}
        host._recommend_domain_from_annotation = lambda _t: None  # type: ignore[method-assign]
        out = host._infer_candidate_domains(
            variable_data={
                "annotation_table": "x zzqq",
                "metadata_table": "y zzqq",
            },
            kb_context={"suggestions": [{"domain": "AE"}]},
        )
        assert out == ["AE"]


class TestRecommendDomainFromAnnotation:
    def test_empty(self):
        host = _Host()
        assert host._recommend_domain_from_annotation("") is None
        assert host._recommend_domain_from_annotation(None) is None

    def test_direct_table_map(self):
        host = _Host()
        assert host._recommend_domain_from_annotation("Adverse Events") == "AE"
