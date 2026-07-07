#!/usr/bin/env python
"""
chunker.py
-----------------------------------
用于读取并切分 SDTMIG / eCRF 等 Parquet 格式知识库文件，
生成可供后续 embedding 和 RAG 检索使用的标准化文本块（Chunk）对象。

当前版本：
- 自动识别知识库类型（SDTMIG / eCRF / 通用）
- 不修改、不保存原始文件（read-only）
- 切分结果保存在 Python 内存中（List[Chunk]）
"""

import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd


# === 定义标准化 Chunk 数据结构 ===
@dataclass
class Chunk:
    id: str
    text: str
    meta: dict[str, Any]


class Chunker:
    """支持自动检测文件类型的通用 Chunker"""

    # 默认排除的文件（可通过环境变量 RAG_EXCLUDE_FILES 覆盖，逗号分隔）
    DEFAULT_EXCLUDE_FILES: ClassVar[set] = set()  # 默认不排除任何文件

    def __init__(self, base_path: str, exclude_files: set | None = None):
        """
        初始化 Chunker
        Args:
            base_path: 知识库主目录路径，例如 'isdtaim/data/knowledge_base/structured'
            exclude_files: 要排除的文件名集合（可选）
        """
        self.base_path = base_path

        # 支持环境变量配置排除文件
        env_exclude = os.getenv("RAG_EXCLUDE_FILES", "")
        if env_exclude:
            self.exclude_files = {f.strip() for f in env_exclude.split(",") if f.strip()}
        elif exclude_files is not None:
            self.exclude_files = exclude_files
        else:
            self.exclude_files = self.DEFAULT_EXCLUDE_FILES.copy()

    @staticmethod
    def _clean(value: Any) -> str:
        if pd.isna(value) or value is None:
            return ""
        return str(value).strip()

    def _parse_metadata(self, meta_raw: Any) -> dict[str, Any]:
        if isinstance(meta_raw, dict):
            return dict(meta_raw)
        if isinstance(meta_raw, str) and meta_raw:
            try:
                return json.loads(meta_raw)
            except json.JSONDecodeError:
                return {"raw_metadata": meta_raw}
        return {}

    @staticmethod
    def _get_case_insensitive(row, key: str) -> Any:
        """获取列值，不区分大小写。支持 dict 和 pd.Series。"""
        keys = row.keys() if isinstance(row, dict) else row.index
        if key in keys:
            return row.get(key)
        key_lower = key.lower()
        for col in keys:
            if col.lower() == key_lower:
                return row.get(col)
        return None

    # 域描述映射（用于增强语义）
    DOMAIN_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "DM": "人口学域 - 受试者基本信息",
        "AE": "不良事件域 - 不良事件记录",
        "CM": "合并用药域 - 合并用药和既往用药",
        "MH": "病史域 - 既往病史和诊断",
        "PR": "手术/操作域 - 手术和操作记录",
        "EX": "暴露域 - 试验药物给药记录",
        "DS": "受试者处置域 - 受试者研究状态",
        "LB": "实验室检查域 - 实验室检测结果",
        "VS": "生命体征域 - 生命体征测量",
        "EG": "心电图域 - ECG检查结果",
        "PE": "体格检查域 - 体格检查结果",
        "TU": "肿瘤识别域 - 肿瘤病灶识别",
        "TR": "肿瘤评估域 - 肿瘤测量评估",
        "RS": "疾病应答域 - 疾病应答评估",
        "SV": "受试者访视域 - 访视日期记录",
        "QS": "问卷域 - 问卷和量表",
        "FA": "发现域 - 其他发现",
        "IE": "入排标准域 - 入选排除标准",
        "SC": "受试者特征域 - 受试者特征",
    }

    def _build_ecrf_chunk(self, row, file_name: str, idx: int) -> Chunk | None:
        # 使用不区分大小写的列名访问
        domain = self._clean(
            self._get_case_insensitive(row, "SDTM_domain") or self._get_case_insensitive(row, "SDTM_Domain")
        )
        variable = self._clean(
            self._get_case_insensitive(row, "SDTM_variable") or self._get_case_insensitive(row, "SDTM_Variable")
        )
        annotation_table = self._clean(row.get("annotation_table"))
        annotation_variable = self._clean(row.get("annotation_variable"))
        metadata_table = self._clean(row.get("metadata_table"))
        metadata_variable = self._clean(row.get("metadata_variable"))
        rawdata = self._clean(row.get("rawdata"))

        # Skip rows without an SDTM target or with placeholder values
        if not domain or not variable:
            return None
        if rawdata in {"-", "--"}:
            rawdata = ""

        # 提取主域代码（处理 SUPP 和管道分隔的情况）
        primary_domain = domain.replace("SUPP", "").split("|")[0].strip()[:2] if domain else ""
        domain_desc = self.DOMAIN_DESCRIPTIONS.get(primary_domain, "")

        # 构建与 sdtm_spec_enhanced 统一格式的 chunk 文本
        # 格式: Variable: XX Label: XX Domain: XX 域描述: XX 关键词: XX eCRF表: XX
        parts = []

        # 提取纯变量名（处理 "QVAL when QNAM=XX" 格式）
        pure_variable = variable.split()[0] if variable else ""

        # 第一部分：核心标识（与 sdtm_spec_enhanced 格式对齐）
        parts.append(f"Variable: {pure_variable}")
        parts.append(f"Label: {annotation_variable}")
        parts.append(f"Domain: {domain}")

        # 第二部分：来源标识
        parts.append("来源: eCRF映射示例")

        # 第三部分：域描述
        if domain_desc:
            parts.append(f"域描述: {domain_desc}")

        # 第四部分：映射详情
        if annotation_table:
            parts.append(f"eCRF表: {annotation_table}")
        if metadata_table:
            parts.append(f"元数据表: {metadata_table}")
        if metadata_variable:
            parts.append(f"元数据变量: {metadata_variable}")
        if rawdata:
            parts.append(f"原始字段: {rawdata}")

        # 第五部分：关键词（丰富语义，便于检索匹配）
        keywords = []
        if annotation_variable:
            keywords.append(annotation_variable)
        if annotation_table:
            keywords.append(annotation_table)
        if pure_variable and pure_variable not in keywords:
            keywords.append(pure_variable)
        if metadata_variable and metadata_variable not in keywords:
            keywords.append(metadata_variable)
        if keywords:
            parts.append(f"关键词: {', '.join(keywords)}")

        text = "\n".join(parts)
        if len(text.strip()) < 10:
            return None

        meta = {
            "type": "sdtm_mapping_example",
            "source": "eCRF",
            "domain": domain,
            "variable": variable,
            "annotation_table": annotation_table,
            "annotation_variable": annotation_variable,
            "metadata_table": metadata_table,
            "metadata_variable": metadata_variable,
            "rawdata": rawdata,
            "language": "zh",
            "confidence_default": 0.95,
            "file": file_name,
            "row_index": idx,
        }

        return Chunk(
            id=f"eCRF_{idx}",
            text=text[:800],
            meta=meta,
        )

    def _build_structured_chunks(self, df: pd.DataFrame, file_name: str) -> list[Chunk]:
        chunks: list[Chunk] = []
        source_name = Path(file_name).stem
        for i, row in enumerate(df.to_dict("records")):
            text = self._clean(row.get("chunk_text"))
            if not text:
                continue

            meta = self._parse_metadata(row.get("metadata"))

            for key in ["domain", "variable", "language", "type"]:
                value = self._clean(row.get(key))
                if value and not meta.get(key):
                    meta[key] = value

            confidence = row.get("confidence_default")
            if confidence is not None and confidence != "":
                with contextlib.suppress(TypeError, ValueError):
                    meta.setdefault("confidence_default", float(confidence))

            meta.setdefault("source", row.get("source") or source_name)
            meta.setdefault("file", file_name)
            meta.setdefault("row_index", i)

            chunk_id = self._clean(row.get("chunk_id")) or f"{source_name}_{i}"

            chunks.append(
                Chunk(
                    id=chunk_id,
                    text=text[:800],
                    meta=meta,
                )
            )
        return chunks

    def chunk_parquet(self, file_path: str) -> list[Chunk]:
        """切分单个 Parquet 文件"""
        df = pd.read_parquet(file_path)
        file_name = os.path.basename(file_path)
        chunks: list[Chunk] = []

        cols = set(df.columns)
        cols_lower = {c.lower() for c in df.columns}  # 用于不区分大小写的检测

        if {"chunk_text", "metadata"}.issubset(cols):
            chunks = self._build_structured_chunks(df, file_name)
            source_type = "structured"
        elif {
            "annotation_table",
            "metadata_table",
            "annotation_variable",
            "metadata_variable",
            "sdtm_domain",
            "sdtm_variable",
        }.issubset(cols_lower):
            # eCRF 类型：不区分大小写检测列名
            source_type = "eCRF"
            for i, row in enumerate(df.to_dict("records")):
                chunk = self._build_ecrf_chunk(row, file_name, i)
                if chunk:
                    chunks.append(chunk)
        else:
            source_type = "generic"
            for i, row in enumerate(df.to_dict("records")):
                text = " | ".join([f"{col}:{self._clean(val)}" for col, val in row.items() if pd.notna(val)])
                if not text or len(text) < 10:
                    continue
                chunks.append(
                    Chunk(
                        id=f"{source_type}_{i}",
                        text=text[:800],
                        meta={
                            "source": source_type,
                            "file": file_name,
                            "row_index": i,
                            "confidence_default": 0.4,
                        },
                    )
                )

        print(f" {file_name}: {len(chunks)} chunks created ({source_type})")
        return chunks

    def load_all_parquet(self) -> list[Chunk]:
        """加载 base_path 下的所有 parquet 文件并合并为一个列表（排除 exclude_files 中的文件）"""
        chunks_all: list[Chunk] = []
        loaded_files = []
        skipped_files = []

        for file in os.listdir(self.base_path):
            if file.endswith(".parquet"):
                # 检查是否在排除列表中
                if file in self.exclude_files:
                    skipped_files.append(file)
                    continue

                full_path = os.path.join(self.base_path, file)
                file_chunks = self.chunk_parquet(full_path)
                chunks_all.extend(file_chunks)
                loaded_files.append(file)

        if skipped_files:
            print(f"⏭️  Skipped files: {', '.join(skipped_files)}")
        print(f"All files processed. Total chunks: {len(chunks_all)}")
        return chunks_all


# === 主程序入口 ===
if __name__ == "__main__":
    # 知识库路径（可根据项目部署修改）
    kb_path = "isdtaim/data/knowledge_base/structured"

    # 初始化并加载
    chunker = Chunker(kb_path)
    chunks = chunker.load_all_parquet()

    # 简要展示前几个结果
    print("\n--- Sample chunks ---")
    for c in chunks[:3]:
        print(f"ID: {c.id}\nText: {c.text}\nMeta: {c.meta}\n")

    print(" Chunking completed successfully.")


# 给 retriever 提供统一接口名
def load_and_chunk_kb(kb_path: str):
    chunker = Chunker(kb_path)
    return chunker.load_all_parquet()
