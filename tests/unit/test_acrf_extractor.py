"""Extractor orchestration: titled grids become their own tables.

The PDF backend is stubbed out, so these exercise the span → section → record
wiring without needing a fixture PDF.
"""

from __future__ import annotations

import pytest

from src.processors.acrf import extractor as ext
from src.processors.acrf.models import AcrfConfig, FormSpan, LineBox, WordBox


def _row(words: list[tuple[str, float]], top: float, size: float = 10.0, page: int = 0) -> LineBox:
    boxes = tuple(WordBox(text=t, x0=x, x1=x + 40.0) for t, x in words)
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


@pytest.fixture
def stub_pdf(monkeypatch: pytest.MonkeyPatch):
    """Install a fake single-form PDF made of the given line boxes."""

    def install(boxes: list[LineBox], form_name: str = "生命体征") -> None:
        monkeypatch.setattr(
            ext._outline,
            "parse_outline",
            lambda path, cfg: ([FormSpan(form_name=form_name, page_start=0, page_end=0)], [], 1),
        )
        monkeypatch.setattr(ext._text, "extract_all_line_boxes", lambda path: ({0: boxes}, {0: 842.0}))
        monkeypatch.setattr(ext._text, "detect_boilerplate", lambda *a, **k: set())

    return install


def test_titled_grid_is_emitted_as_a_separate_table(stub_pdf):
    stub_pdf(
        [
            _row([("检查日期", 109.5)], top=112.4),
            _row([("生命体征明细", 109.5)], top=143.4),
            _row([("#", 117.0), ("检查项目", 144.0), ("检查结果", 233.0)], top=173.4),
            _row([("1", 117.0), ("收缩压", 144.0)], top=200.4),
        ]
    )

    result = ext.extract_acrf("ignored.pdf")

    assert [(r.metadata_table, r.annotation_variable) for r in result.records] == [
        ("生命体征", "检查日期"),
        ("生命体征明细", "检查项目"),
        ("生命体征明细", "检查结果"),
    ]
    assert result.stats["sub_forms"] == 1
    assert result.stats["forms_with_fields"] == 1


def test_grid_heading_repeating_the_form_name_keeps_its_columns(stub_pdf):
    # The heading adds no new table, but its columns must not be discarded.
    stub_pdf(
        [
            _row([("病理检查", 109.5)], top=143.4),
            _row([("#", 117.0), ("检查项目", 144.0), ("检查结果", 233.0)], top=173.4),
            _row([("1", 117.0), ("肝脏", 144.0)], top=200.4),
        ],
        form_name="病理检查",
    )

    result = ext.extract_acrf("ignored.pdf")

    assert [(r.metadata_table, r.annotation_variable) for r in result.records] == [
        ("病理检查", "检查项目"),
        ("病理检查", "检查结果"),
    ]
    assert result.stats["sub_forms"] == 0


def _cells(words: list[tuple[str, float, float]], top: float, size: float = 10.0) -> LineBox:
    boxes = tuple(WordBox(text=t, x0=x0, x1=x1) for t, x0, x1 in words)
    return LineBox(
        text=" ".join(t for t, _, _ in words),
        page=0,
        x0=boxes[0].x0,
        top=top,
        x1=boxes[-1].x1,
        bottom=top + 12,
        size=size,
        words=boxes,
    )


_DETAIL_ONLY_FORM = [
    _row([("Lab Detail", 109.5)], top=140.0),
    _cells([("#", 117, 122), ("Date", 144, 170), ("Result", 233, 265)], top=170.0),
    _cells([("1", 117, 122), ("x", 144, 150)], top=190.0),
]


def test_llm_is_not_called_when_a_detail_table_already_covers_the_form(stub_pdf, monkeypatch):
    """A bookmark holding only a detail table is fully extracted, not sparse."""
    calls: list[str] = []
    monkeypatch.setattr(
        "src.processors.acrf.llm_extractor.extract_fields_llm",
        lambda page_text, **kw: calls.append(page_text) or ["Date", "Result", "Invented"],
    )
    stub_pdf(_DETAIL_ONLY_FORM, form_name="Lab")

    result = ext.extract_acrf("ignored.pdf", use_llm=True, client=object())

    assert calls == []
    assert result.stats["forms_via_llm"] == 0
    # No detail column leaks into the parent bookmark form.
    assert [(r.metadata_table, r.annotation_variable) for r in result.records] == [
        ("Lab Detail", "Date"),
        ("Lab Detail", "Result"),
    ]


def test_llm_input_excludes_detail_table_text(stub_pdf, monkeypatch):
    """When the LLM does run, it must not see text owned by a sub-table."""
    seen: list[str] = []
    monkeypatch.setattr(
        "src.processors.acrf.llm_extractor.extract_fields_llm",
        lambda page_text, **kw: seen.append(page_text) or [],
    )
    # Same detail table, plus a parent line that yields no field on its own.
    stub_pdf([_row([("是", 109.5)], top=120.0), *_DETAIL_ONLY_FORM], form_name="Lab")
    monkeypatch.setattr(ext, "_MAX_REPLACEMENT_RATIO", 1.0)

    ext.extract_acrf("ignored.pdf", use_llm=True, client=object(), cfg=AcrfConfig(llm_min_fields=99))

    assert seen, "the LLM assist should have run for this sparse form"
    assert "Result" not in seen[0]
    assert "Lab Detail" not in seen[0]


def test_detail_table_columns_do_not_mask_a_sparse_parent(stub_pdf, monkeypatch):
    """Sparsity is judged on the parent, which is what receives the labels."""
    seen: list[str] = []
    monkeypatch.setattr(
        "src.processors.acrf.llm_extractor.extract_fields_llm",
        lambda page_text, **kw: seen.append(page_text) or ["Recovered Field"],
    )
    stub_pdf([_row([("Assessment Date", 109.5)], top=120.0), *_DETAIL_ONLY_FORM], form_name="Lab")

    # Parent has 1 field, the detail table 2 — only the parent's count may count.
    result = ext.extract_acrf("ignored.pdf", use_llm=True, client=object(), cfg=AcrfConfig(llm_min_fields=3))

    assert seen, "the parent is sparse and must still reach the LLM"
    assert result.stats["forms_via_llm"] == 1
    assert ("Lab", "Recovered Field") in [(r.metadata_table, r.annotation_variable) for r in result.records]


def test_container_form_skips_the_remote_call_instead_of_prompting_on_air(stub_pdf, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "src.processors.acrf.llm_extractor.extract_fields_llm",
        lambda page_text, **kw: calls.append(page_text) or [],
    )
    stub_pdf(_DETAIL_ONLY_FORM, form_name="Lab")

    ext.extract_acrf("ignored.pdf", use_llm=True, client=object(), cfg=AcrfConfig(llm_min_fields=3))

    assert calls == []
