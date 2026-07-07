"""
Data masking for PHI/PII redaction before sending to LLM APIs.

Ensures sensitive patient information is not transmitted to external
AI services. Follows ClinAgent's ContextMinimizer principle: only
send necessary data to the agent, reducing exposure.
"""

import logging
import re
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


class DataMasker:
    """Redacts PHI/PII from variable data and prompts before LLM transmission.

    This masks common sensitive patterns found in clinical CRF data:
    - Subject identifiers (SUBJID, USUBJID patterns)
    - SSN formats
    - Subject number patterns
    - Date of birth patterns (when not needed for SDTM mapping)
    """

    # Patterns that match sensitive data in variable values or annotations
    SENSITIVE_PATTERNS: ClassVar[list[re.Pattern]] = [
        # SSN format: XXX-XX-XXXX
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        # Subject ID patterns: SITE-SUBJECT (e.g., "001-001", "101-002")
        re.compile(r"\b[A-Z]*\d{3,4}-\d{3,4}\b"),
        # Full name patterns (Last, First)
        re.compile(r"\b[A-Z][a-z]+,\s*[A-Z][a-z]+\b"),
        # Date patterns that could be DOB (YYYY-MM-DD in isolation)
        # Only mask when preceded by DOB/birth context keywords
        re.compile(r"(?i)(?:DOB|birth|born|date of birth)[:\s]*\d{4}-\d{2}-\d{2}"),
        # Email addresses
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
        # Phone numbers
        re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"),
    ]

    REDACTION_MARKER = "[REDACTED]"

    def mask_text(self, text: str) -> str:
        """Mask sensitive patterns in a text string.

        Args:
            text: Input text that may contain sensitive data

        Returns:
            Text with sensitive patterns replaced by [REDACTED]
        """
        if not text:
            return text

        masked = text
        for pattern in self.SENSITIVE_PATTERNS:
            masked = pattern.sub(self.REDACTION_MARKER, masked)
        return masked

    def mask_variable_data(self, variable_data: dict[str, Any]) -> dict[str, Any]:
        """Mask sensitive data in a variable metadata dict.

        Only masks annotation values and other free-text fields that
        might contain patient-identifying information. Preserves
        structural fields needed for SDTM mapping.

        Args:
            variable_data: Variable metadata dict

        Returns:
            Copy with sensitive data redacted
        """
        masked = dict(variable_data)

        # Mask free-text annotation fields
        for field in ["annotation_variable", "annotation_table", "value", "comment"]:
            val = masked.get(field)
            if isinstance(val, str):
                masked[field] = self.mask_text(val)

        return masked
