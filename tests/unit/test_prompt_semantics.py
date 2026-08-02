"""Semantic contracts shared by prompt YAML and the production prompt path."""

from __future__ import annotations

import re

from scripts.prompt_ci.validate_prompts import check_example_output_contract
from src.infrastructure.data_masker import DataMasker
from src.processors.sdtm_processor import SDTMProcessor
from src.prompts import sdtm_rules
from src.prompts.loader import clear_cache, load_examples, load_rules, load_template
from src.prompts.sdtm_prompts_simple import SDTMPromptGenerator

_PURE_VARIABLE_RE = re.compile(r"^[A-Z][A-Z0-9]{0,7}$")


def test_runtime_rule_categories_are_exact_yaml_copies():
    yaml_rules = load_rules()

    assert sdtm_rules.CORE_RULES == yaml_rules["core_rules"]
    assert sdtm_rules.PATTERN_RULES == yaml_rules["pattern_rules"]
    assert sdtm_rules.DOMAIN_RULES == yaml_rules["domain_rules"]
    assert sdtm_rules.DOMAIN_TYPES == yaml_rules["domain_types"]


def test_examples_use_only_structured_output_fields():
    examples = load_examples()["examples"]

    for pattern, pattern_examples in examples.items():
        for example in pattern_examples:
            output = str(example["output"])
            assert _PURE_VARIABLE_RE.fullmatch(output), (pattern, example)
            assert "when" not in output.casefold()
            assert "=" not in output

            if example["type"] == "supp":
                assert output == "QVAL"
                assert _PURE_VARIABLE_RE.fullmatch(str(example["supp_variable"]))
                assert str(example["supp_dataset"]).startswith("SUPP")
            else:
                assert not example.get("supp_dataset")
                assert not example.get("supp_variable")

            testcd = str(example.get("testcd", ""))
            if testcd:
                assert _PURE_VARIABLE_RE.fullmatch(testcd), (pattern, example)


def test_fa_rules_template_and_examples_share_one_structured_contract():
    rules = load_rules()["rules"]
    fa_rules = " ".join(rules[f"RULE_FA_00{index}"] for index in range(1, 4))
    fa_pattern = load_template()["domain_specific_patterns"]["fa"]
    fa_examples = load_examples()["examples"]["fa_specific"]

    assert "FAORRES when" not in fa_rules
    assert "QVAL when" not in fa_rules
    assert "FAORRES when" not in fa_pattern
    assert "QVAL when" not in fa_pattern
    assert "sdtm_variable" in fa_rules
    assert "testcd" in fa_rules

    standard_examples = [example for example in fa_examples if example["type"] == "standard"]
    supp_examples = [example for example in fa_examples if example["type"] == "supp"]
    assert all(example["output"] == "FAORRES" and example["testcd"] for example in standard_examples)
    assert supp_examples == [
        {
            "domain": "FA",
            "input": "肿瘤病史/其他病理类型(THPTYPO)",
            "output": "QVAL",
            "supp_dataset": "SUPPFA",
            "supp_variable": "FAOROTH",
            "testcd": "THCLA",
            "type": "supp",
        }
    ]


def _fresh_prompt_generator() -> SDTMPromptGenerator:
    clear_cache()
    SDTMPromptGenerator.VARIABLE_PROMPT_TEMPLATE = ""
    SDTMPromptGenerator.PATTERN_EXAMPLES = {}
    SDTMPromptGenerator.VARIABLE_PATTERN_KEYWORDS = {}
    return SDTMPromptGenerator(language="en")


def test_rendered_fa_prompt_has_no_composite_sdtm_variable_examples():
    generator = _fresh_prompt_generator()
    prompt = generator.generate_variable_prompt(
        table_name="Tumor History",
        variable_name="THPTYPO",
        variable_data={
            "metadata_variable": "THPTYPO",
            "annotation_table": "Tumor History",
            "annotation_variable": "Other pathology type",
        },
        all_table_variables=[
            {
                "metadata_variable": "THPTYPO",
                "annotation_variable": "Other pathology type",
            }
        ],
        domain_hints=["FA"],
    )

    assert "FAORRES when" not in prompt
    assert "QVAL when" not in prompt
    assert '"sdtm_variable": "FAORRES"' in prompt
    assert '"testcd": "THCLA"' in prompt
    assert '"sdtm_variable": "QVAL"' in prompt
    assert '"supp_dataset": "SUPPFA"' in prompt
    assert '"supp_variable": "FAOROTH"' in prompt


def test_sdtm_processor_production_prompt_path_uses_structured_contract():
    processor = object.__new__(SDTMProcessor)
    processor.data_masker = None
    processor.debug = False
    processor.kb_hints = None
    processor.prompt_generator = _fresh_prompt_generator()
    processor._infer_candidate_domains = lambda variable_data, kb_context: ["FA"]

    prompt = processor._create_enhanced_prompt(
        {
            "metadata_variable": "THPTYPO",
            "annotation_table": "Tumor History",
            "annotation_variable": "Other pathology type",
        },
        {},
    )

    assert "FAORRES when" not in prompt
    assert "QVAL when" not in prompt
    assert '"sdtm_variable": "QVAL"' in prompt
    assert '"supp_variable": "FAOROTH"' in prompt
    assert '"testcd": "THCLA"' in prompt


def test_complete_llm_prompt_is_redacted_after_all_context_is_assembled():
    class _UnsafeRagAugmenter:
        def build_context_block(self, *_args, **_kwargs):
            return "RAG DOB: 1980-05-15; subject 001-0023"

    class _UnsafeHints:
        def build_prompt_section(self, **_kwargs):
            return "\nHint for Smith, John"

    processor = object.__new__(SDTMProcessor)
    processor.data_masker = DataMasker()
    processor.debug = False
    processor.kb_hints = _UnsafeHints()
    processor.rag_augmenter = _UnsafeRagAugmenter()
    processor.rag_char_limit = 1500
    processor.prompt_generator = _fresh_prompt_generator()
    processor._infer_candidate_domains = lambda variable_data, kb_context: []

    prompt = processor._create_enhanced_prompt(
        {
            "metadata_variable": "SUBJECT 001-0023",
            "annotation_table": "DOB: 1980-05-15",
            "annotation_variable": "Smith, John",
        },
        {},
        all_table_variables=[
            {
                "metadata_variable": "SIBLING",
                "annotation_variable": "Contact john@example.com",
            }
        ],
        rag_contexts=[object()],
    )

    for sentinel in (
        "001-0023",
        "1980-05-15",
        "Smith, John",
        "john@example.com",
    ):
        assert sentinel not in prompt
    assert "[REDACTED]" in prompt


def test_prompt_ci_rejects_composite_and_unstructured_supp_examples():
    ok, messages = check_example_output_contract(
        {
            "examples": {
                "fa_specific": [
                    {
                        "domain": "FA",
                        "output": "FAORRES when FATESTCD=THCLA",
                        "type": "standard",
                    },
                    {
                        "domain": "FA",
                        "output": "QVAL",
                        "supp": "SUPPFA",
                        "qnam": "FAOROTH",
                        "type": "supp",
                    },
                ]
            }
        }
    )

    assert ok is False
    assert any("pure SDTM variable" in message for message in messages)
    assert any("supp_dataset" in message for message in messages)
