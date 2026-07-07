"""
Infrastructure layer for compliance, audit logging, and data masking.

Following the ClinAgent five-layer architecture, this module provides
GxP-ready infrastructure including:
- AuditLogger: Structured audit trail for mapping operations
- DataMasker: PHI/PII redaction before sending data to LLM APIs
- configure_logging: centralised logging setup
"""

from .logging import configure_logging, get_logger  # noqa: F401
