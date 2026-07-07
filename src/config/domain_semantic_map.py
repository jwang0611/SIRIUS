"""
Domain semantic mappings for annotation tables and variables.

This module provides:
1. CHINESE_TABLE_DOMAIN_MAP - exact table name → domain mapping
2. ANNOTATION_KEYWORD_DOMAIN_MAP - keyword-based domain hints
3. DOMAIN_KEYWORDS - comprehensive keyword lists per domain (for inference)
4. DOMAIN_VARIABLES - standard variables per domain (loaded from sdtm_spec_enhanced.json)
5. Unified inference functions

The mappings are stored in lowercase to make case-insensitive lookups easy.
"""

import json
from pathlib import Path
from typing import Any

# =============================================================================
# EXACT TABLE NAME MAPPING
# =============================================================================

# Default mapping from Chinese table names to SDTM domains.
# Extended with common variations and abbreviations for better inference
CHINESE_TABLE_DOMAIN_MAP: dict[str, str] = {
    # AE - Adverse Events
    "不良事件": "AE",
    "ae": "AE",
    "adverse event": "AE",
    "adverse events": "AE",
    "安全性": "AE",
    # DM - Demographics
    "人口统计学": "DM",
    "人口学": "DM",
    "人口统计": "DM",
    "基本信息": "DM",
    "受试者信息": "DM",
    "demographics": "DM",
    "dm": "DM",
    # VS - Vital Signs
    "生命体征": "VS",
    "vital signs": "VS",
    "vs": "VS",
    "身高": "VS",
    "体重": "VS",
    "血压": "VS",
    "体温": "VS",
    # LB - Laboratory
    "实验室检查": "LB",
    "实验室": "LB",
    "化验": "LB",
    "检验": "LB",
    "laboratory": "LB",
    "lab": "LB",
    "lb": "LB",
    "血常规": "LB",
    "尿常规": "LB",
    "生化": "LB",
    "凝血": "LB",
    "甲状腺功能": "LB",
    "肝功能": "LB",
    "肾功能": "LB",
    # EG - ECG
    "心电图检查": "EG",
    "心电图": "EG",
    "ecg": "EG",
    "eg": "EG",
    # FA - Findings About (tumor history)
    "肿瘤病史": "FA",
    "癌病史": "FA",
    # TR/TU/RS - Oncology
    "肿瘤测量": "TR",
    "肿瘤病灶": "TU",
    "病灶评估": "RS",
    "疗效评估": "RS",
    "肿瘤评估": "RS",
    "靶病灶": "TU",
    "非靶病灶": "TU",
    "新发病灶": "TU",
    # CM - Concomitant Medications
    "既往及合并用药": "CM",
    "合并用药": "CM",
    "伴随用药": "CM",
    "既往用药": "CM",
    "合并药物": "CM",
    "药物": "CM",
    "concomitant medication": "CM",
    "cm": "CM",
    # MH - Medical History
    "既往病史": "MH",
    "病史": "MH",
    "既往史": "MH",
    "现病史": "MH",
    "medical history": "MH",
    "mh": "MH",
    "过敏史": "MH",
    # EX - Exposure
    "用药记录": "EX",
    "给药": "EX",
    "研究药物": "EX",
    "试验药物": "EX",
    "exposure": "EX",
    "ex": "EX",
    # SV - Subject Visits
    "访视计划": "SV",
    "访视": "SV",
    "visit": "SV",
    "sv": "SV",
    # DS - Disposition
    "受试者处置": "DS",
    "处置": "DS",
    "disposition": "DS",
    "ds": "DS",
    "知情同意": "DS",
    "入组": "DS",
    "随机": "DS",
    "完成": "DS",
    "终止": "DS",
    # PE - Physical Exam
    "体格检查": "PE",
    "体检": "PE",
    "查体": "PE",
    "physical exam": "PE",
    "pe": "PE",
    # QS - Questionnaires
    "问卷与量表": "QS",
    "问卷": "QS",
    "量表": "QS",
    "评分": "QS",
    "questionnaire": "QS",
    "qs": "QS",
    "ecog": "QS",
    # SU - Substance Use
    "吸烟史": "SU",
    "饮酒史": "SU",
    "substance use": "SU",
    "su": "SU",
    # PR - Procedures
    "诊疗操作": "PR",
    "手术": "PR",
    "操作": "PR",
    "放疗": "PR",
    "procedure": "PR",
    "pr": "PR",
    # CE - Clinical Events
    "临床事件": "CE",
    "clinical event": "CE",
    "ce": "CE",
    # CV - Cardiovascular
    "心血管系统检查": "CV",
    "超声心动图": "CV",
    "cardiovascular": "CV",
    "cv": "CV",
    # IS - Immunogenicity
    "免疫原性检验": "IS",
    "免疫原性": "IS",
    "ada": "IS",
    "nab": "IS",
    "immunogenicity": "IS",
    "is": "IS",
    # PC/PP - Pharmacokinetics
    "药代动力学浓度": "PC",
    "药代浓度": "PC",
    "pk浓度": "PC",
    "pharmacokinetics": "PC",
    "pc": "PC",
    "药代动力学参数": "PP",
    "药代参数": "PP",
    "pk参数": "PP",
    "pp": "PP",
    # MB - Microbiology
    "微生物学检查": "MB",
    "微生物": "MB",
    "microbiology": "MB",
    "mb": "MB",
    # MO - Morphology
    "形态学发现": "MO",
    "形态学": "MO",
    "morphology": "MO",
    "mo": "MO",
    # FA - Findings About
    "事件或干预相关发现": "FA",
    "findings about": "FA",
    "fa": "FA",
    # SC - Subject Characteristics
    "个人史": "SC",
    "受试者特征": "SC",
    "subject characteristics": "SC",
    "sc": "SC",
    # IE - Inclusion/Exclusion
    "入排标准": "IE",
    "纳入排除": "IE",
    "inclusion": "IE",
    "exclusion": "IE",
    "ie": "IE",
    # DD - Death Details
    "死亡": "DD",
    "death": "DD",
    "dd": "DD",
    # SS - Subject Status
    "生存状态": "SS",
    "生存随访": "SS",
    "subject status": "SS",
    "ss": "SS",
    # -------------------------------------------------------------------------
    # Extended from ALS2SDTM_TEST.json knowledge base
    # -------------------------------------------------------------------------
    # DM - Demographics (additional variants)
    "人口学资料": "DM",
    "受试者/参与者信息": "DM",
    # XU - Unplanned Procedures/Observations (SDTM IG 3.4)
    "其他计划外检查": "XU",
    # AE / CE / FA
    "低血糖发生情况（糖尿病）": "FA",  # predominantly FA in KB (9/17)
    # MH - Medical History (additional)
    "既往及现病史": "MH",
    "sle诊断信息": "MH",
    "SLE诊断信息": "MH",
    # APMH - Associated Persons Medical History (SDTM IG 3.4)
    "家族史": "APMH",
    # CM - Concomitant Medications (additional)
    "后续抗肿瘤中药治疗史": "CM",
    "后续抗肿瘤药物治疗": "CM",
    "既往抗肿瘤中药治疗史": "CM",
    "既往抗肿瘤药物治疗史": "CM",
    "研究疾病药物治疗史": "CM",
    "润肤剂使用情况": "CM",
    # PR - Procedures (additional)
    "后续抗肿瘤其他治疗": "PR",
    "后续抗肿瘤手术治疗": "PR",
    "后续抗肿瘤放疗": "PR",
    "既往及合并非药物治疗": "PR",
    "既往抗肿瘤其他治疗史": "PR",
    "既往抗肿瘤手术史": "PR",
    "既往抗肿瘤放疗史": "PR",
    "皮损拍照": "PR",
    "研究疾病干细胞移植史": "PR",
    "研究疾病细胞疗法治疗史": "PR",
    # DS - Disposition (additional)
    "治疗结束页": "DS",
    "研究结束页": "DS",
    "入组信息/随机信息": "DS",
    # EX/EC - Exposure / Exposure as Collected
    "试验药物用药情况": "EC",
    # DA - Drug Accountability
    "试验药物发放": "DA",
    # VS - Vital Signs (additional)
    "腰围": "VS",
    "身高和体重": "VS",
    # LB - Laboratory (additional)
    "24小时尿蛋白定量": "LB",
    "7点-SMBG（糖尿病）": "LB",
    "γ-干扰素释放试验(IGRA)检查": "LB",
    "垂体-肾上腺轴": "LB",
    "外周血采集": "LB",
    "妊娠检查": "LB",
    "心肌酶谱": "LB",
    "凝血功能": "LB",
    "空腹血糖（糖尿病）": "LB",
    "细胞因子血液样本采集": "CP",
    "细胞因子血液样本采集-计划外": "CP",
    "自我血糖监测（糖尿病）": "LB",
    "药效学血液样本采集": "LB",
    "药效学血液样本采集-计划外": "LB",
    "药物滥用筛查/尿液药物筛查": "LB",
    "血生化": "LB",
    "骨髓活检": "LB",
    "骨髓穿刺": "LB",
    "病毒学检查": "MB",
    "HBV DNA": "MB",
    "HCV RNA": "MB",
    # MB - Microbiology (additional)
    "感染源标本采集和培养（本地实验室）": "MB",
    "感染源标本采集和培养-基线期（本地实验室）": "MB",
    "血培养标本采集和培养（本地实验室）": "MB",
    # PC - Pharmacokinetics Concentration
    "PK血液样本采集": "PC",
    "PK血液样本采集-计划外": "PC",
    # ADA/IS - Immunogenicity Specimen Collection
    "ADA血液样本采集": "IS",
    "ADA血液样本采集-计划外": "IS",
    "免疫原性采血/免疫细胞采血": "IS",
    "免疫原性采血/免疫细胞采血-计划外": "IS",
    # EG - ECG (additional entries covered by keyword map)
    # PE - Physical Exam (additional)
    "凹槽钉板测试记录表": "PE",
    "振动敏感性检测记录表": "PE",
    "皮肤专科检查": "PE",
    "眼底检查": "OE",
    "胸部CT": "PE",
    "胸部X线": "PE",
    "腹部B超": "PE",
    "骨骼检查": "MK",
    "骨骼检查-筛选期": "MK",
    # GF - Genomic Findings
    "FISH检查": "GF",
    # MI - Microscopic Findings
    "肿瘤组织样本收集": "MI",
    # FA - Findings About (additional)
    "BSA评分": "FA",
    "免疫效应细胞相关神经毒性综合征（ICANS）": "FA",
    "多发性骨髓瘤诊断与类型": "FA",
    "宫颈癌病史": "FA",
    "小细胞肺癌病史": "FA",
    "研究疾病<适应症：精神分裂>": "FA",
    "糖尿病病史（糖尿病）": "FA",
    "细胞因子释放综合征（CRS）": "FA",
    "结直肠癌病史": "FA",
    "肝癌病史": "FA",
    "胃癌病史": "FA",
    "食管癌诊断": "FA",
    "非小细胞肺癌史": "FA",
    "多发性肠道神经系统肿瘤": "FA",
    "恶性血液病病史": "FA",
    "滤泡淋巴瘤病史": "FA",
    # QS - Questionnaires (additional)
    "BILAG-2004评分": "QS",
    "CTCAE神经系统毒性评分标准": "QS",
    "DLQI评分": "QS",
    "EQ-5D-5L评分": "QS",
    "FACT/GOG-Ntx量表": "QS",
    "MINI评估（精神分裂）": "QS",
    "NIS-LL评分": "QS",
    "Norfolk QOL-DN评分": "QS",
    "PGA评分": "QS",
    "PP NRS评分": "QS",
    "SF-36评分": "QS",
    "SLEDAI-2K评分": "QS",
    "TANS量表": "QS",
    "VIGA评分": "QS",
    "WI NRS评分": "QS",
    "临床总体印象量表-疾病严重程度（CGI-S）": "QS",
    "临床总体印象量表（CGI）": "QS",
    "心功能分级（NYHA）评估": "QS",
    "哥伦比亚-自杀严重程度评定量表（C-SSRS）": "QS",
    "皮肤黏膜评分": "QS",
    "视觉调查问卷": "QS",
    "辛普森-安格斯量表（SAS）": "QS",
    # RS - Response/Efficacy
    "EASI评分": "RS",
    "ECOG评分": "RS",
    "Karnofsky功能状态评分": "RS",
    "卡尔加里精神分裂症抑郁量表（CDSS）": "RS",
    "巴恩斯静坐不能量表（BARS）": "RS",
    "异常不自主运动量表（AIMS）": "RS",
    "总体疗效评估（RECIST 1.1）": "RS",
    "总体疗效评估（iRECIST）": "RS",
    "疗效评估(IMWG)": "RS",
    "阳性与阴性症状量表（PANSS）": "RS",
    # TR - Tumor Response
    "浆细胞瘤评估": "TR",
    "浆细胞瘤评估-筛选期": "TR",
    # TU - Tumor/Lesions
    "新病灶": "TU",
    "非靶病灶-基线": "TU",
    "靶病灶-基线": "TU",
    # SR - Skin Response
    "局部耐受性评估": "SR",
    # SC - Subject Characteristics (additional)
    "烟酒史": "SU",
    # SV - Subject Visits
    "肿瘤评估周期": "SV",
    "访视日期": "SV",
    # DD - Death Details
    "死亡记录": "DD",
}


# =============================================================================
# KEYWORD-BASED DOMAIN HINTS
# =============================================================================

# Keyword-based hints applied when the exact table name is unknown.
# These are partial matches - if the keyword appears anywhere in the table name
ANNOTATION_KEYWORD_DOMAIN_MAP: dict[str, str] = {
    # PR - Procedures (prioritize specific terms)
    "手术": "PR",
    "手术史": "PR",
    "放疗": "PR",
    "化疗": "PR",
    "后续抗肿瘤放疗": "PR",
    "既往及合并非药物治疗": "PR",
    "合并非药物治疗": "PR",
    "介入治疗": "PR",
    "内镜": "PR",
    "穿刺": "PR",
    "活检": "PR",
    # CM - Concomitant Medications
    "药物治疗史": "CM",
    "既往及合并用药": "CM",
    "后续抗肿瘤药物治疗": "CM",
    "抗肿瘤药物": "CM",
    "合并药物治疗": "CM",
    "伴随药物治疗": "CM",
    "联合用药": "CM",
    "中药": "CM",
    "西药": "CM",
    # MH - Medical History
    "既往及现病史": "MH",
    "过敏史": "MH",
    "既往病史": "MH",
    # APMH - Associated Persons Medical History (SDTM IG 3.4)
    "家族史": "APMH",
    "现病史": "MH",
    "合并症": "MH",
    "并发症": "MH",
    # EX - Exposure
    "用药情况": "EC",
    "给药情况": "EC",
    "剂量调整": "EC",
    "停药": "EC",
    "减量": "EC",
    # PE - Physical Exam
    "体格检查": "PE",
    "神经系统": "PE",
    "腹部检查": "PE",
    "胸部检查": "PE",
    # QS - Questionnaires
    "量表": "QS",
    "总体疗效评估": "RS",
    "ECOG评分": "RS",  # performance status is efficacy outcome (RS), not questionnaire
    "ecog": "QS",
    "评分": "QS",
    "问卷调查": "QS",
    "生活质量": "QS",
    "疼痛评估": "QS",
    "kps": "QS",
    # SU - Substance Use
    "吸烟史": "SU",
    "饮酒史": "SU",
    "药物滥用史": "SU",
    "吸毒史": "SU",
    "烟酒": "SU",
    # IS - Immunogenicity
    "ADA": "IS",
    "NAB": "IS",
    "抗药抗体": "IS",
    "中和抗体": "IS",
    # CV - Cardiovascular
    "超声心动图": "CV",
    "心脏超声": "CV",
    "射血分数": "CV",
    "lvef": "CV",
    # LB - Laboratory
    "实验室检查": "LB",
    "病毒学": "LB",
    "HBV": "LB",
    "HCV": "LB",
    "HIV": "LB",
    "梅毒": "LB",
    "肌酐清除率": "LB",
    "传染病": "LB",
    "肿瘤标记物": "LB",
    "肿瘤标志物": "LB",
    "血液学": "LB",
    "生化检查": "LB",
    "凝血功能": "LB",
    "尿液分析": "LB",
    "肝肾功能": "LB",
    # MI - Microscopic Findings
    "生物标志物": "MI",
    "病理": "MI",
    "组织学": "MI",
    # DS - Disposition
    "入组信息": "DS",
    "随机": "DS",
    "治疗结束": "DS",
    "研究结束": "DS",
    "知情同意": "DS",
    "筛选": "DS",
    "脱落": "DS",
    "撤回": "DS",
    "完成研究": "DS",
    "提前终止": "DS",
    # SS - Subject Status
    "生存随访": "SS",
    "生存状态": "SS",
    "随访": "SS",
    # DD - Death Details
    "死亡": "DD",
    "死亡原因": "DD",
    # RS - Response
    "疗效": "RS",
    "缓解": "RS",
    "response": "RS",
    "recist": "RS",
    # TU - Tumor
    "病灶": "TU",
    "靶病灶": "TU",
    "lesion": "TU",
    # AE - Adverse Events
    "不良反应": "AE",
    "毒性反应": "AE",
    "安全性事件": "AE",
    "sae": "AE",
    # EG - ECG
    "心电": "EG",
    "qt间期": "EG",
    "qtc": "EG",
    # VS - Vital Signs
    "脉搏": "VS",
    "呼吸频率": "VS",
    "心率": "VS",
    "pulse": "VS",
    # IE - Inclusion/Exclusion
    "入选标准": "IE",
    "排除标准": "IE",
    "纳入标准": "IE",
    # DM - Demographics
    "年龄": "DM",
    "性别": "DM",
    "种族": "DM",
    "民族": "DM",
    # FA - Findings About
    "肿瘤病史": "FA",
    "癌病史": "FA",
    # XU - Unplanned Procedures (SDTM IG 3.4); was incorrectly set to XB
    "其他计划外检查": "XU",
}


# =============================================================================
# COMPREHENSIVE DOMAIN KEYWORDS (for inference)
# =============================================================================

# Keywords associated with each SDTM domain (Chinese + English)
# Used for inferring domain from annotation_table, annotation_variable, metadata_variable
# Note: Keywords should be specific enough to avoid false matches
DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "AE": [
        "不良事件",
        "adverse event",
        "ae",
        "毒性",
        "副作用",
        "不良反应",
        "安全性事件",
        "sae",
        "严重不良",
        "aeterm",
        "aestdtc",
    ],
    "CM": [
        "合并用药",
        "伴随用药",
        "既往用药",
        "concomitant medication",
        "medication",
        "用药",
        "药物治疗",
        "抗肿瘤药物",
        "联合用药",
        "cmtrt",
        "cmdose",
    ],
    "MH": [
        "既往史",
        "病史",
        "既往病史",
        "medical history",
        "history",
        "现病史",
        "过敏史",
        "家族史",
        "基础疾病",
        "mhterm",
        "mhstdtc",
    ],
    "PR": [
        "手术",
        "操作",
        "procedure",
        "既往手术",
        "放疗",
        "非药物治疗",
        "治疗史",
        "化疗",
        "介入",
        "穿刺",
        "活检",
        "内镜",
        "prtrt",
        "prstdtc",
    ],
    "VS": [
        "生命体征",
        "体征",
        "vital sign",
        "血压",
        "脉搏",
        "呼吸",
        "体温",
        "身高",
        "体重",
        "心率",
        "收缩压",
        "舒张压",
        "bmi",
        "vsorres",
        "vstest",
    ],
    "LB": [
        "实验室",
        "化验",
        "laboratory",
        "lab",
        "检验",
        "血常规",
        "尿常规",
        "生化",
        "凝血",
        "肝功",
        "肾功",
        "白细胞",
        "血红蛋白",
        "血小板",
        "lborres",
        "lbtest",
    ],
    "EG": ["心电图", "ecg", "electrocardiogram", "心电", "qt", "qtc", "心率", "pr间期", "qrs", "egorres", "egtest"],
    "PE": ["体格检查", "physical exam", "查体", "体检", "神经系统检查", "腹部", "胸部", "peorres", "petest"],
    "OE": ["眼底", "眼科", "视力", "ophthalm", "ocular", "视觉检查"],
    "EX": [
        "给药",
        "用药记录",
        "exposure",
        "暴露",
        "研究药物",
        "试验药物",
        "剂量",
        "给药途径",
        "extrt",
        "exdose",
        "exstdtc",
    ],
    "DM": [
        "人口学",
        "受试者信息",
        "demographic",
        "基本信息",
        "人口统计",
        "年龄",
        "性别",
        "种族",
        "民族",
        "出生日期",
        "age",
        "sex",
        "race",
    ],
    "RS": [
        "疗效评估",
        "肿瘤评估",
        "response",
        "疗效",
        "总体疗效",
        "缓解",
        "recist",
        "irrecist",
        "cr",
        "pr",
        "sd",
        "pd",
        "rsorres",
        "rstest",
    ],
    "TU": ["病灶", "肿瘤病灶", "tumor", "lesion", "肿瘤识别", "靶病灶", "非靶病灶", "新发病灶", "tuloc", "tuorres"],
    "TR": ["肿瘤结果", "测量", "tumor result", "肿瘤测量", "病灶测量", "直径", "长径", "短径", "trorres", "trtest"],
    "QS": [
        "问卷",
        "量表",
        "评分",
        "questionnaire",
        "scale",
        "ecog",
        "kps",
        "生活质量",
        "疼痛评分",
        "qsorres",
        "qstest",
    ],
    "DS": [
        "处置",
        "disposition",
        "完成",
        "治疗结束",
        "研究结束",
        "终止",
        "退出",
        "筛选",
        "入组",
        "随机",
        "脱落",
        "dsterm",
        "dsstdtc",
    ],
    "PC": ["药代动力学", "浓度", "pharmacokinetic", "pk", "血药浓度", "pcorres", "pctest"],
    "PP": ["药代参数", "pk parameter", "auc", "cmax", "tmax", "半衰期", "pporres", "pptest"],
    "IS": ["免疫原性", "ada", "nab", "抗药抗体", "中和抗体", "isorres", "istest"],
    "MB": ["微生物", "microbiology", "细菌", "培养", "mborres", "mbtest"],
    "FA": ["发现", "findings about", "事件相关", "faorres", "fatest"],
    "SC": ["受试者特征", "subject characteristics", "特征", "scorres", "sctest"],
    "SV": ["访视", "visit", "访视计划", "svstdtc"],
    "CE": ["临床事件", "clinical event", "ceterm"],
    "MO": ["形态学", "morphology", "moorres"],
    "IE": ["入排标准", "纳入", "排除", "inclusion", "exclusion", "ietest", "ieorres"],
    "DD": ["死亡", "death", "死因", "ddorres"],
    "SS": ["生存状态", "subject status", "生存随访", "ssorres"],
    "CV": ["心血管", "cardiovascular", "超声心动", "射血分数", "lvef", "cvorres"],
    "SU": ["物质使用", "substance use", "吸烟", "饮酒", "suorres"],
}


# =============================================================================
# DOMAIN VARIABLES DEFINITIONS (loaded from sdtm_spec_enhanced.json)
# =============================================================================

# Domain metadata: English names and categories
DOMAIN_METADATA: dict[str, dict[str, str]] = {
    # Oncology
    "TU": {"name": "Tumor Identification", "category": "oncology"},
    "TR": {"name": "Tumor Results", "category": "oncology"},
    "RS": {"name": "Disease Response", "category": "oncology"},
    # Safety
    "AE": {"name": "Adverse Events", "category": "safety"},
    "DM": {"name": "Demographics", "category": "safety"},
    "MH": {"name": "Medical History", "category": "safety"},
    "CM": {"name": "Concomitant Medications", "category": "safety"},
    "PR": {"name": "Procedures", "category": "safety"},
    "DS": {"name": "Disposition", "category": "safety"},
    "EX": {"name": "Exposure", "category": "safety"},
    "DV": {"name": "Protocol Deviations", "category": "safety"},
    "IE": {"name": "Inclusion/Exclusion Criteria", "category": "safety"},
    "HO": {"name": "Healthcare Encounters", "category": "safety"},
    "DD": {"name": "Death Details", "category": "safety"},
    # Laboratory & Vital Signs
    "LB": {"name": "Laboratory Test Results", "category": "laboratory"},
    "VS": {"name": "Vital Signs", "category": "laboratory"},
    "EG": {"name": "ECG Test Results", "category": "laboratory"},
    "PE": {"name": "Physical Examinations", "category": "laboratory"},
    "PC": {"name": "Pharmacokinetics Concentrations", "category": "laboratory"},
    "PP": {"name": "Pharmacokinetics Parameters", "category": "laboratory"},
    "IS": {"name": "Immunogenicity Specimen", "category": "laboratory"},
    "MB": {"name": "Microbiology Specimen", "category": "laboratory"},
    "MI": {"name": "Microscopic Findings", "category": "laboratory"},
    "MS": {"name": "Microbiology Susceptibility", "category": "laboratory"},
    "DA": {"name": "Drug Accountability", "category": "laboratory"},
    "EC": {"name": "Exposure as Collected", "category": "laboratory"},
    # Imaging & Morphology
    "MO": {"name": "Morphology", "category": "imaging"},
    # Questionnaire & Findings
    "QS": {"name": "Questionnaires", "category": "questionnaire"},
    "FA": {"name": "Findings About Events or Interventions", "category": "questionnaire"},
    "SC": {"name": "Subject Characteristics", "category": "questionnaire"},
    "SS": {"name": "Subject Status", "category": "questionnaire"},
    "SR": {"name": "Skin Response", "category": "questionnaire"},
    "SU": {"name": "Substance Use", "category": "questionnaire"},
    # Trial Design
    "TA": {"name": "Trial Arms", "category": "trial_design"},
    "TD": {"name": "Trial Disease Assessments", "category": "trial_design"},
    "TE": {"name": "Trial Elements", "category": "trial_design"},
    "TI": {"name": "Trial Inclusion/Exclusion Criteria", "category": "trial_design"},
    "TS": {"name": "Trial Summary", "category": "trial_design"},
    "TV": {"name": "Trial Visits", "category": "trial_design"},
    # Other
    "SV": {"name": "Subject Visits", "category": "other"},
    "SE": {"name": "Subject Elements", "category": "other"},
    "CE": {"name": "Clinical Events", "category": "other"},
    # SDTM IG 3.4 additional domains
    "GF": {"name": "Genomic Findings", "category": "laboratory"},
    "MK": {"name": "Musculoskeletal System Findings", "category": "laboratory"},
    "OE": {"name": "Ophthalmic Examinations", "category": "laboratory"},
    "CP": {"name": "Cell Phenotyping", "category": "laboratory"},
    "XU": {"name": "Unplanned Procedures/Observations", "category": "other"},
    "APMH": {"name": "Associated Persons Medical History", "category": "safety"},
    "CO": {"name": "Comments", "category": "other"},
    "RP": {"name": "Reproductive System Findings", "category": "other"},
    "RELREC": {"name": "Related Records", "category": "other"},
    "SUPPQUAL": {"name": "Supplemental Qualifiers", "category": "other"},
    # SDTM IG 3.4 — additional domains not yet listed above
    "AG": {"name": "Procedure Agents", "category": "safety"},
    "BE": {"name": "Biospecimen Events", "category": "safety"},
    "BS": {"name": "Biospecimen Findings", "category": "laboratory"},
    "CV": {"name": "Cardiovascular System Findings", "category": "laboratory"},
    "FT": {"name": "Functional Tests", "category": "laboratory"},
    "ML": {"name": "Meal Data", "category": "safety"},
    "NV": {"name": "Nervous System Findings", "category": "laboratory"},
    "OI": {"name": "Non-host Organism Identifiers", "category": "other"},
    "RE": {"name": "Respiratory System Findings", "category": "laboratory"},
    "RELSPEC": {"name": "Related Specimens", "category": "other"},
    "RELSUB": {"name": "Related Subjects", "category": "other"},
    "SM": {"name": "Subject Disease Milestones", "category": "other"},
    "TM": {"name": "Trial Disease Milestones", "category": "trial_design"},
    "UR": {"name": "Urinary System Findings", "category": "laboratory"},
}


def _load_domain_variables_from_json() -> dict[str, dict[str, Any]]:
    """
    Load SDTM domain variables.

    Priority:
    1. data/knowledge_base/standards/sdtmig_v3.4_variables.json  (63 domains, IG 3.4 authoritative)
    2. data/knowledge_base/structured/sdtm_spec_enhanced.json     (46 domains, legacy fallback)

    Returns a dictionary with domain as key, containing:
    - name: Domain English name
    - category: Domain category
    - variables: List of {variable, label} dicts
    """
    # --- Attempt 1: IG 3.4 authoritative JSON ---
    ig34_paths = [
        Path("data/knowledge_base/standards/sdtmig_v3.4_variables.json"),
        Path(__file__).parent.parent.parent / "data/knowledge_base/standards/sdtmig_v3.4_variables.json",
    ]
    for p in ig34_paths:
        if p.exists():
            try:
                with open(p, encoding="utf-8") as f:
                    ig34_data: dict[str, dict] = json.load(f)

                # Map IG 3.4 class -> internal category
                _class_to_category: dict[str, str] = {
                    "Interventions": "safety",
                    "Events": "safety",
                    "Findings": "laboratory",
                    "Findings About": "laboratory",
                    "Special Purpose": "other",
                    "Relationship": "other",
                    "Trial Design": "other",
                    "Associated Persons": "safety",
                }
                result: dict[str, dict[str, Any]] = {}
                for domain, entry in ig34_data.items():
                    cls = entry.get("class", "")
                    # Use DOMAIN_METADATA category if available; otherwise derive from class
                    meta = DOMAIN_METADATA.get(domain)
                    category = meta["category"] if meta else _class_to_category.get(cls, "other")
                    name = meta["name"] if meta else entry.get("name", domain)
                    result[domain] = {
                        "name": name,
                        "category": category,
                        "variables": entry.get("variables", []),
                    }
                return result
            except Exception as e:
                print(f"Warning: Failed to load sdtmig_v3.4_variables.json: {e}")

    # --- Attempt 2: legacy sdtm_spec_enhanced.json ---
    legacy_paths = [
        Path("data/knowledge_base/structured/sdtm_spec_enhanced.json"),
        Path(__file__).parent.parent.parent / "data/knowledge_base/structured/sdtm_spec_enhanced.json",
    ]
    for json_path in legacy_paths:
        if not json_path.exists():
            continue
        try:
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)

            domain_vars: dict[str, list[dict[str, str]]] = {}
            for item in data:
                domain = item.get("domain", "").strip().upper()
                variable = item.get("variable", "").strip()
                label = item.get("label", "").strip()
                if not domain or not variable:
                    continue
                if variable in ("STUDYID", "DOMAIN", "USUBJID"):
                    continue
                if domain not in domain_vars:
                    domain_vars[domain] = []
                existing_vars = [v["variable"] for v in domain_vars[domain]]
                if variable not in existing_vars:
                    domain_vars[domain].append({"variable": variable, "label": label})

            result_legacy: dict[str, dict[str, Any]] = {}
            for domain, vars_list in domain_vars.items():
                meta = DOMAIN_METADATA.get(domain, {"name": domain, "category": "other"})
                result_legacy[domain] = {
                    "name": meta["name"],
                    "category": meta["category"],
                    "variables": vars_list,
                }
            return result_legacy
        except Exception as e:
            print(f"Warning: Failed to load sdtm_spec_enhanced.json: {e}")

    return {}


def _get_fallback_domain_variables() -> dict[str, dict[str, Any]]:
    """Fallback domain variables if JSON loading fails."""
    fallback = {}
    for domain, meta in DOMAIN_METADATA.items():
        fallback[domain] = {
            "name": meta["name"],
            "category": meta["category"],
            "variables": [],  # Empty, will use basic format
        }
    return fallback


# Load domain variables from JSON (with fallback)
_loaded_domain_variables = _load_domain_variables_from_json()
DOMAIN_VARIABLES: dict[str, dict[str, Any]] = (
    _loaded_domain_variables if _loaded_domain_variables else _get_fallback_domain_variables()
)

# Domain categories for grouping (dynamic based on loaded data)
DOMAIN_CATEGORIES: dict[str, list[str]] = {
    "oncology": ["TU", "TR", "RS"],
    "safety": ["AE", "DM", "MH", "CM", "PR", "DS", "EX", "DV", "IE", "HO", "DD"],
    "laboratory": ["LB", "VS", "EG", "PE", "PC", "PP", "IS", "MB", "MI", "MS", "DA", "EC"],
    "imaging": ["MO"],
    "questionnaire": ["QS", "FA", "SC", "SS", "SR", "SU"],
    "trial_design": ["TA", "TD", "TE", "TI", "TS", "TV"],
    "other": ["SV", "SE", "CE", "CO", "RP", "RELREC", "SUPPQUAL"],
}

# Default domains to show when no hints available (most common)
# Reduced to 6 most frequent domains to minimize prompt size when inference fails
# Full list was: ["AE", "CM", "DM", "DS", "EX", "LB", "MH", "PE", "PR", "QS", "VS"]
DEFAULT_DOMAINS: list[str] = ["AE", "CM", "DM", "LB", "MH", "VS"]

# All valid SDTM domains for validation
VALID_SDTM_DOMAINS: set[str] = {
    # Events
    "AE",
    "CE",
    "DS",
    "DV",
    "HO",
    "MH",
    # Interventions
    "CM",
    "EC",
    "EX",
    "PR",
    "SU",
    # Findings
    "CV",
    "DA",
    "DD",
    "EG",
    "FA",
    "IE",
    "IS",
    "LB",
    "MB",
    "MI",
    "MO",
    "MS",
    "PC",
    "PE",
    "PP",
    "QS",
    "RP",
    "RS",
    "SC",
    "SR",
    "SS",
    "TR",
    "TU",
    "VS",
    # Special Purpose
    "CO",
    "DM",
    "SE",
    "SM",
    "SV",
    # Trial Design
    "TA",
    "TD",
    "TE",
    "TI",
    "TS",
    "TV",
    # Relationship
    "RELREC",
    "RELSPEC",  # Related Specimens
    "RELSUB",  # Related Subjects
    # SDTM IG 3.4 additional domains
    "AG",  # Procedure Agents
    "BE",  # Biospecimen Events
    "BS",  # Biospecimen Findings
    "FT",  # Functional Tests
    "GF",  # Genomic Findings
    "MK",  # Musculoskeletal System Findings
    "ML",  # Meal Data
    "NV",  # Nervous System Findings
    "OE",  # Ophthalmic Examinations
    "OI",  # Non-host Organism Identifiers
    "RE",  # Respiratory System Findings
    "TM",  # Trial Disease Milestones
    "UR",  # Urinary System Findings
    "CP",  # Cell Phenotyping
    "XU",  # Unplanned Procedures/Observations (study-specific)
    "APMH",  # Associated Persons Medical History
    "SUPPQUAL",  # Supplemental Qualifiers
}


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def normalize_semantic_map(mapping: dict[str, str]) -> dict[str, str]:
    """Return a normalized, lowercase-key map with uppercase domain codes."""
    normalized: dict[str, str] = {}
    for key, code in mapping.items():
        if not key or not code:
            continue
        key_norm = key.strip().lower()
        code_norm = code.strip().upper()
        if key_norm and code_norm:
            normalized[key_norm] = code_norm
    return normalized


# Pre-normalized maps for faster lookup
_NORMALIZED_TABLE_MAP: dict[str, str] = normalize_semantic_map(CHINESE_TABLE_DOMAIN_MAP)
_NORMALIZED_KEYWORD_MAP: dict[str, str] = normalize_semantic_map(ANNOTATION_KEYWORD_DOMAIN_MAP)

# Longest-match-first sorted keyword lists to prevent short keywords from
# shadowing longer, more specific ones (e.g. "肿瘤" shadowing "肿瘤病史").
_KEYWORD_MAP_SORTED: list[tuple] = sorted(_NORMALIZED_KEYWORD_MAP.items(), key=lambda kv: len(kv[0]), reverse=True)
_DOMAIN_KEYWORDS_SORTED: list[tuple] = sorted(
    ((kw.lower(), domain) for domain, kws in DOMAIN_KEYWORDS.items() for kw in kws),
    key=lambda kv: len(kv[0]),
    reverse=True,
)


def infer_domain_from_table(table_name: str) -> str | None:
    """
    Infer SDTM domain from table name using exact match first, then keywords.

    Parameters
    ----------
    table_name : str
        The annotation table name (Chinese or English)

    Returns
    -------
    Optional[str]
        Inferred domain code (e.g., "AE", "CM") or None if not found
    """
    if not table_name:
        return None

    lower = table_name.strip().lower()

    # 1. Exact match
    if lower in _NORMALIZED_TABLE_MAP:
        return _NORMALIZED_TABLE_MAP[lower]

    # 2. Keyword match from ANNOTATION_KEYWORD_DOMAIN_MAP (longest match first)
    for keyword, domain in _KEYWORD_MAP_SORTED:
        if keyword in lower:
            return domain

    # 3. Keyword match from DOMAIN_KEYWORDS (longest match first)
    for kw, domain in _DOMAIN_KEYWORDS_SORTED:
        if kw in lower:
            return domain

    return None


def infer_domains(
    annotation_table: str = "",
    annotation_variable: str = "",
    metadata_variable: str = "",
) -> list[str]:
    """
    Infer possible SDTM domains from annotation information.

    This is the unified domain inference function that combines all strategies:
    1. Table name exact match / keyword match (highest confidence)
    2. Annotation variable keyword search
    3. Combined text search as fallback

    Note: metadata_variable prefix is NOT used for inference because CRF variables
    do not necessarily follow SDTM naming conventions.

    Parameters
    ----------
    annotation_table : str
        The annotation table name (e.g., "不良事件", "合并用药")
    annotation_variable : str
        The annotation variable description (e.g., "开始日期", "严重程度")
    metadata_variable : str
        The metadata variable name - used only in combined text search

    Returns
    -------
    List[str]
        List of inferred domain codes, ordered by confidence.
        Empty list if no domains could be inferred.

    Examples
    --------
    >>> infer_domains("不良事件", "不良事件名称", "AETERM")
    ['AE']
    >>> infer_domains("合并用药", "开始日期", "CMSTDTC")
    ['CM']
    >>> infer_domains("肿瘤病灶", "病灶位置", "TULOC")
    ['TU']
    """
    hints: set[str] = set()
    ordered: list[str] = []

    def add_hint(domain: str | None) -> None:
        if domain and domain not in hints:
            hints.add(domain)
            ordered.append(domain)

    # 1. From table name (highest confidence)
    #    Uses exact match first, then keyword match
    add_hint(infer_domain_from_table(annotation_table))

    # 2. From annotation variable keywords (longest match first)
    if annotation_variable:
        lower_var = annotation_variable.lower()
        for kw, domain in _DOMAIN_KEYWORDS_SORTED:
            if kw in lower_var:
                add_hint(domain)

    # 3. Combined text search as fallback (longest match first)
    #    Note: metadata_variable is included here but NOT used for prefix extraction
    combined = f"{annotation_table} {annotation_variable} {metadata_variable}".lower()
    for kw, domain in _DOMAIN_KEYWORDS_SORTED:
        if kw in combined and domain not in hints:
            add_hint(domain)

    return ordered


def get_domain_info(domain: str) -> dict | None:
    """
    Get domain information including name and variables.

    Parameters
    ----------
    domain : str
        Domain code (e.g., "AE", "CM")

    Returns
    -------
    Optional[Dict]
        Domain info dict with 'name', 'category', 'variables' or None
    """
    return DOMAIN_VARIABLES.get(domain.upper())


def get_domains_by_category(category: str) -> list[str]:
    """
    Get all domains in a category.

    Parameters
    ----------
    category : str
        Category name (e.g., "oncology", "safety", "laboratory")

    Returns
    -------
    List[str]
        List of domain codes in the category
    """
    return DOMAIN_CATEGORIES.get(category.lower(), [])


def format_domain_variables_section(
    domain_hints: list[str] | None = None,
    max_domains: int = 6,
    max_vars_per_domain: int = 8,
    expand_related: bool = False,
) -> str:
    """
    Format domain variables for prompt inclusion.

    Dynamically generates the "Available SDTM Domains and Variables" section
    based on inferred domains instead of using a hardcoded list.

    OPTIMIZED: Reduced output size to minimize prompt tokens:
    - Reduced max_domains from 8 to 6
    - Reduced max_vars_per_domain from 15 to 8
    - Disabled automatic domain expansion by default
    - Simplified variable format (no labels by default)

    Parameters
    ----------
    domain_hints : List[str], optional
        List of inferred domain codes. If None or empty, uses DEFAULT_DOMAINS.
    max_domains : int
        Maximum number of domains to include (default 6, reduced from 8)
    max_vars_per_domain : int
        Maximum variables to show per domain (default 8, reduced from 15)
    expand_related : bool
        Whether to add related domains by category (default False, disabled for speed)

    Returns
    -------
    str
        Formatted string for prompt inclusion

    Examples
    --------
    >>> print(format_domain_variables_section(["AE", "CM"]))
    **Available SDTM Domains and Variables:**

    **Safety Domains:**
    - AE (Adverse Events): AETERM, AEDECOD, AESTDTC, AEENDTC, AESER, AEREL, AEACN, AEOUT
    - CM (Concomitant Medications): CMTRT, CMDECOD, CMSTDTC, CMENDTC, ...
    """
    # Use defaults if no hints
    domains_to_show = list(domain_hints) if domain_hints else list(DEFAULT_DOMAINS)

    # When only 1-2 domains are hinted, show ALL variables (no truncation)
    # so the LLM can see the complete standard variable list and won't
    # hallucinate non-existent variable names.
    if domain_hints and len(domain_hints) <= 2:
        max_vars_per_domain = 999

    # Ensure we don't exceed max
    domains_to_show = domains_to_show[:max_domains]

    # DISABLED: Auto-expansion of related domains (for prompt size optimization)
    # Only expand if explicitly requested
    if expand_related and len(domains_to_show) < max_domains:
        # Find related domains by category
        related: set[str] = set()
        for d in domains_to_show:
            info = DOMAIN_VARIABLES.get(d)
            if info:
                cat = info.get("category")
                if cat:
                    for related_d in DOMAIN_CATEGORIES.get(cat, []):
                        if related_d not in domains_to_show:
                            related.add(related_d)

        # Add related up to max
        for rd in list(related)[: max_domains - len(domains_to_show)]:
            domains_to_show.append(rd)

    # Group by category
    categorized: dict[str, list[str]] = {}
    for domain in domains_to_show:
        info = DOMAIN_VARIABLES.get(domain)
        if info:
            cat = info.get("category", "other")
            categorized.setdefault(cat, []).append(domain)

    # Format output
    lines: list[str] = ["**Available SDTM Domains and Variables:**"]

    # Category display names
    cat_names = {
        "oncology": "Oncology Domains",
        "safety": "Safety Domains",
        "laboratory": "Laboratory & Vital Signs Domains",
        "imaging": "Imaging Domains",
        "questionnaire": "Questionnaire Domains",
        "trial_design": "Trial Design Domains",
        "other": "Other Domains",
    }

    # Output in preferred order
    for cat in ["oncology", "safety", "laboratory", "imaging", "questionnaire", "trial_design", "other"]:
        if cat not in categorized:
            continue

        lines.append(f"\n**{cat_names.get(cat, cat.title())}:**")

        for domain in categorized[cat]:
            info = DOMAIN_VARIABLES.get(domain)
            if not info:
                continue

            name = info.get("name", "")
            variables = info.get("variables", [])

            # Handle both old format (list of strings) and new format (list of dicts)
            if variables and isinstance(variables[0], dict):
                # New format: list of {variable, label} dicts
                # OPTIMIZED: Only show variable names (no labels) to reduce size
                var_parts = [v.get("variable", "") for v in variables[:max_vars_per_domain]]
                var_str = ", ".join(var_parts)
                if len(variables) > max_vars_per_domain:
                    var_str += f", ... (+{len(variables) - max_vars_per_domain} more)"
            else:
                # Old format: list of strings (fallback)
                var_str = ", ".join(variables[:max_vars_per_domain])
                if len(variables) > max_vars_per_domain:
                    var_str += f", ... (+{len(variables) - max_vars_per_domain} more)"

            lines.append(f"- {domain} ({name}): {var_str}")

    return "\n".join(lines)


def get_all_domains() -> list[str]:
    """Return a list of all defined SDTM domain codes."""
    return sorted(DOMAIN_VARIABLES.keys())


def is_valid_domain(domain: str) -> bool:
    """
    Check if a domain code is a valid SDTM domain.

    Parameters
    ----------
    domain : str
        Domain code to validate (e.g., "AE", "LB", "CC")

    Returns
    -------
    bool
        True if domain is valid, False otherwise

    Examples
    --------
    >>> is_valid_domain("AE")
    True
    >>> is_valid_domain("CC")  # Not a valid SDTM domain
    False
    """
    if not domain:
        return False
    return domain.strip().upper() in VALID_SDTM_DOMAINS


def strip_supp_prefix(domain: str) -> str:
    """Return the base domain for a SUPP-qualified domain code.

    ``"SUPPAE"`` → ``"AE"``; anything not prefixed with ``SUPP`` is
    returned unchanged (upper-cased). ``"SUPP"`` on its own is preserved.
    """
    if not domain:
        return ""
    up = domain.strip().upper()
    return up[4:] if up.startswith("SUPP") and len(up) > 4 else up


__all__ = [
    "ANNOTATION_KEYWORD_DOMAIN_MAP",
    "CHINESE_TABLE_DOMAIN_MAP",
    "DEFAULT_DOMAINS",
    "DOMAIN_CATEGORIES",
    "DOMAIN_KEYWORDS",
    "DOMAIN_VARIABLES",
    "VALID_SDTM_DOMAINS",
    "_DOMAIN_KEYWORDS_SORTED",
    "_KEYWORD_MAP_SORTED",
    "format_domain_variables_section",
    "get_all_domains",
    "get_domain_info",
    "get_domains_by_category",
    "infer_domain_from_table",
    "infer_domains",
    "is_valid_domain",
    "normalize_semantic_map",
    "strip_supp_prefix",
]
