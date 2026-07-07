# src/rag/retriever.py
# new branch switch test, 与代码内容无关
"""
通用 RAG 检索器（内存版）
- 从知识库（经 chunker 切分）生成/加载向量
- 对输入查询做向量化，计算余弦相似度
- 返回 Top-K 最相关的知识块（含分数与元数据）
"""

from __future__ import annotations

import argparse
from typing import Any

import numpy as np
from dotenv import load_dotenv

# 依赖你现有的模块（函数名可在此对齐）
from src.rag.chunker import Chunker  # -> List[{"id","text","metadata"}]
from src.rag.embeddings import (
    OpenRouterEmbeddingClient,  # .embed_texts(List[str]) -> List[List[float]]
    build_embeddings,  # -> List[{"id","text","embedding","metadata"}]
)
from src.rag.vector_store import RAGVectorStore


def load_and_chunk_kb(kb_path: str):
    """加载并切分知识库，返回 List[Chunk]"""
    chunker = Chunker(base_path=kb_path)  # ★ 传入 kb_path
    chunks = chunker.load_all_parquet()
    return chunks


def l2_normalize(vecs: np.ndarray) -> np.ndarray:
    """行向量 L2 归一化，避免长度差异影响相似度。"""
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return vecs / norms


def cosine_similarity(query_vec: np.ndarray, doc_mat: np.ndarray) -> np.ndarray:
    """query(1,d) 与 docs(n,d) 的余弦相似度。输入需先 L2 归一化。"""
    return np.dot(doc_mat, query_vec.T).ravel()


class InMemoryRetriever:
    """基于内存的向量检索器（余弦相似度，支持 Top-K）"""

    def __init__(self, docs: list[dict[str, Any]]):
        # 只接收有向量的文档
        self._docs = []
        embs = []
        for d in docs:
            if isinstance(d, dict) and "embedding" in d:
                vec = np.asarray(d["embedding"], dtype=np.float32).ravel()
                if vec.size > 0:
                    self._docs.append(d)
                    embs.append(vec)

        if len(embs) == 0:
            self._X = np.zeros((0, 1), dtype=np.float32)
        else:
            X = np.vstack(embs).astype(np.float32)  # [N, D]
            # 归一化，做余弦
            norms = np.linalg.norm(X, axis=1, keepdims=True) + 1e-12
            self._X = X / norms  # [N, D]

    def search(self, q_vec: np.ndarray, top_k: int = 5) -> list[dict[str, Any]]:
        if self._X.shape[0] == 0:
            return []

        q = np.asarray(q_vec, dtype=np.float32).ravel()
        if q.size == 0:
            return []

        q = q / (np.linalg.norm(q) + 1e-12)  # [D]

        # 余弦相似度 = 归一化后点积
        scores = self._X @ q  # [N]

        k = int(max(1, min(top_k, scores.shape[0])))
        # 先局部取前 k，再降序排序
        idx = np.argpartition(scores, -k)[-k:]
        idx = idx[np.argsort(scores[idx])[::-1]]

        results = []
        for i in idx:
            d = self._docs[int(i)]
            results.append(
                {
                    "score": float(scores[int(i)]),
                    "text": d.get("text", ""),
                    "metadata": d.get("meta") or d.get("metadata") or {},
                }
            )
        return results


def build_or_reuse_embeddings(
    kb_path: str, model: str = "Qwen3-Embed", force_rebuild: bool = True
) -> list[dict[str, Any]]:
    """
    基于知识库构建向量（当前为内存版）。
    """
    store = RAGVectorStore()

    if not force_rebuild:
        cached_docs = store.load(kb_path, model)
        if cached_docs:
            print(f"♻️  已从缓存加载 {len(cached_docs)} 个知识向量。")
            return cached_docs

    print("🔄 正在构建最新的知识库向量……")
    docs: list[dict[str, Any]] = build_embeddings(kb_path, model=model)

    # 确保最终都是 numpy 向量
    new_docs: list[dict[str, Any]] = []
    for item in docs:
        if isinstance(item, dict):
            record = dict(item)
            embedding = record.get("embedding")
            if embedding is not None and not isinstance(embedding, np.ndarray):
                record["embedding"] = np.array(embedding, dtype=np.float32)
            new_docs.append(record)
        else:
            new_docs.append({"embedding": np.array(item, dtype=np.float32)})

    store.save(kb_path, model, new_docs)
    print(f"✅ 已生成并缓存 {len(new_docs)} 个知识向量。")
    return new_docs


def make_query_text(raw_query: str, query_from: str = "text") -> str:
    """
    你可以在这里做“查询构造”：
    - query_from == "text": 直接使用自然语言
    - 也可以扩展：基于 CRF JSON 组装为检索提示（域/变量/label等）
    """
    # 未来：若 query_from == "crf_json" 则解析 JSON 并拼出检索短语
    return raw_query.strip()


def main():
    load_dotenv()  # 读取 .env（需要 OPENROUTER_API_KEY / OPENROUTER_BASE_URL）

    parser = argparse.ArgumentParser(description="RAG 内存检索器（相似度 Top-K）")
    parser.add_argument("--kb-path", required=True, help="知识库根目录（已存放 parquet 等）")
    parser.add_argument("--query", required=True, help="检索查询文本（演示可用自然语言或变量名）")
    parser.add_argument("--top-k", type=int, default=5, help="返回结果条数（默认 5）")
    parser.add_argument("--rebuild", action="store_true", help="强制重建向量（默认每次重建）")
    parser.add_argument("--model", default="Qwen3-Embed", help="用于嵌入的模型 ID（默认 Qwen3-Embed）")
    args = parser.parse_args()

    # 初始化嵌入客户端
    client = OpenRouterEmbeddingClient(model=args.model)

    print(f"📘 使用模型：{args.model}")
    print(f"📚 知识库：{args.kb_path}")

    # 1) 构建（或复用）知识库向量（当前为内存实时构建）
    docs = build_or_reuse_embeddings(args.kb_path, model=args.model, force_rebuild=args.rebuild)
    print(f"✅ 已加载 {len(docs)} 个向量化知识块。")
    print(f"📊 准备传入检索器的文档数量：{len(docs)}")
    if len(docs) > 0:
        first_doc = docs[0]
        print(f"📋 第一条文档的键: {list(first_doc.keys())}")
        if "embedding" in first_doc:
            print(
                f"📈 第一条embedding类型: {type(first_doc['embedding'])}, 长度: {len(first_doc['embedding']) if hasattr(first_doc['embedding'], '__len__') else 'N/A'}"
            )

    # 2) 处理查询 → 向量化
    query_text = make_query_text(args.query, query_from="text")
    q_vec = np.array(client.embed_texts([query_text])[0], dtype=np.float32)

    # === 3) 相似检索 ===
    retriever = InMemoryRetriever(docs)
    results = retriever.search(q_vec, top_k=args.top_k)

    # 🔧 确保结果是列表
    if isinstance(results, dict):
        results = [results]
    elif not isinstance(results, list):
        results = []

    print(f"\n🔍 检索结果 (Top-{args.top_k}):实际返回 {len(results)} 条")
    if not results:
        print("⚠️ 未找到相似结果。")
    else:
        for i, r in enumerate(results[: args.top_k], 1):
            # 确保安全访问
            score = r["score"] if isinstance(r, dict) and "score" in r else 0.0
            text = r["text"] if isinstance(r, dict) and "text" in r else str(r)
            meta = r.get("metadata", {}) if isinstance(r, dict) else {}

            preview = text.replace("\n", " ")[:180]
            print(f"[{i}] 🧩 score={score:.4f}")
            if meta:
                kv = " | ".join([f"{k}:{meta[k]}" for k in list(meta.keys())[:6]])
                print(f"    📘 meta: {kv}")
            print(f"    📄 text: {preview}...\n")

    print("✅ 检索完成。\n")


if __name__ == "__main__":
    main()
