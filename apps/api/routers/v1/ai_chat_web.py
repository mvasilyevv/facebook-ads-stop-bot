# -*- coding: utf-8 -*-
"""Веб-чат с AI-ассистентом: POST /ai/chat (плавающий виджет дашборда).

Отличия от Telegram-канала (/ai в боте):
- Историю диалога держит КЛИЕНТ (React-state) и шлёт с каждым запросом —
  серверного состояния нет, максимум 12 сообщений за запрос.
- Инструменты только READ_ONLY + CREATIVE: подтверждать money-черновики в вебе
  нечем (кнопки dr_ok живут в Telegram), поэтому draft-инструменты канал
  не видит и не исполняет (guard в ChatSession по risk-уровню).
- Rate-limit 30/час per IP (Redis, паттерн /ai/analyze) + встроенный лимитер
  ChatSession. READ-инструменты Meta используют общий MetaApiClient из API lifespan.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from apps.api.deps import DepEngine, DepMetaApiClient, DepRedis, DepSettings
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
from core.ai_assistant.text import html_to_plain_text
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
_PULSE_LOCK_PREFIX = "ai:webpulse-lock:"
# Lock живёт дольше самого календарного часа. Даже если builder упал после
# платного AI-вызова или cache SET не удался, повторный запрос этого часа не
# должен инициировать второй вызов.
_PULSE_LOCK_TTL = _PULSE_CACHE_TTL
_PULSE_WAIT_INTERVAL_SECONDS = 0.2
# Окно анализа пульса — прошедший час.
_PULSE_WINDOW = timedelta(hours=1)
# Один build одновременно внутри API-процесса. Redis SET NX ниже координирует
# разные процессы/реплики; локальный lock не даёт вкладкам одного процесса даже
# входить в polling-ветку.
_PULSE_BUILD_LOCK = asyncio.Lock()


async def _read_cached_pulse(redis, cache_key: str, *, log_errors: bool = True) -> dict | None:
    """Прочитать и провалидировать payload пульса. Битый кэш считается miss."""
    try:
        cached_raw = await redis.get(cache_key)
        if not cached_raw:
            return None
        return AIPulseResponse.model_validate(json.loads(cached_raw)).model_dump()
    except Exception:  # noqa: BLE001 — кэш не должен ронять endpoint
        if log_errors:
            logger.warning("ai_pulse: не смог прочитать кэш %s", cache_key, exc_info=True)
        return None


async def _wait_for_cached_pulse(
    redis,
    cache_key: str,
    *,
    timeout_seconds: float,
) -> dict | None:
    """Подождать результат другой API-реплики, уже строящей этот час."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(_PULSE_WAIT_INTERVAL_SECONDS)
        cached = await _read_cached_pulse(redis, cache_key, log_errors=False)
        if cached is not None:
            return cached
    return None


@router.post("/ai/chat", response_model=AIChatResponse)
async def ai_chat(
    request: Request,
    body: AIChatRequest,
    engine: DepEngine,
    redis: DepRedis,
    settings: DepSettings,
    meta_api_client: DepMetaApiClient,
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
        meta_api_client=meta_api_client,
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

    model_label = response.model or (
        settings.anthropic_model if settings.anthropic_api_key else settings.openai_model
    )
    payload = AIChatResponse(
        answer=html_to_plain_text(response.answer),
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
    hour_key = f"{now:%Y-%m-%dT%H}"
    cache_key = f"{_PULSE_CACHE_PREFIX}{hour_key}"
    lock_key = f"{_PULSE_LOCK_PREFIX}{hour_key}"

    cached = await _read_cached_pulse(redis, cache_key)
    if cached is not None:
        return JSONResponse(content=cached)

    async with _PULSE_BUILD_LOCK:
        # Пока ждали локальный lock, соседняя вкладка могла уже заполнить кэш.
        cached = await _read_cached_pulse(redis, cache_key)
        if cached is not None:
            return JSONResponse(content=cached)

        redis_available = True
        try:
            acquired = bool(await redis.set(lock_key, "1", nx=True, ex=_PULSE_LOCK_TTL))
        except Exception:  # noqa: BLE001 — без Redis глобальный hourly cap недоказуем
            redis_available = False
            acquired = False
            logger.warning(
                "ai_pulse: Redis-lock недоступен — fail-closed без AI-вызова",
                exc_info=True,
            )

        if not redis_available:
            return JSONResponse(
                status_code=503,
                content={"detail": "Почасовой пульс временно недоступен: нет Redis-lock"},
            )

        if not acquired:
            wait_seconds = min(
                float(_PULSE_LOCK_TTL - 1),
                max(10.0, float(settings.ai_timeout_seconds) + 15.0),
            )
            cached = await _wait_for_cached_pulse(
                redis,
                cache_key,
                timeout_seconds=wait_seconds,
            )
            if cached is not None:
                return JSONResponse(content=cached)
            # Не запускаем второй AI-вызов: потерянный/медленный builder безопаснее
            # показать как временную недоступность, чем нарушить глобальный hourly cap.
            return JSONResponse(
                status_code=503,
                content={"detail": "Почасовой пульс ещё формируется — повтори запрос позже"},
            )

        text = await build_pulse(engine, since=now - _PULSE_WINDOW, now=now, html=False)
        payload = AIPulseResponse(
            important=text is not None,
            text=text,
            generated_at=now.isoformat(),
        ).model_dump()

        try:
            await redis.set(cache_key, json.dumps(payload, ensure_ascii=False), ex=_PULSE_CACHE_TTL)
        except Exception:  # noqa: BLE001
            logger.warning("ai_pulse: не смог сохранить кэш %s", cache_key, exc_info=True)

        return JSONResponse(content=payload)
