"""
Base AI client abstract class.

This module defines the abstract base class that all AI clients must implement.
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseAIClient(ABC):
    """Abstract base class for AI clients."""

    def __init__(self, api_key: str | None = None):
        """
        Initialize the base AI client.

        Args:
            api_key: Optional API key for the service
        """
        self.api_key = api_key
        self.last_usage: dict[str, Any] | None = None

    @abstractmethod
    def initialize(self) -> bool:
        """
        Initialize the AI client.

        Returns:
            bool: True if initialization was successful
        """

    @abstractmethod
    def generate_content(
        self, prompt: str, max_output_tokens: int = 2000, temperature: float = 0.7, top_p: float = 0.98, top_k: int = 80
    ) -> str:
        """
        Generate content using the AI service.

        Args:
            prompt: The input prompt
            max_output_tokens: Maximum number of tokens to generate
            temperature: Sampling temperature (0.0-1.0)
            top_p: Nucleus sampling parameter
            top_k: Top-k sampling parameter

        Returns:
            Generated content as string
        """

    @abstractmethod
    def get_error_message(self, error: Exception) -> str:
        """
        Extract and format error message from API exceptions.

        Args:
            error: The exception object

        Returns:
            Formatted error message string
        """

    @abstractmethod
    def get_sanitized_model_name(self, model_name: str | None = None) -> str:
        """
        Get a sanitized version of the model name for file naming.

        Args:
            model_name: Optional model name to use instead of the currently set model

        Returns:
            Sanitized model name suitable for filenames
        """

    def get_last_usage(self) -> dict[str, Any] | None:
        """
        Get the usage statistics from the last API call.

        Returns:
            Dictionary with usage statistics or None if not available
        """
        return self.last_usage

    def list_available_models(self) -> list[str]:
        """
        List available models for this client.

        Returns:
            List of available model names
        """
        return []

    @abstractmethod
    def set_model(self, model_name: str) -> None:
        """
        Set the model to use for generation.

        Args:
            model_name: Name of the model to use
        """
