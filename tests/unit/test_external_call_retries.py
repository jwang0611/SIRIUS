"""Retry and typed-default coverage for external AI calls."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
import requests

from src.clients.openrouter_client import OpenRouterClient
from src.config.settings import reload_settings
from src.rag.embeddings import OpenRouterEmbeddingClient


def test_openrouter_client_uses_typed_model_retry_and_timeout(clean_env, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("DEFAULT_MODEL", "test/default-model")
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "45")
    reload_settings()

    with patch("src.clients.openrouter_client.OpenAI") as openai_cls:
        client = OpenRouterClient()
        assert client.model_name == "test/default-model"
        assert client.initialize() is True

    openai_cls.assert_called_once_with(
        api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
        max_retries=3,
        timeout=45.0,
    )
    reload_settings()


def _run_embedding_server(failures_before_success: int):
    class Handler(BaseHTTPRequestHandler):
        attempts = 0

        def do_POST(self):
            Handler.attempts += 1
            if Handler.attempts <= failures_before_success:
                self.send_response(429 if failures_before_success < 3 else 503)
                self.send_header("Retry-After", "0")
                self.end_headers()
                return
            body = json.dumps({"data": [{"embedding": [0.1, 0.2]}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, Handler


def test_embedding_retries_429_then_succeeds(clean_env):
    server, handler = _run_embedding_server(failures_before_success=1)
    try:
        client = OpenRouterEmbeddingClient(
            api_key="test-key",
            base_url=f"http://127.0.0.1:{server.server_port}",
            timeout=2,
            max_retries=2,
        )
        assert client.embed_texts(["AE term"]) == [[0.1, 0.2]]
        assert handler.attempts == 2
    finally:
        server.shutdown()
        server.server_close()


def test_embedding_continuous_failure_raises_after_retry_budget(clean_env):
    server, handler = _run_embedding_server(failures_before_success=99)
    try:
        client = OpenRouterEmbeddingClient(
            api_key="test-key",
            base_url=f"http://127.0.0.1:{server.server_port}",
            timeout=2,
            max_retries=2,
        )
        with pytest.raises(requests.HTTPError):
            client.embed_texts(["AE term"])
        assert handler.attempts == 3
    finally:
        server.shutdown()
        server.server_close()
