# -*- coding: utf-8 -*-
"""Регрессии failover при битом 2xx-ответе LLM gateway."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import core.ai_assistant.providers as providers_module
from core.ai_assistant.client import AIClient
from core.ai_assistant.providers import AIResponse, AnthropicProvider, ProviderError


class _FakeHTTPClient:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeHTTPClient:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def post(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
        return self._response


class _FallbackProvider:
    async def chat(self, **_kwargs: Any) -> AIResponse:
        return AIResponse(text="fallback ok", provider="openai", model="gpt-5.6-luna")


@pytest.mark.asyncio
async def test_anthropic_invalid_json_raises_provider_error(monkeypatch) -> None:
    response = httpx.Response(
        200,
        content=b"<html>gateway error</html>",
        request=httpx.Request("POST", "https://gateway.test/messages"),
    )
    monkeypatch.setattr(
        providers_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeHTTPClient(response),
    )
    provider = AnthropicProvider(
        api_key="secret",
        base_url="https://gateway.test",
        model="primary-model",
    )

    with pytest.raises(ProviderError, match="невалидный JSON"):
        await provider.chat(messages=[{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_invalid_primary_2xx_switches_to_fallback(monkeypatch) -> None:
    response = httpx.Response(
        200,
        json={"unexpected": "shape"},
        request=httpx.Request("POST", "https://gateway.test/messages"),
    )
    monkeypatch.setattr(
        providers_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeHTTPClient(response),
    )
    primary = AnthropicProvider(
        api_key="secret",
        base_url="https://gateway.test",
        model="primary-model",
    )
    client = AIClient(primary=primary, fallback=_FallbackProvider())  # type: ignore[arg-type]

    result = await client.chat(messages=[{"role": "user", "content": "hi"}])

    assert result.text == "fallback ok"
    assert result.provider == "openai"
    assert result.model == "gpt-5.6-luna"
