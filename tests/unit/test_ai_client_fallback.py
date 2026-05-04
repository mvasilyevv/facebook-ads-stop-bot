# -*- coding: utf-8 -*-
"""Тесты AIClient: fallback и no-op при пустых ключах."""

from __future__ import annotations

import pytest

from core.ai_assistant.client import (
    AIClient,
    AIUnavailableError,
    reset_ai_client_for_tests,
)
from core.ai_assistant.providers import AIResponse, ProviderError


class _OkProvider:
    def __init__(self, name: str = "ok") -> None:
        self.name = name
        self.called = False

    async def chat(self, **kwargs):
        self.called = True
        return AIResponse(text="ok-from-" + self.name)


class _FailProvider:
    def __init__(self) -> None:
        self.called = False

    async def chat(self, **kwargs):
        self.called = True
        raise ProviderError("primary down")


# Сценарий: primary отвечает успешно — fallback не должен быть вызван.
@pytest.mark.asyncio
async def test_primary_success_skips_fallback():
    primary = _OkProvider("primary")
    fallback = _OkProvider("fallback")
    client = AIClient(primary=primary, fallback=fallback)
    resp = await client.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.text == "ok-from-primary"
    assert primary.called is True
    assert fallback.called is False


# Сценарий: primary падает с ProviderError — должен сработать fallback.
@pytest.mark.asyncio
async def test_primary_failure_uses_fallback():
    primary = _FailProvider()
    fallback = _OkProvider("fallback")
    client = AIClient(primary=primary, fallback=fallback)
    resp = await client.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.text == "ok-from-fallback"
    assert primary.called is True
    assert fallback.called is True


# Сценарий: оба провайдера None (пустые ключи) — кидаем AIUnavailableError, не падая.
@pytest.mark.asyncio
async def test_no_providers_raises_unavailable():
    reset_ai_client_for_tests()
    client = AIClient(primary=None, fallback=None)
    assert client.is_available is False
    with pytest.raises(AIUnavailableError):
        await client.chat(messages=[{"role": "user", "content": "hi"}])


# Сценарий: primary падает, fallback тоже — обе ошибки → AIUnavailableError.
@pytest.mark.asyncio
async def test_both_fail_raises_unavailable():
    primary = _FailProvider()
    fallback = _FailProvider()
    client = AIClient(primary=primary, fallback=fallback)
    with pytest.raises(AIUnavailableError):
        await client.chat(messages=[{"role": "user", "content": "hi"}])
