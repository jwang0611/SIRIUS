"""Template file record data model."""

from dataclasses import dataclass


@dataclass
class TemplateRecord:
    """Represents a single record from the SDTM Spec Template Excel file.

    Attributes:
        variable_name: 变量名称 (Variable name) - matches with SDTM Variable in ALS
        transformation_def: 转换定义 (Transformation definition) - to be filled
        row_index: Row index in the Excel file (1-based, Excel row number)
        col_index: Column index for transformation definition (1-based, Excel column number)
        sheet_name: The sheet name this record belongs to
        domain: The domain extracted from sheet_name (for 2-char sheets)

    Examples:
        >>> record = TemplateRecord(
        ...     variable_name="USUBJID",
        ...     transformation_def="",
        ...     row_index=15,
        ...     col_index=5,
        ...     sheet_name="DM",
        ...     domain="DM"
        ... )
        >>> print(record.variable_name)
        USUBJID
    """

    variable_name: str
    transformation_def: str
    row_index: int
    col_index: int
    sheet_name: str
    domain: str | None = None

    def __post_init__(self) -> None:
        """Validate and clean data after initialization."""
        # Strip whitespace
        self.variable_name = self.variable_name.strip() if self.variable_name else ""
        self.transformation_def = self.transformation_def.strip() if self.transformation_def else ""
        self.sheet_name = self.sheet_name.strip() if self.sheet_name else ""

        # Auto-extract domain from 2-char sheet names
        if not self.domain and len(self.sheet_name) == 2:
            self.domain = self.sheet_name

    def is_valid(self) -> bool:
        """Check if the record has all required fields.

        Returns:
            True if all required fields are non-empty, False otherwise.
        """
        return bool(self.variable_name and self.row_index > 0 and self.col_index > 0 and self.sheet_name)


@dataclass
class CellUpdate:
    """Represents a cell update operation for the Excel writer.

    Attributes:
        sheet_name: The sheet name to update
        row: Row number (1-based, Excel row number)
        col: Column number (1-based, Excel column number)
        value: The new value to write to the cell
        reason: Optional description of why this update is being made

    Examples:
        >>> update = CellUpdate(
        ...     sheet_name="DM",
        ...     row=15,
        ...     col=5,
        ...     value="[RAW]DM.SUBJID",
        ...     reason="Simple 1:1 mapping"
        ... )
    """

    sheet_name: str
    row: int
    col: int
    value: str
    reason: str | None = None

    def __post_init__(self) -> None:
        """Validate data after initialization."""
        if self.row <= 0:
            raise ValueError(f"Row must be positive, got {self.row}")
        if self.col <= 0:
            raise ValueError(f"Column must be positive, got {self.col}")
