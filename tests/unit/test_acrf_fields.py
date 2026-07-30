"""Deterministic field heuristics + boilerplate detection (pure, no PDF backend)."""

from __future__ import annotations

from src.processors.acrf.fields import (
    _looks_like_field,
    annotation_labels,
    clean_label,
    detect_grids,
    extract_field_candidates,
    extract_form_sections,
    sub_table_line_ids,
    validate_field_set,
)
from src.processors.acrf.models import AcrfConfig, LineBox, WordBox
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


def _row(
    words: list[tuple[str, float]],
    page: int = 0,
    top: float = 100.0,
    size: float = 10.0,
    width: float = 40.0,
) -> LineBox:
    """A line built from positioned words, as the PDF backend produces them."""
    boxes = tuple(WordBox(text=t, x0=x, x1=x + width) for t, x in words)
    return LineBox(
        text=" ".join(t for t, _ in words),
        page=page,
        x0=boxes[0].x0,
        top=top,
        x1=boxes[-1].x1,
        bottom=top + 12,
        size=size,
        words=boxes,
    )


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


def test_clean_label_cuts_entry_box_answer_and_its_unit():
    assert clean_label("检查日期 |_|_|_|_|/|_|_|/|_|_|") == "检查日期"
    assert clean_label("身高(cm) |_|_|_|.|_| cm") == "身高(cm)"
    assert clean_label("年龄 |_|_|") == "年龄"
    # A single bar is a separator inside a label, not an entry box.
    assert clean_label("诊断|疾病名称") == "诊断|疾病名称"


def test_wrapped_label_merges_across_interleaved_answer_line():
    # The option row for the question is emitted between the two label halves.
    boxes = [
        _lb("是否因方案变更重新签署知情同", top=143.4, x0=109.5),
        _lb("○ 是 ○ 否", top=152.4, x0=259.9),
        _lb("意书?", top=156.4, x0=109.5),
    ]
    assert extract_field_candidates(boxes, set(), AcrfConfig()) == ["是否因方案变更重新签署知情同意书?"]


def test_wrapped_label_merges_when_options_are_inline_on_the_first_half():
    boxes = [
        _lb("受试者是否存在任何既往史（除小细胞 ○ 是 ○ 否", top=138.0, x0=43.5),
        _lb("肺癌外）？", top=150.0, x0=40.0),
    ]
    assert extract_field_candidates(boxes, set(), AcrfConfig()) == ["受试者是否存在任何既往史（除小细胞肺癌外）？"]


def test_stacked_labels_with_their_own_answers_are_not_merged_as_wraps():
    # Lab forms stack one label per line, tightly enough to look like a wrap.
    boxes = [
        _lb("结果 |_|_|_|_|.|_|", top=244.0, x0=42.0, size=12.0),
        _lb("临床评估 |__|", top=260.0, x0=42.0, size=12.0),
        _lb("单位 ____", top=276.0, x0=42.0, size=12.0),
        _lb("下限 |_|_|.|_|", top=292.0, x0=42.0, size=12.0),
    ]
    assert extract_field_candidates(boxes, set(), AcrfConfig()) == ["结果", "临床评估", "单位", "下限"]


def test_grid_header_splits_into_columns_and_data_rows_are_dropped():
    boxes = [
        _row([("No.", 40.0), ("诊断/疾病名称", 66.0), ("开始日期", 189.0), ("结束日期", 300.0)], top=170.0, size=12.0),
        _row([("1", 40.0), ("糖尿病", 66.0)], top=190.0, size=12.0),
        _row([("2", 40.0), ("高血压", 66.0)], top=206.0, size=12.0),
    ]
    assert extract_field_candidates(boxes, set(), AcrfConfig()) == ["诊断/疾病名称", "开始日期", "结束日期"]


def test_grid_column_headers_stitch_wrapped_continuations_above_and_below():
    boxes = [
        _row([("异常有临床意义", 411.0)], top=168.4),
        _row([("#", 117.0), ("检查项目", 144.0), ("检查结果", 233.0)], top=173.4),
        _row([("，请描述", 411.0)], top=178.4),
        _row([("1", 117.0), ("收缩压(mmHg)", 144.0)], top=232.4),
    ]
    assert extract_field_candidates(boxes, set(), AcrfConfig()) == [
        "检查项目",
        "检查结果",
        "异常有临床意义，请描述",
    ]


def test_grid_heading_becomes_its_own_section():
    boxes = [
        _row([("检查日期", 109.5)], top=112.4),
        _row([("生命体征明细", 109.5)], top=143.4),
        _row([("#", 117.0), ("检查项目", 144.0), ("检查结果", 233.0)], top=173.4),
        _row([("1", 117.0), ("收缩压", 144.0)], top=200.4),
    ]
    sections = extract_form_sections(boxes, set(), AcrfConfig())
    assert [(s.name, s.fields) for s in sections] == [
        (None, ["检查日期"]),
        ("生命体征明细", ["检查项目", "检查结果"]),
    ]


def test_question_or_label_with_value_above_a_grid_is_not_a_heading():
    # "单位 mg" is a label and its unit; the "若选择…" line is the question the
    # table answers. Neither names the table.
    for above in (
        _row([("单位", 43.5), ("mg", 120.0)], top=180.0, size=12.0),
        _row([("若选择“否”，请选择不符合的标准", 43.5)], top=180.0, size=12.0),
    ):
        boxes = [
            above,
            _row([("No.", 40.0), ("入选标准", 66.0), ("NO#", 200.0)], top=210.0, size=12.0),
            _row([("1", 40.0), ("标准一", 66.0)], top=226.0, size=12.0),
        ]
        sections = extract_form_sections(boxes, set(), AcrfConfig())
        assert [s.name for s in sections] == [None], above.text
        assert clean_label(above.text) in sections[0].fields


def test_detect_grids_reports_columns_and_owned_lines():
    header = _row([("No.", 40.0), ("检查项", 66.0), ("结果", 189.0)], top=170.0, size=12.0)
    data = _row([("1", 40.0), ("收缩压", 66.0)], top=190.0, size=12.0)
    grids = detect_grids([header, data])
    assert len(grids) == 1
    assert grids[0].columns == ["检查项", "结果"]
    assert grids[0].consumed == frozenset({id(header), id(data)})


def test_annotation_lines_yield_labels_but_are_never_fields_themselves():
    boxes = [
        _lb("VSCLSIG(临床意义判断):1-正常, 2-异常", top=338.0, size=12.0),
        _lb("YN(是否进行给药？|中断后是否再次输液？):1-是, 2-否", top=354.0, size=12.0),
        _lb("VSTEST(检查项) : L1-收缩压, L2-舒张压", top=370.0, size=12.0),
    ]
    assert annotation_labels(boxes) == [
        "临床意义判断",
        "是否进行给药？",
        "中断后是否再次输液？",
        "检查项",
    ]
    # The raw annotation text must not survive as a field label.
    fields = extract_field_candidates(boxes, set(), AcrfConfig())
    assert fields == annotation_labels(boxes)


def test_annotation_labels_ignore_a_study_code_in_parentheses():
    assert annotation_labels([_lb("QL1706-307(Ⅲ期)")]) == []


def test_repeated_far_right_column_is_answers_even_without_option_glyphs():
    # A Veeva-style print draws the radio button as a rectangle, so the choices
    # carry neither a glyph nor stock wording.
    boxes = [_lb("On average, how often were you woken?", top=157.0, x0=90.7, x1=350.0)]
    for i, choice in enumerate(["Never", "Hardly ever", "A few times", "Several times", "Many times"]):
        boxes.append(_lb(choice, top=161.0 + i * 24, x0=397.7, x1=460.0))
    assert extract_field_candidates(boxes, set(), AcrfConfig()) == ["On average, how often were you woken?"]


def test_outdented_group_heading_is_dropped_but_nested_subfield_is_kept():
    boxes = [
        _lb("Demographics", top=117.0, x0=88.7, x1=220.0),
        _lb("What is the subject's year of birth?", top=137.0, x0=90.7, x1=300.0),
        _lb("What is the sex of the subject?", top=157.0, x0=90.7, x1=300.0),
        _lb("Ethnicity", top=177.0, x0=90.7, x1=300.0),
        _lb("Specify other ethnicity", top=197.0, x0=119.3, x1=300.0),
    ]
    assert extract_field_candidates(boxes, set(), AcrfConfig()) == [
        "What is the subject's year of birth?",
        "What is the sex of the subject?",
        "Ethnicity",
        "Specify other ethnicity",
    ]


def test_generic_answer_word_above_a_grid_never_names_a_table():
    # An ALS may carry "Other" as one checkbox of a group, so it stays a field —
    # but it must not invent a table, which would propagate into the Spec.
    boxes = [
        _row([("Other", 43.0)], top=180.0, size=12.0),
        _row([("No.", 40.0), ("Reason", 66.0), ("Date", 200.0)], top=210.0, size=12.0),
        _row([("1", 40.0), ("Screen failure", 66.0)], top=226.0, size=12.0),
    ]
    sections = extract_form_sections(boxes, set(), AcrfConfig())

    assert [s.name for s in sections] == [None]
    assert sections[0].fields == ["Other", "Reason", "Date"]


def _cells(
    words: list[tuple[str, float, float]],
    top: float = 170.0,
    size: float = 10.0,
    page: int = 0,
) -> LineBox:
    """A header line with explicit word extents, so gaps are realistic."""
    boxes = tuple(WordBox(text=t, x0=x0, x1=x1) for t, x0, x1 in words)
    return LineBox(
        text=" ".join(t for t, _, _ in words),
        page=page,
        x0=boxes[0].x0,
        top=top,
        x1=boxes[-1].x1,
        bottom=top + 12,
        size=size,
        words=boxes,
    )


def test_multi_word_english_grid_header_stays_one_column_per_cell():
    # "Start Date" is one column split by a space. Treating each word as its own
    # column would also mint a duplicate "Date" variable downstream.
    header = _cells(
        [
            ("No.", 40, 58),
            ("Start", 66, 92),
            ("Date", 94.8, 120),
            ("End", 200, 220),
            ("Date", 222.8, 248),
        ]
    )
    row_1 = _cells([("1", 40, 47), ("Headache", 66, 120)], top=190.0)

    grids = detect_grids([header, row_1])

    assert len(grids) == 1
    assert grids[0].columns == ["Start Date", "End Date"]


def test_adjacent_narrow_english_columns_are_not_merged():
    # Date/Time and Low/High are the tightest real column pairs: 0.5 em at 10pt
    # type, against intra-cell gaps that top out at 0.295 em.
    header = _cells([("No.", 40, 58), ("Date", 66, 100), ("Time", 105, 130), ("Low", 140, 160), ("High", 165, 189)])
    row_1 = _cells([("1", 40, 47), ("x", 66, 72)], top=190.0)

    assert detect_grids([header, row_1])[0].columns == ["Date", "Time", "Low", "High"]


def test_body_column_start_splits_a_cell_at_an_intra_cell_gap():
    # Direct layout evidence beats the gap: the data proves "Time" opens its own
    # column, so it stays split despite sitting only 0.2 em from "Date".
    header = _cells([("No.", 40, 58), ("Date", 66, 100), ("Time", 102, 126)])
    rows = [
        _cells([("1", 40, 47), ("Y", 66, 73), ("N", 102, 109)], top=190.0),
        _cells([("2", 40, 47), ("Y", 66, 73), ("N", 102, 109)], top=210.0),
    ]

    assert detect_grids([header, *rows])[0].columns == ["Date", "Time"]


def test_multi_word_body_value_does_not_split_its_header():
    # "Visit 1" is one value; its second word sits under the header word "Name"
    # and must not be read as a column start, which would split "Visit Name".
    header = _cells([("No.", 40, 58), ("Visit", 66, 92), ("Name", 94.8, 124), ("Date", 200, 224)])
    rows = [
        _cells([("1", 40, 47), ("Visit", 66, 92), ("1", 94.8, 101), ("2024-01-01", 200, 256)], top=190.0),
        _cells([("2", 40, 47), ("Visit", 66, 92), ("2", 94.8, 101), ("2024-02-01", 200, 256)], top=210.0),
    ]

    assert detect_grids([header, *rows])[0].columns == ["Visit Name", "Date"]


def test_rule_from_another_page_does_not_split_this_pages_header():
    # A bookmark form may span several pages with the same y layout. Geometry
    # from page 1 must not become column evidence for a page-0 grid.
    header = _cells(
        [("No.", 40, 58), ("Visit", 66, 92), ("Name", 94.8, 124), ("Date", 200, 224)],
        page=0,
    )
    row = _cells(
        [("1", 40, 47), ("Visit 1", 66, 124), ("2024-01-01", 200, 256)],
        top=190.0,
        page=0,
    )
    page_1_rule_near_name = (1, 94.8, 160.0, 210.0)

    assert detect_grids([header, row], (page_1_rule_near_name,))[0].columns == ["Visit Name", "Date"]


def test_bare_no_opens_a_grid_when_numbered_rows_corroborate_it():
    # "No" is both an answer and an abbreviation for Number; geometry decides.
    header = _cells([("No", 40, 55), ("Date", 66, 100), ("Result", 150, 190)])
    row_1 = _cells([("1", 40, 47), ("x", 66, 72)], top=190.0)

    assert detect_grids([header, row_1])[0].columns == ["Date", "Result"]


def test_sub_table_line_ids_keeps_a_grid_named_after_its_own_form():
    # The extractor folds such a grid back into the parent, so its text must
    # stay in the parent's LLM prompt.
    boxes = [
        _row([("病理检查", 109.5)], top=140.0),
        _cells([("#", 117, 122), ("检查项目", 144, 190)], top=170.0),
        _cells([("1", 117, 122), ("x", 144, 150)], top=190.0),
    ]

    assert sub_table_line_ids(boxes, form_name="病理检查") == frozenset()
    assert sub_table_line_ids(boxes, form_name="其他表") == frozenset(id(lb) for lb in boxes)


def test_tightly_packed_cjk_grid_columns_are_never_merged():
    # Chinese headers pack columns as close as 6pt at 12pt type, which is inside
    # any plausible word-space threshold — the script has to decide, not the gap.
    header = _cells(
        [("No.", 40, 60), ("检查结果", 230, 278), ("异常情况", 285, 333), ("临床意义判", 340, 400)],
        size=12.0,
    )
    row_1 = _cells([("1", 40, 47), ("血压", 230, 254)], top=190.0, size=12.0)

    assert detect_grids([header, row_1])[0].columns == ["检查结果", "异常情况", "临床意义判"]


def test_sibling_sub_tables_keep_their_shared_column_names():
    # Detail tables routinely repeat "Date"/"Result"; a shared de-dup set would
    # let the first table swallow the second, emitting nothing for it at all.
    boxes = [
        _row([("Lab A Detail", 109.5)], top=140.0),
        _cells([("#", 117, 122), ("Date", 144, 170), ("Result", 233, 265)], top=170.0),
        _cells([("1", 117, 122), ("x", 144, 150)], top=190.0),
        _row([("Lab B Detail", 109.5)], top=260.0),
        _cells([("#", 117, 122), ("Date", 144, 170), ("Result", 233, 265)], top=290.0),
        _cells([("1", 117, 122), ("y", 144, 150)], top=310.0),
    ]

    sections = extract_form_sections(boxes, set(), AcrfConfig())

    assert [(s.name, s.fields) for s in sections] == [
        ("Lab A Detail", ["Date", "Result"]),
        ("Lab B Detail", ["Date", "Result"]),
    ]


def test_bare_no_answer_is_not_a_grid_marker():
    # "No" in a right-hand answer column must not open a table; only "No." does.
    answers = [_cells([("No", 398, 410)], top=104.0), _cells([("Yes", 398, 414)], top=128.0)]

    assert detect_grids(answers) == []


def test_sub_table_line_ids_covers_only_titled_grids():
    titled = [
        _row([("Lab Detail", 109.5)], top=140.0),
        _cells([("#", 117, 122), ("Date", 144, 170)], top=170.0),
        _cells([("1", 117, 122), ("x", 144, 150)], top=190.0),
    ]
    assert sub_table_line_ids(titled) == frozenset(id(lb) for lb in titled)

    untitled = [
        _cells([("No.", 40, 60), ("诊断", 66, 90)], top=170.0, size=12.0),
        _cells([("1", 40, 47), ("糖尿病", 66, 102)], top=190.0, size=12.0),
    ]
    assert sub_table_line_ids(untitled) == frozenset()
