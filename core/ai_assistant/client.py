# -*- coding: utf-8 -*-
"""AIClient — primary (Anthropic) + fallback (OpenAI) с no-op при пустых ключах."""

from __future__ import annotations

import logging
from typing import Any

from core.ai_assistant.providers import (
    AIResponse,
    AnthropicProvider,
    OpenAIProvider,
    ProviderError,
)
from core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class AIUnavailableError(Exception):
    """Все провайдеры недоступны (нет ключей либо сетевые ошибки)."""


class AIClient:
    """Координатор: пробует Anthropic, при ошибке — OpenAI."""

    def __init__(
        self,
        *,
        primary: AnthropicProvider | None,
        fallback: OpenAIProvider | None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    @property
    def is_available(self) -> bool:
        return self._primary is not None or self._fallback is not None

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
    ) -> AIResponse:
        """Отправляет запрос. Бросает AIUnavailableError если все провайдеры легли."""
        last_err: Exception | None = None

        if self._primary is not None:
            try:
                return await self._primary.chat(
                    messages=messages, system=system, tools=tools, max_tokens=max_tokens
                )
            except ProviderError as exc:
                logger.warning("AI primary (anthropic) failed: %s — пробую fallback", exc)
                last_err = exc

        if self._fallback is not None:
            try:
                return await self._fallback.chat(
                    messages=messages, system=system, tools=tools, max_tokens=max_tokens
                )
            except ProviderError as exc:
                logger.error("AI fallback (openai) failed: %s", exc)
                last_err = exc

        if last_err is not None:
            raise AIUnavailableError(f"AI недоступен: {last_err}") from last_err
        raise AIUnavailableError("AI недоступен: ни один провайдер не настроен")


_client_singleton: AIClient | None = None


def get_ai_client(settings: Settings | None = None) -> AIClient:
    """Ленивый синглтон AIClient на основе текущих Settings."""
    global _client_singleton
    if _client_singleton is not None:
        return _client_singleton
    settings = settings or get_settings()

    primary: AnthropicProvider | None = None
    fallback: OpenAIProvider | None = None

    if settings.anthropic_api_key:
        primary = AnthropicProvider(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url,
            model=settings.anthropic_model,
            timeout=float(settings.ai_timeout_seconds),
        )
    if settings.openai_api_key:
        fallback = OpenAIProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.openai_model,
            timeout=float(settings.ai_timeout_seconds),
        )

    if primary is None and fallback is None:
        logger.warning(
            "AI: ни ANTHROPIC_API_KEY, ни OPENAI_API_KEY не заданы — AI-помощник работает в no-op."
        )

    _client_singleton = AIClient(primary=primary, fallback=fallback)
    return _client_singleton


def reset_ai_client_for_tests() -> None:
    """Сбросить синглтон между тестами."""
    global _client_singleton
    _client_singleton = None
