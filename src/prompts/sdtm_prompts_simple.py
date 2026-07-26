"""
SDTM prompt templates - Simplified Version
简化版SDTM提示词模板

核心理念：
1. 只维护一套英文模板
2. 利用LLM的多语言理解能力
3. 通过指令控制输出语言
4. 最小化维护成本
5. 动态域/变量列表（根据推断域选择）

Template, examples, and pattern keywords are now loaded from YAML files under
src/prompts/templates/ and src/prompts/examples/.  Public API is unchanged.
"""

import json
import logging
from typing import Any, ClassVar

from src.config.domain_semantic_map import (
    DOMAIN_VARIABLES,
    format_domain_variables_section,
    infer_domains,
)
from src.prompts.loader import load_examples as _load_examples
from src.prompts.loader import load_template as _load_template
from src.prompts.sdtm_rules import format_rules_for_prompt

# 日志由入口统一配置，这里仅获取 logger
logger = logging.getLogger(__name__)


def format_sdtm_rules(domain_hints: list[str] | None = None) -> str:
    """
    Format SDTM rules for inclusion in prompts.
    支持动态规则选择，只加载相关域的规则。

    Parameters
    ----------
    domain_hints : List[str], optional
        推断的 SDTM 域列表。如果为 None 或空，返回所有规则。

    Returns
    -------
    str
        格式化的规则文本
    """
    return format_rules_for_prompt(domain_hints)


class SDTMPromptGenerator:
    """
    Simplified SDTM Prompt Generator
    只维护英文模板，通过language参数控制输出语言偏好
    支持动态域/变量列表生成
    """

    # ---------------------------------------------------------------------------
    # Template + examples are loaded from YAML at class definition time so that
    # the ClassVar type annotations remain correct for static analysis.
    # ---------------------------------------------------------------------------

    # 统一的英文模板（LLM会自动处理多语言输入）
    # 注意：{domain_variables_section} 会被动态生成的域/变量列表替换
    VARIABLE_PROMPT_TEMPLATE: ClassVar[str] = ""  # loaded from YAML in __init__

    # ==========================================================================
    # PATTERN-BASED EXAMPLES (for dynamic selection)
    # ==========================================================================
    PATTERN_EXAMPLES: ClassVar[dict[str, list[dict[str, str]]]] = {}  # loaded from YAML

    # Variable keyword to pattern mapping (for smart pattern detection)
    VARIABLE_PATTERN_KEYWORDS: ClassVar[dict[str, list[str]]] = {}  # loaded from YAML

    def __init__(self, language: str = "en"):
        """
        Initialize the prompt generator.

        Args:
            language: Language preference for communication ("en" or "cn")
                     This only affects instruction tone, not template language
        """
        self.language = language.lower()
        self.standard_catalog: dict[str, Any] = {"domains": {}}

        # Load YAML data once (cached by loader)
        tpl_data = _load_template()
        ex_data = _load_examples()

        # Populate class-level attributes from YAML on first instance
        if not SDTMPromptGenerator.VARIABLE_PROMPT_TEMPLATE:
            SDTMPromptGenerator.VARIABLE_PROMPT_TEMPLATE = tpl_data["template"]
            SDTMPromptGenerator.PATTERN_EXAMPLES = {
                k: [dict(item) for item in v] for k, v in ex_data["examples"].items()
            }
            SDTMPromptGenerator.VARIABLE_PATTERN_KEYWORDS = {
                k: list(v) for k, v in ex_data["variable_pattern_keywords"].items()
            }

        self._tpl_data = tpl_data
        self._ex_data = ex_data

        logger.info(
            f"SDTM Prompt Generator initialized with language preference: {self.language} "
            f"(template v{tpl_data['version']}, examples v{ex_data['version']})"
        )
        logger.info("Using unified English template with dynamic domain/variable selection")

    def _get_language_instruction(self) -> str:
        """
        Get concise language-specific instruction for LLM.
        LLM 天然支持多语言理解，只需简单指令。
        """
        lang_map: dict[str, str] = self._tpl_data.get("language_instructions", {})
        default = "- Process Chinese/English inputs naturally; output SDTM variables in English"
        return lang_map.get(self.language, default)

    def _build_domain_examples_section(
        self,
        domain_hints: list[str] | None = None,
        annotation_variable: str = "",
    ) -> str:
        """
        Build examples section dynamically based on inferred domains AND variable patterns.

        Uses smart pattern detection to select the most relevant examples for the
        current mapping task.

        Parameters
        ----------
        domain_hints : List[str], optional
            Inferred domain codes. If None, includes common examples.
        annotation_variable : str, optional
            The annotation variable name (used for pattern detection).

        Returns
        -------
        str
            Formatted examples section
        """
        lines = ["**Examples (select relevant patterns for reference):**", ""]

        # Detect relevant patterns from annotation_variable
        detected_patterns = self._detect_variable_patterns(annotation_variable)

        # Always include some core patterns
        # Use dict as an ordered set to ensure deterministic iteration order.
        _patterns_ordered: dict[str, None] = dict.fromkeys(detected_patterns)
        if not _patterns_ordered:
            # Default patterns if none detected
            _patterns_ordered = dict.fromkeys(["term", "date", "supp"])

        # Always add supp pattern (important for LLM to understand SUPP structure)
        _patterns_ordered["supp"] = None

        # Add domain-specific patterns
        if domain_hints:
            triggers: dict[str, list[str]] = self._ex_data.get("domain_pattern_triggers", {})
            for domain in domain_hints:
                for pat in triggers.get(domain.upper(), []):
                    _patterns_ordered[pat] = None

        # Collect examples by category (OPTIMIZED: reduced limits for shorter prompts)
        standard_examples: list[str] = []
        result_examples: list[str] = []
        supp_examples: list[str] = []

        for pattern in _patterns_ordered:
            examples = self.PATTERN_EXAMPLES.get(pattern, [])
            for ex in examples:
                if domain_hints and ex.get("domain") not in domain_hints:
                    continue

                formatted = self._format_example(ex)

                if ex.get("type") == "supp":
                    if len(supp_examples) < 2:  # Reduced from 3 to 2
                        supp_examples.append(formatted)
                elif ex.get("testcd"):
                    if len(result_examples) < 2:  # Reduced from 3 to 2
                        result_examples.append(formatted)
                else:
                    if len(standard_examples) < 2:  # Reduced from 4 to 2
                        standard_examples.append(formatted)

        # Build output with categories
        if standard_examples:
            lines.append("Standard variables:")
            lines.extend(standard_examples)
            lines.append("")

        if result_examples:
            lines.append("--ORRES variables (MUST include testcd):")
            lines.extend(result_examples)
            lines.append("")

        if supp_examples:
            lines.append("SUPP variables (sdtm_variable='QVAL', supp_variable=QNAM):")
            lines.extend(supp_examples)

        return "\n".join(lines)

    def _build_domain_specific_patterns(self, domain_hints: list[str] | None = None) -> str:
        """
        Build domain-specific pattern hints based on inferred domains.

        Only includes patterns NOT already covered by the Rules section.
        Findings/ORRES, AE severity/action, dose, and date patterns are
        handled by RULE_FIND_*, RULE_AE_*, RULE_DOSE_*, and RULE_DATE_*
        respectively, so they are omitted here.
        """
        if not domain_hints:
            return ""

        patterns: list[str] = []
        domains_upper = [d.upper() for d in domain_hints if d]
        dsp: dict = self._tpl_data.get("domain_specific_patterns", {})
        oncology_domains: list[str] = [d.upper() for d in self._tpl_data.get("oncology_domains", [])]

        if any(d in oncology_domains for d in domains_upper):
            patterns.append(dsp.get("oncology", "- 肿瘤: 病灶→TU, 测量→TR, 评估→RS"))

        if "FA" in domains_upper:
            patterns.append(dsp.get("fa", ""))

        if patterns:
            return "**Domain-Specific Patterns:**\n" + "\n".join(p for p in patterns if p)
        return ""

    def _detect_variable_patterns(self, annotation_variable: str) -> list[str]:
        """
        Detect which patterns are relevant based on annotation variable keywords.

        Parameters
        ----------
        annotation_variable : str
            The annotation variable name/description

        Returns
        -------
        List[str]
            List of detected pattern names
        """
        if not annotation_variable:
            return []

        lower_var = annotation_variable.lower()
        detected = []

        for pattern, keywords in self.VARIABLE_PATTERN_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in lower_var:
                    detected.append(pattern)
                    break

        return detected

    def _build_sibling_variables_section(
        self,
        table_name: str,
        variable_name: str,
        all_table_variables: list[dict[str, Any]],
    ) -> str:
        """Build a compact list of all variables in the same CRF table.

        This gives the LLM a holistic view of the table structure,
        helping it infer the correct SDTM domain and naming patterns.
        """
        if not all_table_variables or len(all_table_variables) <= 1:
            return ""

        lines = [f'**Sibling Variables in Same Table "{table_name}"** ({len(all_table_variables)} total):']
        for var in all_table_variables:
            meta_var = var.get("metadata_variable", "")
            ann_var = var.get("annotation_variable", "")
            if not meta_var:
                continue
            marker = "  ← current" if meta_var == variable_name else ""
            if ann_var:
                lines.append(f"- {meta_var} ({ann_var}){marker}")
            else:
                lines.append(f"- {meta_var}{marker}")

        return "\n".join(lines)

    def _build_table_context_section(
        self,
        completed_table_mappings: list[dict[str, Any]] | None,
    ) -> str:
        """Format already-completed sibling variable mappings as LLM context.

        When processing a table sequentially, earlier variables' results are
        fed here so the LLM can maintain domain and naming consistency.
        """
        if not completed_table_mappings:
            return ""

        lines = [
            "**Table Context (Already Mapped Sibling Variables — maintain consistency):**",
        ]
        for mapping in completed_table_mappings[:15]:
            domain = mapping.get("domain", "?")
            sdtm_var = mapping.get("sdtm_variable", "?")
            var_type = mapping.get("sdtm_variable_type", "standard")
            var_name = mapping.get("variable_name", "")
            score = mapping.get("score", 0)
            testcd = mapping.get("testcd", "")

            entry = f"- {var_name} → {domain}.{sdtm_var} ({var_type}"
            if testcd:
                entry += f", testcd={testcd}"
            entry += f", score: {score:.2f})"
            lines.append(entry)

        lines.append("")
        lines.append("Use the same domain and naming conventions for consistency.")
        return "\n".join(lines)

    def _format_example(self, ex: dict[str, str]) -> str:
        """
        Format a single example dict into a display string.

        Parameters
        ----------
        ex : Dict[str, str]
            Example dictionary with domain, input, output, etc.

        Returns
        -------
        str
            Formatted example string
        """
        domain = ex.get("domain", "")
        input_text = ex.get("input", "")
        output = ex.get("output", "")

        # Parse input (format: "table/variable")
        if "/" in input_text:
            table, var = input_text.split("/", 1)
        else:
            table, var = "", input_text

        structured = {
            "domain": domain,
            "sdtm_variable": output,
            "sdtm_variable_type": ex.get("type", "standard"),
        }
        if ex.get("supp_dataset"):
            structured["supp_dataset"] = ex["supp_dataset"]
        if ex.get("supp_variable"):
            structured["supp_variable"] = ex["supp_variable"]
        if ex.get("testcd"):
            structured["testcd"] = ex["testcd"]

        return f'- "{table}/{var}" → {json.dumps(structured, ensure_ascii=False)}'

    def set_standard_catalog(self, catalog: dict[str, Any] | None) -> None:
        """Attach a preprocessed standard catalog."""
        if isinstance(catalog, dict) and isinstance(catalog.get("domains"), dict):
            self.standard_catalog = {"domains": catalog["domains"]}
        else:
            self.standard_catalog = {"domains": {}}

    def generate_variable_prompt(
        self,
        table_name: str,
        variable_name: str,
        variable_data: dict[str, Any],
        all_table_variables: list[dict[str, Any]],
        domain_hints: list[str] | None = None,
        completed_table_mappings: list[dict[str, Any]] | None = None,
    ) -> str:
        """
        Generate prompt for a specific variable-table pair.

        The prompt uses English template but LLM will understand Chinese inputs naturally.
        Supports dynamic rule selection and dynamic domain/variable list based on inferred domains.

        Args:
            table_name: Table name (Chinese or English)
            variable_name: Variable name
            variable_data: Variable data (may contain Chinese annotations)
            all_table_variables: List of all variables in the table
            domain_hints: Optional list of inferred SDTM domains for rule selection.
                         If None, will auto-infer from variable_data.
            completed_table_mappings: Already-completed mapping results from
                sibling variables in the same table (for consistency).

        Returns:
            Formatted prompt string
        """
        _INPUT_KEYS = ("metadata_variable", "annotation_variable", "annotation_table", "metadata_table", "type")
        clean_variable = {k: v for k, v in variable_data.items() if k in _INPUT_KEYS and v}
        pair_data = {"table_name": table_name, "variable": clean_variable}
        pair_json = json.dumps(pair_data, ensure_ascii=False, indent=2)

        language_instruction = self._get_language_instruction()

        if domain_hints is None:
            domain_hints = infer_domains(
                annotation_table=variable_data.get("annotation_table", "") or table_name,
                annotation_variable=variable_data.get("annotation_variable", ""),
                metadata_variable=variable_data.get("metadata_variable", "") or variable_name,
            )

        sdtm_rules = format_sdtm_rules(domain_hints)
        domain_variables_section = format_domain_variables_section(domain_hints)

        annotation_variable = variable_data.get("annotation_variable", "")
        domain_examples_section = self._build_domain_examples_section(
            domain_hints=domain_hints,
            annotation_variable=annotation_variable,
        )

        domain_specific_patterns = self._build_domain_specific_patterns(domain_hints)

        sibling_variables_section = self._build_sibling_variables_section(
            table_name,
            variable_name,
            all_table_variables,
        )

        table_context_section = self._build_table_context_section(completed_table_mappings)

        if domain_hints:
            logger.debug(f"Dynamic rules/variables selected for domains: {domain_hints}")
        else:
            logger.debug("Using default domains (no domain hints inferred)")

        prompt = self.VARIABLE_PROMPT_TEMPLATE.format(
            pair_json=pair_json,
            table_name=table_name,
            variable_name=variable_name,
            sdtm_rules=sdtm_rules,
            language_instruction=language_instruction,
            domain_variables_section=domain_variables_section,
            domain_examples_section=domain_examples_section,
            domain_specific_patterns=domain_specific_patterns,
            sibling_variables_section=sibling_variables_section,
            table_context_section=table_context_section,
        )

        logger.info(
            f"Generated prompt for {table_name} - {variable_name} "
            f"(language: {self.language}, domains: {domain_hints or 'default'}, "
            f"siblings: {len(all_table_variables)}, "
            f"table_context: {len(completed_table_mappings or [])})"
        )
        return prompt

    def switch_language(self, language: str) -> None:
        """
        Switch language preference.
        Note: This only affects instruction tone, template remains in English.
        """
        if language.lower() in ["en", "cn"]:
            self.language = language.lower()
            logger.info(f"Language preference switched to: {self.language}")
        else:
            logger.warning(f"Unsupported language: {language}")

    def get_available_domains(self) -> dict[str, str]:
        """Get available SDTM domains from unified config."""
        return {domain: info.get("name", "") for domain, info in DOMAIN_VARIABLES.items()}


# 示例使用
if __name__ == "__main__":
    from src.config.domain_semantic_map import infer_domains as unified_infer

    print("=" * 80)
    print("统一域推断演示")
    print("=" * 80)

    # 测试统一域推断
    test_cases = [
        ("不良事件", "不良事件名称", "AETERM"),
        ("合并用药", "开始日期", "CMSTDTC"),
        ("生命体征", "血压收缩压", ""),
        ("肿瘤病灶", "病灶位置", "TULOC"),
        ("实验室检查", "白细胞计数", ""),
        ("体格检查", "查体备注", ""),
    ]

    print("\n域推断测试 (使用统一接口):")
    for table, var, meta in test_cases:
        hints = unified_infer(table, var, meta)
        print(f"  - {table}/{var}/{meta} → {hints}")

    print("\n" + "=" * 80)
    print("动态 Domain/Variables 列表演示")
    print("=" * 80)

    # 测试动态域/变量列表
    print("\n【AE 域】:")
    print(format_domain_variables_section(["AE"]))

    print("\n【LB + VS 域】:")
    print(format_domain_variables_section(["LB", "VS"]))

    print("\n【无提示（默认）】:")
    print(format_domain_variables_section(None))

    print("\n" + "=" * 80)
    print("Prompt 生成测试")
    print("=" * 80)

    generator = SDTMPromptGenerator(language="cn")

    sample_data: dict[str, Any] = {
        "table_name": "不良事件表",
        "variable_name": "AETERM",
        "variable_data": {
            "metadata_variable": "AETERM",
            "annotation_variable": "不良事件名称",
            "annotation_table": "不良事件",
            "type": "text",
        },
        "all_table_variables": [
            {"metadata_variable": "AETERM", "annotation_variable": "不良事件名称"},
            {"metadata_variable": "AESTDTC", "annotation_variable": "开始日期"},
        ],
    }

    prompt = generator.generate_variable_prompt(
        table_name=sample_data["table_name"],
        variable_name=sample_data["variable_name"],
        variable_data=sample_data["variable_data"],
        all_table_variables=sample_data["all_table_variables"],
    )

    print(f"\n输入: {sample_data['variable_data']}")
    print(f"\n提示词长度: {len(prompt)} 字符")
    print(f"包含中文输入: {'✅' if '不良事件' in prompt else '❌'}")
    print("动态域/变量: ✅")
    print("动态规则选择: ✅")
    print("统一域推断: ✅")
