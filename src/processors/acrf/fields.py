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
_QUESTION_TAIL_RE = re.compile(r"^(.+?[？?])\s+\S.*$")
_UNDERSCORE_RUN_RE = re.compile(r"_{2,}")
_LEADING_PUNCT_RE = re.compile(r"^[，。、；：）)】」』,.;:]+")
# A small table-row number followed by whitespace ("1 收缩压"); the required
# space avoids clipping numbers fused to text such as "12导联心电图".
_ROW_NUM_RE = re.compile(r"^\d{1,2}\s+(?=\S)")
_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")
_COMPLETE_LABEL_ENDINGS = ("：", ":", "？", "?", "。", "！", ".", "!")

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

# These values are used only as spatial anchors: a matching value does not
# classify a whole page column as answers unless the column is repeated,
# separated from the body by a strong horizontal gap, and aligned with a
# left-side label.
_ANSWER_COLUMN_ANCHORS: frozenset[str] = STOP_EXACT | frozenset(
    {
        "positive",
        "negative",
        "present",
        "absent",
        "mild",
        "moderate",
        "severe",
        "related",
        "unrelated",
        "fatal",
        "not applicable",
        "serum",
        "urine",
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
    r"(?:筛选期|基线|访视\s*\d+|周期\s*\d+|第\s*\d+\s*(?:周期|访视|周|天|月)|"
    r"随访(?:\s*\d+)?|计划外|治疗期|cycle\s*\d+|visit\s*\d+|day\s*[-\d]+|"
    r"week\s*\d+|screening|baseline|unscheduled)",
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
    if match := _QUESTION_TAIL_RE.match(t):
        t = match.group(1)
    t = _UNDERSCORE_RUN_RE.sub(" ", t)
    t = _ENUM_PREFIX_RE.sub("", t)
    t = _ROW_NUM_RE.sub("", t)
    t = _LEADING_PUNCT_RE.sub("", t)
    t = _TRAILING_COLON_RE.sub("", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


def _merge_wrapped_line_boxes(line_boxes: list[LineBox]) -> list[LineBox]:
    """Merge visual continuation lines while preserving normal row boundaries."""
    merged: list[LineBox] = []
    last_text_index: int | None = None
    for current in line_boxes:
        has_text = bool(_CJK_RE.search(current.text) or re.search(r"[A-Za-z]", current.text))
        if not has_text:
            if last_text_index is not None and merged[last_text_index].page != current.page:
                last_text_index = None
            merged.append(current)
            continue

        if last_text_index is None:
            merged.append(current)
            last_text_index = len(merged) - 1
            continue

        previous = merged[last_text_index]
        gap = current.top - previous.bottom
        max_gap = max(4.0, min(previous.size, current.size) * 0.45)
        same_visual_column = (
            current.page == previous.page
            and abs(current.x0 - previous.x0) <= 2.0
            and abs(current.size - previous.size) <= 0.5
        )
        is_continuation = (
            same_visual_column
            and -1.0 <= gap <= max_gap
            and not previous.text.rstrip().endswith(_COMPLETE_LABEL_ENDINGS)
            and not _ENUM_PREFIX_RE.match(current.text)
            and norm(current.text) not in STOP_EXACT
        )
        if not is_continuation:
            merged.append(current)
            last_text_index = len(merged) - 1
            continue

        left = previous.text.rstrip()
        right = current.text.lstrip()
        separator = "" if (_CJK_RE.search(left[-1:]) or _CJK_RE.match(right)) else " "
        merged[last_text_index] = LineBox(
            text=f"{left}{separator}{right}",
            page=previous.page,
            x0=min(previous.x0, current.x0),
            top=previous.top,
            x1=max(previous.x1, current.x1),
            bottom=current.bottom,
            size=previous.size,
            bold=previous.bold or current.bold,
        )
    return merged


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
    if _VISIT_HEADER_RE.fullmatch(cleaned.strip()):
        return False
    # Drop pure numbers / punctuation / measurement scaffolding.
    if not _CJK_RE.search(cleaned) and not re.search(r"[A-Za-z]", cleaned):
        return False
    # Instructions/notes tend to be long and end with declarative punctuation.
    # Question marks are kept: "是否…？" style labels are legitimate fields.
    is_question = cleaned.endswith(("？", "?"))
    if len(cleaned) > 40 and not is_question:
        return False
    if cleaned.endswith(("。", "！", ".", "!")) and len(cleaned) > 12:
        return False
    # A single stray latin/CJK char is noise.
    return len(n) >= 2


def _detect_answer_columns(line_boxes: list[LineBox]) -> dict[int, list[float]]:
    """Detect repeated far-right answer columns using text and layout evidence."""
    by_page: dict[int, list[LineBox]] = {}
    for lb in line_boxes:
        if lb.text.strip():
            by_page.setdefault(lb.page, []).append(lb)

    detected: dict[int, list[float]] = {}
    for page, boxes in by_page.items():
        min_x = min(lb.x0 for lb in boxes)
        width = max(lb.x1 for lb in boxes) - min_x
        anchor_lines = [lb for lb in boxes if norm(clean_label(lb.text)) in _ANSWER_COLUMN_ANCHORS]
        for anchor in anchor_lines:
            column = [lb for lb in boxes if abs(lb.x0 - anchor.x0) <= 6.0]
            if len(column) < 2:
                continue
            if anchor.x0 - min_x < max(100.0, width * 0.25):
                continue
            aligned_label = any(
                left.x0 < anchor.x0 - 50.0
                and abs(left.top - option.top) <= max(15.0, left.size * 1.5)
                and _looks_like_field(clean_label(left.text))
                for left in boxes
                for option in column
            )
            anchor_count = sum(norm(clean_label(option.text)) in _ANSWER_COLUMN_ANCHORS for option in column)
            if not aligned_label and anchor_count < 2:
                continue
            page_columns = detected.setdefault(page, [])
            if not any(abs(existing - anchor.x0) <= 6.0 for existing in page_columns):
                page_columns.append(anchor.x0)
    return detected


def extract_field_candidates(
    line_boxes: list[LineBox],
    boilerplate: frozenset[str] | set[str],
    cfg: AcrfConfig,
    page_height: float | None = None,
    form_name: str | None = None,
) -> list[str]:
    """Return ordered, de-duplicated field labels for one form's lines.

    ``boilerplate`` is the set of **raw** repeated header/footer strings from
    :func:`text.detect_boilerplate`; whole boilerplate lines are dropped and any
    boilerplate glued inline to a label is stripped out. ``page_height`` (if
    given) enables the header/footer margin-band drop via
    ``cfg.header_footer_band``. ``form_name`` is the bookmark-derived form name
    used to remove same-size page titles without treating all body text as a
    title.
    """
    line_boxes = _merge_wrapped_line_boxes(line_boxes)
    answer_columns = _detect_answer_columns(line_boxes)
    bp_norm = {norm(b) for b in boilerplate}
    bp_raw = sorted((b for b in boilerplate if b), key=len, reverse=True)
    form_name_norm = norm(form_name or "")

    # A largest-font line is title-like only when it is materially larger than
    # the modal body size. Uniform-font forms must retain their body lines.
    page_max: dict[int, float] = {}
    page_sizes: dict[int, Counter[float]] = {}
    for lb in line_boxes:
        if lb.size > 0:
            page_max[lb.page] = max(page_max.get(lb.page, 0.0), lb.size)
            page_sizes.setdefault(lb.page, Counter())[round(lb.size, 1)] += 1

    page_body_size = {page: sizes.most_common(1)[0][0] for page, sizes in page_sizes.items()}
    title_line_ids: set[int] = set()
    for page, hi in page_max.items():
        if hi <= page_body_size.get(page, hi) + 0.5:
            continue
        candidates = [
            lb
            for lb in line_boxes
            if lb.page == page
            and lb.size >= hi
            and "：" not in lb.text
            and ":" not in lb.text
            and (
                not page_height
                or (
                    lb.top >= cfg.header_footer_band * page_height
                    and lb.bottom <= (1 - cfg.header_footer_band) * page_height
                )
            )
        ]
        if candidates:
            title_line_ids.add(id(min(candidates, key=lambda lb: lb.top)))

    seen: set[str] = set()
    out: list[str] = []
    for lb in line_boxes:
        raw = lb.text
        if not raw or not raw.strip():
            continue
        if any(abs(lb.x0 - x0) <= 6.0 for x0 in answer_columns.get(lb.page, [])):
            continue
        if norm(raw) in bp_norm:
            continue
        # Header/footer margin band.
        if page_height and page_height > 0:
            band = cfg.header_footer_band * page_height
            if lb.top < band or lb.bottom > (page_height - band):
                continue
        raw_norm = norm(raw)
        if form_name_norm and form_name_norm == raw_norm:
            title_band = page_height * 0.25 if page_height else 200.0
            if lb.top < title_band:
                continue
        if id(lb) in title_line_ids:
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
