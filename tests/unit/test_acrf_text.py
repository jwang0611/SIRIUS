"""Character → word grouping, the geometry table-column recovery relies on."""

from __future__ import annotations

from src.processors.acrf.text import _chars_to_words


def _chars(spec: list[tuple[str, float, float]]) -> list[dict]:
    return [{"text": t, "x0": x0, "x1": x1} for t, x0, x1 in spec]


def test_wide_gaps_split_words_and_kerning_does_not():
    words = _chars_to_words(
        _chars(
            [
                ("N", 40.0, 47.0),
                ("o", 47.0, 53.0),
                (".", 53.2, 56.0),  # kerning-sized gap stays inside the word
                ("检", 66.0, 78.0),  # column gap splits
                ("查", 78.0, 90.0),
                ("结", 189.0, 201.0),
                ("果", 201.0, 213.0),
            ]
        )
    )

    assert [(w.text, w.x0, w.x1) for w in words] == [
        ("No.", 40.0, 56.0),
        ("检查", 66.0, 90.0),
        ("结果", 189.0, 213.0),
    ]


def test_whitespace_breaks_a_word_without_becoming_one():
    words = _chars_to_words(_chars([("单", 43.0, 55.0), (" ", 55.0, 58.0), ("位", 58.0, 70.0)]))

    assert [w.text for w in words] == ["单", "位"]


def test_chars_without_geometry_are_skipped():
    chars = [*_chars([("A", 40.0, 48.0)]), {"text": "B"}]

    assert [w.text for w in _chars_to_words(chars)] == ["A"]


def test_no_chars_yields_no_words():
    assert _chars_to_words([]) == ()
