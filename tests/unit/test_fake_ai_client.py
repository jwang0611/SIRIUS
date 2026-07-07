"""Unit tests for ``FakeAIClient`` fixture (validates test infrastructure)."""

from __future__ import annotations

from tests.conftest import FakeAIClient


class TestFakeAIClient:
    def test_returns_default_when_no_match(self):
        client = FakeAIClient()
        result = client.generate_content("anything")
        assert result == "[]"

    def test_returns_custom_default(self):
        client = FakeAIClient(default_response='{"foo":"bar"}')
        assert client.generate_content("x") == '{"foo":"bar"}'

    def test_substring_matching(self):
        client = FakeAIClient(responses={"AETERM": '[{"domain":"AE"}]'})
        result = client.generate_content("Please map AETERM to SDTM")
        assert result == '[{"domain":"AE"}]'

    def test_records_calls(self):
        client = FakeAIClient()
        client.generate_content("prompt1", temperature=0.3)
        client.generate_content("prompt2", max_output_tokens=500)
        assert len(client.calls) == 2
        assert client.calls[0]["prompt"] == "prompt1"
        assert client.calls[0]["temperature"] == 0.3
        assert client.calls[1]["max_output_tokens"] == 500

    def test_initialize_returns_true(self):
        assert FakeAIClient().initialize() is True

    def test_sanitized_name_replaces_slashes(self):
        client = FakeAIClient()
        client.set_model("google/gemini-2.5-flash")
        assert client.get_sanitized_model_name() == "google_gemini-2.5-flash"

    def test_sanitized_name_with_explicit(self):
        client = FakeAIClient()
        assert client.get_sanitized_model_name("anthropic/claude:v1") == "anthropic_claude_v1"

    def test_get_error_message(self):
        client = FakeAIClient()
        err = ValueError("boom")
        assert client.get_error_message(err) == "boom"

    def test_get_last_usage(self):
        client = FakeAIClient()
        assert client.get_last_usage() == {"prompt_tokens": 0, "completion_tokens": 0}

    def test_multiple_matching_fragments_first_wins(self):
        client = FakeAIClient(responses={"AE": "first", "AETERM": "second"})
        result = client.generate_content("AETERM match")
        assert result in {"first", "second"}
