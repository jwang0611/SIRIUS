"""Data model for conditional mapping records."""

from dataclasses import dataclass


@dataclass
class ConditionalRecord:
    """Represents a conditional mapping to be written to a special sheet.

    This records the information needed to create condition-value mappings
    in sheets like FATEST, MITEST, etc.

    Supports two types of conditional mappings:
    1. TESTCD conditions: column_names = ['CRF_TESTCD', 'CRF_ORRES']
    2. TEST conditions: column_names = ['CRF_TEST', 'CRF_ORRES']

    When both TESTCD and TEST conditions exist for the same domain,
    they are stored as separate ConditionalRecord objects.

    Attributes:
        sheet_name: Name of the sheet (e.g., 'FATEST', 'MITEST')
        column_names: List of column names:
                      - For TESTCD: ['CRF_TESTCD', 'CRF_ORRES']
                      - For TEST: ['CRF_TEST', 'CRF_ORRES']
        mappings: List of tuples, one per row. Each tuple contains values for all columns
                  e.g., [('THDIA', '[RAW]TH.THDIA'), ('THFDTC', '[RAW]TH.THFDAT')]
    """

    sheet_name: str
    column_names: list[str]  # All column names in order
    mappings: list[tuple[str, ...]]  # Each tuple has len(column_names) values

    def __repr__(self) -> str:
        """String representation."""
        return f"ConditionalRecord(sheet={self.sheet_name}, cols={self.column_names}, count={len(self.mappings)})"
