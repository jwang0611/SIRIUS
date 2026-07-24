"""Positioned text extraction via ``pdfplumber`` + boilerplate detection.

``pdfplumber`` is imported lazily so the pure helpers (:func:`detect_boilerplate`)
and the rest of the package import cleanly even when the PDF stack is absent.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict

from src.processors.acrf.fields import norm
from src.processors.acrf.models import LineBox


class PdfBackendError(RuntimeError):
    """Raised when the PDF text backend is unavailable or the PDF is unreadable."""


def _require_pdfplumber():  # type: ignore[no-untyped-def]
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - environment guard
        raise PdfBackendError(
            "pdfplumber is required for aCRF PDF text extraction. Install it with: pip install pdfplumber"
        ) from exc
    return pdfplumber


def _line_to_box(line: dict, page_index: int) -> LineBox | None:
    text = (line.get("text") or "").strip()
    if not text:
        return None
    chars = line.get("chars") or []
    sizes = [c.get("size") for c in chars if isinstance(c.get("size"), (int, float))]
    size = float(statistics.median(sizes)) if sizes else 0.0
    bold = any("bold" in str(c.get("fontname", "")).lower() for c in chars)
    return LineBox(
        text=text,
        page=page_index,
        x0=float(line.get("x0", 0.0) or 0.0),
        top=float(line.get("top", 0.0) or 0.0),
        x1=float(line.get("x1", 0.0) or 0.0),
        bottom=float(line.get("bottom", 0.0) or 0.0),
        size=size,
        bold=bold,
    )


def extract_all_line_boxes(
    pdf_path: str,
) -> tuple[dict[int, list[LineBox]], dict[int, float]]:
    """Return ``{page_index: [LineBox, ...]}`` and ``{page_index: page_height}``.

    Raises :class:`PdfBackendError` if the PDF cannot be opened.
    """
    pdfplumber = _require_pdfplumber()
    boxes_by_page: dict[int, list[LineBox]] = {}
    heights: dict[int, float] = {}
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for idx, page in enumerate(pdf.pages):
                heights[idx] = float(page.height or 0.0)
                try:
                    lines = page.extract_text_lines(layout=False, strip=True, return_chars=True)
                except Exception:
                    lines = []
                boxes = [b for line in lines if (b := _line_to_box(line, idx))]
                boxes_by_page[idx] = boxes
    except PdfBackendError:
        raise
    except Exception as exc:
        raise PdfBackendError(f"unreadable pdf text: {exc}") from exc
    return boxes_by_page, heights


def replacement_char_ratio(line_boxes: list[LineBox]) -> float:
    """Fraction of characters that are the Unicode replacement char (U+FFFD).

    A high ratio means the fonts lack usable ToUnicode maps and extracted text
    is garbage — the caller should reject the form rather than emit noise.
    """
    total = 0
    bad = 0
    for lb in line_boxes:
        for ch in lb.text:
            total += 1
            if ch == "�":
                bad += 1
    return (bad / total) if total else 0.0


def detect_boilerplate(
    boxes_by_page: dict[int, list[LineBox]],
    heights: dict[int, float],
    band_frac: float = 0.08,
    min_pages: int | None = None,
    pos_tolerance: float = 3.0,
) -> set[str]:
    """Raw line texts that are genuine header/footer/watermark boilerplate.

    Frequency alone is **not** enough: real questions such as "Assessment Date"
    or "Comments" recur across many forms and must not be deleted. A line is
    treated as boilerplate only when it also sits at a **stable vertical
    position** (top range within ``pos_tolerance`` points) inside the top/bottom
    **edge band** (``band_frac`` of page height) on every page it appears on.

    Returns the **raw** representative text for each such line so callers can
    both drop whole boilerplate lines and strip the header when it is glued to
    a field label on the same visual line (e.g. a study code in a page banner).
    """
    n_pages = len(boxes_by_page)
    if n_pages <= 1 or band_frac <= 0:
        return set()
    if min_pages is None:
        min_pages = max(3, math.ceil(0.5 * n_pages))

    occurrences: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    raw_by_key: dict[str, str] = {}
    for page_index, boxes in boxes_by_page.items():
        page_height = heights.get(page_index, 0.0)
        for lb in boxes:
            key = norm(lb.text)
            if key:
                occurrences[key].append((page_index, lb.top, page_height))
                raw_by_key.setdefault(key, lb.text.strip())

    def _in_edge_band(top: float, page_height: float) -> bool:
        if page_height <= 0:
            return False
        band = band_frac * page_height
        return top < band or top > (page_height - band)

    result: set[str] = set()
    for key, occ in occurrences.items():
        if len({page for page, _, _ in occ}) < min_pages:
            continue
        tops = [top for _, top, _ in occ]
        if max(tops) - min(tops) > pos_tolerance:
            continue  # not a fixed-position header/footer
        if all(_in_edge_band(top, ph) for _, top, ph in occ):
            result.add(raw_by_key[key])
    return result
