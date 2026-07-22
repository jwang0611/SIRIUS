"""Outline parsing on a committed ASCII fixture PDF (integration)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.processors.acrf.models import AcrfConfig
from src.processors.acrf.outline import parse_outline
from src.processors.acrf.text import PdfBackendError

pytestmark = pytest.mark.integration


def test_parse_outline_skips_cover_and_ranges_pages(sample_acrf_pdf: Path):
    spans, skipped, total = parse_outline(str(sample_acrf_pdf), AcrfConfig())

    assert total == 3
    assert "Cover" in skipped
    assert [s.form_name for s in spans] == ["Demographics", "Vital Signs"]
    assert (spans[0].page_start, spans[0].page_end) == (1, 1)
    assert (spans[1].page_start, spans[1].page_end) == (2, 2)


def test_pdf_without_outline_raises(tmp_path: Path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 not really a pdf")
    with pytest.raises(PdfBackendError):
        parse_outline(str(broken), AcrfConfig())
