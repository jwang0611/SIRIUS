"""Excel manipulation helpers extracted from :class:`ExcelWriter`.

These modules contain pure, unit-testable helpers that operate on
openpyxl workbooks/worksheets. ``ExcelWriter`` delegates to them while
keeping its public API stable.
"""

from __future__ import annotations

from .format_utils import (
    apply_row_format,
    copy_row_format,
    find_last_row_with_value,
    find_merged_ranges_overlapping,
    highlight_sheet_tabs,
    set_column_wrap_text,
    unmerge_rows_in_range,
)

__all__ = [
    "apply_row_format",
    "copy_row_format",
    "find_last_row_with_value",
    "find_merged_ranges_overlapping",
    "highlight_sheet_tabs",
    "set_column_wrap_text",
    "unmerge_rows_in_range",
]
