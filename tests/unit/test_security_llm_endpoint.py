"""Unit tests for LLM endpoint allowlist / SSRF validation helpers."""

from __future__ import annotations

import pytest

from src.web.security import (
    allowed_llm_hosts,
    is_server_default_llm_endpoint,
    server_default_llm_host,
    validate_llm_base_url,
)


@pytest.fixture(autouse=True)
def clean_llm_env(monkeypatch):
    monkeypatch.delenv("SIRIUS_LLM_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)


class TestAllowedHosts:
    def test_builtin_provider_hosts_pass(self):
        for url in (
            "https://openrouter.ai/api/v1",
            "https://api.openai.com/v1",
            "https://api.deepseek.com/v1",
        ):
            assert validate_llm_base_url(url) == url

    def test_unknown_host_rejected(self):
        with pytest.raises(ValueError, match="允许列表"):
            validate_llm_base_url("https://evil.example.com/v1")

    def test_subdomain_spoof_rejected(self):
        # 精确匹配，不做子域通配，防 openrouter.ai.evil.com 绕过
        with pytest.raises(ValueError):
            validate_llm_base_url("https://openrouter.ai.evil.com/v1")

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:11434/v1",
            "http://localhost:1234/v1",
            "http://10.0.0.5/v1",
            "http://192.168.1.20/v1",
            "http://172.16.0.1/v1",
        ],
    )
    def test_loopback_and_private_rejected_by_default(self, url):
        with pytest.raises(ValueError):
            validate_llm_base_url(url)

    def test_cloud_metadata_rejected_even_if_allowlisted(self, monkeypatch):
        monkeypatch.setenv("SIRIUS_LLM_ALLOWED_HOSTS", "169.254.169.254")
        with pytest.raises(ValueError, match="元数据"):
            validate_llm_base_url("http://169.254.169.254/latest/meta-data/")

    def test_admin_allowlist_extends_hosts(self, monkeypatch):
        monkeypatch.setenv("SIRIUS_LLM_ALLOWED_HOSTS", "llm-gw.internal, 192.168.1.50")
        assert "llm-gw.internal" in allowed_llm_hosts()
        assert validate_llm_base_url("https://llm-gw.internal/v1") == "https://llm-gw.internal/v1"
        assert validate_llm_base_url("http://192.168.1.50:8000/v1") == "http://192.168.1.50:8000/v1"

    def test_server_base_url_host_allowed(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_BASE_URL", "https://ai-api.qilu-pharma.com/v1")
        assert server_default_llm_host() == "ai-api.qilu-pharma.com"
        assert validate_llm_base_url("https://ai-api.qilu-pharma.com/v1")

    def test_builtin_provider_http_downgrade_rejected(self):
        # 内置公共 Provider 禁止明文 HTTP（防止服务器密钥经明文传输）
        for url in ("http://openrouter.ai/api/v1", "http://api.openai.com/v1", "http://api.deepseek.com/v1"):
            with pytest.raises(ValueError, match="https"):
                validate_llm_base_url(url)


class TestSchemeAndUserinfo:
    @pytest.mark.parametrize("url", ["ftp://openrouter.ai/v1", "openrouter.ai/v1", "file:///etc/passwd"])
    def test_bad_scheme_rejected(self, url):
        with pytest.raises(ValueError, match="http"):
            validate_llm_base_url(url)

    def test_userinfo_rejected(self):
        with pytest.raises(ValueError, match="用户名"):
            validate_llm_base_url("https://user:pass@openrouter.ai/v1")


class TestServerDefaultEndpoint:
    def test_none_or_empty_is_default(self):
        assert is_server_default_llm_endpoint(None) is True
        assert is_server_default_llm_endpoint("") is True

    def test_default_host_matches(self, monkeypatch):
        # 缺省即 openrouter.ai
        assert is_server_default_llm_endpoint("https://openrouter.ai/api/v1") is True
        monkeypatch.setenv("OPENROUTER_BASE_URL", "https://ai-api.qilu-pharma.com/v1")
        assert is_server_default_llm_endpoint("https://ai-api.qilu-pharma.com/other") is True

    def test_non_default_host_is_not_default(self):
        assert is_server_default_llm_endpoint("https://api.deepseek.com/v1") is False

    def test_http_downgrade_same_host_is_not_default(self):
        # 服务器默认 https，同 host 的 http 覆盖不得被视为默认（否则密钥经明文外泄）
        assert is_server_default_llm_endpoint("http://openrouter.ai/api/v1") is False

    def test_port_mismatch_same_host_is_not_default(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_BASE_URL", "https://ai-api.qilu-pharma.com/v1")
        assert is_server_default_llm_endpoint("https://ai-api.qilu-pharma.com:8443/v1") is False
        assert is_server_default_llm_endpoint("https://ai-api.qilu-pharma.com/v1") is True
