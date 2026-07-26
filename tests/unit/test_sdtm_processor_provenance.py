"""Production-path provenance contracts for ``SDTMProcessor``."""

from __future__ import annotations

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
