"""Dataclasses and tunable configuration for aCRF/eCRF PDF extraction.

These types are dependency-free (no ``pypdf`` / ``pdfplumber`` / ``pandas``
imports) so the pure-function layers — record assembly, field heuristics,
boilerplate detection — can be imported and unit-tested without the PDF or
Excel stack installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Bookmark titles that are structural pages, never data-entry forms. Compared
# after NFC + whitespace normalisation and case-folding (see ``fields.norm``).
DEFAULT_SKIP_FORMS: frozenset[str] = frozenset(
    {
        "封面",
        "目录",
        "签名页",
        "版本历史",
        "修订历史",
        "修订说明",
        "填写说明",
        "cover",
        "cover page",
        "table of contents",
        "toc",
        "contents",
        "revision history",
        "version history",
    }
)


@dataclass(frozen=True)
class AcrfConfig:
    """Tunables for the extractor. Mirrors ``AcrfSettings`` env config.

    ``metadata_variable_mode``:
        - ``"label"`` (default): ``metadata_variable`` = field label, with a
          ``_2``/``_3`` suffix on repeats within a form (human/traceable).
        - ``"synthetic"``: ``metadata_variable`` = ``F{form:02d}_{field:03d}``
          (ASCII-clean placeholder codes).
    """

    skip_forms: frozenset[str] = DEFAULT_SKIP_FORMS
    min_fields: int = 1
    max_fields: int = 300
    header_footer_band: float = 0.08  # fraction of page height treated as margin
    metadata_variable_mode: str = "label"  # "label" | "synthetic"
    form_level: int | None = None  # None → auto-detect the form bookmark level
    use_llm: bool = False
    # A form whose deterministic pass yields fewer than this many fields is a
    # candidate for the LLM assist (when a client is supplied).
    llm_min_fields: int = 1


@dataclass(frozen=True)
class FormSpan:
    """A form (bookmark) and the 0-based inclusive page range it covers."""

    form_name: str
    page_start: int
    page_end: int
    level: int = 1


@dataclass
class LineBox:
    """One visual text line with position and font hints (page-top origin)."""

    text: str
    page: int
    x0: float
    top: float
    x1: float
    bottom: float
    size: float
    bold: bool = False


@dataclass
class AcrfRecord:
    """One extracted (form, field) row in the canonical 4-field shape."""

    num: int
    metadata_table: str
    metadata_variable: str
    annotation_table: str
    annotation_variable: str


@dataclass
class ExtractionResult:
    """Everything ``extract_acrf`` produces for one PDF."""

    records: list[AcrfRecord] = field(default_factory=list)
    skipped_forms: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, object] = field(default_factory=dict)
