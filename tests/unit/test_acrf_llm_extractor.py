"""Optional LLM field-extraction pass: validation + masking (no creds)."""

from __future__ import annotations

from src.processors.acrf.llm_extractor import extract_fields_llm, parse_label_list


class _FakeClient:
    """Minimal BaseAIClient-shaped stub: returns a canned response."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.prompt: str | None = None

    def generate_content(self, prompt: str, **_kwargs) -> str:
        self.prompt = prompt
        return self.response


def test_llm_drops_hallucination_and_options_keeps_valid_and_merges_wraps():
    page_text = "性别 ○ 男 ○ 女\n性别为女性时，是否需要进行妊\n娠检查？"
    # A valid label, a legitimately merged wrapped label, a hallucination, an option.
    response = '```json\n["性别", "性别为女性时，是否需要进行妊娠检查？", "凭空捏造字段", "○ 是"]\n```'

    out = extract_fields_llm(page_text, form_name="人口学", client=_FakeClient(response))

    assert "性别" in out
    assert "性别为女性时，是否需要进行妊娠检查？" in out  # wrap-merge allowed
    assert "凭空捏造字段" not in out  # not in page text → hallucination dropped
    assert all(x != "是" for x in out)  # option answer dropped by stoplist


def test_llm_masks_text_before_sending():
    class _Masker:
        def mask_text(self, text: str) -> str:
            return text.replace("001-002", "[REDACTED]")

    client = _FakeClient("[]")
    extract_fields_llm("Subject 001-002", form_name="x", client=client, masker=_Masker())

    assert client.prompt is not None
    assert "001-002" not in client.prompt
    assert "[REDACTED]" in client.prompt


def test_llm_client_error_returns_empty():
    class _Boom:
        def generate_content(self, *_a, **_k) -> str:
            raise RuntimeError("boom")

    assert extract_fields_llm("some text", form_name="x", client=_Boom()) == []


def test_parse_label_list_is_tolerant():
    assert parse_label_list('noise ["a", "b"] trailing') == ["a", "b"]
    assert parse_label_list("not json at all") == []
    assert parse_label_list("") == []
    assert parse_label_list('{"not": "a list"}') == []
