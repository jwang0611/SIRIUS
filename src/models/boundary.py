"""Pydantic boundary models for the recommendation pipeline.

These are **boundary** objects — they flow between modules (processor ↔
spec mapper ↔ web layer), are serialised to JSON, and frequently come in
from untrusted JSON produced by the LLM. Runtime validation here catches
schema drift early rather than at a random downstream `KeyError`.

In-memory configs that never cross module boundaries stay as plain
dataclasses (see ``sdtm_models.py``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VariableInput(BaseModel):
    """Input record describing one eCRF variable to map."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    metadata_table: str = ""
    metadata_variable: str = ""
    annotation_table: str = ""
    annotation_variable: str = ""
    SDTM_Domain: str | None = None
    SDTM_Variable: str | None = None


class DomainRecommendationRecord(BaseModel):
    """Recommendation for a single (variable → SDTM variable) candidate."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    domain: str
    sdtm_variable: str
    sdtm_variable_type: Literal["standard", "supplementary", "unknown"] = "standard"
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    variable_name: str = ""
    source: str = "LLM"
    priority: int | None = None
    confidence: float | None = None
    rationale: str | None = None
    cascade_level: int | None = Field(default=None, ge=1, le=4)

    @field_validator("domain", "sdtm_variable")
    @classmethod
    def _upper(cls, value: str) -> str:
        return (value or "").strip().upper()


class CascadeResult(BaseModel):
    """Result of the ``CascadePredictor`` for a single variable."""

    model_config = ConfigDict(extra="forbid")

    level: Literal[1, 2, 3, 4]
    source: Literal["KB_DIRECT", "KB_HIGH", "RAG_HIGH", "LLM"]
    confidence: float = Field(ge=0.0, le=1.0)
    domain: str | None = None
    sdtm_variable: str | None = None
    sdtm_variable_type: str | None = None
    short_circuited: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class MappingResult(BaseModel):
    """Per-variable mapping outcome after the full pipeline."""

    model_config = ConfigDict(extra="allow")

    variable_input: VariableInput
    recommendations: list[DomainRecommendationRecord] = Field(default_factory=list)
    cascade: CascadeResult | None = None
    errors: list[str] = Field(default_factory=list)


class TableRecommendationRecord(BaseModel):
    """Aggregate recommendations for an entire annotation table."""

    model_config = ConfigDict(extra="allow")

    table_name: str
    domain_recommendations: list[DomainRecommendationRecord] = Field(default_factory=list)
    original_mappings: list[VariableInput] = Field(default_factory=list)


__all__ = [
    "CascadeResult",
    "DomainRecommendationRecord",
    "MappingResult",
    "TableRecommendationRecord",
    "VariableInput",
]
