"""
OpenRouter client implementation.
"""

import json
import os
from typing import Any, cast

from openai import OpenAI

from .base_client import BaseAIClient


class OpenRouterClient(BaseAIClient):
    """OpenRouter client implementation."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        """
        Initialize the OpenRouter client.

        Args:
            api_key: Optional API key (will use env variable if not provided)
            base_url: Optional base URL (defaults to OpenRouter API)
        """
        super().__init__(api_key)
        self.base_url = base_url or "https://openrouter.ai/api/v1"
        self.client: OpenAI | None = None
        self.model_name = "google/gemini-2.5-flash"  # Default model

    def initialize(self) -> bool:
        """
        Initialize the OpenRouter API client.

        Returns:
            bool: True if initialization was successful
        """
        try:
            # Try to get API key from environment if not provided
            if not self.api_key:
                self.api_key = os.getenv("OPENROUTER_API_KEY")
                if not self.api_key:
                    raise ValueError("No API key provided and OPENROUTER_API_KEY environment variable not set")

            # Initialize the OpenAI client with OpenRouter configuration
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            return True

        except Exception as e:
            print(f"Error initializing OpenRouter client: {e!s}")
            return False

    def generate_content(
        self, prompt: str, max_output_tokens: int = 2000, temperature: float = 0.3, top_p: float = 0.98, top_k: int = 80
    ) -> str:
        """
        Generate content using OpenRouter's API.

        Args:
            prompt: The input prompt
            max_output_tokens: Maximum number of tokens to generate
            temperature: Sampling temperature (0.0-1.0)
            top_p: Nucleus sampling parameter
            top_k: Top-k sampling parameter (not used by OpenAI-compatible APIs)

        Returns:
            Generated content as string
        """
        if not self.client:
            raise ValueError("OpenRouter client not initialized. Call initialize() first.")

        try:
            # Create the request payload
            system_instruction = (
                "You are a CDISC SDTM mapping expert. "
                "ALWAYS strictly follow the provided SDTM mapping rules if present. "
                "When rules conflict with heuristics, the rules take precedence. "
                "Respond with minimal valid JSON."
            )
            payload = {
                "model": self.model_name,
                "messages": [{"role": "system", "content": system_instruction}, {"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_output_tokens,
                "response_format": {"type": "json_object"},
            }

            # Make API call (payload is dict; OpenAI stubs expect typed kwargs)
            response = self.client.chat.completions.create(**cast(Any, payload))

            # Capture usage if provided (OpenAI-compatible response)
            try:
                usage = getattr(response, "usage", None)
                if usage:
                    self.last_usage = {
                        "prompt_tokens": getattr(usage, "prompt_tokens", None)
                        or (usage.get("prompt_tokens", 0) if hasattr(usage, "get") else None),
                        "completion_tokens": getattr(usage, "completion_tokens", None)
                        or (usage.get("completion_tokens", 0) if hasattr(usage, "get") else None),
                        "total_tokens": getattr(usage, "total_tokens", None)
                        or (usage.get("total_tokens", 0) if hasattr(usage, "get") else None),
                    }
            except Exception:
                self.last_usage = None

            # Extract and return the generated content
            return response.choices[0].message.content.strip()

        except Exception as e:
            error_msg = self.get_error_message(e)
            raise Exception(f"Error generating content: {error_msg}") from e

    def get_error_message(self, error: Exception) -> str:
        """
        Extract and format error message from OpenRouter API exceptions.

        Args:
            error: The exception object

        Returns:
            Formatted error message string
        """
        # Handle OpenRouter API-specific errors (similar to OpenAI)
        if hasattr(error, "response"):
            try:
                # Try to extract structured error info from OpenRouter response
                error_data = error.response.json()
                if "error" in error_data:
                    error_info = error_data["error"]
                    msg = f"API Error: {error_info.get('message', 'Unknown error')}"
                    if "type" in error_info:
                        msg += f" (Type: {error_info['type']})"
                    if "code" in error_info:
                        msg += f" (Code: {error_info['code']})"
                    return msg
            except (AttributeError, ValueError, json.JSONDecodeError):
                pass

        # For OpenRouter-specific errors
        if hasattr(error, "details"):
            return f"OpenRouter Error: {error.details}"

        # Fall back to string representation of the exception
        return f"Error: {error!s}"

    def list_available_models(self) -> list[str]:
        """
        List available OpenRouter models.

        Returns:
            List of available model names
        """
        try:
            if not self.client:
                raise ValueError("OpenRouter client not initialized. Call initialize() first.")

            models = self.client.models.list()
            # OpenRouter provides models from various providers
            return [model.id for model in models.data]
        except Exception as e:
            print(f"Error listing models: {e!s}")
            return []

    def set_model(self, model_name: str) -> None:
        """
        Set the model to use for generation.

        Args:
            model_name: Name of the model to use (e.g., "openai/gpt-4o", "anthropic/claude-3-opus")
        """
        self.model_name = model_name

    def get_sanitized_model_name(self, model_name: str | None = None) -> str:
        """
        Get a sanitized version of the model name for file naming.

        Args:
            model_name: Optional model name to use instead of the currently set model

        Returns:
            Sanitized model name suitable for filenames
        """
        # Use the provided model name or the current model name
        name_to_sanitize = model_name or self.model_name

        # OpenRouter model names are in format "provider/model-name"
        # e.g., "openai/gpt-4o" -> "openai_gpt-4o"
        # e.g., "anthropic/claude-3-opus" -> "anthropic_claude-3-opus"

        # Replace forward slashes with underscores
        sanitized = name_to_sanitize.replace("/", "_")

        # Remove any special characters that might cause issues in filenames
        sanitized = sanitized.replace(":", "-").replace(" ", "_")

        # Limit length to avoid overly long filenames
        return sanitized[:30]
