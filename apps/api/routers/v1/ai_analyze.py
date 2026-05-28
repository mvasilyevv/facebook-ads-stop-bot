# -*- coding: utf-8 -*-
"""Роутер AI-анализа: POST /ai/analyze.

Принимает block_type + scope_key, возвращает AI-анализ с Redis-кэшем TTL 600s.
Rate-limit: 20 запросов/час per remote IP.
  - Счётчик хранится в Redis (ключ ai:ratelimit:analyze:{client_key}, TTL 3600s).
  - За reverse-proxy используется первый IP из X-Forwarded-For.
  - При сбое Redis — in-memory secondary cap (5 запросов / 60с) как защита от лавины.
  - Логика rate-limit'а переиспользует core/ai_assistant/tools/_ratelimit.py.

Prompt-templates построены как простые user-сообщения для каждого block_type,
без доступа к tool-use (allow_tools=False) — endpoint аналитический, не мутирующий.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from apps.api.deps import DepRedis, DepSettings
from apps.api.routers.v1.schemas.ai import AIAnalyzeRequest, AIAnalyzeResponse
from core.ai_assistant.chat import ChatMessage, ChatRateLimitedError, ChatSession
from core.ai_assistant.client import AIUnavailableError, get_ai_client
from core.ai_assistant.tools._ratelimit import RateLimitExceeded, check_and_increment

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai"])

# TTL кэша в секундах
_CACHE_TTL = 600

# Rate-limit для /ai/analyze: 20 запросов/час per IP (Redis-backed)
_ANALYZE_RATE_LIMIT = 20

# Namespace Redis-ключей: ai:ratelimit:analyze:{client_key}
_RATE_LIMIT_NAMESPACE = "analyze"


def _extract_client_key(request: Request) -> str:
    """Извлечь ключ клиента для rate-limit.

    За reverse-proxy (k8s-ingress, nginx) используем первый IP из X-Forwarded-For.
    Fallback — request.client.host. При полностью неизвестном IP — 'unknown'.
    """
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        # X-Forwarded-For: <client>, <proxy1>, <proxy2>
        # Берём самый левый (реальный клиент)
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip
    return (request.client.host if request.client else None) or "unknown"


def _build_prompt(block_type: str, scope_key: str, client_data: dict | None) -> str:
    """Строит промпт для AI-анализа по типу блока."""
    context = ""
    if client_data:
        try:
            context = (
                f"\n\nДанные от клиента:\n{json.dumps(client_data, ensure_ascii=False, indent=2)}"
            )
        except (TypeError, ValueError):
            context = ""

    prompts: dict[str, str] = {
        "dashboard_overview": (
            f"Дай краткий аналитический обзор дашборда рекламных кампаний Facebook Ads "
            f"(scope: {scope_key}). Укажи ключевые метрики, аномалии, тренды. "
            f"Ответ — 2-4 абзаца.{context}"
        ),
        "ad_detail": (
            f"Проанализируй объявление {scope_key}. Оцени эффективность по метрикам "
            f"(CTR, CPC, CPM, частота, лиды). Выяви проблемы или точки роста. "
            f"Ответ — 2-3 абзаца.{context}"
        ),
        "campaign_summary": (
            f"Дай сводку по кампании {scope_key}. Оцени бюджет, охват, конверсии, "
            f"сравни группы объявлений. Рекомендации — 1-2 конкретных действия.{context}"
        ),
        "history_summary": (
            f"Проанализируй историю событий (алерты, отключения, включения) "
            f"за период {scope_key}. Выяви паттерны срабатывания стоп-правил. "
            f"Ответ — 2-3 абзаца.{context}"
        ),
    }
    return prompts.get(block_type, f"Проанализируй данные по блоку {block_type}.{context}")


@router.post("/ai/analyze", response_model=AIAnalyzeResponse)
async def ai_analyze(
    request: Request,
    body: AIAnalyzeRequest,
    redis: DepRedis,
    settings: DepSettings,
) -> JSONResponse:
    """Возвращает AI-анализ блока данных с Redis-кэшем TTL 600s.

    При force_refresh=True кэш игнорируется и перезаписывается.
    Rate-limit: 20 запросов/час per remote IP. Превышение → 429.
    Если AI-провайдеры не настроены → 503.
    """
    # Проверяем доступность AI до лимитов
    ai = get_ai_client(settings)
    if not ai.is_available:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "AI-провайдеры не настроены — задай ANTHROPIC_API_KEY или OPENAI_API_KEY"
            },
        )

    # Rate-limit per remote IP через Redis sliding-window
    client_key = _extract_client_key(request)
    try:
        await check_and_increment(
            redis,
            client_key=client_key,
            max_per_hour=_ANALYZE_RATE_LIMIT,
            namespace=_RATE_LIMIT_NAMESPACE,
        )
    except RateLimitExceeded:
        return JSONResponse(
            status_code=429,
            content={"detail": "Превышен лимит запросов: 20 в час для /ai/analyze"},
        )

    cache_key = f"ai:cache:analyze:{body.block_type}:{body.scope_key}"

    # Проверяем кэш (если не force_refresh)
    if not body.force_refresh:
        try:
            cached_raw = await redis.get(cache_key)
            if cached_raw:
                cached = json.loads(cached_raw)
                cached["from_cache"] = True
                return JSONResponse(content=cached)
        except Exception as exc:
            logger.warning("Не удалось прочитать AI-кэш %s: %s", cache_key, exc)

    # Строим промпт и запрашиваем AI
    prompt = _build_prompt(body.block_type, body.scope_key, body.client_data)
    session = ChatSession(allow_tools=False)

    # Определяем, какой провайдер будет использован
    if settings.anthropic_api_key:
        model_label = settings.anthropic_model
    else:
        model_label = settings.openai_model

    try:
        response = await session.ask(
            history=[ChatMessage(role="user", content=prompt)],
            client_key=f"analyze:{client_key}",
        )
    except ChatRateLimitedError as exc:
        return JSONResponse(
            status_code=429,
            content={"detail": str(exc)},
        )
    except AIUnavailableError as exc:
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc)},
        )

    generated_at = datetime.now(UTC).isoformat()
    payload = {
        "block_type": body.block_type,
        "scope_key": body.scope_key,
        "analysis_text": response.answer,
        "from_cache": False,
        "generated_at": generated_at,
        "model": model_label,
    }

    # Сохраняем в Redis-кэш
    try:
        await redis.set(cache_key, json.dumps(payload, ensure_ascii=False), ex=_CACHE_TTL)
    except Exception as exc:
        logger.warning("Не удалось сохранить AI-анализ в кэш %s: %s", cache_key, exc)

    return JSONResponse(content=payload)
