"""Unit tests for :class:`PostprocessMixin`.

The mixin relies on module-level ``_STANDARD_VARS_BY_DOMAIN`` and on a
few host attributes (``debug``, ``_deterministic_validator``, plus
``_recommend_domain_from_annotation`` / ``_map_table_to_domain`` for
the fallback builder). We stitch together a minimal host that provides
only what each test needs.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.processors.postprocess import PostprocessMixin


class _Host(PostprocessMixin):
    def __init__(self) -> None:
        self.debug = False
        self.domain_override_threshold = 0.85

    def _recommend_domain_from_annotation(self, annotation_table: Any):
        return None

    def _map_table_to_domain(self, table_name: Any):  # pragma: no cover
        return None


class TestInferTestcd:
    def test_returns_mapped_testcd_by_keyword(self):
        host = _Host()
        assert host._infer_testcd("收缩压", "", "VS") == "SYSBP"
        assert host._infer_testcd("Systolic BP", "sbp", "VS") == "SYSBP"

    def test_prefers_first_matching_keyword(self):
        host = _Host()
        assert host._infer_testcd("pulse rate", "", "VS") == "PULSE"

    def test_fa_domain_returns_variable_upper_truncated(self):
        host = _Host()
        assert host._infer_testcd("MyLongVarname", "", "FA") == "MYLONGVA"

    def test_strips_orres_suffix_when_extracting(self):
        host = _Host()
        # var_upper[2:] => "MYORRES", strip ORRES => "MY"
        assert host._infer_testcd("AEMYORRES", "", "AE") == "MY"

    def test_non_fa_uses_tail_fallback(self):
        host = _Host()
        # "LBGLU" -> tail "GLU" -> returns "GLU"
        assert host._infer_testcd("LBGLU", "", "LB") == "GLU"

    def test_empty_input_returns_empty(self):
        host = _Host()
        assert host._infer_testcd("", "", "") == ""


class TestFindParentFatestcd:
    def test_finds_parent_testcd(self):
        host = _Host()
        domain_recs = [
            {
                "variable_name": "DIAGNOSIS",
                "domain": "FA",
                "sdtm_variable_type": "standard",
                "testcd": "DIAG",
            }
        ]
        result = host._find_parent_fatestcd("DIAGNOSISOTHER", domain_recs, [])
        assert result == "DIAG"

    def test_infers_testcd_when_parent_missing(self):
        host = _Host()
        domain_recs = [
            {
                "variable_name": "MYVAR",
                "domain": "FA",
                "sdtm_variable_type": "standard",
                "testcd": "",
            }
        ]
        orig = [{"metadata_variable": "MYVAR", "annotation_variable": "ann"}]
        # FA + MYVAR -> upper[:8] = "MYVAR"
        result = host._find_parent_fatestcd("MYVAROTHER", domain_recs, orig)
        assert result == "MYVAR"

    def test_empty_supp_name_returns_empty(self):
        host = _Host()
        assert host._find_parent_fatestcd("", [], []) == ""

    def test_no_match_returns_empty(self):
        host = _Host()
        assert host._find_parent_fatestcd("XOTHER", [], []) == ""


class TestBuildFallbackRecommendations:
    def test_uses_target_domain(self):
        host = _Host()
        recs = host._build_fallback_recommendations(
            table_name="demo",
            variable_data={"metadata_variable": "age", "annotation_table": "DM"},
            target_domain="DM",
            reason="KB miss",
        )
        assert len(recs) == 1
        assert recs[0]["domain"] == "DM"
        assert recs[0]["sdtm_variable"] == "DM_AGE_PENDING"
        assert recs[0]["source"] == "FALLBACK"
        assert recs[0]["fallback_reason"] == "KB miss"

    def test_defaults_to_unmapped(self):
        host = _Host()
        recs = host._build_fallback_recommendations(
            table_name="tbl",
            variable_data={"metadata_variable": "v"},
            target_domain=None,
            reason="no KB hit",
        )
        assert recs[0]["domain"] == "UNMAPPED"
        assert recs[0]["sdtm_variable"] == "UNMAPPED_V_PENDING"


class TestNormalizeDomainRecsBasic:
    def test_enforces_target_domain(self):
        host = _Host()
        recs = [
            {"domain": "AE", "sdtm_variable": "AETERM", "score": 0.9, "sdtm_variable_type": "standard"},
            {"domain": "DM", "sdtm_variable": "AGE", "score": 0.8, "sdtm_variable_type": "standard"},
        ]
        out = host._normalize_domain_recs(
            table_name="t",
            variable_name="aeterm",
            domain_recs=recs,
            target_domain="AE",
            enforce_domain=True,
        )
        assert all(r["domain"] == "AE" for r in out)

    def test_returns_empty_when_no_domain_match_with_enforce(self):
        host = _Host()
        recs = [
            {"domain": "DM", "sdtm_variable": "AGE", "score": 0.8, "sdtm_variable_type": "standard"},
        ]
        out = host._normalize_domain_recs(
            table_name="t",
            variable_name="v",
            domain_recs=recs,
            target_domain="AE",
            enforce_domain=True,
        )
        assert out == []

    def test_empty_input_returns_empty(self):
        host = _Host()
        out = host._normalize_domain_recs(
            table_name="t",
            variable_name="v",
            domain_recs=[],
            target_domain=None,
        )
        assert out == []

    def test_comment_variable_gets_supp(self):
        host = _Host()
        recs = [
            {
                "domain": "AE",
                "sdtm_variable": "AETERM",
                "score": 0.9,
                "sdtm_variable_type": "standard",
            }
        ]
        out = host._normalize_domain_recs(
            table_name="t",
            variable_name="备注 comment",
            domain_recs=recs,
            target_domain="AE",
            enforce_domain=True,
        )
        assert out
        assert out[0]["sdtm_variable_type"] == "supp"

    def test_when_clause_decomposed_into_testcd(self):
        host = _Host()
        recs = [
            {
                "domain": "FA",
                "sdtm_variable": "FAORRES when FATESTCD=THDIAG",
                "score": 0.9,
                "sdtm_variable_type": "standard",
            }
        ]
        out = host._normalize_domain_recs(
            table_name="t",
            variable_name="diag",
            domain_recs=recs,
            target_domain="FA",
            enforce_domain=True,
        )
        assert out
        assert out[0]["testcd"] == "THDIAG"

    def test_general_when_clause_survives_production_postprocess(self):
        host = _Host()
        recs = [
            {
                "domain": "EC",
                "sdtm_variable": "ECDOSE when ECMOOD=ADMINISTERED",
                "score": 0.9,
                "sdtm_variable_type": "standard",
                "source": "LLM",
            }
        ]

        out = host._normalize_domain_recs(
            table_name="t",
            variable_name="dose",
            domain_recs=recs,
            target_domain="EC",
            enforce_domain=True,
        )

        assert out[0]["sdtm_variable"] == "ECDOSE"
        assert out[0]["conditions"] == [{"variable": "ECMOOD", "value": "ADMINISTERED"}]

    def test_multi_domain_resolution(self):
        host = _Host()
        recs = [
            {
                "domain": "AE|DM",
                "sdtm_variable": "AETERM|AGE",
                "score": 0.8,
                "sdtm_variable_type": "standard",
                "source": "LLM",
            }
        ]
        out = host._normalize_domain_recs(
            table_name="t",
            variable_name="v",
            domain_recs=recs,
            target_domain=None,
            enforce_domain=False,
        )
        assert out
        # First valid domain picked
        assert out[0]["domain"] in {"AE", "DM"}

    def test_deduplicates_identical_entries(self):
        host = _Host()
        base = {"domain": "AE", "sdtm_variable": "AETERM", "sdtm_variable_type": "standard"}
        recs = [
            {**base, "score": 0.8},
            {**base, "score": 0.9},
            {**base, "score": 0.7},
        ]
        out = host._normalize_domain_recs(
            table_name="t",
            variable_name="v",
            domain_recs=recs,
            target_domain="AE",
            enforce_domain=True,
        )
        assert len(out) == 1
        assert out[0]["score"] == pytest.approx(0.9)
