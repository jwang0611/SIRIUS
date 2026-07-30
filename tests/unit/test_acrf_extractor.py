"""Extractor orchestration: titled grids become their own tables.

The PDF backend is stubbed out, so these exercise the span → section → record
wiring without needing a fixture PDF.
"""

from __future__ import annotations

import pytest

from src.processors.acrf import extractor as ext
from src.processors.acrf.models import FormSpan, LineBox, WordBox


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
