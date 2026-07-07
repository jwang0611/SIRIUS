"""Unit tests for :mod:`src.spec_mapper.helpers`."""

from __future__ import annotations

from src.spec_mapper.helpers import calculate_char_length


class TestCalculateCharLength:
    def test_empty_string(self):
        assert calculate_char_length("") == 0

    def test_none_handled(self):
        assert calculate_char_length(None) == 0

    def test_ascii_chars_count_as_one(self):
        assert calculate_char_length("Hello") == 5
        assert calculate_char_length("ABC123") == 6

    def test_cjk_chars_count_as_three(self):
        assert calculate_char_length("中文") == 6

    def test_mixed(self):
        assert calculate_char_length("中文ABC") == 9
        assert calculate_char_length("ABC中") == 6

    def test_cjk_compatibility(self):
        # U+F900 is in CJK Compatibility Ideographs
        assert calculate_char_length("\uf900") == 3

    def test_cjk_extension_a(self):
        # U+3400 is in CJK Extension A
        assert calculate_char_length("\u3400") == 3
