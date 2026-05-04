# -*- coding: utf-8 -*-
"""Интерактивный чат с поддержкой tool-use."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from core.ai_assistant.client import AIUnavailableError, get_ai_client
from core.ai_assistant.prompts import build_chat_system_prompt
from core.ai_assistant.tools import TOOL_SCHEMAS, ToolError, execute_tool
from core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """Одно сообщение в истории."""

    role: str  # "user" | "assistant"
    content: str


@dataclass
class ToolCallTrace:
    """След выполнения tool-use (для возврата в UI)."""

    name: str
    args: dict[str, Any]
    result: str
    error: str | None = None


@dataclass
class ChatResponse:
    """Финальный ответ ассистента."""

    answer: str
    tool_calls: list[ToolCallTrace] = field(default_factory=list)


# --- Простой rate-limit: 30 запросов/час на ключ ---


class _RateLimiter:
    """In-memory rate-limit (sliding window)."""

    def __init__(self, max_per_hour: int = 30) -> None:
        self._max = max_per_hour
        self._hits: dict[str, list[float]] = {}

    def hit(self, key: str) -> bool:
        """True если запрос разрешён, False если лимит исчерпан."""
        now = time.monotonic()
        window = 3600.0
        bucket = self._hits.setdefault(key, [])
        # Чистим устаревшие
        bucket[:] = [t for t in bucket if now - t < window]
        if len(bucket) >= self._max:
            return False
        bucket.append(now)
        return True


_rate_limiter: _RateLimiter | None = None


def get_rate_limiter() -> _RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = _RateLimiter(max_per_hour=get_settings().ai_rate_limit_per_hour)
    return _rate_limiter


class ChatRateLimitedError(Exception):
    """Превышен rate-limit."""


# --- Сессия чата ---


class ChatSession:
    """Проводит один request/response чат-цикл с поддержкой нескольких раундов tool-use."""

    def __init__(self, *, allow_tools: bool = True) -> None:
        self._allow_tools = allow_tools

    async def ask(
        self,
        history: list[ChatMessage],
        *,
        client_key: str = "default",
    ) -> ChatResponse:
        """Запросить ответ AI на основе истории.

        history — только user/assistant сообщения. Системный промпт добавляется
        автоматически.
        """
        settings = get_settings()
        if not settings.ai_chat_enabled:
            raise AIUnavailableError("AI-чат отключён настройками")

        if not get_rate_limiter().hit(client_key):
            raise ChatRateLimitedError("Превышен лимит 30 запросов/час")

        ai = get_ai_client(settings)
        if not ai.is_available:
            raise AIUnavailableError("AI-провайдеры не настроены — проверь .env")

        system = build_chat_system_prompt()
        tools = TOOL_SCHEMAS if self._allow_tools else None

        # Преобразуем историю в anthropic-формат
        messages: list[dict[str, Any]] = [
            {"role": m.role, "content": m.content}
            for m in history
            if m.role in ("user", "assistant")
        ]

        traces: list[ToolCallTrace] = []
        max_iters = max(1, settings.ai_max_tool_iterations)

        for _ in range(max_iters):
            try:
                response = await asyncio.wait_for(
                    ai.chat(messages=messages, system=system, tools=tools, max_tokens=1500),
                    timeout=float(settings.ai_timeout_seconds) * 2,
                )
            except (TimeoutError, asyncio.TimeoutError) as exc:
                raise AIUnavailableError("AI: таймаут запроса") from exc

            if not response.has_tool_uses:
                return ChatResponse(answer=response.text or "(пустой ответ)", tool_calls=traces)

            # LLM просит выполнить инструменты — выполняем все, кладём результаты в историю
            assistant_blocks: list[dict[str, Any]] = []
            if response.text:
                assistant_blocks.append({"type": "text", "text": response.text})
            for tu in response.tool_uses:
                assistant_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tu.id,
                        "name": tu.name,
                        "input": tu.input,
                    }
                )
            messages.append({"role": "assistant", "content": assistant_blocks})

            tool_results: list[dict[str, Any]] = []
            for tu in response.tool_uses:
                try:
                    result = await execute_tool(tu.name, tu.input)
                    traces.append(ToolCallTrace(name=tu.name, args=dict(tu.input), result=result))
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": [{"type": "text", "text": result}],
                        }
                    )
                except ToolError as exc:
                    err = str(exc)
                    traces.append(
                        ToolCallTrace(name=tu.name, args=dict(tu.input), result="", error=err)
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": [{"type": "text", "text": f"ошибка: {err}"}],
                            "is_error": True,
                        }
                    )

            messages.append({"role": "user", "content": tool_results})
            # Идём на следующую итерацию

        # Иначе — превысили лимит итераций
        return ChatResponse(
            answer="Достигнут лимит шагов с инструментами. Сформулируй вопрос точнее.",
            tool_calls=traces,
        )
