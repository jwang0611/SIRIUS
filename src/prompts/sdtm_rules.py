"""
sdtm_rules.py
-------------
A lightweight rule library for mapping aCRF metadata → CDISC SDTM domains/variables.

Based on CDISC SDTM IG v3.4 and Controlled Terminology, covering oncology, safety,
laboratory, vital signs, and other common clinical domains.

Simplified version: merged similar rules across domains for better LLM comprehension.

Rules are now loaded from src/prompts/rules/sdtm_rules.yaml; this module exposes the
same public API as before so callers are unaffected.
"""

from src.prompts.loader import load_rules as _load_rules

# ---------------------------------------------------------------------------
# Load data from YAML (cached after first import)
# ---------------------------------------------------------------------------
_data = _load_rules()

# Public module-level dicts (same names as before)
RULES: dict[str, str] = dict(_data["rules"])

# =============================================================================
# RULE CATEGORIES AND DYNAMIC SELECTION
# =============================================================================

# 核心规则（所有映射都需要）
CORE_RULES: list[str] = list(_data["core_rules"])

# 通用模式规则（根据变量特征选择）
PATTERN_RULES: dict[str, list[str]] = {k: list(v) for k, v in _data["pattern_rules"].items()}

# 域特定规则映射
DOMAIN_RULES: dict[str, list[str]] = {k: list(v) for k, v in _data["domain_rules"].items()}

# 域类型分组（用于加载通用模式规则）
DOMAIN_TYPES: dict[str, list[str]] = {k: list(v) for k, v in _data["domain_types"].items()}


def get_rule_text(rule_id: str) -> str:
    """
    Fetch the textual description of a mapping rule.

    Parameters
    ----------
    rule_id : str
        The ID of the rule (e.g., 'RULE_AE_001').

    Returns
    -------
    str
        The rule text, or an empty string if not found.
    """
    return RULES.get(rule_id, "")


def list_rules() -> dict:
    """
    Return all rules as a dictionary.

    Returns
    -------
    dict
        A dictionary of rule_id → rule_text.
    """
    return RULES.copy()


def search_rules(keyword: str) -> dict:
    """
    Search rules by keyword.

    Parameters
    ----------
    keyword : str
        Keyword to search (case-insensitive).

    Returns
    -------
    dict
        A dictionary of rule_id → rule_text that match the keyword.
    """
    k = keyword.lower()
    return {rid: txt for rid, txt in RULES.items() if k in txt.lower() or k in rid.lower()}


# =============================================================================
# RULE CATEGORIES AND DYNAMIC SELECTION
# =============================================================================

# 核心规则（所有映射都需要）
CORE_RULES = ["RULE_VAR_001", "RULE_SUPP_001", "RULE_SUPP_002"]

# 通用模式规则（根据变量特征选择）
PATTERN_RULES = {
    "DATE": ["RULE_DATE_001", "RULE_DATE_002"],  # 日期相关
    "FIND": ["RULE_FIND_001", "RULE_FIND_002", "RULE_FIND_003", "RULE_FIND_004"],  # 检查结果
    "TERM": ["RULE_TERM_001", "RULE_TERM_002"],  # 术语/名称
    "CAT": ["RULE_CAT_001"],  # 分类
    "LOC": ["RULE_LOC_001", "RULE_LOC_002"],  # 位置
    "METHOD": ["RULE_METHOD_001"],  # 方法
    "DOSE": ["RULE_DOSE_001", "RULE_DOSE_002"],  # 剂量
    "STAT": ["RULE_STAT_001"],  # 状态
}

# 域特定规则映射
# - 有特定规则的域：列出规则ID
# - 只用模式规则的域：空列表（会通过 DOMAIN_TYPES 自动加载模式规则）
DOMAIN_RULES = {
    # ===== Events 类 =====
    "AE": ["RULE_AE_001", "RULE_AE_002", "RULE_AE_003"],  # 不良事件
    "DS": ["RULE_DS_001"],  # 处置
    # ===== Interventions 类 =====
    "CM": ["RULE_CM_001", "RULE_CM_002"],  # 合并用药
    "MH": ["RULE_MH_001"],  # 既往病史
    "PR": ["RULE_PR_001"],  # 手术/操作
    "EX": ["RULE_EX_001"],  # 给药
    # ===== Demographics =====
    "DM": ["RULE_DM_001"],  # 人口学
    # ===== Oncology 类 =====
    "TU": ["RULE_ONCO_001", "RULE_ONCO_002", "RULE_ONCO_003"],  # 肿瘤识别
    "TR": ["RULE_ONCO_001", "RULE_ONCO_002", "RULE_ONCO_003"],  # 肿瘤结果
    "RS": ["RULE_ONCO_001", "RULE_ONCO_002", "RULE_ONCO_003"],  # 疗效评估
    # ===== Findings 类（有特定规则）=====
    "QS": ["RULE_QS_001"],  # 问卷
    "PC": ["RULE_PK_001"],  # PK浓度
    "PP": ["RULE_PK_001"],  # PK参数
    "IS": ["RULE_IS_001"],  # 免疫原性
    # ===== Findings 类（仅用模式规则）=====
    "LB": [],  # 实验室
    "VS": [],  # 生命体征
    "EG": [],  # 心电图
    "PE": [],  # 体格检查
    "MB": [],  # 微生物
    "MO": [],  # 形态学
    "FA": ["RULE_FA_001", "RULE_FA_002", "RULE_FA_003"],  # 发现关于事件/干预
    "SC": [],  # 受试者特征
    # ===== SDTM IG 3.4 新增域 =====
    "OE": ["RULE_OE_001"],  # 眼科检查
    "MK": ["RULE_MK_001"],  # 骨骼检查
    "GF": ["RULE_GF_001"],  # 基因组发现
    "CP": ["RULE_CP_001"],  # 细胞表型
    "XU": ["RULE_XU_001"],  # 计划外检查
    "APMH": ["RULE_APMH_001"],  # 关联人员病史（家族史）
}

# 域类型分组（用于加载通用模式规则）
DOMAIN_TYPES = {
    # Findings 类域（需要 FIND 模式规则）—— 含 SDTM IG 3.4 新增域
    "FINDINGS": [
        "LB",
        "VS",
        "EG",
        "PE",
        "QS",
        "PC",
        "PP",
        "TR",
        "RS",
        "IS",
        "MB",
        "FA",
        "SC",
        "OE",
        "MK",
        "GF",
        "CP",
        "XU",
    ],
    # Events 类域（需要 TERM 模式规则）
    "EVENTS": ["AE", "DS"],
    # Interventions 类域（需要 TERM + DOSE 模式规则）
    "INTERVENTIONS": ["CM", "MH", "PR", "EX", "APMH"],
    # 肿瘤学域
    "ONCOLOGY": ["TU", "TR", "RS"],
}


def get_core_rules() -> dict:
    """
    获取核心规则（所有映射都需要遵守的基本规则）。

    Returns
    -------
    dict
        核心规则字典 {rule_id: rule_text}
    """
    return {rid: RULES[rid] for rid in CORE_RULES if rid in RULES}


def get_pattern_rules(patterns: list) -> dict:
    """
    获取指定类型的模式规则。

    Parameters
    ----------
    patterns : list
        模式类型列表，如 ['DATE', 'FIND', 'TERM']

    Returns
    -------
    dict
        规则字典 {rule_id: rule_text}
    """
    result = {}
    for pattern in patterns:
        rule_ids = PATTERN_RULES.get(pattern, [])
        for rid in rule_ids:
            if rid in RULES:
                result[rid] = RULES[rid]
    return result


def get_rules_by_domain(domain: str) -> dict:
    """
    获取域特定规则。

    Parameters
    ----------
    domain : str
        SDTM 域代码 (e.g., 'AE', 'CM', 'LB', 'VS').

    Returns
    -------
    dict
        域特定规则字典 {rule_id: rule_text}
    """
    domain_upper = domain.upper()
    rule_ids = DOMAIN_RULES.get(domain_upper, [])
    return {rid: RULES[rid] for rid in rule_ids if rid in RULES}


def get_rules_for_domains(domain_hints: list) -> dict:
    """
    根据推断的域获取相关规则（核心 + 模式 + 域特定）。

    智能选择规则策略：
    1. 核心规则（总是加载）
    2. 日期模式规则（总是加载，几乎所有变量都涉及日期）
    3. 根据域类型加载相应的模式规则
    4. 加载域特定规则

    Parameters
    ----------
    domain_hints : list
        推断的 SDTM 域列表，如 ['AE'] 或 ['LB', 'VS']

    Returns
    -------
    dict
        合并后的规则字典 {rule_id: rule_text}
    """
    # 1. 核心规则
    selected_rules = get_core_rules()

    # 2. 日期模式（总是加载）
    selected_rules.update(get_pattern_rules(["DATE"]))

    if not domain_hints:
        # 无域提示时，只返回核心规则 + 日期规则 + 通用TERM/CAT规则
        # 而不是返回所有规则，以减少prompt长度
        selected_rules.update(get_pattern_rules(["TERM", "CAT"]))
        return selected_rules

    # 3. 根据域类型选择模式规则
    # Use a list (not set) to preserve deterministic insertion order across runs.
    _seen_patterns: dict[str, None] = {}  # ordered-set via dict keys
    domains_upper = [d.upper() for d in domain_hints if d]

    for domain in domains_upper:
        # Findings 域 (FA has its own RULE_FA_* rules, skip generic FIND)
        if domain in DOMAIN_TYPES["FINDINGS"]:
            if domain != "FA":
                _seen_patterns.update(dict.fromkeys(["FIND", "LOC", "METHOD"]))
            else:
                _seen_patterns.update(dict.fromkeys(["LOC", "METHOD"]))
        # Events 域
        if domain in DOMAIN_TYPES["EVENTS"]:
            _seen_patterns.update(dict.fromkeys(["TERM", "CAT"]))
        # Interventions 域
        if domain in DOMAIN_TYPES["INTERVENTIONS"]:
            _seen_patterns.update(dict.fromkeys(["TERM", "DOSE", "CAT"]))
        # 肿瘤学域
        if domain in DOMAIN_TYPES["ONCOLOGY"]:
            _seen_patterns.update(dict.fromkeys(["FIND", "LOC", "METHOD", "STAT"]))

    selected_rules.update(get_pattern_rules(list(_seen_patterns)))

    # 4. 域特定规则
    for domain in domains_upper:
        selected_rules.update(get_rules_by_domain(domain))

    return selected_rules


def format_rules_for_prompt(domain_hints: list | None = None) -> str:
    """
    格式化规则用于 prompt（支持动态规则选择）。

    Parameters
    ----------
    domain_hints : list, optional
        推断的 SDTM 域列表。如果为 None，返回核心规则 + 通用模式规则。

    Returns
    -------
    str
        格式化的规则文本
    """
    # Always use get_rules_for_domains for consistent behavior
    # When domain_hints is None/empty, it returns core + date + term + cat rules
    rules = get_rules_for_domains(domain_hints or [])

    if domain_hints:
        header = f"Mapping rules (for {', '.join(domain_hints)}):"
    else:
        header = "Mapping rules (core):"

    lines = [header]
    for _rid, text in rules.items():
        lines.append(f"- {text}")  # 简化输出，不显示规则ID

    return "\n".join(lines)


def get_available_domains() -> list:
    """
    Get a list of all SDTM domains that have rules defined.

    Returns
    -------
    list
        A sorted list of domain codes.
    """
    return sorted(DOMAIN_RULES.keys())


def get_rule_categories() -> dict:
    """
    Get rules organized by categories.

    Returns
    -------
    dict
        A dictionary where keys are categories and values are rule dictionaries.
    """
    return {
        "CORE": get_core_rules(),
        "PATTERN": {rid: RULES[rid] for rids in PATTERN_RULES.values() for rid in rids if rid in RULES},
        "DOMAIN": {rid: RULES[rid] for rids in DOMAIN_RULES.values() for rid in rids if rid in RULES},
    }


def get_rule_stats() -> dict:
    """
    获取规则统计信息。

    Returns
    -------
    dict
        统计信息
    """
    all_rules = list_rules()

    return {
        "total_count": len(all_rules),
        "core_count": len(CORE_RULES),
        "pattern_count": sum(len(v) for v in PATTERN_RULES.values()),
        "domain_count": sum(len(v) for v in DOMAIN_RULES.values()),
    }
