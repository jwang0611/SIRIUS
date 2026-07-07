"""
Data models for SDTM recommendations.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class VariableMapping:
    """Represents a mapping between metadata and annotation variables."""

    metadata_table: str
    metadata_variable: str
    annotation_table: str
    annotation_variable: str


@dataclass
class DomainRecommendation:
    """Represents a single SDTM domain recommendation."""

    domain: str
    sdtm_variable: str
    score: float
    priority: int
    variable_name: str


@dataclass
class TableRecommendation:
    """Represents recommendations for an entire table."""

    table_name: str
    domain_recommendations: list[DomainRecommendation]
    original_mappings: list[VariableMapping]


@dataclass
class GenerationConfig:
    """Configuration for content generation."""

    max_output_tokens: int = 2000
    temperature: float = 0.3
    top_p: float = 0.95
    top_k: int = 40


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""

    requests_per_minute: int = 30


def create_variable_mapping(data: dict[str, Any]) -> VariableMapping:
    """Create a VariableMapping from dictionary data."""
    return VariableMapping(
        metadata_table=data.get("metadata_table", ""),
        metadata_variable=data.get("metadata_variable", ""),
        annotation_table=data.get("annotation_table", ""),
        annotation_variable=data.get("annotation_variable", ""),
    )


def create_domain_recommendation(data: dict[str, Any]) -> DomainRecommendation:
    """Create a DomainRecommendation from dictionary data."""
    return DomainRecommendation(
        domain=data.get("domain", ""),
        sdtm_variable=data.get("sdtm_variable", ""),
        score=float(data.get("score", 0.0)),
        priority=int(data.get("priority", 1)),
        variable_name=data.get("variable_name", ""),
    )


def create_table_recommendation(data: dict[str, Any]) -> TableRecommendation:
    """Create a TableRecommendation from dictionary data."""
    return TableRecommendation(
        table_name=data.get("table_name", ""),
        domain_recommendations=[create_domain_recommendation(rec) for rec in data.get("domain_recommendations", [])],
        original_mappings=[create_variable_mapping(mapping) for mapping in data.get("original_mappings", [])],
    )
