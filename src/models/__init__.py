"""SDTM data models.

Two tiers:
- ``sdtm_models``: legacy dataclasses used internally by processors.
- ``boundary``: pydantic models used at module boundaries (JSON I/O,
  LLM responses, inter-service contracts).
"""

from src.models.boundary import (  # noqa: F401
    CascadeResult,
    DomainRecommendationRecord,
    MappingResult,
    TableRecommendationRecord,
    VariableInput,
)
from src.models.sdtm_models import *  # noqa: F403
