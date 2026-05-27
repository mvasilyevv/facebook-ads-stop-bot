# -*- coding: utf-8 -*-
"""Интерактивный чат с поддержкой tool-use.

ChatSession собирает ToolContext из инжектированных зависимостей и пробрасывает
его в execute_tool на каждый раунд tool-use. Per-client_key rate-limit поверх
in-memory кеша (быстрая защита от спама) + опционально Redis (см. tools.check_rate_limit).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.ai_assistant.client import AIUnavailableError, get_ai_client
from core.ai_assistant.prompts import build_chat_system_prompt
from core.ai_assistant.tools import (
    GLOBAL_REGISTRY,
    ToolContext,
    ToolError,
    check_rate_limit,
    execute_tool,
)
from core.config import get_settings

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncEngine

    from core.meta_api.client import MetaApiClient

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """Одно сообщение в истории."""

    role: str  # "user" | "assistant"
    content: str


@dataclass
class ToolCallTrace:
    """След выполнения tool-use — для возврата в UI."""

    name: str
    args: dict[str, Any]
    result: str
    error: str | None = None


@dataclass
class ChatResponse:
    """Финальный ответ ассистента."""

    answer: str
    tool_calls: list[ToolCallTrace] = field(default_factory=list)


# --- Простой in-memory rate-limit на ChatSession-уровне ---


class _RateLimiter:
    """Sliding-window rate-limit поверх dict[key, list[timestamps]]."""

    def __init__(self, max_per_hour: int = 30) -> None:
        self._max = max_per_hour
        self._hits: dict[str, list[float]] = {}

    def hit(self, key: str) -> bool:
        """True если запрос разрешён, False если лимит исчерпан."""
        now = time.monotonic()
        window = 3600.0
        bucket = self._hits.setdefault(key, [])
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
    """Превышен rate-limit ChatSession."""


# --- Сессия чата ---


class ChatSession:
    """Один request/response чат-цикл с поддержкой нескольких раундов tool-use.

    Зависимости (engine/redis/meta_api_client) проброшены через конструктор и
    собираются в ToolContext на старте `ask`. Сами по себе они опциональны:
    конкретный tool проверяет наличие нужной зависимости в `ToolContext.require_*`.
    """

    def __init__(
        self,
        *,
        allow_tools: bool = True,
        engine: AsyncEngine | None = None,
        redis_client: Any | None = None,
        meta_api_client: MetaApiClient | None = None,
    ) -> None:
        self._allow_tools = allow_tools
        self._engine = engine
        self._redis = redis_client
        self._meta_api = meta_api_client

    async def ask(
        self,
        history: list[ChatMessage],
        *,
        client_key: str = "default",
        requested_by: str = "",
    ) -> ChatResponse:
        """Запросить ответ AI на основе истории.

        history — только user/assistant сообщения; системный промпт добавляется.
        client_key — идентификатор клиента (rate-limit + audit).
        """
        settings = get_settings()
        if not settings.ai_chat_enabled:
            raise AIUnavailableError("AI-чат отключён настройками")

        if not get_rate_limiter().hit(client_key):
            raise ChatRateLimitedError("Превышен лимит запросов/час")

        ai = get_ai_client(settings)
        if not ai.is_available:
            raise AIUnavailableError("AI-провайдеры не настроены — проверь .env")

        ctx = ToolContext(
            client_key=client_key,
            engine=self._engine,
            redis_client=self._redis,
            meta_api_client=self._meta_api,
            requested_by=requested_by,
        )

        # Дополнительный per-tool rate-limit поверх Redis (опционально).
        try:
            await check_rate_limit(ctx, max_per_hour=settings.ai_rate_limit_per_hour)
        except ToolError as exc:
            raise ChatRateLimitedError(str(exc)) from exc

        system = build_chat_system_prompt()
        tools = GLOBAL_REGISTRY.schemas() if self._allow_tools else None

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
                    result = await execute_tool(tu.name, tu.input, ctx)
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

        return ChatResponse(
            answer="Достигнут лимит шагов с инструментами. Сформулируй вопрос точнее.",
            tool_calls=traces,
        )
