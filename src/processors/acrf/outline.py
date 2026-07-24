"""Form discovery from PDF bookmarks/outline via ``pypdf``.

Bookmark titles are decoded UTF-16/PDFDoc text (independent of the page content
fonts), which makes them a reliable, deterministic source of **form names** even
when the page body uses subset CID fonts. ``pypdf`` is imported lazily.
"""

from __future__ import annotations

from src.processors.acrf.fields import norm
from src.processors.acrf.models import AcrfConfig, FormSpan
from src.processors.acrf.text import PdfBackendError

# A flattened outline entry: (level, title, page_index_0based).
OutlineEntry = tuple[int, str, int]


def _require_pypdf():  # type: ignore[no-untyped-def]
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - environment guard
        raise PdfBackendError(
            "pypdf is required for aCRF PDF outline parsing. Install it with: pip install pypdf"
        ) from exc
    return PdfReader


def _flatten(node: object, reader: object, level: int, out: list[OutlineEntry]) -> None:
    """Depth-first walk of ``reader.outline`` producing ordered entries."""
    if not isinstance(node, list):
        return
    for item in node:
        if isinstance(item, list):
            _flatten(item, reader, level + 1, out)
            continue
        title = str(getattr(item, "title", "") or "").strip()
        if not title:
            continue
        try:
            page = reader.get_destination_page_number(item)  # type: ignore[attr-defined]
        except Exception:
            page = None
        if page is None:
            continue
        out.append((level, title, int(page)))


def flatten_outline(reader: object) -> list[OutlineEntry]:
    """All page-bearing bookmark entries in document order."""
    out: list[OutlineEntry] = []
    outline = getattr(reader, "outline", None)
    if outline:
        _flatten(outline, reader, 1, out)
    return out


def _auto_form_level(entries: list[OutlineEntry]) -> int:
    """The bookmark level that most likely enumerates forms.

    Heuristic: the level with the most entries; ties break to the shallowest.
    This skips a lone wrapping root (level 1 with a single child list).
    """
    counts: dict[int, int] = {}
    for level, _, _ in entries:
        counts[level] = counts.get(level, 0) + 1
    if not counts:
        return 1
    best = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))
    return best[0]


def has_usable_outline(reader: object) -> bool:
    return bool(flatten_outline(reader))


def build_form_spans(
    entries: list[OutlineEntry],
    total_pages: int,
    cfg: AcrfConfig,
) -> tuple[list[FormSpan], list[str]]:
    """Return (kept form spans, skipped form titles) from flattened entries."""
    if not entries:
        return [], []
    form_level = cfg.form_level if cfg.form_level is not None else _auto_form_level(entries)

    # Order by page (then level) so page ranges are computed in reading order.
    seq = sorted(entries, key=lambda e: (e[2], e[0]))
    skip_norm = {norm(s) for s in cfg.skip_forms}

    spans: list[FormSpan] = []
    skipped: list[str] = []
    for idx, (level, title, page) in enumerate(seq):
        if level != form_level:
            continue
        page_end = total_pages - 1
        for level2, _title2, page2 in seq[idx + 1 :]:
            if level2 <= form_level and page2 > page:
                page_end = page2 - 1
                break
        span = FormSpan(
            form_name=title,
            page_start=page,
            page_end=max(page, page_end),
            level=level,
        )
        if norm(title) in skip_norm:
            skipped.append(title)
        else:
            spans.append(span)
    return spans, skipped


def parse_outline(pdf_path: str, cfg: AcrfConfig) -> tuple[list[FormSpan], list[str], int]:
    """Open the PDF and return (form spans, skipped titles, total_pages).

    Raises :class:`PdfBackendError` if the file cannot be opened or has no
    usable outline.
    """
    PdfReader = _require_pypdf()
    try:
        reader = PdfReader(pdf_path)
        total_pages = len(reader.pages)
    except Exception as exc:
        raise PdfBackendError(f"unreadable pdf: {exc}") from exc

    entries = flatten_outline(reader)
    if not entries:
        raise PdfBackendError("no outline found: aCRF PDF ingestion requires bookmarked forms")
    spans, skipped = build_form_spans(entries, total_pages, cfg)
    return spans, skipped, total_pages
