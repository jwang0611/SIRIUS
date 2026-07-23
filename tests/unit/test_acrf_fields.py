"""Deterministic field heuristics + boilerplate detection (pure, no PDF backend)."""

from __future__ import annotations

from src.processors.acrf.fields import (
    _looks_like_field,
    clean_label,
    extract_field_candidates,
    validate_field_set,
)
from src.processors.acrf.models import AcrfConfig, LineBox
from src.processors.acrf.text import detect_boilerplate


def _lb(text: str, page: int = 0, top: float = 100.0, size: float = 10.0) -> LineBox:
    return LineBox(text=text, page=page, x0=72.0, top=top, x1=300.0, bottom=top + 12, size=size)


def test_clean_label_cuts_inline_options():
    assert clean_label("性别 ○ 男 ○ 女") == "性别"
    assert clean_label("是否检查？ ○ 是 ○ 否") == "是否检查？"


def test_clean_label_strips_enumeration_and_row_numbers():
    assert clean_label("1. 收缩压") == "收缩压"
    assert clean_label("（1）既往病史") == "既往病史"
    assert clean_label("1 收缩压(mmHg)") == "收缩压(mmHg)"
    # A number fused to text (no space) must not be clipped.
    assert clean_label("12导联心电图") == "12导联心电图"


def test_looks_like_field_keeps_labels_drops_noise():
    assert _looks_like_field("性别")
    assert _looks_like_field("是否进行生命体征检查？")  # question labels are valid
    assert not _looks_like_field("是")  # standalone option answer
    assert not _looks_like_field("正常")
    assert not _looks_like_field("筛选期")  # visit/timing header
    assert not _looks_like_field("Cycle 1")
    assert not _looks_like_field("# 检查项目 检查结果")  # table scaffolding


def test_detect_boilerplate_flags_stable_edge_band_lines_only():
    heights = dict.fromkeys(range(4), 842.0)
    # A page banner at a fixed top inside the header edge band on every page.
    boxes = {p: [_lb("QL1706-307", page=p, top=20.0), _lb(f"unique text {p}", page=p, top=200.0)] for p in range(4)}
    bp = detect_boilerplate(boxes, heights)

    assert "QL1706-307" in bp
    assert not any("unique text" in b for b in bp)


def test_detect_boilerplate_ignores_repeated_mid_page_fields():
    # A real question that recurs mid-page across forms must not be treated as
    # boilerplate just because it is frequent.
    heights = dict.fromkeys(range(4), 842.0)
    boxes = {p: [_lb("Assessment Date", page=p, top=300.0)] for p in range(4)}

    assert detect_boilerplate(boxes, heights) == set()


def test_extract_candidates_strips_boilerplate_and_options_and_title():
    boxes = [
        _lb("受试者信息", top=90, size=12.0),  # form title (largest font) → dropped
        _lb("研究代码 QL1706-307", top=120),  # inline study-code banner stripped
        _lb("性别 ○ 男 ○ 女", top=160),  # option tail cut
        _lb("QL1706-307", top=800),  # footer band + boilerplate → dropped
    ]
    cands = extract_field_candidates(boxes, {"QL1706-307"}, AcrfConfig(), page_height=842.0)

    assert "研究代码" in cands
    assert "性别" in cands
    assert "受试者信息" not in cands
    assert not any("QL1706" in c for c in cands)


def test_extract_candidates_dedupes_within_form():
    boxes = [_lb("出生日期", top=120), _lb("出生日期", page=1, top=120)]
    assert extract_field_candidates(boxes, set(), AcrfConfig(), page_height=842.0) == ["出生日期"]


def test_validate_field_set_flags_empty_and_truncates_over_max():
    cfg = AcrfConfig(min_fields=1, max_fields=2)

    fields, warns = validate_field_set([], cfg)
    assert "no fields extracted" in warns[0]

    fields, warns = validate_field_set(["a", "b", "c"], cfg)
    assert fields == ["a", "b"]
    assert any("over-extracted" in w for w in warns)
