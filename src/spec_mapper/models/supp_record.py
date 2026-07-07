"""SUPP record model for new row insertion."""

from dataclasses import dataclass


@dataclass
class SUPPRecord:
    """Represents a SUPP record to be inserted.

    Attributes:
        sheet_name: Target sheet/domain (e.g., "DM")
        variable_name: Variable name (QVAL field name, e.g., "SITENAM")
        variable_label: Variable label (from ALS variable name)
        type_value: Data type (default: "Char")
        length: Length (default: "200")
        source: Source (default: "CRF")
        transformation_def: Transformation definition
        variable_type: Variable type (default: "SUPP")
        insert_row: Row number where to insert

    Examples:
        >>> record = SUPPRecord(
        ...     sheet_name="DM",
        ...     variable_name="SITENAM",
        ...     variable_label="中心名称",
        ...     transformation_def="[RAW]SUBJECT.SITENAME"
        ... )
    """

    sheet_name: str
    variable_name: str
    variable_label: str
    transformation_def: str
    insert_row: int
    type_value: str = "Char"
    length: str = "200"
    source: str = "CRF"
    variable_type: str = "SUPP"
    core: str = ""
    controlled_terms: str = ""
    variable_order: str = ""
    cdisc_notes: str = ""
    source_order: int = 0

    def to_row_data(self) -> dict:
        """Convert to row data dictionary with column indices.

        Returns:
            Dictionary mapping column indices (1-based) to values

        Column mapping (standard SDTM template):
            1: Variable Name
            2: Variable Label
            3: Type
            4: Length
            5: Controlled Terms
            6: Source
            7: Core
            8: Transformation definition
            9: Variable Type
            10: Variable Order
            11: CDISC Notes
        """
        return {
            1: self.variable_name,
            2: self.variable_label,
            3: self.type_value,
            4: self.length,
            5: self.controlled_terms,
            6: self.source,
            7: self.core,
            8: self.transformation_def,
            9: self.variable_type,
            10: self.variable_order,
            11: self.cdisc_notes,
        }

    def __repr__(self) -> str:
        """String representation."""
        return f"SUPPRecord(sheet={self.sheet_name}, var={self.variable_name}, row={self.insert_row})"
