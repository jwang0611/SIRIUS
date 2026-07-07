"""Data model for CODELIST records."""

from dataclasses import dataclass


@dataclass
class CodelistRecord:
    """Represents a CODELIST entry to be written to CODELIST sheet.

    For xxTESTCD values extracted from conditional mappings.

    Attributes:
        domain: Domain name (e.g., "EG") - Column A
        testcd_var: TESTCD variable name (e.g., "EGTESTCD") - Column B
        data_type: Data type, always "Char" - Column E
        testcd_value: TESTCD value (e.g., "QT") - Column F
        source_variable: Original variable name from annotation (e.g., "QT间期（ms）") - Column I
        metadata_variable: EDC variable name (e.g., "QTINT") - Column J

    Examples:
        >>> record = CodelistRecord(
        ...     domain="EG",
        ...     testcd_var="EGTESTCD",
        ...     data_type="Char",
        ...     testcd_value="QT",
        ...     source_variable="QT间期（ms）"
        ... )
    """

    domain: str
    testcd_var: str
    testcd_value: str
    source_variable: str
    metadata_variable: str = ""
    data_type: str = "Char"

    def to_row_data(self) -> dict:
        """Convert to row data dictionary with column indices.

        Returns:
            Dictionary mapping column indices (1-based) to values

        Column mapping (CODELIST sheet):
            1 (A): Domain
            2 (B): TESTCD variable name
            5 (E): Data type (Char)
            6 (F): TESTCD value
            9 (I): Source variable name
            10 (J): EDC variable name (metadata_variable)
        """
        return {
            1: self.domain,
            2: self.testcd_var,
            5: self.data_type,
            6: self.testcd_value,
            9: self.source_variable,
            10: self.metadata_variable,
        }

    def __repr__(self) -> str:
        """String representation."""
        return f"CodelistRecord(domain={self.domain}, testcd={self.testcd_var}, value={self.testcd_value})"
