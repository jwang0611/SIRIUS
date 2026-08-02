"""Positioned text extraction via ``pdfplumber`` + boilerplate detection.

``pdfplumber`` is imported lazily so the pure helpers (:func:`detect_boilerplate`)
and the rest of the package import cleanly even when the PDF stack is absent.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict

from src.processors.acrf.fields import norm
from src.processors.acrf.models import LineBox, RuleBox, WordBox

# Glyph gap (in points) above which two adjacent characters belong to different
# words. CJK is set solid, so any real gap is meaningful; 1pt keeps kerning
# noise from splitting a word while still separating table columns.
_WORD_GAP = 1.0
# Below this height a vector edge cannot contribute meaningful vertical ruling
# coverage. Final column evidence is filtered against the table span in fields.py.
_MIN_RULE_HEIGHT = 2.0


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


def _chars_to_words(chars: list[dict]) -> tuple[WordBox, ...]:
    """Group a line's characters into positioned words by horizontal gap.

    Note that real PDFs generally carry **no** space glyphs: ``pdfplumber``
    synthesises the spaces in ``line["text"]`` from positions. Word boundaries
    are therefore always positional, which is why callers separate an intra-cell
    space from a cell boundary by measuring the gap (see ``fields._header_cells``)
    rather than looking for a space character.
    """
    words: list[WordBox] = []
    buf: list[dict] = []

    def flush() -> None:
        if not buf:
            return
        text = "".join(str(c.get("text") or "") for c in buf).strip()
        if text:
            words.append(WordBox(text=text, x0=float(buf[0]["x0"]), x1=float(buf[-1]["x1"])))
        buf.clear()

    for char in chars:
        if not isinstance(char.get("x0"), (int, float)) or not isinstance(char.get("x1"), (int, float)):
            continue
        if str(char.get("text") or "").isspace():
            flush()
            continue
        if buf and float(char["x0"]) - float(buf[-1]["x1"]) > _WORD_GAP:
            flush()
        buf.append(char)
    flush()
    return tuple(words)


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
        words=_chars_to_words(chars),
    )


def _page_column_rules(page: object, page_index: int) -> tuple[RuleBox, ...]:
    """Vertical rules and cell-box edges as ``(page, x, top, bottom)``.

    A blank CRF draws its entry boxes as vector graphics, so on a table whose
    data rows hold nothing but a row number these edges are the only evidence of
    where each column begins. Horizontal rules are skipped — they say nothing
    about columns — and each rectangle contributes both of its vertical sides.
    """
    rules: set[RuleBox] = set()
    for obj in list(getattr(page, "lines", []) or []) + list(getattr(page, "rects", []) or []):
        try:
            x0, x1 = float(obj["x0"]), float(obj["x1"])
            top, bottom = float(obj["top"]), float(obj["bottom"])
        except (KeyError, TypeError, ValueError):
            continue
        if bottom - top < _MIN_RULE_HEIGHT:
            continue
        rules.add((page_index, round(x0, 1), top, bottom))
        rules.add((page_index, round(x1, 1), top, bottom))
    return tuple(sorted(rules))


def extract_all_line_boxes(
    pdf_path: str,
) -> tuple[dict[int, list[LineBox]], dict[int, float], dict[int, tuple[RuleBox, ...]]]:
    """Return per-page line boxes, page heights, and vertical rule geometry.

    Raises :class:`PdfBackendError` if the PDF cannot be opened.
    """
    pdfplumber = _require_pdfplumber()
    boxes_by_page: dict[int, list[LineBox]] = {}
    heights: dict[int, float] = {}
    rules_by_page: dict[int, tuple[RuleBox, ...]] = {}
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
                try:
                    rules_by_page[idx] = _page_column_rules(page, idx)
                except Exception:
                    rules_by_page[idx] = ()
    except PdfBackendError:
        raise
    except Exception as exc:
        raise PdfBackendError(f"unreadable pdf text: {exc}") from exc
    return boxes_by_page, heights, rules_by_page


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
