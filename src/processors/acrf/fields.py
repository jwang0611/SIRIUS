"""Deterministic field-label extraction from positioned text lines.

Pure functions only (operate on :class:`LineBox` / ``str``) so they are unit
testable without a PDF backend. The heuristics are intentionally conservative
and vendor-agnostic: keep question-like labels, drop page furniture, option
choices, visit/timing headers, and instructions.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from src.processors.acrf.models import AcrfConfig, LineBox

# --- normalisation ---------------------------------------------------------

_WS_RE = re.compile(r"\s+")
# Leading enumeration: "1." "1、" "1)" "（1）" "(1)" "①"
_ENUM_PREFIX_RE = re.compile(r"^\s*(?:[（(]\s*\d+\s*[)）]|\d+\s*[.、)）]|[①②③④⑤⑥⑦⑧⑨⑩])\s*")
_TRAILING_COLON_RE = re.compile(r"[：:]\s*$")
# Option/checkbox glyphs. A field label ends where its first choice begins, so
# everything from the first such glyph onward is trimmed off the label.
_OPTION_GLYPHS = "□☐☑☒■○◯●√✓✔◇◆"
_OPTION_CUT_RE = re.compile(f"[{_OPTION_GLYPHS}].*$")
_UNDERSCORE_RUN_RE = re.compile(r"_{2,}")
_LEADING_PUNCT_RE = re.compile(r"^[，。、；：）)】」』,.;:]+")
# A small table-row number followed by whitespace ("1 收缩压"); the required
# space avoids clipping numbers fused to text such as "12导联心电图".
_ROW_NUM_RE = re.compile(r"^\d{1,2}\s+(?=\S)")
_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")

# Exact normalised strings that are never fields (standalone option answers,
# yes/no cells, "not done" cells, table column scaffolding).
STOP_EXACT: frozenset[str] = frozenset(
    {
        "是",
        "否",
        "有",
        "无",
        "未查",
        "未做",
        "正常",
        "异常",
        "阴性",
        "阳性",
        "男",
        "女",
        "其他",
        "序号",
        "n/a",
        "na",
        "yes",
        "no",
        "male",
        "female",
        "normal",
        "abnormal",
        "not done",
        "unknown",
    }
)

# Normalised lines containing any of these tokens are visit/timing headers or
# page furniture rather than data-entry labels. Keep these specific — broad
# tokens like "crf" would wrongly drop real fields such as "eCRF版本号".
STOP_SUBSTRINGS: tuple[str, ...] = (
    "第 页",
    "共 页",
    "受试者姓名缩写",
    "研究者签名",
    "case report form",
)

# Visit / cycle / period column headers frequently span a form as table columns.
_VISIT_HEADER_RE = re.compile(
    r"(筛选期|基线|访视\s*\d|周期\s*\d|第\s*\d+\s*(周期|访视|周|天|月)|随访|计划外|治疗期|"
    r"cycle\s*\d|visit\s*\d|day\s*[-\d]|week\s*\d|screening|baseline|unscheduled)",
    re.IGNORECASE,
)


def norm(text: str) -> str:
    """NFC-normalise, collapse whitespace, strip a trailing colon, casefold."""
    if not text:
        return ""
    t = unicodedata.normalize("NFC", text)
    t = _WS_RE.sub(" ", t).strip()
    t = _TRAILING_COLON_RE.sub("", t)
    return t.strip().casefold()


def clean_label(text: str) -> str:
    """Reduce a raw line to just its field-label part.

    Cuts inline options/choices (everything from the first option glyph),
    strips enumeration prefixes, underscore runs, a trailing colon, and any
    leading continuation punctuation.
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFC", text)
    t = _OPTION_CUT_RE.sub("", t)  # drop "… ○ 是 ○ 否" tail
    t = _UNDERSCORE_RUN_RE.sub(" ", t)
    t = _ENUM_PREFIX_RE.sub("", t)
    t = _ROW_NUM_RE.sub("", t)
    t = _LEADING_PUNCT_RE.sub("", t)
    t = _TRAILING_COLON_RE.sub("", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


def _looks_like_field(cleaned: str) -> bool:
    """Whether a cleaned line reads like a data-entry question/label."""
    if not cleaned:
        return False
    if cleaned.startswith("#"):  # table column-header scaffolding
        return False
    n = norm(cleaned)
    if not n or n in STOP_EXACT:
        return False
    if any(sub in n for sub in STOP_SUBSTRINGS):
        return False
    if _VISIT_HEADER_RE.search(cleaned):
        return False
    # Drop pure numbers / punctuation / measurement scaffolding.
    if not _CJK_RE.search(cleaned) and not re.search(r"[A-Za-z]", cleaned):
        return False
    # Instructions/notes tend to be long and end with declarative punctuation.
    # Question marks are kept: "是否…？" style labels are legitimate fields.
    if len(cleaned) > 40:
        return False
    if cleaned.endswith(("。", "！", ".", "!")) and len(cleaned) > 12:
        return False
    # A single stray latin/CJK char is noise.
    return len(n) >= 2


def extract_field_candidates(
    line_boxes: list[LineBox],
    boilerplate: frozenset[str] | set[str],
    cfg: AcrfConfig,
    page_height: float | None = None,
) -> list[str]:
    """Return ordered, de-duplicated field labels for one form's lines.

    ``boilerplate`` is the set of **raw** repeated header/footer strings from
    :func:`text.detect_boilerplate`; whole boilerplate lines are dropped and any
    boilerplate glued inline to a label is stripped out. ``page_height`` (if
    given) enables the header/footer margin-band drop via
    ``cfg.header_footer_band``.
    """
    bp_norm = {norm(b) for b in boilerplate}
    bp_raw = sorted((b for b in boilerplate if b), key=len, reverse=True)

    # Per-page largest font size marks the form title (skip it as a field).
    page_max: dict[int, float] = {}
    page_count: dict[int, int] = {}
    for lb in line_boxes:
        if lb.size > 0:
            page_max[lb.page] = max(page_max.get(lb.page, 0.0), lb.size)
            page_count[lb.page] = page_count.get(lb.page, 0) + 1

    seen: set[str] = set()
    out: list[str] = []
    for lb in line_boxes:
        raw = lb.text
        if not raw or not raw.strip():
            continue
        if norm(raw) in bp_norm:
            continue
        # Header/footer margin band.
        if page_height and page_height > 0:
            band = cfg.header_footer_band * page_height
            if lb.top < band or lb.bottom > (page_height - band):
                continue
        # Skip the single largest line on a content-rich page (the form title),
        # unless it carries a colon (a genuinely labelled field).
        hi = page_max.get(lb.page, 0.0)
        if hi > 0 and lb.size >= hi and page_count.get(lb.page, 0) > 2 and "：" not in raw and ":" not in raw:
            continue
        # Strip any inline repeated header (e.g. a study code banner) glued to
        # the label on the same visual line.
        stripped = raw
        for b in bp_raw:
            if b in stripped:
                stripped = stripped.replace(b, " ")
        cleaned = clean_label(stripped)
        if not _looks_like_field(cleaned):
            continue
        key = norm(cleaned)
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def validate_field_set(fields: list[str], cfg: AcrfConfig) -> tuple[list[str], list[str]]:
    """Count sanity checks. Returns (fields, warnings)."""
    warnings: list[str] = []
    if len(fields) < cfg.min_fields:
        warnings.append("no fields extracted")
    if len(fields) > cfg.max_fields:
        warnings.append(f"over-extracted ({len(fields)} > {cfg.max_fields}); truncated")
        fields = fields[: cfg.max_fields]
    return fields, warnings


def merge_field_lists(primary: list[str], extra: list[str]) -> list[str]:
    """Union two ordered label lists, de-duplicating on the normalised key."""
    seen = {norm(f) for f in primary}
    merged = list(primary)
    for f in extra:
        k = norm(f)
        if k and k not in seen:
            seen.add(k)
            merged.append(f)
    return merged


def most_common_size(line_boxes: list[LineBox]) -> float:
    """Modal font size across lines (the body-text size), 0.0 if unknown."""
    sizes = Counter(round(lb.size, 1) for lb in line_boxes if lb.size > 0)
    return sizes.most_common(1)[0][0] if sizes else 0.0
