#!/usr/bin/env python
"""
embeddings.py
-----------------------------------
功能：
- 读取 chunker.py 生成的文本块（Chunk 对象）
- 调用公司内网 OpenRouter 的 /embeddings 接口生成向量
- 默认模型：Qwen3-Embed
- 向量仅保存在内存中（不写文件）

依赖：
- requests
- numpy, pandas
- src/rag/chunker.py (必须存在)

环境变量：
- OPENROUTER_API_KEY （公司内网 Key）
- OPENROUTER_BASE_URL （默认 https://ai-api.qilu-pharma.com/v1）

示例运行：
python -m src.rag.embeddings --kb-path data/knowledge_base/structured
"""

import argparse
import json
import os
import sys
from typing import Any

import numpy as np
import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config.settings import get_settings
from src.rag.chunker import Chunk, Chunker

load_dotenv()


class OpenRouterEmbeddingClient:
    """
    调用公司内网 OpenRouter（OpenAI兼容协议）的 Embeddings 接口
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "Qwen3-Embed",
        timeout: float | None = None,
        max_retries: int | None = None,
    ):
        settings = get_settings().ai
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("❌ 未找到 API Key，请设置 OPENROUTER_API_KEY 环境变量。")

        bu = (
            base_url
            or os.getenv("OPENROUTER_BASE_URL", "https://ai-api.qilu-pharma.com/v1")
            or "https://ai-api.qilu-pharma.com/v1"
        )
        self.base_url = bu
        self.model = model
        self.timeout = timeout if timeout is not None else settings.timeout_seconds
        self.endpoint = f"{bu.rstrip('/')}/embeddings"
        retry_count = settings.max_retries if max_retries is None else max_retries
        retry = Retry(
            total=retry_count,
            connect=retry_count,
            read=retry_count,
            status=retry_count,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"POST"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session = requests.Session()
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        调用 /embeddings 接口，生成文本向量
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": self.model, "input": texts}

        resp = self.session.post(self.endpoint, headers=headers, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()

        if "data" not in data:
            raise RuntimeError(f"Invalid embeddings response: {json.dumps(data, ensure_ascii=False)[:500]}")

        vectors = [item["embedding"] for item in data["data"]]
        return vectors


def build_embeddings(
    kb_path: str,
    model: str = "Qwen3-Embed",
    batch_size: int = 100,
) -> list[dict[str, Any]]:
    """
    从知识库读取并生成内存中的 Embeddings

    Args:
        kb_path: 知识库目录路径
        model: 使用的模型名（默认 Qwen3-Embed）
    Returns:
        dict: 包含 chunks, embeddings, dim
    """
    print(f"\n📘 开始生成 Embeddings（模型：{model}）")

    # 1. 加载知识库文本块
    chunker = Chunker(kb_path)
    chunks: list[Chunk] = chunker.load_all_parquet()
    if not chunks:
        raise RuntimeError("未在知识库中找到任何 chunk。")

    texts = [c.text for c in chunks]
    print(f"✅ 已加载 {len(chunks)} 个文本块。")

    # 2. 调用公司内网 OpenRouter
    if batch_size <= 0:
        raise ValueError("Embedding batch size must be positive")

    client = OpenRouterEmbeddingClient(model=model)
    emb_list: list[list[float]] = []
    for batch_start in range(0, len(texts), batch_size):
        batch_texts = texts[batch_start : batch_start + batch_size]
        batch_embeddings = client.embed_texts(batch_texts)
        if len(batch_embeddings) != len(batch_texts):
            raise RuntimeError(
                f"Embedding response count mismatch: expected {len(batch_texts)}, got {len(batch_embeddings)}"
            )
        emb_list.extend(batch_embeddings)
        print(f"Embedding progress: {len(emb_list)}/{len(texts)}")
    embeddings_arr = np.array(emb_list, dtype=np.float32)
    dim = int(embeddings_arr.shape[1])
    print(f"✅ 生成完成：{len(chunks)} 个向量，每个维度 {dim}")

    fused_docs: list[dict[str, Any]] = []
    for chunk, emb in zip(chunks, embeddings_arr, strict=False):
        fused_docs.append(
            {
                "id": getattr(chunk, "id", None),
                "text": getattr(chunk, "text", ""),
                "meta": getattr(chunk, "meta", {}),
                "embedding": np.array(emb, dtype=np.float32),
            }
        )

    print(f"✅ 已融合 {len(fused_docs)} 个文档，每条向量维度 {dim}")
    return fused_docs


def parse_args():
    p = argparse.ArgumentParser(description="生成知识库 Embeddings（OpenRouter 内网版本）")
    p.add_argument("--kb-path", default="data/knowledge_base/structured", help="知识库目录（包含 .parquet 文件）")
    p.add_argument("--model", default="Qwen3-Embed", help="Embedding 模型名")
    return p.parse_args()


def main():
    args = parse_args()
    try:
        build_embeddings(kb_path=args.kb_path, model=args.model)
    except Exception as e:
        print(f"❌ 出错：{e}")
        if os.getenv("DEBUG"):
            raise
        sys.exit(1)


if __name__ == "__main__":
    main()
