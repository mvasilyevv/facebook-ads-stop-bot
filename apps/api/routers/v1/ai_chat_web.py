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

import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from apps.api.deps import DepEngine, DepRedis, DepSettings
from apps.api.routers.v1.ai_analyze import _extract_client_key
from apps.api.routers.v1.schemas.ai_chat import (
    AIChatRequest,
    AIChatResponse,
    AIPulseResponse,
    ToolCallOut,
)
from core.ai_assistant.chat import ChatMessage, ChatRateLimitedError, ChatSession
from core.ai_assistant.client import AIUnavailableError, get_ai_client
from core.ai_assistant.pulse import build_pulse
from core.ai_assistant.tools._ratelimit import RateLimitExceeded, check_and_increment
from core.ai_assistant.tools.base import RiskLevel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai"])

# Rate-limit веб-чата: 30 запросов/час per IP (как ChatSession-лимитер по умолчанию).
_CHAT_RATE_LIMIT = 30
_RATE_LIMIT_NAMESPACE = "webchat"

# Веб-канал без подтверждающих кнопок → только чтение и креатив.
_WEB_RISK_LEVELS = frozenset({RiskLevel.READ_ONLY, RiskLevel.CREATIVE})

# Почасовой пульс: кэш результата на календарный час (UTC). Сколько бы вкладок
# ни опрашивало — детерминированная проверка/AI выполняются максимум раз в час.
_PULSE_CACHE_PREFIX = "ai:webpulse:"
_PULSE_CACHE_TTL = 2 * 3600
# Окно анализа пульса — прошедший час.
_PULSE_WINDOW = timedelta(hours=1)


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


@router.get("/ai/pulse", response_model=AIPulseResponse)
async def ai_pulse(
    request: Request,
    engine: DepEngine,
    redis: DepRedis,
    settings: DepSettings,
) -> JSONResponse:
    """Почасовой пульс кабинета для веб-виджета.

    Виджет опрашивает раз в час (пока вкладка открыта). Двухступенчатый контракт
    против шума и лишних токенов: детерминированный pre-check сигналов (стопы /
    упавшие задачи / шквал warnings за прошедший час) — если пусто, AI НЕ
    вызывается и возвращается important=false (виджет молчит). Результат
    кэшируется на календарный час — повторные опросы и вторые вкладки бесплатны.
    """
    _ = request  # авторизация — общими middleware (X-API-Key), per-IP лимит не нужен: кэш почасовой
    now = datetime.now(UTC)
    cache_key = f"{_PULSE_CACHE_PREFIX}{now:%Y-%m-%dT%H}"

    try:
        cached_raw = await redis.get(cache_key)
        if cached_raw:
            return JSONResponse(content=json.loads(cached_raw))
    except Exception:  # noqa: BLE001 — кэш недоступен: считаем заново
        logger.warning("ai_pulse: не смог прочитать кэш %s", cache_key)

    text = await build_pulse(engine, since=now - _PULSE_WINDOW, now=now, html=False)
    payload = AIPulseResponse(
        important=text is not None,
        text=text,
        generated_at=now.isoformat(),
    ).model_dump()

    try:
        await redis.set(cache_key, json.dumps(payload, ensure_ascii=False), ex=_PULSE_CACHE_TTL)
    except Exception:  # noqa: BLE001
        logger.warning("ai_pulse: не смог сохранить кэш %s", cache_key)

    return JSONResponse(content=payload)
