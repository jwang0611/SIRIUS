"""Grid recovery through the real PDF backend, not hand-built LineBoxes.

The unit tests construct ``LineBox`` values directly, which cannot catch a wrong
assumption about what ``pdfplumber`` actually returns. These build a PDF and read
it back, so the whole chars → words → cells → columns chain is exercised.

The table is written with one text-positioning operator per cell, which is how a
real CRF table is typeset: column boundaries become wide gaps while an intra-cell
word space stays a narrow font space. (A page that draws a whole line with a
single ``Tj`` has uniform spacing and therefore no column structure to recover.)
"""

from __future__ import annotations

from pathlib import Path

from src.processors.acrf.fields import detect_grids
from src.processors.acrf.text import extract_all_line_boxes

_FONT_SIZE = 11.0


def _write_table_pdf(
    path: Path,
    rows: list[list[tuple[str, float]]],
    rules: list[tuple[float, float, float]] | None = None,
) -> None:
    """Write a one-page PDF.

    ``rows`` is ``[[(cell_text, x), ...], ...]``; ``rules`` is
    ``[(x, y_bottom, y_top), ...]`` in PDF space, drawn as stroked vertical
    lines — the column rulings a blank CRF uses instead of printed values.
    """

    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    operators: list[str] = []
    y = 700.0
    for row in rows:
        operators += [f"BT /F1 {_FONT_SIZE} Tf 1 0 0 1 {x} {y} Tm ({esc(text)}) Tj ET" for text, x in row]
        y -= 30.0
    operators += [f"{x} {y0} m {x} {y1} l S" for x, y0, y1 in rules or []]
    stream = "\n".join(operators).encode()

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R /Outlines 4 0 R /PageMode /UseOutlines >>",
        2: b"<< /Type /Pages /Kids [6 0 R] /Count 1 >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        4: b"<< /Type /Outlines /First 5 0 R /Last 5 0 R /Count 1 >>",
        5: b"<< /Title (Adverse Events) /Parent 4 0 R /Dest [6 0 R /Fit] >>",
        6: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents 7 0 R >>"
        ),
        7: b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
    }

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    xref = len(out)
    last = max(objects)
    out += f"xref\n0 {last + 1}\n".encode() + b"0000000000 65535 f \n"
    out += b"".join(f"{offsets[n]:010d} 00000 n \n".encode() for n in range(1, last + 1))
    out += f"trailer\n<< /Size {last + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    path.write_bytes(bytes(out))


def test_real_pdf_recovers_multi_word_and_adjacent_narrow_columns(tmp_path: Path):
    pdf = tmp_path / "grid.pdf"
    # "Start Date" / "End Date": two words in one cell, separated by a font space.
    # "Low" / "High": adjacent narrow columns, the case a gap alone cannot judge.
    _write_table_pdf(
        pdf,
        [
            [("No.", 72), ("Start Date", 110), ("End Date", 200), ("Low", 290), ("High", 320)],
            [("1", 72), ("2024-01-01", 110), ("2024-02-01", 200), ("3", 290), ("9", 320)],
        ],
    )

    boxes, _heights, rules = extract_all_line_boxes(str(pdf))
    grids = detect_grids(boxes[0], rules[0])

    assert len(grids) == 1
    assert grids[0].columns == ["Start Date", "End Date", "Low", "High"]


def test_real_pdf_carries_no_space_glyphs(tmp_path: Path):
    """Guards the assumption the cell logic rests on.

    ``pdfplumber`` synthesises the spaces in ``line["text"]`` from positions, so
    word boundaries are only ever positional. Any future rule that keys off a
    space character would silently never fire.
    """
    pdf = tmp_path / "spaces.pdf"
    _write_table_pdf(pdf, [[("Start Date", 110)]])

    boxes, _heights, _rules = extract_all_line_boxes(str(pdf))
    line = boxes[0][0]

    assert line.text == "Start Date"
    assert [word.text for word in line.words] == ["Start", "Date"]


def test_real_pdf_multi_word_body_value_keeps_its_header_intact(tmp_path: Path):
    """A value with a space must not argue against a multi-word header.

    "Visit 1"'s second word lands under the header word "Name"; reading raw word
    positions as column starts would split "Visit Name" and add a variable that
    the CRF never had.
    """
    pdf = tmp_path / "body_cells.pdf"
    _write_table_pdf(
        pdf,
        [
            [("No.", 72), ("Visit Name", 110), ("Date", 200)],
            [("1", 72), ("Visit 1", 110), ("2024-01-01", 200)],
            [("2", 72), ("Visit 2", 110), ("2024-02-01", 200)],
        ],
    )

    boxes, _heights, rules = extract_all_line_boxes(str(pdf))

    assert detect_grids(boxes[0], rules[0])[0].columns == ["Visit Name", "Date"]


def test_real_pdf_stable_body_rows_prove_a_tight_column(tmp_path: Path):
    """Short values two rows deep are evidence that a close header word is a column."""
    pdf = tmp_path / "tight.pdf"
    _write_table_pdf(
        pdf,
        [
            [("No.", 72), ("Date", 110), ("Time", 137)],  # 0.35 em apart
            [("1", 72), ("Y", 110), ("N", 137)],
            [("2", 72), ("Y", 110), ("N", 137)],
        ],
    )

    boxes, _heights, rules = extract_all_line_boxes(str(pdf))

    assert detect_grids(boxes[0], rules[0])[0].columns == ["Date", "Time"]


def test_real_pdf_single_body_row_still_proves_a_column(tmp_path: Path):
    """A grid may legitimately pre-print one row; that row is evidence enough."""
    pdf = tmp_path / "one_row.pdf"
    _write_table_pdf(pdf, [[("No.", 72), ("Date", 110), ("Time", 137)], [("1", 72), ("Y", 110), ("N", 137)]])

    boxes, _heights, rules = extract_all_line_boxes(str(pdf))

    assert detect_grids(boxes[0], rules[0])[0].columns == ["Date", "Time"]


def test_real_pdf_vector_rules_prove_columns_when_cells_are_blank(tmp_path: Path):
    """The common blank aCRF: rows hold only a number, boxes are vector graphics.

    With no values to read, the drawn column rulings are the only evidence that
    "Time" opens its own column rather than continuing "Date".
    """
    pdf = tmp_path / "ruled.pdf"
    _write_table_pdf(
        pdf,
        [[("No.", 72), ("Date", 110), ("Time", 137)], [("1", 72)], [("2", 72)]],
        rules=[(68, 635, 712), (106, 635, 712), (133, 635, 712), (170, 635, 712)],
    )

    boxes, _heights, rules = extract_all_line_boxes(str(pdf))

    assert rules[0], "the stroked rulings must reach the extractor"
    assert {rule[0] for rule in rules[0]} == {0}, "each ruling must retain its page"
    assert detect_grids(boxes[0], rules[0])[0].columns == ["Date", "Time"]


def test_real_pdf_short_control_edge_does_not_split_a_header(tmp_path: Path):
    """A checkbox/input edge inside one row is not a table-column ruling."""
    pdf = tmp_path / "short_control.pdf"
    _write_table_pdf(
        pdf,
        [
            [("No.", 72), ("Visit Name", 110), ("Date", 200)],
            [("1", 72), ("Visit 1", 110), ("2024-01-01", 200)],
        ],
        # Ten points high and aligned under "Name": enough to reproduce the
        # false split when any vertical overlap is accepted as a column.
        rules=[(134, 665, 675)],
    )

    boxes, _heights, rules = extract_all_line_boxes(str(pdf))

    assert rules[0], "the short control edge must reach the coverage filter"
    assert detect_grids(boxes[0], rules[0])[0].columns == ["Visit Name", "Date"]
