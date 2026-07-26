"""Prompt augmentation helpers that combine RAG results with user queries."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.rag.chunker import Chunk
from src.rag.embeddings import OpenRouterEmbeddingClient
from src.rag.retriever import InMemoryRetriever, build_or_reuse_embeddings
from src.rag.vector_store import RAGVectorStore


@dataclass
class RAGContext:
    score: float
    text: str
    metadata: dict[str, Any]
    weight: float = 1.0
    source: str = "default"  # 标记来源：default 或 session

    @property
    def effective_score(self) -> float:
        return self.score * self.weight


class RAGPromptAugmenter:
    """Encapsulates retrieval + formatting logic for prompt augmentation."""

    def __init__(
        self,
        kb_path: str,
        embedding_model: str = "Qwen3-Embed",
        top_k: int = 5,
        cache_dir: str | None = None,
        extra_kb_files: list[str] | None = None,
    ) -> None:
        self.kb_path = kb_path
        self.embedding_model = embedding_model
        self.top_k = top_k
        self.store = RAGVectorStore(cache_dir=cache_dir)
        self.embed_client = OpenRouterEmbeddingClient(model=self.embedding_model)

        # 查询向量缓存：cache_key -> np.ndarray
        # 用于避免重复调用嵌入 API
        self._query_vector_cache: dict[str, np.ndarray] = {}
        self._cache_lock = threading.Lock()

        # 加载默认知识库向量
        self._docs = build_or_reuse_embeddings(
            kb_path=self.kb_path,
            model=self.embedding_model,
            force_rebuild=False,
        )

        # 标记默认文档来源
        for doc in self._docs:
            doc["source"] = "default"

        # 加载额外的 KB 文件（用户上传的 session KB）
        self._extra_docs: list[dict[str, Any]] = []
        if extra_kb_files:
            self._load_extra_kb_files(extra_kb_files)

        # 合并所有文档到检索器
        all_docs = self._docs + self._extra_docs
        self._retriever = InMemoryRetriever(all_docs)

    def _load_extra_kb_files(self, file_paths: list[str]) -> None:
        """
        加载额外的 KB 文件并生成向量（不缓存，仅内存）。
        仅支持 .parquet 文件。
        """
        for file_path in file_paths:
            path = Path(file_path)
            if not path.exists():
                continue

            suffix = path.suffix.lower()
            if suffix != ".parquet":
                # 仅处理 parquet 文件，跳过其他格式
                continue

            try:
                df = pd.read_parquet(path)

                if df.empty:
                    print(f"⚠️ Empty KB file: {file_path}")
                    continue

                # 生成 chunks（使用简化的 eCRF 格式）
                chunks = self._df_to_chunks(df, source_file=path.name)
                if not chunks:
                    print(f"⚠️ No chunks generated from: {file_path}")
                    continue

                # 生成向量
                texts = [c.text for c in chunks]
                print(f"🔄 为 {path.name} 生成 {len(texts)} 个向量...")
                embeddings = self.embed_client.embed_texts(texts)

                # 构建文档
                for chunk, emb in zip(chunks, embeddings, strict=False):
                    self._extra_docs.append(
                        {
                            "id": chunk.id,
                            "text": chunk.text,
                            "meta": chunk.meta,
                            "embedding": np.array(emb, dtype=np.float32),
                            "source": "session",
                        }
                    )

                print(f"✅ 已加载 session KB: {path.name} ({len(chunks)} chunks)")

            except Exception as e:
                print(f"⚠️ Failed to load extra KB file {file_path}: {e}")

    def _df_to_chunks(self, df: pd.DataFrame, source_file: str) -> list[Chunk]:
        """将 DataFrame 转换为 Chunk 列表（eCRF 格式）"""
        chunks: list[Chunk] = []

        # 检测必需列
        required_cols = ["annotation_table", "metadata_variable"]
        has_required = all(col in df.columns for col in required_cols)

        if not has_required:
            # 尝试大小写不敏感匹配
            col_map = {}
            for req_col in required_cols:
                for col in df.columns:
                    if col.lower() == req_col.lower():
                        col_map[req_col] = col
                        break
            if len(col_map) == len(required_cols):
                df = df.rename(columns={v: k for k, v in col_map.items()})
                has_required = True

        if not has_required:
            print(f"⚠️ KB file missing required columns: {required_cols}")
            return chunks

        extra_cols = [
            c for c in ["annotation_variable", "metadata_table", "SDTM_Domain", "SDTM_Variable"] if c in df.columns
        ]
        sdtm_meta_cols = [c for c in ["SDTM_Domain", "SDTM_Variable"] if c in df.columns]

        for idx, row in enumerate(df.to_dict("records")):
            parts = []
            table = str(row.get("annotation_table", "")).strip()
            variable = str(row.get("metadata_variable", "")).strip()

            if table:
                parts.append(f"表: {table}")
            if variable:
                parts.append(f"变量: {variable}")

            for col in extra_cols:
                val = str(row.get(col, "")).strip()
                if val and val.lower() not in ("nan", "none", ""):
                    parts.append(f"{col}: {val}")

            if not parts:
                continue

            text = " | ".join(parts)
            chunk_id = f"{source_file}_{idx}"

            meta = {
                "source_file": source_file,
                "row_index": idx,
                "annotation_table": table,
                "metadata_variable": variable,
            }
            for col in sdtm_meta_cols:
                meta[col] = str(row.get(col, "")).strip()

            chunks.append(Chunk(id=chunk_id, text=text, meta=meta))

        return chunks

    # ==================== 查询向量缓存方法 ====================

    def _normalize_query(self, query: str) -> str:
        """
        规范化查询文本，确保缓存键的一致性。

        Args:
            query: 原始查询文本

        Returns:
            规范化后的查询文本
        """
        if not query:
            return ""
        # 去除首尾空格，统一多个空格为单个
        return " ".join(query.strip().split())

    def _make_cache_key(self, query: str) -> str:
        """
        生成查询的缓存键。

        Args:
            query: 规范化后的查询文本

        Returns:
            缓存键（SHA256 哈希的前16位）
        """
        # 包含模型名以避免跨模型缓存污染
        normalized = f"{query}::{self.embedding_model}"
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def precompute_query_embeddings(
        self,
        queries: list[str],
        batch_size: int = 100,
    ) -> tuple[int, int]:
        """
        批量预计算查询向量并缓存。

        通过单次（或少量）API 调用生成所有查询的向量，
        避免后续逐个调用 API 的开销。

        Args:
            queries: 查询文本列表
            batch_size: 每批处理的最大查询数（API 可能有限制）

        Returns:
            (cached_count, total_unique): 已缓存的数量和唯一查询总数
        """
        if not queries:
            return 0, 0

        # 1. 收集唯一的、未缓存的查询
        unique_queries: dict[str, str] = {}  # cache_key -> normalized_query
        with self._cache_lock:
            for query in queries:
                normalized = self._normalize_query(query)
                if not normalized:
                    continue
                cache_key = self._make_cache_key(normalized)
                if cache_key in self._query_vector_cache:
                    continue
                if cache_key not in unique_queries:
                    unique_queries[cache_key] = normalized

        if not unique_queries:
            return 0, len({self._normalize_query(q) for q in queries if q})

        # 2. 按批次调用嵌入 API
        keys_list = list(unique_queries.keys())
        queries_list = [unique_queries[k] for k in keys_list]
        total_to_embed = len(queries_list)

        print(f"🔄 批量预计算 {total_to_embed} 个查询向量...")

        cached_count = 0
        for batch_start in range(0, total_to_embed, batch_size):
            batch_end = min(batch_start + batch_size, total_to_embed)
            batch_queries = queries_list[batch_start:batch_end]
            batch_keys = keys_list[batch_start:batch_end]

            try:
                # 批量调用嵌入 API（单次网络请求）
                embeddings = self.embed_client.embed_texts(batch_queries)

                with self._cache_lock:
                    for cache_key, emb in zip(batch_keys, embeddings, strict=False):
                        self._query_vector_cache[cache_key] = np.array(emb, dtype=np.float32)
                        cached_count += 1

                if total_to_embed > batch_size:
                    print(f"   ✓ 已处理 {batch_end}/{total_to_embed} 个查询")

            except Exception as e:
                print(f"⚠️ 批量嵌入失败 (batch {batch_start}-{batch_end}): {e}")
                # 继续处理下一批，不中断整个流程
                continue

        print(f"✅ 已缓存 {cached_count} 个查询向量")

        total_unique = len({self._normalize_query(q) for q in queries if q})
        return cached_count, total_unique

    def get_cached_vector(self, query: str) -> np.ndarray | None:
        """
        获取缓存的查询向量。

        Args:
            query: 查询文本

        Returns:
            缓存的向量，如果未命中则返回 None
        """
        normalized = self._normalize_query(query)
        if not normalized:
            return None
        cache_key = self._make_cache_key(normalized)
        with self._cache_lock:
            return self._query_vector_cache.get(cache_key)

    def clear_query_cache(self) -> int:
        """
        清空查询向量缓存。

        Returns:
            清除的缓存条目数
        """
        with self._cache_lock:
            count = len(self._query_vector_cache)
            self._query_vector_cache.clear()
            return count

    def get_cache_stats(self) -> dict[str, Any]:
        """
        获取缓存统计信息。

        Returns:
            包含缓存统计的字典
        """
        with self._cache_lock:
            size = len(self._query_vector_cache)
        return {
            "cached_queries": size,
            "memory_estimate_mb": size * 4 * 1024 / (1024 * 1024),
        }

    def refresh(self, force: bool = False) -> None:
        """刷新默认知识库向量（不影响 session KB）"""
        if force:
            self.store.invalidate(self.kb_path, self.embedding_model)
        self._docs = build_or_reuse_embeddings(
            kb_path=self.kb_path,
            model=self.embedding_model,
            force_rebuild=force,
        )
        for doc in self._docs:
            doc["source"] = "default"

        # 重新合并所有文档
        all_docs = self._docs + self._extra_docs
        self._retriever = InMemoryRetriever(all_docs)

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        verbose: bool = False,
        variable_name: str | None = None,
    ) -> list[RAGContext]:
        """
        检索与查询相关的 RAG 上下文。

        Args:
            query: 查询文本
            top_k: 返回的最大结果数
            verbose: 是否输出详细日志
            variable_name: 变量名（用于日志标识）

        Returns:
            RAG 上下文列表
        """
        if not query.strip():
            return []

        k = top_k or self.top_k

        normalized_query = self._normalize_query(query)
        cache_key = self._make_cache_key(normalized_query)

        with self._cache_lock:
            cached_vector = self._query_vector_cache.get(cache_key)
        cache_hit = cached_vector is not None
        if cache_hit:
            assert cached_vector is not None
            vector = cached_vector
        else:
            vector = np.array(self.embed_client.embed_texts([normalized_query])[0], dtype=np.float32)
            with self._cache_lock:
                self._query_vector_cache[cache_key] = vector

        results = self._retriever.search(vector, top_k=k)

        contexts: list[RAGContext] = []
        for item in results:
            meta = item.get("metadata", {})
            if meta is None:
                meta = item.get("meta", {})
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except json.JSONDecodeError:
                    meta = {"raw_metadata": meta}
            elif not isinstance(meta, dict):
                meta = {"raw_metadata": meta}

            weight = 1.0
            default_conf = meta.get("confidence_default")
            if default_conf is None:
                default_conf = item.get("confidence_default")
            try:
                if default_conf is not None:
                    weight = float(default_conf)
            except (TypeError, ValueError):
                weight = 1.0

            # 获取来源标记
            source = item.get("source", "default")

            contexts.append(
                RAGContext(
                    score=item.get("score", 0.0),
                    text=item.get("text", ""),
                    metadata=meta,
                    weight=weight if weight > 0 else 1.0,
                    source=source,
                )
            )

        contexts.sort(key=lambda ctx: ctx.effective_score, reverse=True)

        # 详细日志输出
        if verbose and contexts:
            var_label = variable_name or "unknown"
            print(f"\n{'=' * 60}")
            print(f"[RAG] Retrieval Details | Variable: {var_label}")
            print(f"{'=' * 60}")
            print(f"[Query] {normalized_query[:80]}{'...' if len(normalized_query) > 80 else ''}")
            print(f"[Cache] {'HIT' if cache_hit else 'MISS'} | Results: {len(contexts)}")
            print(f"{'-' * 60}")

            for i, ctx in enumerate(contexts, 1):
                meta = ctx.metadata
                domain = meta.get("SDTM_Domain", meta.get("domain", "?"))
                sdtm_var = meta.get("SDTM_Variable", meta.get("variable", "?"))
                ann_var = meta.get("annotation_variable", meta.get("variable_label", ""))
                ann_table = meta.get("annotation_table", "")
                chunk_type = meta.get("chunk_type", meta.get("type", "?"))

                print(f"  [{i}] Score: {ctx.score:.3f} | Domain: {domain} | SDTM: {sdtm_var}")
                if ann_var:
                    print(f"      Source Var: {ann_var} | Source Table: {ann_table}")
                print(f"      Type: {chunk_type} | Origin: {ctx.source}")
                # 显示文本片段（截取前100字符）
                text_preview = ctx.text.replace("\n", " ")[:100]
                print(f"      Content: {text_preview}{'...' if len(ctx.text) > 100 else ''}")
                print()

            print(f"{'=' * 60}\n")

        return contexts

    def get_stats(self) -> dict[str, Any]:
        """获取 RAG 统计信息"""
        default_count = len(self._docs)
        session_count = len(self._extra_docs)
        cache_stats = self.get_cache_stats()
        return {
            "default_docs": default_count,
            "session_docs": session_count,
            "total_docs": default_count + session_count,
            "cached_queries": cache_stats["cached_queries"],
        }

    def build_context_block(
        self,
        contexts: list[RAGContext],
        char_limit: int = 1500,
        structured: bool = True,
    ) -> str:
        """
        Build formatted context block for prompt augmentation.

        Args:
            contexts: List of RAG contexts
            char_limit: Maximum characters (0 = unlimited)
            structured: If True, use structured format for better LLM comprehension

        Returns:
            Formatted context string
        """
        if not contexts:
            return ""

        if structured:
            return self._build_structured_context(contexts, char_limit)
        else:
            return self._build_simple_context(contexts, char_limit)

    def _build_simple_context(
        self,
        contexts: list[RAGContext],
        char_limit: int = 1500,
    ) -> str:
        """Build simple flat context (legacy format)."""
        lines: list[str] = []
        used_chars = 0

        for ctx in contexts:
            snippet = ctx.text.replace("\n", " ").strip()
            if char_limit > 0:
                remaining = char_limit - used_chars
                if remaining <= 0:
                    break
                snippet = snippet[: max(remaining, 0)]
            lines.append(f"- ({ctx.effective_score:.2f}) {snippet}")
            used_chars += len(snippet)

            if ctx.metadata:
                meta_items = list(ctx.metadata.items())[:4]
                meta_preview = " | ".join(f"{k!s}:{v!s}" for k, v in meta_items)
                if meta_preview:
                    lines.append(f"  meta: {meta_preview}")

        return "\n".join(lines)

    def _build_structured_context(
        self,
        contexts: list[RAGContext],
        char_limit: int = 1500,
    ) -> str:
        """
        Build structured context block optimized for LLM comprehension.

        Format:
        **Similar Historical Mappings (from knowledge base):**

        1. [Similarity: 0.85] Source: 不良事件/AETERM → AE.AETERM
        2. [Similarity: 0.72] Source: 合并用药/CMSTDTC → CM.CMSTDTC
        """
        lines: list[str] = ["**Similar Historical Mappings (from knowledge base):**", ""]
        used_chars = 0

        for i, ctx in enumerate(contexts, 1):
            if char_limit > 0 and used_chars >= char_limit:
                break

            meta = ctx.metadata or {}

            # Extract key fields
            source_table = meta.get("annotation_table", "")
            source_var = meta.get("metadata_variable", meta.get("variable", ""))
            sdtm_domain = meta.get("SDTM_Domain", meta.get("domain", ""))
            sdtm_var = meta.get(
                "SDTM_Variable",
                meta.get("sdtm_variable", meta.get("variable", "")),
            )
            source_type = "[Project]" if ctx.source == "session" else "[Standard]"

            # Build structured entry
            if sdtm_domain and sdtm_var:
                # Has SDTM mapping info
                mapping_line = f"{i}. [Score: {ctx.effective_score:.2f}] {source_table}/{source_var} → **{sdtm_domain}.{sdtm_var}** ({source_type})"
            elif sdtm_domain:
                # Only domain
                mapping_line = f"{i}. [Score: {ctx.effective_score:.2f}] {source_table}/{source_var} → **{sdtm_domain}** ({source_type})"
            else:
                # No SDTM info, show raw text
                snippet = ctx.text.replace("\n", " ").strip()[:100]
                mapping_line = f"{i}. [Score: {ctx.effective_score:.2f}] {snippet} ({source_type})"

            lines.append(mapping_line)
            used_chars += len(mapping_line)

            # Add annotation_variable if different from metadata_variable
            annotation_var = meta.get("annotation_variable", "")
            if annotation_var and annotation_var != source_var:
                detail = f"   中文标注: {annotation_var}"
                lines.append(detail)
                used_chars += len(detail)

        if len(lines) <= 2:
            return ""

        return "\n".join(lines)

    def format_context_block(
        self,
        query: str,
        top_k: int | None = None,
        char_limit: int = 1500,
        min_score: float = 0.0,
    ) -> str:
        contexts = self.retrieve(query, top_k=top_k)
        if min_score:
            contexts = [ctx for ctx in contexts if ctx.effective_score >= min_score]
        return self.build_context_block(contexts, char_limit=char_limit)


__all__ = ["RAGContext", "RAGPromptAugmenter"]
