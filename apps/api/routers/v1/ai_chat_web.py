# -*- coding: utf-8 -*-
"""Веб-чат с AI-ассистентом: POST /ai/chat (плавающий виджет дашборда).

Отличия от Telegram-канала (/ai в боте):
- Историю диалога держит КЛИЕНТ (React-state) и шлёт с каждым запросом —
  серверного состояния нет, максимум 12 сообщений за запрос.
- Инструменты только READ_ONLY + CREATIVE: подтверждать money-черновики в вебе
  нечем (кнопки dr_ok живут в Telegram), поэтому draft-инструменты канал
  не видит и не исполняет (guard в ChatSession по risk-уровню).
- Rate-limit 30/час per IP (Redis, паттерн /ai/analyze) + встроенный лимитер
  ChatSession. MetaApiClient в API-процессе нет — meta-инструменты мягко
  деградируют (ToolError), БД/Redis/трекер/creative работают.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from apps.api.deps import DepEngine, DepRedis, DepSettings
from apps.api.routers.v1.ai_analyze import _extract_client_key
from apps.api.routers.v1.schemas.ai_chat import AIChatRequest, AIChatResponse, ToolCallOut
from core.ai_assistant.chat import ChatMessage, ChatRateLimitedError, ChatSession
from core.ai_assistant.client import AIUnavailableError, get_ai_client
from core.ai_assistant.tools._ratelimit import RateLimitExceeded, check_and_increment
from core.ai_assistant.tools.base import RiskLevel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai"])

# Rate-limit веб-чата: 30 запросов/час per IP (как ChatSession-лимитер по умолчанию).
_CHAT_RATE_LIMIT = 30
_RATE_LIMIT_NAMESPACE = "webchat"

# Веб-канал без подтверждающих кнопок → только чтение и креатив.
_WEB_RISK_LEVELS = frozenset({RiskLevel.READ_ONLY, RiskLevel.CREATIVE})


@router.post("/ai/chat", response_model=AIChatResponse)
async def ai_chat(
    request: Request,
    body: AIChatRequest,
    engine: DepEngine,
    redis: DepRedis,
    settings: DepSettings,
) -> JSONResponse:
    """Ответ ассистента на вопрос из веб-виджета (с tool-use, read-only канал).

    429 — превышен лимит; 503 — AI-провайдеры не настроены.
    """
    # Rate-limit первым (до проверки провайдера) — как в /ai/analyze (M9).
    client_key = _extract_client_key(
        request,
        trust_proxy=settings.trust_proxy_headers,
        trusted_proxy_count=settings.trusted_proxy_count,
    )
    try:
        await check_and_increment(
            redis,
            client_key=client_key,
            max_per_hour=_CHAT_RATE_LIMIT,
            namespace=_RATE_LIMIT_NAMESPACE,
        )
    except RateLimitExceeded:
        return JSONResponse(
            status_code=429,
            content={"detail": f"Превышен лимит запросов: {_CHAT_RATE_LIMIT} в час для /ai/chat"},
        )

    ai = get_ai_client(settings)
    if not ai.is_available:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "AI-провайдеры не настроены — задай ANTHROPIC_API_KEY или OPENAI_API_KEY"
            },
        )

    history = [ChatMessage(role=m.role, content=m.content) for m in body.messages]

    session = ChatSession(
        allow_tools=True,
        engine=engine,
        redis_client=redis,
        meta_api_client=None,
        skills=("web_chat",),
        allowed_risk_levels=_WEB_RISK_LEVELS,
    )
    try:
        response = await session.ask(
            history,
            client_key=f"web:{client_key}",
            requested_by=f"web:{client_key}",
        )
    except ChatRateLimitedError as exc:
        return JSONResponse(status_code=429, content={"detail": str(exc)})
    except AIUnavailableError as exc:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    model_label = settings.anthropic_model if settings.anthropic_api_key else settings.openai_model
    payload = AIChatResponse(
        answer=response.answer,
        tool_calls=[ToolCallOut(name=t.name, error=t.error) for t in response.tool_calls],
        generated_at=datetime.now(UTC).isoformat(),
        model=model_label,
    )
    return JSONResponse(content=payload.model_dump())
