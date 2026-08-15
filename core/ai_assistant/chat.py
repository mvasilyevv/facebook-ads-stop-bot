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
from core.safe_diagnostics import redact_sensitive_text

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncEngine

    from core.meta_api.client import MetaApiClient

logger = logging.getLogger(__name__)


def _redact_trace_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_trace_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_trace_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_trace_value(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


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
    provider: str = ""
    model: str = ""


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
        skills: tuple[str, ...] | list[str] | None = None,
        allowed_risk_levels: frozenset | set | None = None,
    ) -> None:
        self._allow_tools = allow_tools
        self._engine = engine
        self._redis = redis_client
        self._meta_api = meta_api_client
        # Имена скилов из prompts/skills/*.md — подмешиваются в системный промпт.
        self._skills = tuple(skills or ())
        # Ограничение по RiskLevel для канала (веб-чат: только READ_ONLY+CREATIVE,
        # т.к. подтверждать драфты в вебе нечем — кнопки dr_ok живут в Telegram).
        # None = все уровни (TG-канал). Фильтрует и schemas, и ИСПОЛНЕНИЕ (guard
        # ниже: даже если модель галлюцинирует запрещённый tool — он не исполнится).
        if allowed_risk_levels is None:
            self._allowed_tool_names: frozenset[str] | None = None
        else:
            self._allowed_tool_names = frozenset(
                h.name for rl in allowed_risk_levels for h in GLOBAL_REGISTRY.list_by_risk(rl)
            )

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
            raise ChatRateLimitedError("Лимит AI-инструментов исчерпан") from exc

        system = build_chat_system_prompt(skills=self._skills)
        tools = GLOBAL_REGISTRY.schemas() if self._allow_tools else None
        if tools is not None and self._allowed_tool_names is not None:
            tools = [t for t in tools if t.get("name") in self._allowed_tool_names]

        messages: list[dict[str, Any]] = [
            {"role": m.role, "content": m.content}
            for m in history
            if m.role in ("user", "assistant")
        ]

        traces: list[ToolCallTrace] = []
        last_provider = ""
        last_model = ""
        max_iters = max(1, settings.ai_max_tool_iterations)

        for _ in range(max_iters):
            try:
                response = await asyncio.wait_for(
                    ai.chat(messages=messages, system=system, tools=tools, max_tokens=1500),
                    timeout=float(settings.ai_timeout_seconds) * 2,
                )
            except (TimeoutError, asyncio.TimeoutError) as exc:
                raise AIUnavailableError("AI: таймаут запроса") from exc

            last_provider = response.provider
            last_model = response.model

            if not response.has_tool_uses:
                return ChatResponse(
                    answer=redact_sensitive_text(response.text) or "(пустой ответ)",
                    tool_calls=traces,
                    provider=last_provider,
                    model=last_model,
                )

            # MID-19 hard-guard: allow_tools=False запрещает исполнение ЛЮБОГО tool_use,
            # даже если модель его всё-таки вернула (например, провайдер проигнорировал
            # пустой tools=None, или баг в промпте/API). Раньше отсутствие tools в запросе
            # было единственной защитой — не hard-guard в самом цикле. Здесь — явный отказ:
            # ни один tool не исполняется, ERROR в лог, модели во всех tool_result уходит
            # отказ, чтобы она сформулировала обычный текстовый ответ на следующем шаге.
            if not self._allow_tools:
                logger.error(
                    "ChatSession(allow_tools=False): модель вернула %d tool_use — "
                    "исполнение заблокировано hard-guard'ом (tools=%s)",
                    len(response.tool_uses),
                    [tu.name for tu in response.tool_uses],
                )
                assistant_blocks = []
                if response.text:
                    assistant_blocks.append(
                        {"type": "text", "text": redact_sensitive_text(response.text)}
                    )
                for tu in response.tool_uses:
                    assistant_blocks.append(
                        {"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input}
                    )
                messages.append({"role": "assistant", "content": assistant_blocks})

                refusal = (
                    "Инструменты отключены в этом режиме — вызов недоступен. "
                    "Ответь текстом без использования tool-use."
                )
                tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": [{"type": "text", "text": refusal}],
                        "is_error": True,
                    }
                    for tu in response.tool_uses
                ]
                traces.append(
                    ToolCallTrace(
                        name=",".join(tu.name for tu in response.tool_uses),
                        args={},
                        result="",
                        error="allow_tools=False: вызов инструментов заблокирован",
                    )
                )
                messages.append({"role": "user", "content": tool_results})
                continue

            assistant_blocks: list[dict[str, Any]] = []
            if response.text:
                assistant_blocks.append(
                    {"type": "text", "text": redact_sensitive_text(response.text)}
                )
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
                # Guard канала: tool вне разрешённых risk-уровней не исполняется,
                # даже если модель вернула его вопреки отфильтрованным schemas.
                if self._allowed_tool_names is not None and tu.name not in self._allowed_tool_names:
                    refusal = (
                        f"Инструмент {tu.name} недоступен в этом канале — "
                        "действия выполняются через Telegram-бота."
                    )
                    traces.append(
                        ToolCallTrace(
                            name=tu.name,
                            args=_redact_trace_value(dict(tu.input)),
                            result="",
                            error=refusal,
                        )
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": [{"type": "text", "text": refusal}],
                            "is_error": True,
                        }
                    )
                    continue
                try:
                    result = await execute_tool(tu.name, tu.input, ctx)
                    safe_result = redact_sensitive_text(result)
                    traces.append(
                        ToolCallTrace(
                            name=tu.name,
                            args=_redact_trace_value(dict(tu.input)),
                            result=safe_result,
                        )
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": [{"type": "text", "text": safe_result}],
                        }
                    )
                except ToolError as exc:
                    err = redact_sensitive_text(exc)
                    traces.append(
                        ToolCallTrace(
                            name=tu.name,
                            args=_redact_trace_value(dict(tu.input)),
                            result="",
                            error=err,
                        )
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
            provider=last_provider,
            model=last_model,
        )
