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
from apps.api.middleware.api_problem import api_problem_payload, request_correlation_id
from apps.api.routers.v1.schemas.ai import AIAnalyzeRequest, AIAnalyzeResponse
from core.ai_assistant.chat import ChatMessage, ChatRateLimitedError, ChatSession
from core.ai_assistant.client import AIUnavailableError, get_ai_client
from core.ai_assistant.tools._ratelimit import RateLimitExceeded, check_and_increment
from core.safe_diagnostics import safe_exception_diagnostic

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai"])

# TTL кэша в секундах
_CACHE_TTL = 600

# Rate-limit для /ai/analyze: 20 запросов/час per IP (Redis-backed)
_ANALYZE_RATE_LIMIT = 20

# Namespace Redis-ключей: ai:ratelimit:analyze:{client_key}
_RATE_LIMIT_NAMESPACE = "analyze"


def _problem(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    correlation_id = request_correlation_id(request.scope)
    return JSONResponse(
        status_code=status_code,
        content=api_problem_payload(
            code=code,
            message=message,
            correlation_id=correlation_id,
        ),
        headers={"X-Request-Id": correlation_id},
    )


def _extract_client_key(
    request: Request, *, trust_proxy: bool = False, trusted_proxy_count: int = 1
) -> str:
    """Извлечь ключ клиента для rate-limit.

    trust_proxy=True (settings.trust_proxy_headers — API за доверенным reverse-proxy):
    берём реальный client-IP из X-Forwarded-For. Иначе (дефолт) — request.client.host
    (реальный TCP-peer). H7a: XFF подделывается любым клиентом.

    M-16 (аудит 2026-07-12): при XFF `<client>, <proxy1>, ..., <proxyN>` каждый
    доверенный прокси дописывает peer СПРАВА, поэтому реальный клиент — (N+1)-й справа,
    а левые элементы клиент-контролируемы. Раньше брали самый левый → обход rate-limit
    даже за корректным прокси. Берём элемент с индексом trusted_proxy_count с конца.
    """
    if trust_proxy:
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
            if parts:
                # Каждый доверенный прокси дописывает peer справа: реальный клиент —
                # N-й элемент С КОНЦА (N = trusted_proxy_count). Если XFF короче цепочки
                # прокси (аномалия) — берём самый левый (клемп, безопаснее).
                n = min(trusted_proxy_count, len(parts))
                return parts[-n]
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
    # M9: rate-limit ПЕРВЫМ (до проверки провайдера), иначе незалимиченный enumeration
    # доступности AI. XFF учитывается только за доверенным прокси (H7a), иначе TCP-peer.
    client_key = _extract_client_key(
        request,
        trust_proxy=settings.trust_proxy_headers,
        trusted_proxy_count=settings.trusted_proxy_count,
    )
    try:
        await check_and_increment(
            redis,
            client_key=client_key,
            max_per_hour=_ANALYZE_RATE_LIMIT,
            namespace=_RATE_LIMIT_NAMESPACE,
        )
    except RateLimitExceeded:
        return _problem(
            request,
            status_code=429,
            code="ai_rate_limited",
            message="Превышен лимит запросов: 20 в час для /ai/analyze",
        )

    # Доступность AI — после лимита.
    ai = get_ai_client(settings)
    if not ai.is_available:
        return _problem(
            request,
            status_code=503,
            code="ai_unavailable",
            message="AI-провайдеры не настроены",
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
            logger.warning(
                "Не удалось прочитать AI-кэш (%s)",
                safe_exception_diagnostic(exc),
            )

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
    except ChatRateLimitedError:
        return _problem(
            request,
            status_code=429,
            code="ai_rate_limited",
            message="AI временно ограничил частоту запросов",
        )
    except AIUnavailableError:
        return _problem(
            request,
            status_code=503,
            code="ai_unavailable",
            message="AI-провайдер временно недоступен",
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
        logger.warning(
            "Не удалось сохранить AI-анализ в кэш (%s)",
            safe_exception_diagnostic(exc),
        )

    return JSONResponse(content=payload)
