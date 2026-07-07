#!/usr/bin/env python
"""
Enhance SDTM Spec Knowledge Base for Better RAG Retrieval

This script enriches the sdtm_spec_preprocessed knowledge base with:
1. Chinese keywords and synonyms
2. Common annotation table names
3. Variable descriptions and usage scenarios
4. Related variables
"""

from pathlib import Path
from typing import Any

import pandas as pd

# Chinese keywords mapping for common SDTM variables
VARIABLE_KEYWORDS: dict[str, dict[str, Any]] = {
    # CM Domain - Concomitant Medications
    "CMTRT": {
        "keywords": ["药物名称", "用药名称", "治疗名称", "药品名称", "合并用药"],
        "tables": ["既往及合并用药", "合并用药", "药物治疗史", "化疗明细"],
        "description": "合并用药或既往用药的药物名称",
    },
    "CMSTDTC": {
        "keywords": ["药物开始日期", "用药开始日期", "给药开始日期", "治疗开始日期", "开始日期"],
        "tables": ["既往及合并用药", "合并用药", "药物治疗史", "化疗明细"],
        "description": "合并用药或既往用药的开始日期时间，ISO8601格式",
    },
    "CMENDTC": {
        "keywords": ["药物结束日期", "用药结束日期", "给药结束日期", "治疗结束日期", "停药日期", "结束日期"],
        "tables": ["既往及合并用药", "合并用药", "药物治疗史", "化疗明细"],
        "description": "合并用药或既往用药的结束日期时间，ISO8601格式",
    },
    "CMDOSE": {
        "keywords": ["剂量", "用药剂量", "给药剂量", "药物剂量"],
        "tables": ["既往及合并用药", "合并用药", "药物治疗史"],
        "description": "合并用药的剂量数值",
    },
    "CMDOSU": {
        "keywords": ["剂量单位", "用药单位", "给药单位"],
        "tables": ["既往及合并用药", "合并用药"],
        "description": "合并用药的剂量单位",
    },
    "CMROUTE": {
        "keywords": ["给药途径", "用药途径", "给药方式"],
        "tables": ["既往及合并用药", "合并用药"],
        "description": "合并用药的给药途径",
    },
    "CMINDC": {
        "keywords": ["用药原因", "适应症", "用药指征"],
        "tables": ["既往及合并用药", "合并用药"],
        "description": "合并用药的适应症或用药原因",
    },
    # AE Domain - Adverse Events
    "AETERM": {
        "keywords": ["不良事件名称", "不良事件", "AE名称", "事件名称"],
        "tables": ["不良事件", "AE"],
        "description": "不良事件的报告名称（原始术语）",
    },
    "AESTDTC": {
        "keywords": ["不良事件开始日期", "AE开始日期", "事件开始日期", "发生日期", "开始日期"],
        "tables": ["不良事件", "AE"],
        "description": "不良事件的开始日期时间，ISO8601格式",
    },
    "AEENDTC": {
        "keywords": ["不良事件结束日期", "AE结束日期", "事件结束日期", "恢复日期", "结束日期"],
        "tables": ["不良事件", "AE"],
        "description": "不良事件的结束日期时间，ISO8601格式",
    },
    "AESER": {
        "keywords": ["严重不良事件", "SAE", "是否严重", "严重性"],
        "tables": ["不良事件", "AE"],
        "description": "是否为严重不良事件的标识",
    },
    "AEREL": {
        "keywords": ["与研究药物关系", "因果关系", "相关性", "关联性"],
        "tables": ["不良事件", "AE"],
        "description": "不良事件与研究药物的因果关系",
    },
    "AEOUT": {
        "keywords": ["转归", "结局", "结果", "恢复情况"],
        "tables": ["不良事件", "AE"],
        "description": "不良事件的转归/结局",
    },
    "AESEV": {
        "keywords": ["严重程度", "等级", "分级", "CTCAE"],
        "tables": ["不良事件", "AE", "严重程度明细"],
        "description": "不良事件的严重程度等级",
    },
    "AEACN": {
        "keywords": ["采取的措施", "处理措施", "治疗措施", "对药物采取的措施"],
        "tables": ["不良事件", "AE"],
        "description": "针对不良事件对研究药物采取的措施",
    },
    # PR Domain - Procedures
    "PRTRT": {
        "keywords": ["手术名称", "操作名称", "治疗名称", "放疗", "化疗"],
        "tables": ["手术史", "放疗史", "既往治疗"],
        "description": "手术或治疗操作的名称",
    },
    "PRSTDTC": {
        "keywords": ["手术开始日期", "操作开始日期", "治疗开始日期", "开始日期"],
        "tables": ["手术史", "放疗史", "既往治疗"],
        "description": "手术或治疗操作的开始日期时间",
    },
    "PRENDTC": {
        "keywords": ["手术结束日期", "操作结束日期", "治疗结束日期", "结束日期"],
        "tables": ["手术史", "放疗史", "既往治疗"],
        "description": "手术或治疗操作的结束日期时间",
    },
    # MH Domain - Medical History
    "MHTERM": {
        "keywords": ["病史名称", "疾病名称", "既往病史", "诊断名称"],
        "tables": ["病史", "既往病史", "医疗史"],
        "description": "医疗史/既往病史的诊断名称",
    },
    "MHSTDTC": {
        "keywords": ["病史开始日期", "诊断日期", "发病日期", "开始日期"],
        "tables": ["病史", "既往病史"],
        "description": "医疗史的开始日期时间",
    },
    "MHENDTC": {
        "keywords": ["病史结束日期", "痊愈日期", "结束日期"],
        "tables": ["病史", "既往病史"],
        "description": "医疗史的结束日期时间",
    },
    # VS Domain - Vital Signs
    "VSTESTCD": {
        "keywords": ["生命体征代码", "体征代码", "检查代码"],
        "tables": ["生命体征", "体格检查", "VS"],
        "description": "生命体征检查的测试代码",
    },
    "VSORRES": {
        "keywords": ["生命体征结果", "体征结果", "测量值", "检查结果"],
        "tables": ["生命体征", "体格检查", "VS"],
        "description": "生命体征的原始检查结果",
    },
    "VSDTC": {
        "keywords": ["生命体征日期", "体征检查日期", "测量日期"],
        "tables": ["生命体征", "体格检查", "VS"],
        "description": "生命体征检查的日期时间",
    },
    # LB Domain - Laboratory
    "LBTESTCD": {
        "keywords": ["实验室检查代码", "检验代码", "化验代码"],
        "tables": ["实验室检查", "化验", "LB"],
        "description": "实验室检查的测试代码",
    },
    "LBORRES": {
        "keywords": ["实验室结果", "检验结果", "化验结果"],
        "tables": ["实验室检查", "化验", "LB"],
        "description": "实验室检查的原始结果",
    },
    # EG Domain - ECG
    "EGTESTCD": {
        "keywords": ["心电图代码", "ECG代码", "EKG代码"],
        "tables": ["心电图", "ECG", "EG"],
        "description": "心电图检查的测试代码",
    },
    "EGORRES": {
        "keywords": ["心电图结果", "ECG结果", "EKG结果"],
        "tables": ["心电图", "ECG", "EG"],
        "description": "心电图检查的原始结果",
    },
    # TU/TR/RS Domains - Tumor
    "TULOC": {
        "keywords": ["肿瘤位置", "病灶位置", "部位"],
        "tables": ["肿瘤评估", "病灶", "TU"],
        "description": "肿瘤/病灶的解剖位置",
    },
    "TRORRES": {
        "keywords": ["肿瘤测量结果", "病灶大小", "直径"],
        "tables": ["肿瘤评估", "病灶测量", "TR"],
        "description": "肿瘤测量的原始结果",
    },
    "RSORRES": {
        "keywords": ["疗效评价", "肿瘤反应", "疗效结果", "RECIST"],
        "tables": ["疗效评价", "肿瘤反应", "RS"],
        "description": "疾病反应/疗效评价的原始结果",
    },
}

# Domain descriptions in Chinese
DOMAIN_DESCRIPTIONS: dict[str, str] = {
    "AE": "不良事件域 - 记录受试者在研究期间发生的不良事件信息",
    "CM": "合并用药域 - 记录受试者的合并用药和既往用药信息",
    "DM": "人口学域 - 记录受试者的基本人口学信息",
    "DS": "受试者处置域 - 记录受试者的研究进展和处置信息",
    "EG": "心电图域 - 记录受试者的心电图检查结果",
    "EX": "暴露域 - 记录受试者的研究药物暴露信息",
    "LB": "实验室检查域 - 记录受试者的实验室检查结果",
    "MH": "病史域 - 记录受试者的既往病史和医疗史",
    "PE": "体格检查域 - 记录受试者的体格检查结果",
    "PR": "手术/操作域 - 记录受试者的手术和医疗操作信息",
    "RS": "疾病反应域 - 记录肿瘤疗效评价结果",
    "TR": "肿瘤结果域 - 记录肿瘤测量结果",
    "TU": "肿瘤识别域 - 记录肿瘤/病灶的识别信息",
    "VS": "生命体征域 - 记录受试者的生命体征检查结果",
    "QS": "问卷域 - 记录受试者的问卷调查结果",
}


def enhance_chunk_text(row: dict[str, Any]) -> str:
    """Generate enhanced chunk text with keywords and descriptions."""
    variable = row.get("variable", "")
    label = row.get("label", "")
    domain = row.get("domain", "")

    lines = [
        f"Variable: {variable}",
        f"Label: {label}",
        f"Domain: {domain}",
    ]

    # Add domain description
    if domain in DOMAIN_DESCRIPTIONS:
        lines.append(f"域描述: {DOMAIN_DESCRIPTIONS[domain]}")

    # Add variable-specific enhancements
    if variable in VARIABLE_KEYWORDS:
        var_info = VARIABLE_KEYWORDS[variable]

        if var_info.get("description"):
            lines.append(f"变量描述: {var_info['description']}")

        if var_info.get("keywords"):
            lines.append(f"关键词: {', '.join(var_info['keywords'])}")

        if var_info.get("tables"):
            lines.append(f"常见表名: {', '.join(var_info['tables'])}")

    return "\n".join(lines)


def enhance_knowledge_base(input_path: str, output_path: str):
    """Enhance the SDTM spec knowledge base."""

    # Load existing data
    if input_path.endswith(".parquet"):
        df = pd.read_parquet(input_path)
    else:
        df = pd.read_json(input_path)

    print(f"Loaded {len(df)} records from {input_path}")

    # Enhance each record
    enhanced_records = []
    for _, row in df.iterrows():
        record = row.to_dict()

        # Generate enhanced chunk text
        record["chunk_text"] = enhance_chunk_text(record)

        # Add keywords to metadata if available
        variable = record.get("variable", "")
        if variable in VARIABLE_KEYWORDS:
            var_info = VARIABLE_KEYWORDS[variable]
            record["keywords"] = var_info.get("keywords", [])
            record["common_tables"] = var_info.get("tables", [])
            record["var_description"] = var_info.get("description", "")

        enhanced_records.append(record)

    # Create enhanced DataFrame
    enhanced_df = pd.DataFrame(enhanced_records)

    # Save to both JSON and Parquet
    output_base = Path(output_path).stem
    output_dir = Path(output_path).parent

    # Save Parquet
    parquet_path = output_dir / f"{output_base}.parquet"
    enhanced_df.to_parquet(parquet_path, index=False)
    print(f"Saved enhanced Parquet to {parquet_path}")

    # Save JSON
    json_path = output_dir / f"{output_base}.json"
    enhanced_df.to_json(json_path, orient="records", force_ascii=False, indent=2)
    print(f"Saved enhanced JSON to {json_path}")

    # Print sample
    print("\n--- Sample Enhanced chunk_text ---")
    for _, row in enhanced_df.head(3).iterrows():
        print(f"\n{row['chunk_text']}")
        print("-" * 50)

    return enhanced_df


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Enhance SDTM Spec Knowledge Base")
    parser.add_argument(
        "--input",
        default="data/knowledge_base/structured/sdtm_spec_preprocessed.parquet",
        help="Input file path (parquet or json)",
    )
    parser.add_argument(
        "--output", default="data/knowledge_base/structured/sdtm_spec_enhanced.parquet", help="Output file path"
    )
    parser.add_argument("--replace", action="store_true", help="Replace original file instead of creating new one")

    args = parser.parse_args()

    output_path = args.output
    if args.replace:
        output_path = args.input

    enhance_knowledge_base(args.input, output_path)
    print("\n✅ Enhancement complete!")


if __name__ == "__main__":
    main()
