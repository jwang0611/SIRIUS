"""aCRF/eCRF PDF ingestion: form names + field labels → canonical records.

Public API:
    extract_acrf(pdf_path, ...) -> ExtractionResult
    write_processed_json / write_processed_xlsx / write_als2sdtm_xlsx
"""

from __future__ import annotations

from src.processors.acrf.extractor import extract_acrf
from src.processors.acrf.models import (
    AcrfConfig,
    AcrfRecord,
    ExtractionResult,
    FormSpan,
    LineBox,
)
from src.processors.acrf.writers import (
    write_als2sdtm_xlsx,
    write_processed_json,
    write_processed_xlsx,
)

__all__ = [
    "AcrfConfig",
    "AcrfRecord",
    "ExtractionResult",
    "FormSpan",
    "LineBox",
    "extract_acrf",
    "write_als2sdtm_xlsx",
    "write_processed_json",
    "write_processed_xlsx",
]
