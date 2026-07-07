"""ALS file record data model."""

from dataclasses import dataclass, field


@dataclass
class ALSRecord:
    """Represents a single record from the ALS Excel file.

    Attributes:
        table: 表 (Table name, fallback for [RAW] reference)
        variable: 变量 (Variable name)
        variable_label: 变量名 (Variable label)
        format: 格式 (Format)
        sdtm_domain: SDTM domain
        sdtm_variable: SDTM Variable
        row_index: Original row index in the Excel file (0-based)
        metadata_table: metadata_table (preferred table name for [RAW] reference)
        component_type: 组件类型 (Component type, e.g., "单选", "多选", "文本")
        codelist_name: 编码名称 (Codelist name, if applicable)

    Examples:
        >>> record = ALSRecord(
        ...     table="DM",
        ...     variable="SUBJID",
        ...     variable_label="受试者编号",
        ...     format="TEXT",
        ...     sdtm_domain="DM",
        ...     sdtm_variable="USUBJID",
        ...     row_index=5
        ... )
        >>> print(record.table)
        DM
    """

    table: str
    variable: str
    variable_label: str
    format: str | None
    sdtm_domain: str
    sdtm_variable: str
    row_index: int
    metadata_table: str | None = field(default=None)
    component_type: str | None = field(default=None)
    codelist_name: str | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate and clean data after initialization."""
        # Strip whitespace from string fields
        self.table = self.table.strip() if self.table else ""
        self.variable = self.variable.strip() if self.variable else ""
        self.variable_label = self.variable_label.strip() if self.variable_label else ""
        self.sdtm_domain = self.sdtm_domain.strip() if self.sdtm_domain else ""
        self.sdtm_variable = self.sdtm_variable.strip() if self.sdtm_variable else ""
        if self.format:
            self.format = self.format.strip()
        if self.metadata_table:
            self.metadata_table = self.metadata_table.strip()
        if self.component_type:
            self.component_type = self.component_type.strip()
        if self.codelist_name:
            self.codelist_name = self.codelist_name.strip()

    def is_valid(self) -> bool:
        """Check if the record has all required fields.

        Returns:
            True if all required fields are non-empty, False otherwise.
        """
        return bool(self.table and self.variable and self.sdtm_domain and self.sdtm_variable)

    def get_raw_reference(self) -> str:
        """Generate the [RAW]table.variable reference string.

        Uses metadata_table if available, otherwise falls back to table.

        Returns:
            Formatted reference string like "[RAW]DM.SUBJID"

        Examples:
            >>> record = ALSRecord("DM", "SUBJID", "受试者编号", "TEXT",
            ...                    "DM", "USUBJID", 5, metadata_table="DM_RAW")
            >>> record.get_raw_reference()
            '[RAW]DM_RAW.SUBJID'
        """
        # Prefer metadata_table over table for [RAW] reference
        table_name = self.metadata_table or self.table
        return f"[RAW]{table_name}.{self.variable}"

    def needs_codelist_hint(self, trigger_component_type: str = "单选") -> bool:
        """Check if this record needs a codelist hint note.

        Returns True if:
        - codelist_name has a non-empty value
        - component_type equals the trigger value (default: "单选")

        Args:
            trigger_component_type: Component type that triggers the hint (default: "单选")

        Returns:
            True if codelist hint should be added, False otherwise

        Examples:
            >>> record = ALSRecord(..., component_type="单选", codelist_name="SEX")
            >>> record.needs_codelist_hint()
            True
            >>> record = ALSRecord(..., component_type="文本", codelist_name="")
            >>> record.needs_codelist_hint()
            False
        """
        return bool(self.codelist_name and self.component_type == trigger_component_type)
