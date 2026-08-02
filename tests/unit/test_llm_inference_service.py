"""Unit tests for ``LLMInferenceService``."""

from __future__ import annotations

from typing import List

import pytest

from src.processors.llm_inference_service import (
    LLMInferenceService,
    LLMInferenceServiceError,
)
from tests.conftest import FakeAIClient


class TestParseResponse:
    def test_parses_json_array(self):
        out = LLMInferenceService.parse_response('[{"domain":"AE","sdtm_variable":"AETERM"}]')
        assert out == [{"domain": "AE", "sdtm_variable": "AETERM"}]

    def test_parses_single_object(self):
        out = LLMInferenceService.parse_response('{"domain":"AE","sdtm_variable":"AETERM"}')
        assert out == [{"domain": "AE", "sdtm_variable": "AETERM"}]

    def test_handles_markdown_fence(self):
        raw = '```json\n[{"domain":"AE"}]\n```'
        assert LLMInferenceService.parse_response(raw) == [{"domain": "AE"}]

    def test_extracts_array_from_noisy_text(self):
        raw = 'Here is my answer: [{"domain":"DM"}] cheers!'
        assert LLMInferenceService.parse_response(raw) == [{"domain": "DM"}]

    def test_empty_input_returns_empty(self):
        assert LLMInferenceService.parse_response("") == []
        assert LLMInferenceService.parse_response(None) == []

    def test_malformed_json_returns_empty(self):
        assert LLMInferenceService.parse_response("[not valid json") == []

    def test_filters_non_dict_entries(self):
        out = LLMInferenceService.parse_response('[{"a":1}, "str", 2, null]')
        assert out == [{"a": 1}]

    def test_returns_empty_for_scalar_json(self):
        assert LLMInferenceService.parse_response("42") == []
        assert LLMInferenceService.parse_response('"hello"') == []


class TestInfer:
    def test_infer_uses_client(self):
        client = FakeAIClient(default_response='[{"domain":"AE"}]')
        service = LLMInferenceService(client=client)
        out = service.infer(prompt="please map")
        assert out == [{"domain": "AE"}]
        assert client.calls and client.calls[0]["prompt"] == "please map"

    def test_default_boundary_redacts_sensitive_prompt_without_opt_in(self):
        client = FakeAIClient(default_response="[]")
        service = LLMInferenceService(client=client)

        service.infer(prompt="DOB: 1980-05-15 Subject 001-0023")

        sent = client.calls[0]["prompt"]
        assert "1980-05-15" not in sent
        assert "001-0023" not in sent
        assert "[REDACTED]" in sent

    def test_infer_records_client_params(self):
        client = FakeAIClient(default_response="[]")
        service = LLMInferenceService(client=client, max_output_tokens=500, temperature=0.1)
        service.infer(prompt="p")
        assert client.calls[0]["max_output_tokens"] == 500
        assert client.calls[0]["temperature"] == 0.1

    def test_infer_with_masker_redacts_prompt(self):
        recorded: List[str] = []

        class CapturingClient(FakeAIClient):
            def generate_content(self, prompt, **kwargs):  # type: ignore[override]
                recorded.append(prompt)
                return super().generate_content(prompt, **kwargs)

        class Masker:
            def mask_text(self, text: str) -> str:
                return text.replace("john@example.com", "<EMAIL>")

        svc = LLMInferenceService(client=CapturingClient(default_response="[]"), data_masker=Masker())
        svc.infer(prompt="contact john@example.com")
        assert recorded == ["contact <EMAIL>"]

    def test_masker_exception_blocks_model_call(self):
        class BrokenMasker:
            def mask_text(self, text: str) -> str:
                raise RuntimeError("boom")

        client = FakeAIClient(default_response="[]")
        svc = LLMInferenceService(client=client, data_masker=BrokenMasker())
        with pytest.raises(LLMInferenceServiceError, match="model call blocked"):
            svc.infer(prompt="hello")
        assert client.calls == []

    def test_infer_handles_empty_response(self):
        client = FakeAIClient(default_response="")
        svc = LLMInferenceService(client=client)
        assert svc.infer(prompt="p") == []


class TestExtractJson:
    def test_prefers_array_over_object(self):
        text = '{"x":1} then [{"y":2}]'
        assert LLMInferenceService._extract_json(text) == '[{"y":2}]'

    def test_returns_none_when_no_json(self):
        assert LLMInferenceService._extract_json("plain text") is None
