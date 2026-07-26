"""Unit tests for ``src.processors.normalizer``."""

from __future__ import annotations

import pytest

from src.processors.normalizer import (
    COMMENT_KEYWORDS,
    QVARS,
    STANDARD_SUFFIXES,
    classify_variable_type,
    decompose_when_clause,
    dedupe_by_key,
    filter_recs_by_domain,
    is_comment_like_variable,
    is_standard_variable_name,
    resolve_multi_domain,
    to_cleaned_dict,
)


class TestIsStandardVariableName:
    @pytest.mark.parametrize(
        "name",
        ["AEOCCUR", "DMDECOD", "VSORRES", "EXDOSE", "LBSTRESC"],
    )
    def test_valid_standard_names(self, name):
        assert is_standard_variable_name(name) is True

    @pytest.mark.parametrize(
        "name",
        ["", "AE", "XY", None, "123", "AEQ"],
    )
    def test_rejects_short_or_empty(self, name):
        assert is_standard_variable_name(name) is False

    def test_case_insensitive(self):
        assert is_standard_variable_name("aeoccur") is True

    def test_non_alpha_prefix(self):
        assert is_standard_variable_name("1XOCCUR") is False

    def test_suffix_endswith_match(self):
        assert is_standard_variable_name("AEFOOTERM") is True


class TestIsCommentLikeVariable:
    @pytest.mark.parametrize(
        "name",
        ["备注", "Comment", "Please specify other", "notes", "其他说明", "remark field"],
    )
    def test_hits(self, name):
        assert is_comment_like_variable(name) is True

    @pytest.mark.parametrize("name", ["SUBJID", "AETERM", "", None])
    def test_misses(self, name):
        assert is_comment_like_variable(name or "") is False


class TestFilterRecsByDomain:
    def test_empty_target_returns_all(self, sample_domain_rec):
        recs = [sample_domain_rec]
        assert filter_recs_by_domain(recs, None) == recs
        assert filter_recs_by_domain(recs, "") == recs

    def test_exact_match(self, sample_domain_rec):
        assert filter_recs_by_domain([sample_domain_rec], "AE") == [sample_domain_rec]

    def test_case_insensitive(self, sample_domain_rec):
        assert filter_recs_by_domain([sample_domain_rec], "ae") == [sample_domain_rec]

    def test_rejects_mismatched(self, sample_domain_rec):
        assert filter_recs_by_domain([sample_domain_rec], "DM") == []

    def test_supp_domain_aliased_to_parent(self):
        rec = {"domain": "SUPPAE"}
        assert filter_recs_by_domain([rec], "AE") == [rec]
        assert filter_recs_by_domain([rec], "SUPPAE") == [rec]

    def test_multi_token_domain(self):
        rec = {"domain": "AE|FA"}
        assert filter_recs_by_domain([rec], "AE") == [rec]
        assert filter_recs_by_domain([rec], "FA") == [rec]
        assert filter_recs_by_domain([rec], "DM") == []


class TestDecomposeWhenClause:
    def test_no_when_returns_copy(self):
        rec = {"sdtm_variable": "AETERM"}
        out = decompose_when_clause(rec)
        assert out == rec
        assert out is not rec

    def test_extracts_testcd(self):
        rec = {"sdtm_variable": "FAORRES when FATESTCD=THDIAG"}
        out = decompose_when_clause(rec)
        assert out["sdtm_variable"] == "FAORRES"
        assert out["testcd"] == "THDIAG"

    def test_extracts_qnam(self):
        rec = {"sdtm_variable": "QVAL when QNAM=AESEV"}
        out = decompose_when_clause(rec)
        assert out["sdtm_variable"] == "QVAL"
        assert out["supp_variable"] == "AESEV"

    def test_extracts_general_condition_without_polluting_variable(self):
        rec = {"sdtm_variable": "ECDOSE when ECMOOD=ADMINISTERED"}

        out = decompose_when_clause(rec)

        assert out["sdtm_variable"] == "ECDOSE"
        assert out["conditions"] == [{"variable": "ECMOOD", "value": "ADMINISTERED"}]

    def test_preserves_structured_conditions_when_decomposing_reserved_fields(self):
        rec = {
            "sdtm_variable": "QVAL when QNAM=EXREAS when ECMOOD=NOT DONE",
            "conditions": [{"variable": "EPOCH", "value": "TREATMENT"}],
        }

        out = decompose_when_clause(rec)

        assert out["supp_variable"] == "EXREAS"
        assert out["conditions"] == [
            {"variable": "EPOCH", "value": "TREATMENT"},
            {"variable": "ECMOOD", "value": "NOT DONE"},
        ]

    def test_preserves_existing_testcd(self):
        rec = {"sdtm_variable": "FAORRES when FATESTCD=NEW", "testcd": "ORIG"}
        out = decompose_when_clause(rec)
        assert out["testcd"] == "ORIG"

    def test_non_string_sdtm_variable(self):
        rec = {"sdtm_variable": None}
        assert decompose_when_clause(rec) == rec


class TestResolveMultiDomain:
    def _is_valid(self, domain):
        return domain in {"AE", "FA", "DM", "VS", "LB"}

    def test_single_domain_untouched(self):
        rec = {"domain": "AE", "sdtm_variable": "AETERM"}
        assert resolve_multi_domain(rec, self._is_valid) == rec

    def test_picks_first_valid_domain(self):
        rec = {"domain": "XX|AE", "sdtm_variable": "AETERM"}
        out = resolve_multi_domain(rec, self._is_valid)
        assert out["domain"] == "AE"
        assert out["original_multi_domain"] == "XX|AE"

    def test_aligns_variable_to_domain(self):
        rec = {"domain": "AE|FA", "sdtm_variable": "AETERM|FAORRES"}
        out = resolve_multi_domain(rec, self._is_valid)
        assert out["domain"] == "AE"
        assert out["sdtm_variable"] == "AETERM"

    def test_picks_second_when_first_invalid(self):
        rec = {"domain": "XX|FA", "sdtm_variable": "ZZZ|FAORRES"}
        out = resolve_multi_domain(rec, self._is_valid)
        assert out["domain"] == "FA"
        assert out["sdtm_variable"] == "FAORRES"

    def test_pipe_slash_semicolon_separators(self):
        for sep in ["|", "/", ";"]:
            rec = {"domain": f"XX{sep}AE"}
            out = resolve_multi_domain(rec, self._is_valid)
            assert out["domain"] == "AE"


class TestClassifyVariableType:
    def test_explicit_standard(self):
        assert classify_variable_type({"sdtm_variable_type": "standard"}, "AETERM") == "standard"

    def test_explicit_supp(self):
        assert classify_variable_type({"sdtm_variable_type": "supp"}, "FOO") == "supp"

    def test_comment_like_promoted_to_supp(self):
        rec = {"sdtm_variable_type": "standard"}
        assert classify_variable_type(rec, "备注") == "supp"

    def test_qvar_forces_supp(self):
        rec = {"sdtm_variable_type": "standard", "sdtm_variable": "QVAL"}
        assert classify_variable_type(rec, "XX") == "supp"

    def test_suppxx_domain_forces_supp(self):
        rec = {"sdtm_variable_type": "standard", "domain": "SUPPAE"}
        assert classify_variable_type(rec, "XX") == "supp"

    def test_unknown_type_defaults_to_standard(self):
        assert classify_variable_type({}, "AETERM") == "standard"

    def test_unknown_type_with_supp_variable(self):
        assert classify_variable_type({"supp_variable": "AESEV"}, "XX") == "supp"


class TestDedupeByKey:
    def test_empty(self):
        assert dedupe_by_key([]) == []

    def test_keeps_highest_score(self):
        recs = [
            {"domain": "AE", "sdtm_variable": "AETERM", "score": 0.7},
            {"domain": "AE", "sdtm_variable": "AETERM", "score": 0.9},
            {"domain": "AE", "sdtm_variable": "AETERM", "score": 0.5},
        ]
        out = dedupe_by_key(recs)
        assert len(out) == 1
        assert out[0]["score"] == 0.9

    def test_distinct_testcd_kept(self):
        recs = [
            {"domain": "FA", "sdtm_variable": "FAORRES", "testcd": "A", "score": 0.9},
            {"domain": "FA", "sdtm_variable": "FAORRES", "testcd": "B", "score": 0.8},
        ]
        assert len(dedupe_by_key(recs)) == 2

    def test_distinct_supp_variable(self):
        recs = [
            {"domain": "SUPPAE", "sdtm_variable": "QVAL", "supp_variable": "X", "score": 0.9},
            {"domain": "SUPPAE", "sdtm_variable": "QVAL", "supp_variable": "Y", "score": 0.9},
        ]
        assert len(dedupe_by_key(recs)) == 2

    def test_case_insensitive_key(self):
        recs = [
            {"domain": "ae", "sdtm_variable": "aeterm", "score": 0.8},
            {"domain": "AE", "sdtm_variable": "AETERM", "score": 0.9},
        ]
        out = dedupe_by_key(recs)
        assert len(out) == 1
        assert out[0]["score"] == 0.9


class TestToCleanedDict:
    def test_standard_shape(self, sample_domain_rec):
        out = to_cleaned_dict(sample_domain_rec, variable_name="不良事件名称")
        assert out["domain"] == "AE"
        assert out["sdtm_variable"] == "AETERM"
        assert out["variable_name"] == "不良事件名称"
        assert "supp_dataset" not in out
        assert "supp_variable" not in out

    def test_supp_includes_extra_fields(self):
        rec = {
            "domain": "SUPPAE",
            "sdtm_variable": "QVAL",
            "sdtm_variable_type": "supp",
            "supp_dataset": "SUPPAE",
            "supp_variable": "AESEV",
            "score": 0.88,
        }
        out = to_cleaned_dict(rec, variable_name="严重性")
        assert out["supp_dataset"] == "SUPPAE"
        assert out["supp_variable"] == "AESEV"

    def test_missing_score_defaults(self):
        rec = {"domain": "AE", "sdtm_variable": "AETERM"}
        out = to_cleaned_dict(rec, variable_name="x")
        assert out["score"] == 0.9


def test_frozen_constants_are_lookup_sets():
    assert isinstance(QVARS, frozenset)
    assert "QVAL" in QVARS
    assert isinstance(STANDARD_SUFFIXES, frozenset)
    assert "TERM" in STANDARD_SUFFIXES
    assert isinstance(COMMENT_KEYWORDS, tuple)
