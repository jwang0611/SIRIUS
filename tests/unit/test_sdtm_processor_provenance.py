"""Production-path provenance contracts for ``SDTMProcessor``."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.processors.sdtm_processor import SDTMProcessor


def _bare_processor() -> SDTMProcessor:
    processor = object.__new__(SDTMProcessor)
    processor.audit_logger = None
    processor.debug = False
    return processor


def test_successful_result_gets_cascade_level_even_without_audit_logger():
    processor = _bare_processor()
    recs = [{"domain": "FA", "sdtm_variable": "FAORRES", "source": "RAG"}]

    processor._audit_mapping_result({}, recs, cascade_level=3)

    assert recs[0]["cascade_level"] == 3


def test_not_submitted_prefilter_has_level_zero():
    processor = _bare_processor()
    processor.kb_hints = type(
        "Hints",
        (),
        {"is_not_submitted_table": lambda self, value: True},
    )()

    recs = processor.process_variable_pair(
        "SUBJECT",
        {
            "metadata_variable": "CRFVER",
            "annotation_table": "Questionnaire",
        },
        [],
    )

    assert recs is not None
    assert recs[0]["source"] == "KB_NOT_SUBMITTED"
    assert recs[0]["cascade_level"] == 0


def test_llm_fallback_has_level_four():
    processor = _bare_processor()

    recs = processor._build_fallback_recommendations(
        "AE",
        {"metadata_variable": "AETERM"},
        "AE",
        "Failed to parse AI JSON",
    )

    assert recs[0]["source"] == "FALLBACK"
    assert recs[0]["cascade_level"] == 4


def test_coverage_repair_does_not_invent_cascade_level():
    processor = _bare_processor()
    processor._recommend_domain_from_annotation = lambda annotation_table: "AE"
    processor._map_table_to_domain = lambda table_name: "AE"

    recommendations = processor._ensure_all_variables_covered(
        [],
        [
            {
                "metadata_table": "AE",
                "metadata_variable": "AETERM",
                "annotation_table": "Adverse Events",
                "annotation_variable": "Adverse event term",
            }
        ],
    )

    rec = recommendations[0]["domain_recommendations"][0]
    assert rec["source"] == "UNMAPPED"
    assert rec["cascade_level"] is None


def test_level_four_passes_retrieved_rag_contexts_to_prompt_builder():
    processor = _bare_processor()
    processor.kb_hints = None
    processor._get_knowledge_base_context = lambda variable_data: {}
    processor._recommend_domain_from_annotation = lambda annotation_table: "XX"
    retrieved_contexts = [object()]
    processor._try_cascade_shortcircuit = lambda *args: SimpleNamespace(
        recs=None,
        rag_contexts=retrieved_contexts,
        rag_info={"top_score": 0.5},
    )
    observed: dict[str, object] = {}

    def observe_prompt_call(
        variable_data,
        kb_context,
        all_table_variables,
        completed_table_mappings,
        rag_contexts=None,
    ):
        observed["rag_contexts"] = rag_contexts
        raise RuntimeError("prompt wiring observed")

    processor._create_enhanced_prompt = observe_prompt_call

    with pytest.raises(RuntimeError, match="prompt wiring observed"):
        processor.process_variable_pair(
            "SYNTHETIC",
            {
                "metadata_variable": "RAWVAR",
                "annotation_table": "Synthetic Table",
            },
            [],
        )

    assert observed["rag_contexts"] is retrieved_contexts


def test_enhanced_prompt_appends_structured_rag_context_block():
    processor = _bare_processor()
    processor.data_masker = None
    processor.kb_hints = None
    processor.rag_char_limit = 1500
    processor._infer_candidate_domains = lambda variable_data, kb_context: ["XX"]
    processor.prompt_generator = SimpleNamespace(
        generate_variable_prompt=lambda **kwargs: "BASE PROMPT",
    )
    observed: dict[str, object] = {}

    def build_context_block(contexts, char_limit, structured=True):
        observed.update(
            contexts=contexts,
            char_limit=char_limit,
            structured=structured,
        )
        return "STRUCTURED RAG CONTEXT"

    processor.rag_augmenter = SimpleNamespace(
        build_context_block=build_context_block,
    )
    retrieved_contexts = [object()]

    prompt = processor._create_enhanced_prompt(
        {
            "metadata_variable": "RAWVAR",
            "annotation_table": "Synthetic Table",
        },
        {},
        rag_contexts=retrieved_contexts,
    )

    assert prompt.endswith("STRUCTURED RAG CONTEXT")
    assert observed == {
        "contexts": retrieved_contexts,
        "char_limit": 1500,
        "structured": True,
    }
