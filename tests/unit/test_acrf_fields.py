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


def _lb(
    text: str,
    page: int = 0,
    top: float = 100.0,
    size: float = 10.0,
    x0: float = 72.0,
    x1: float = 300.0,
) -> LineBox:
    return LineBox(text=text, page=page, x0=x0, top=top, x1=x1, bottom=top + 12, size=size)


def test_clean_label_cuts_inline_options():
    assert clean_label("性别 ○ 男 ○ 女") == "性别"
    assert clean_label("是否检查？ ○ 是 ○ 否") == "是否检查？"
    assert clean_label("What was the outcome? Recovered/Resolved") == "What was the outcome?"


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


def test_visit_headers_match_whole_line_without_dropping_follow_up_questions():
    assert _looks_like_field("是否进行生存随访？")
    assert not _looks_like_field("随访")
    assert not _looks_like_field("Visit 1")
    assert not _looks_like_field("Cycle 2")


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


def test_uniform_font_page_keeps_body_fields_when_form_name_is_known():
    boxes = [
        _lb("QLC5508-301 入组确认 ENROLL", top=30, size=12.0),
        _lb("入组确认", top=110, size=12.0),
        _lb("受试者是否入组？ □ 是 □ 否", top=138, size=12.0),
        _lb("入组确认日期：", top=158, size=12.0),
    ]

    fields = extract_field_candidates(
        boxes,
        set(),
        AcrfConfig(),
        page_height=842.0,
        form_name="入组确认",
    )

    assert fields == ["受试者是否入组？", "入组确认日期"]


def test_extract_candidates_merges_tightly_wrapped_cjk_label_only():
    boxes = [
        _lb("治疗原因为不良事件，请填写不", top=100, size=10.0),
        _lb("___________", top=112.5, size=10.0, x0=260, x1=320),
        _lb("良事件", top=115, size=10.0),
        _lb("治疗开始日期", top=150, size=10.0),
    ]

    fields = extract_field_candidates(boxes, set(), AcrfConfig())

    assert fields == ["治疗原因为不良事件，请填写不良事件", "治疗开始日期"]


def test_extract_candidates_drops_detected_right_answer_column():
    boxes = [
        _lb("Ongoing?", top=100, x0=90, x1=130),
        _lb("No", top=104, x0=398, x1=410),
        _lb("Yes", top=128, x0=398, x1=414),
        _lb("Outcome", top=160, x0=90, x1=130),
        _lb("Fatal", top=164, x0=398, x1=420),
        _lb("Recovered/Resolved", top=188, x0=398, x1=480),
        _lb("Death", top=230, x0=109, x1=135),
        _lb("Hospitalization", top=254, x0=109, x1=175),
    ]

    fields = extract_field_candidates(boxes, set(), AcrfConfig())

    assert fields == ["Ongoing?", "Outcome", "Death", "Hospitalization"]


def test_answer_column_anchor_can_follow_its_aligned_first_option():
    boxes = [
        _lb("Event Outcome", top=100, x0=90, x1=150),
        _lb("Recovered/Resolved", top=104, x0=398, x1=480),
        _lb("Fatal", top=128, x0=398, x1=420),
    ]

    fields = extract_field_candidates(boxes, set(), AcrfConfig())

    assert fields == ["Event Outcome"]


def test_long_question_is_valid_label_and_anchors_right_answer_column():
    question = "Is the medical history disease/condition or event ongoing?"
    boxes = [
        _lb(question, top=100, x0=90, x1=330),
        _lb("Yes", top=104, x0=398, x1=414),
        _lb("No", top=128, x0=398, x1=410),
    ]

    fields = extract_field_candidates(boxes, set(), AcrfConfig())

    assert fields == [question]


def test_multiple_option_anchors_detect_right_column_without_row_alignment():
    boxes = [
        _lb("Event Outcome", top=100, x0=90, x1=150),
        _lb("Not Recovered/Not Resolved", top=150, x0=398, x1=500),
        _lb("Fatal", top=174, x0=398, x1=420),
        _lb("Unknown", top=198, x0=398, x1=440),
    ]

    fields = extract_field_candidates(boxes, set(), AcrfConfig())

    assert fields == ["Event Outcome"]


def test_validate_field_set_flags_empty_and_truncates_over_max():
    cfg = AcrfConfig(min_fields=1, max_fields=2)

    fields, warns = validate_field_set([], cfg)
    assert "no fields extracted" in warns[0]

    fields, warns = validate_field_set(["a", "b", "c"], cfg)
    assert fields == ["a", "b"]
    assert any("over-extracted" in w for w in warns)
