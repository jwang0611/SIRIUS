"""Record assembly invariants for aCRF extraction (pure, no PDF backend)."""

from __future__ import annotations

from src.processors.acrf.models import AcrfConfig
from src.processors.acrf.records import assemble_records, records_to_json_rows


def test_metadata_variable_unique_within_form():
    # A repeated field label within one form must not collapse to one key.
    recs = assemble_records([("人口学", ["性别", "性别", "年龄"])], AcrfConfig())

    assert [r.metadata_variable for r in recs] == ["性别", "性别_2", "年龄"]
    assert all(r.metadata_table for r in recs)  # non-empty (else pipeline drops it)
    keys = [(r.metadata_table, r.metadata_variable) for r in recs]
    assert len(keys) == len(set(keys))  # unique within the metadata_table group
    assert [r.num for r in recs] == [1, 2, 3]  # 1-based, monotonic


def test_duplicate_form_names_stay_unique_across_forms():
    # Two bookmarks sharing a title merge into one metadata_table group in the
    # pipeline, so their variables must still be globally unique.
    recs = assemble_records([("访视", ["日期"]), ("访视", ["日期"])], AcrfConfig())

    keys = [(r.metadata_table, r.metadata_variable) for r in recs]
    assert keys == [("访视", "日期"), ("访视", "日期_2")]
    assert len(keys) == len(set(keys))


def test_synthetic_mode_emits_ascii_codes():
    cfg = AcrfConfig(metadata_variable_mode="synthetic")
    recs = assemble_records([("受试者信息", ["x", "y"]), ("生命体征", ["z"])], cfg)

    assert [r.metadata_variable for r in recs] == ["F01_001", "F01_002", "F02_001"]
    # annotation side still carries the human labels
    assert [r.annotation_variable for r in recs] == ["x", "y", "z"]


def test_empty_and_blank_labels_are_skipped():
    recs = assemble_records([("F", ["", "  ", "a"])], AcrfConfig())

    assert [r.annotation_variable for r in recs] == ["a"]
    assert recs[0].num == 1


def test_empty_form_name_gets_nonempty_placeholder():
    recs = assemble_records([("", ["a"])], AcrfConfig())

    assert recs[0].metadata_table  # never empty (pipeline requires it)
    assert recs[0].metadata_table.startswith("FORM_")


def test_json_rows_have_exact_four_key_schema():
    recs = assemble_records([("F", ["a", "b"])], AcrfConfig())
    rows = records_to_json_rows(recs)

    assert all(
        set(r) == {"metadata_table", "metadata_variable", "annotation_table", "annotation_variable"} for r in rows
    )
    assert len(rows) == 2
