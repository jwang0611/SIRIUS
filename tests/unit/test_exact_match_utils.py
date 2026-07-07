"""Unit tests for :mod:`src.knowledge_base.exact_match_utils`."""

from __future__ import annotations

import math

import pandas as pd
from pandas.api.types import is_object_dtype, is_string_dtype

from src.knowledge_base.exact_match_utils import (
    EXACT_MATCH_COLUMNS,
    build_exact_match_key,
    normalize_exact_match_value,
    prepare_match_column,
)


class TestNormalizeExactMatchValue:
    def test_none_returns_empty(self):
        assert normalize_exact_match_value(None) == ""

    def test_nan_returns_empty(self):
        assert normalize_exact_match_value(math.nan) == ""

    def test_whitespace_only_returns_empty(self):
        assert normalize_exact_match_value("   ") == ""

    def test_strip_and_lower(self):
        assert normalize_exact_match_value("  AE Term  ") == "ae term"

    def test_numeric_coerced_to_string(self):
        assert normalize_exact_match_value(42) == "42"

    def test_pandas_na(self):
        assert normalize_exact_match_value(pd.NA) == ""


class TestBuildExactMatchKey:
    def test_all_fields_present(self):
        record = {
            "metadata_variable": "AETERM",
            "annotation_variable": "Adverse Event Term",
            "metadata_table": "AE",
            "annotation_table": "Adverse Events",
        }
        key = build_exact_match_key(record)
        assert key == "aeterm|||adverse event term|||ae|||adverse events"

    def test_missing_field_returns_empty(self):
        record = {
            "metadata_variable": "AETERM",
            "annotation_variable": "",
            "metadata_table": "AE",
            "annotation_table": "Adverse Events",
        }
        assert build_exact_match_key(record) == ""

    def test_whitespace_field_returns_empty(self):
        record = dict.fromkeys(EXACT_MATCH_COLUMNS, "   ")
        assert build_exact_match_key(record) == ""

    def test_key_is_stable_across_casing(self):
        a = dict.fromkeys(EXACT_MATCH_COLUMNS, "VAL")
        b = dict.fromkeys(EXACT_MATCH_COLUMNS, " val ")
        assert build_exact_match_key(a) == build_exact_match_key(b)

    def test_custom_columns(self):
        record = {"x": "A", "y": "B"}
        assert build_exact_match_key(record, columns=["x", "y"]) == "a|||b"


class TestPrepareMatchColumn:
    def test_none_series_returns_empty(self):
        out = prepare_match_column(None)
        assert out.empty
        # pandas 2.2+ uses StringDtype for Series(dtype=str); older builds used object
        assert is_object_dtype(out.dtype) or is_string_dtype(out)

    def test_strips_strings(self):
        s = pd.Series(["  foo  ", "bar"])
        out = prepare_match_column(s)
        assert out.tolist() == ["foo", "bar"]

    def test_fills_nan_with_empty(self):
        s = pd.Series(["a", None, pd.NA])
        out = prepare_match_column(s)
        assert out.tolist() == ["a", "", ""]

    def test_categorical_handled(self):
        s = pd.Series(pd.Categorical(["x", "y", "x"]))
        out = prepare_match_column(s)
        assert out.tolist() == ["x", "y", "x"]

    def test_numeric_coerced_to_string(self):
        s = pd.Series([1, 2, 3])
        out = prepare_match_column(s)
        assert out.tolist() == ["1", "2", "3"]
