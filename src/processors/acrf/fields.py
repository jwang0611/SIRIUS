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
from dataclasses import dataclass

from src.processors.acrf.models import AcrfConfig, FormSection, LineBox, WordBox

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
# Empty entry boxes printed after a label, e.g. "检查日期 |_|_|_|_|/|_|_|/|_|_|".
# Two or more bars are required so a single "A|B" separator is left alone. Like
# an option glyph, the first box ends the label — what follows is the answer and
# its unit ("身高(cm) |_|_|_|.|_| cm").
_ENTRY_BOX_RE = re.compile(r"[|｜](?:[\s_]*[|｜])+(?:\s*[/／\-]\s*[|｜](?:[\s_]*[|｜])+)*")
_ENTRY_BOX_CUT_RE = re.compile(f"{_ENTRY_BOX_RE.pattern}.*$")
# An aCRF annotation printed in the page body: "VSTEST(检查项) : L1-收缩压…" or
# "YN(是否持续？|当前是否持续？):1-是, 2-否". The parenthesised text is the CRF
# question the SDTM variable is annotating, which makes it a high-precision
# source of field labels — and the line itself is never a field.
_ANNOTATION_RE = re.compile(r"^([A-Z][A-Z0-9_]{1,15})\s*\((.+?)\)\s*[:：]")
_UNDERSCORE_RUN_RE = re.compile(r"_{2,}")
_LEADING_PUNCT_RE = re.compile(r"^[，。、；：）)】」』,.;:]+")
# A small table-row number followed by whitespace ("1 收缩压"); the required
# space avoids clipping numbers fused to text such as "12导联心电图".
_ROW_NUM_RE = re.compile(r"^\d{1,2}\s+(?=\S)")
_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_COMPLETE_LABEL_ENDINGS = ("：", ":", "？", "?", "。", "！", ".", "!")

# The row-number column that opens a repeating table ("grid"). Its presence is
# what distinguishes a table header row from an ordinary wrapped label.
_GRID_MARKER_RE = re.compile(r"^(?:#|no\.?|s/?n|序号|编号)$", re.IGNORECASE)
# A bare "No" is equally the answer to a yes/no question, so it opens a table
# only with corroborating geometry: sibling header cells *and* numbered rows.
_AMBIGUOUS_GRID_MARKER_RE = re.compile(r"^no$", re.IGNORECASE)
# Largest gap, as a fraction of the font size, that still reads as within one
# header cell. Calibrated on this project's corpus: intra-cell gaps reach
# 0.295 em, the tightest real column boundary is 0.512 em.
_MAX_INTRA_CELL_GAP = 0.4
_GRID_ROW_NUMBER_RE = re.compile(r"^\d{1,3}$")
# Marks that prove a line carries an answer/value, so it is a data-entry row
# rather than a section heading.
_ANSWER_MARK_RE = re.compile(f"[{_OPTION_GLYPHS}]|_{{2,}}|[|｜][\\s_]*[|｜]")
# Table names are noun phrases; internal sentence punctuation means the line is
# a question or an instruction instead.
_SENTENCE_PUNCT_RE = re.compile(r"[，,。、；;？?！!]")
_MAX_GRID_TITLE_LEN = 20
# Words that may legitimately be a *field* (one checkbox of a group) but never
# the name of a table. Kept separate from ``STOP_EXACT`` so naming a table stays
# strict without costing those fields.
_GENERIC_TITLE_WORDS: frozenset[str] = frozenset({"other", "none", "not reported", "specify", "unknown"})
# How many lines must share a far-right x0 before the column reads as a list of
# answer choices rather than a second column of real fields.
_MIN_ANSWER_COLUMN_RUN = 4

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
    t = _ENTRY_BOX_CUT_RE.sub("", t)  # drop "… |_|_|_|/|_|_| cm" answer tail
    if match := _QUESTION_TAIL_RE.match(t):
        t = match.group(1)
    t = _UNDERSCORE_RUN_RE.sub(" ", t)
    t = _ENUM_PREFIX_RE.sub("", t)
    t = _ROW_NUM_RE.sub("", t)
    t = _LEADING_PUNCT_RE.sub("", t)
    t = _TRAILING_COLON_RE.sub("", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


def _label_head(text: str) -> str:
    """The label part of a raw line: everything before its first answer mark."""
    t = _OPTION_CUT_RE.sub("", unicodedata.normalize("NFC", text))
    return _ENTRY_BOX_RE.split(t, maxsplit=1)[0].rstrip()


def _join_labels(left: str, right: str) -> str:
    """Concatenate two label fragments, spacing them only when non-CJK."""
    left, right = left.rstrip(), right.lstrip()
    if not left:
        return right
    if not right:
        return left
    separator = "" if (_CJK_RE.search(left[-1:]) or _CJK_RE.match(right)) else " "
    return f"{left}{separator}{right}"


def _is_continuation(previous: LineBox, previous_head: str, current: LineBox, head: str) -> bool:
    """Whether ``current`` visually continues the wrapped label in ``previous``."""
    if previous.page != current.page:
        return False
    size = min(previous.size, current.size) or max(previous.size, current.size)
    if abs(current.x0 - previous.x0) > max(4.0, size * 0.4):
        return False
    if abs(current.size - previous.size) > 0.5:
        return False
    if not -1.0 <= current.top - previous.bottom <= max(4.0, size * 0.45):
        return False
    if previous_head.endswith(_COMPLETE_LABEL_ENDINGS):
        return False
    if _ENUM_PREFIX_RE.match(current.text) or _ANNOTATION_RE.match(current.text.strip()):
        return False
    # A line with its own answer is a complete field row, not the tail of the
    # label above it. Lab forms stack one label per line ("结果 |_|_|", "单位 ____")
    # tightly enough that spacing alone reads as a wrap.
    if _ANSWER_MARK_RE.search(current.text):
        return False
    return norm(head) not in STOP_EXACT


def _merge_wrapped_line_boxes(line_boxes: list[LineBox]) -> list[LineBox]:
    """Merge visual continuation lines while preserving normal row boundaries.

    A wrapped label keeps its left edge but the answer column for the *same*
    question is emitted between the two halves in reading order (the option row
    sits lower than the first line but higher than the second). The scan
    therefore tracks the most recent open line **per left-edge column** instead
    of just the previous line, and joins the option-stripped heads so an inline
    "○ 是 ○ 否" cannot wedge itself into the middle of a label.
    """
    merged: list[LineBox] = []
    heads: dict[int, str] = {}  # merged index -> its current label head
    open_indices: list[int] = []  # most recent first, one per left-edge column

    for current in line_boxes:
        head = _label_head(current.text)
        target: int | None = None
        if head and (_CJK_RE.search(head) or _LATIN_RE.search(head)):
            for idx in open_indices:
                if _is_continuation(merged[idx], heads[idx], current, head):
                    target = idx
                    break

        if target is None:
            merged.append(current)
            index = len(merged) - 1
            # Only a line that carries actual words can absorb a continuation;
            # rules and blank entry boxes must not swallow the next label.
            if head and (_CJK_RE.search(head) or _LATIN_RE.search(head)):
                heads[index] = head
                open_indices = [index] + [i for i in open_indices if abs(merged[i].x0 - current.x0) > 4.0]
                del open_indices[8:]
            continue

        previous = merged[target]
        heads[target] = _join_labels(heads[target], head)
        merged[target] = LineBox(
            text=heads[target],
            page=previous.page,
            x0=min(previous.x0, current.x0),
            top=previous.top,
            x1=max(previous.x1, current.x1),
            bottom=current.bottom,
            size=previous.size,
            bold=previous.bold or current.bold,
            words=previous.words + current.words,
        )
        open_indices = [target] + [i for i in open_indices if i != target]
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


def _words_of(line_box: LineBox) -> tuple[WordBox, ...]:
    """Positioned words for a line, falling back to the whole line as one word."""
    if line_box.words:
        return line_box.words
    text = line_box.text.strip()
    return (WordBox(text=text, x0=line_box.x0, x1=line_box.x1),) if text else ()


def _min_word_x0(line_box: LineBox) -> float:
    words = _words_of(line_box)
    return min(w.x0 for w in words) if words else line_box.x0


@dataclass(frozen=True)
class _Grid:
    """A repeating table: its column labels plus the lines it owns."""

    page: int
    top: float
    columns: list[str]
    title: str | None
    consumed: frozenset[int]  # id() of every LineBox the grid absorbs


def _header_cells(line_box: LineBox, marker: WordBox, column_starts: tuple[float, ...] = ()) -> list[WordBox]:
    """One line's header words merged into table cells.

    Two words join only when every test agrees they sit in one cell:

    * the gap is at most :data:`_MAX_INTRA_CELL_GAP` of the font size — measured
      over this project's corpus, intra-cell gaps top out at 0.295 em while the
      tightest real column boundary is 0.512 em, so 0.4 em splits them with
      margin on both sides (a bare gap threshold is required because real PDFs
      carry no space glyphs at all — ``pdfplumber`` synthesises them);
    * both sides are Latin, since CJK is set solid and any gap between two CJK
      words is a boundary;
    * the second word does not start a column that the data rows established,
      which is direct layout evidence and overrides the gap.

    The row-number marker is dropped before merging: it sits a few points from
    the first column and would otherwise force the bound implausibly tight.
    """
    words = [w for w in _words_of(line_box) if w is not marker]
    if not words:
        return []
    limit = max(2.0, (line_box.size or 10.0) * _MAX_INTRA_CELL_GAP)

    cells = [words[0]]
    for word in words[1:]:
        previous = cells[-1]
        same_cell = (
            word.x0 - previous.x1 <= limit
            and not _CJK_RE.search(previous.text)
            and not _CJK_RE.search(word.text)
            and not any(abs(word.x0 - start) <= 3.0 for start in column_starts)
        )
        if not same_cell:
            cells.append(word)
            continue
        cells[-1] = WordBox(text=_join_labels(previous.text, word.text), x0=previous.x0, x1=word.x1)
    return cells


def _numbered_rows(boxes: list[LineBox], body: list[int], marker_x: float) -> list[LineBox]:
    """Body lines that actually open with a row number in the marker column."""
    rows: list[LineBox] = []
    for index in body:
        words = _words_of(boxes[index])
        if words and _GRID_ROW_NUMBER_RE.match(words[0].text.strip()) and abs(words[0].x0 - marker_x) <= 6.0:
            rows.append(boxes[index])
    return rows


def _body_column_starts(rows: list[LineBox], marker_x: float) -> tuple[float, ...]:
    """Column left edges witnessed by the grid's own data rows."""
    starts: list[float] = []
    for row in rows:
        for word in _words_of(row):
            if abs(word.x0 - marker_x) <= 6.0:
                continue
            if not any(abs(word.x0 - start) <= 3.0 for start in starts):
                starts.append(word.x0)
    return tuple(sorted(starts))


def _stitch_grid_columns(band: list[LineBox], marker: WordBox, column_starts: tuple[float, ...] = ()) -> list[str]:
    """Rebuild column headers from a (possibly wrapped) header band.

    Column headers wrap downward inside their own column, so words that share an
    ``x0`` belong to the same header no matter which visual line they landed on.
    """
    placed: list[list[tuple[float, WordBox]]] = []
    for line_box in band:
        # Merge each line's words into cells first. "No. Start Date End Date" is
        # four words but two columns, and clustering words directly would split
        # every multi-word English header — and then mint a duplicate "Date"
        # variable downstream.
        for word in _header_cells(line_box, marker, column_starts):
            for column in placed:
                if abs(column[0][1].x0 - word.x0) <= 3.0:
                    column.append((line_box.top, word))
                    break
            else:
                placed.append([(line_box.top, word)])

    columns: list[str] = []
    for column in sorted(placed, key=lambda c: c[0][1].x0):
        label = ""
        for _top, word in sorted(column, key=lambda t: t[0]):
            label = _join_labels(label, word.text)
        cleaned = clean_label(label)
        if _looks_like_field(cleaned):
            columns.append(cleaned)
    return columns


def _grid_header_band(boxes: list[LineBox], index: int, interior_x: float) -> list[int]:
    """Indices of the lines forming the header band around ``boxes[index]``.

    Continuation lines of a wrapped column header may be emitted *above* the
    marker line (their glyphs start higher on the page), so the band grows in
    both directions while lines stay inside the table body and close together.
    """
    marker = boxes[index]
    size = marker.size or 10.0
    band = [index]

    edge = marker.top
    for j in range(index - 1, -1, -1):
        candidate = boxes[j]
        if candidate.bottom < edge - 1.8 * size:
            break
        if _min_word_x0(candidate) <= interior_x or not clean_label(candidate.text):
            break
        band.append(j)
        edge = candidate.top

    edge = marker.bottom
    for k in range(index + 1, len(boxes)):
        candidate = boxes[k]
        if candidate.top > edge + 1.8 * size:
            break
        if _min_word_x0(candidate) <= interior_x or not clean_label(candidate.text):
            break
        band.append(k)
        edge = candidate.bottom

    return sorted(band)


def _grid_body(boxes: list[LineBox], start: int, marker_x: float, interior_x: float) -> list[int]:
    """Indices of the numbered data rows that follow a grid header."""
    body: list[int] = []
    for k in range(start, len(boxes)):
        words = _words_of(boxes[k])
        first = words[0] if words else None
        numbered_row = (
            first is not None
            and _GRID_ROW_NUMBER_RE.match(first.text.strip()) is not None
            and abs(first.x0 - marker_x) <= 6.0
        )
        if _min_word_x0(boxes[k]) <= interior_x and not numbered_row:
            break
        body.append(k)
    return body


def _grid_title(boxes: list[LineBox], band_start: int, interior_x: float, size: float) -> LineBox | None:
    """The in-page heading naming a grid, when it has one.

    Naming a grid creates a whole new table, so the bar is deliberately high: a
    heading is a short, single-token noun printed alone just above the table,
    with no answer of its own. That separates "生命体征明细" (its own form in the
    ALS) from the ordinary field labels printed above an untitled table —
    "单位 mg" (a label and its unit) or "若选择“否”，请选择…" (a question whose
    answer *is* the table).
    """
    band_top = boxes[band_start].top
    for j in range(band_start - 1, -1, -1):
        candidate = boxes[j]
        cleaned = clean_label(candidate.text)
        if not cleaned:
            continue
        if band_top - candidate.bottom > 3.0 * size:
            return None
        if _min_word_x0(candidate) > interior_x:
            return None
        if len(_words_of(candidate)) != 1:  # a label printed with its value
            return None
        if _SENTENCE_PUNCT_RE.search(cleaned) or len(cleaned) > _MAX_GRID_TITLE_LEN:
            return None
        # A generic answer word never names a table, even though an ALS may well
        # carry it as a field ("Other" as one checkbox of a group).
        if norm(cleaned) in _GENERIC_TITLE_WORDS:
            return None
        if _ANSWER_MARK_RE.search(candidate.text):
            return None
        if cleaned != clean_label(_label_head(candidate.text)):
            return None
        if candidate.text.rstrip().endswith(_COMPLETE_LABEL_ENDINGS):
            return None
        overlaps = any(
            other is not candidate
            and other.page == candidate.page
            and other.top < candidate.bottom
            and other.bottom > candidate.top
            for other in boxes
        )
        return None if overlaps else candidate
    return None


def detect_grids(line_boxes: list[LineBox]) -> list[_Grid]:
    """Find every repeating table and recover its column labels.

    Returns one :class:`_Grid` per table found, each owning the header, data and
    (optional) heading lines so the caller can skip them during the label scan.
    """
    by_page: dict[int, list[LineBox]] = {}
    for line_box in line_boxes:
        if line_box.text.strip():
            by_page.setdefault(line_box.page, []).append(line_box)

    grids: list[_Grid] = []
    for page, unsorted_boxes in by_page.items():
        boxes = sorted(unsorted_boxes, key=lambda lb: (lb.top, lb.x0))
        consumed_upto = -1
        for index, line_box in enumerate(boxes):
            if index <= consumed_upto:
                continue
            words = _words_of(line_box)
            if not words or not _GRID_MARKER_RE.match(words[0].text.strip()):
                continue
            marker = words[0]
            size = line_box.size or 10.0
            interior_x = marker.x0 + max(8.0, size)

            band = _grid_header_band(boxes, index, interior_x)
            body = _grid_body(boxes, band[-1] + 1, marker.x0, interior_x)
            numbered = _numbered_rows(boxes, body, marker.x0)
            # "No" is also the answer to a yes/no question. Only real numbered
            # rows corroborate a table — a merely non-empty body does not, since
            # any line right of the marker column joins it.
            if _AMBIGUOUS_GRID_MARKER_RE.match(marker.text.strip()) and not numbered:
                continue
            columns = _stitch_grid_columns(
                [boxes[i] for i in band],
                marker,
                _body_column_starts(numbered, marker.x0),
            )
            if not columns:
                continue
            title_box = _grid_title(boxes, band[0], interior_x, size)

            owned = [boxes[i] for i in band] + [boxes[i] for i in body]
            if title_box is not None:
                owned.append(title_box)
            grids.append(
                _Grid(
                    page=page,
                    top=boxes[band[0]].top,
                    columns=columns,
                    title=clean_label(title_box.text) if title_box is not None else None,
                    consumed=frozenset(id(lb) for lb in owned),
                )
            )
            consumed_upto = body[-1] if body else band[-1]
    return grids


def sub_table_line_ids(line_boxes: list[LineBox], form_name: str | None = None) -> frozenset[int]:
    """``id()`` of every line owned by a grid that becomes its *own* table.

    Callers that feed page text to another consumer (the LLM assist) use this to
    keep a detail table's text out of its parent form. A grid whose heading just
    repeats ``form_name`` is folded back into that form by the extractor, so its
    lines stay — excluding them would hand the model an empty page.
    """
    form_key = norm(form_name or "")
    owned: set[int] = set()
    for grid in detect_grids(line_boxes):
        if grid.title and norm(grid.title) != form_key:
            owned |= grid.consumed
    return frozenset(owned)


def annotation_labels(line_boxes: list[LineBox]) -> list[str]:
    """Field labels recovered from aCRF annotations printed in the page body.

    ``VSTEST(检查项) : L1-收缩压…`` annotates the CRF question "检查项" with its
    SDTM variable, so the parenthesised text is a field label the layout scan may
    have missed. A single annotation may cover several questions, separated by
    ``|`` inside the parentheses.
    """
    labels: list[str] = []
    for line_box in line_boxes:
        match = _ANNOTATION_RE.match(line_box.text.strip())
        if not match:
            continue
        for part in re.split(r"[|｜]", match.group(2)):
            cleaned = clean_label(part)
            if _looks_like_field(cleaned):
                labels.append(cleaned)
    return labels


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
        gap = max(100.0, width * 0.25)
        anchor_lines = [lb for lb in boxes if norm(clean_label(lb.text)) in _ANSWER_COLUMN_ANCHORS]
        for anchor in anchor_lines:
            column = [lb for lb in boxes if abs(lb.x0 - anchor.x0) <= 6.0]
            if len(column) < 2:
                continue
            if anchor.x0 - min_x < gap:
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

        # Vocabulary-free path: a long run of lines sharing one far-right x0 is a
        # list of answer choices. Prints that draw the radio button as a vector
        # rectangle carry no option glyph and no stock wording ("A few times",
        # "Very slightly limited"), so text-based anchors cannot see them.
        for x0 in {round(lb.x0, 1) for lb in boxes}:
            if x0 - min_x < gap:
                continue
            column = [lb for lb in boxes if abs(lb.x0 - x0) <= 2.0]
            if len(column) < _MIN_ANSWER_COLUMN_RUN:
                continue
            top, bottom = min(lb.top for lb in column), max(lb.bottom for lb in column)
            asked = any(
                left.x0 < x0 - 50.0
                and left.bottom > top
                and left.top < bottom
                and _looks_like_field(clean_label(left.text))
                for left in boxes
            )
            if not asked:
                continue
            page_columns = detected.setdefault(page, [])
            if not any(abs(existing - x0) <= 6.0 for existing in page_columns):
                page_columns.append(x0)
    return detected


def _label_column_x(boxes: list[LineBox], answer_columns: list[float]) -> float | None:
    """The x0 of the field-label column on one page.

    The busiest non-answer column: most lines on a form page are field labels,
    while section headings sit a couple of points *outside* that edge and
    conditional sub-fields sit further in. Picking the leftmost column instead
    would latch onto the heading indent, which repeats once per item group.
    """
    if not boxes:
        return None
    # Labels start on the left, so only the left part of the text area is
    # eligible. Without this an answer column that slipped past detection could
    # win the count and take the whole page's real labels with it.
    left = min(lb.x0 for lb in boxes)
    cutoff = left + 0.35 * (max(lb.x1 for lb in boxes) - left)

    counts: Counter[float] = Counter()
    for lb in boxes:
        if lb.x0 > cutoff or any(abs(lb.x0 - x0) <= 6.0 for x0 in answer_columns):
            continue
        counts[round(lb.x0, 1)] += 1
    if not counts:
        return None
    # Ties break to the left so a page with one label per indent keeps them all.
    x0, best = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return x0 if best >= 2 else min(counts)


def extract_form_sections(
    line_boxes: list[LineBox],
    boilerplate: frozenset[str] | set[str],
    cfg: AcrfConfig,
    page_height: float | None = None,
    form_name: str | None = None,
) -> list[FormSection]:
    """Split one form's lines into the bookmark form plus any titled sub-tables.

    Sections are returned in document order. ``FormSection.name`` is ``None`` for
    the bookmark form itself and set for a grid that carries its own in-page
    heading — the source ALS models such a table as a separate form, so keeping
    them merged would collapse distinct tables into one.
    """
    grids = detect_grids(line_boxes)
    owned = frozenset().union(*(g.consumed for g in grids)) if grids else frozenset()
    body = [lb for lb in line_boxes if id(lb) not in owned]

    labels = _scan_field_labels(body, boilerplate, cfg, page_height, form_name)
    for label in annotation_labels(line_boxes):
        labels.append((_document_key(line_boxes, label), label))

    events: list[tuple[tuple[int, float], str | None, list[str]]] = [(key, None, [label]) for key, label in labels]
    events += [((g.page, g.top), g.title, g.columns) for g in grids]
    events.sort(key=lambda e: e[0])

    sections: list[FormSection] = []
    # De-duplication is per output table, not global: sibling detail tables
    # routinely repeat "Date" / "Result" / "检查项目", and a shared set would let
    # the first table swallow every later table's columns — often emptying them
    # so completely that the table is never emitted at all.
    seen_by_table: dict[str | None, set[str]] = {}
    for _key, title, values in events:
        seen = seen_by_table.setdefault(title, set())
        fresh = [v for v in values if norm(v) and norm(v) not in seen]
        seen.update(norm(v) for v in fresh)
        if not fresh:
            continue
        if not sections or sections[-1].name != title:
            sections.append(FormSection(name=title, fields=[]))
        sections[-1].fields.extend(fresh)
    return sections


def _document_key(line_boxes: list[LineBox], label: str) -> tuple[int, float]:
    """Position of the line an annotation-derived label came from."""
    target = norm(label)
    for line_box in line_boxes:
        match = _ANNOTATION_RE.match(line_box.text.strip())
        if match and any(norm(clean_label(p)) == target for p in re.split(r"[|｜]", match.group(2))):
            return (line_box.page, line_box.top)
    return (line_boxes[-1].page, line_boxes[-1].bottom) if line_boxes else (0, 0.0)


def extract_field_candidates(
    line_boxes: list[LineBox],
    boilerplate: frozenset[str] | set[str],
    cfg: AcrfConfig,
    page_height: float | None = None,
    form_name: str | None = None,
) -> list[str]:
    """Every field label for one form, flattened across sub-tables.

    Thin wrapper over :func:`extract_form_sections` for callers that do not care
    which table a label belongs to (the LLM merge path and the CLI smoke tests).
    """
    return [
        f
        for section in extract_form_sections(line_boxes, boilerplate, cfg, page_height, form_name)
        for f in section.fields
    ]


def _scan_field_labels(
    line_boxes: list[LineBox],
    boilerplate: frozenset[str] | set[str],
    cfg: AcrfConfig,
    page_height: float | None = None,
    form_name: str | None = None,
) -> list[tuple[tuple[int, float], str]]:
    """Positioned, de-duplicated field labels from ordinary (non-grid) lines.

    ``boilerplate`` is the set of **raw** repeated header/footer strings from
    :func:`text.detect_boilerplate`; whole boilerplate lines are dropped and any
    boilerplate glued inline to a label is stripped out. ``page_height`` (if
    given) enables the header/footer margin-band drop via
    ``cfg.header_footer_band``. ``form_name`` is the bookmark-derived form name
    used to remove same-size page titles without treating all body text as a
    title.
    """
    line_boxes = _merge_wrapped_line_boxes(line_boxes)

    # Column geometry is measured on data-entry lines only: header/footer
    # furniture and annotation lines would otherwise be mistaken for the label
    # column and drag the whole page's left edge with them.
    def _is_body(lb: LineBox) -> bool:
        if not lb.text.strip() or _ANNOTATION_RE.match(lb.text.strip()):
            return False
        if norm(lb.text) in {norm(b) for b in boilerplate}:
            return False
        if page_height and page_height > 0:
            band = cfg.header_footer_band * page_height
            return not (lb.top < band or lb.bottom > (page_height - band))
        return True

    body_lines = [lb for lb in line_boxes if _is_body(lb)]
    answer_columns = _detect_answer_columns(body_lines)
    label_x: dict[int, float | None] = {}
    for lb in body_lines:
        label_x.setdefault(
            lb.page,
            _label_column_x([b for b in body_lines if b.page == lb.page], answer_columns.get(lb.page, [])),
        )

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
    out: list[tuple[tuple[int, float], str]] = []
    for lb in line_boxes:
        raw = lb.text
        if not raw or not raw.strip():
            continue
        if any(abs(lb.x0 - x0) <= 6.0 for x0 in answer_columns.get(lb.page, [])):
            continue
        if norm(raw) in bp_norm:
            continue
        # An annotation line documents a field, it is never a field itself; its
        # label is recovered separately by ``annotation_labels``.
        if _ANNOTATION_RE.match(raw.strip()):
            continue
        # Outdented from the label column: an item-group / section heading
        # ("Asthma Control Questionnaire" above its questions), not a field.
        page_label_x = label_x.get(lb.page)
        if page_label_x is not None and lb.x0 < page_label_x - 0.5:
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
        out.append(((lb.page, lb.top), cleaned))
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
