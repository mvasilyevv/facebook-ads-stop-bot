# -*- coding: utf-8 -*-
"""Короткие LLM-объяснения почему сработало стоп-правило.

Используется при STOP/WARNING алертах в Telegram: добавляет 1-2 предложения
на русском языке, чтобы байер сразу понял причину без захода в дашборд.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from core.ai_assistant.client import AIUnavailableError, get_ai_client
from core.config import get_settings

logger = logging.getLogger(__name__)

# In-memory кэш: ключ → (текст ответа, время истечения)
_explain_cache: dict[str, tuple[str, float]] = {}

# TTL кэша — 1 час
_CACHE_TTL_SECONDS = 3600

# Человекочитаемые названия правил для промпта
_RULE_NAMES_RU: dict[str, str] = {
    "cpl_stop": "CPL-стоп (высокая стоимость лида)",
    "cpl_warning": "CPL-предупреждение (растущая стоимость лида)",
    "cpr_stop": "CPR-стоп (высокая стоимость регистрации)",
    "cpr_warning": "CPR-предупреждение (растущая стоимость регистрации)",
    "cpc_stop": "CPC-стоп (высокая стоимость клика)",
    "cpc_warning": "CPC-предупреждение (растущая стоимость клика)",
    "spend_no_dep_stop": "расход без депозитов",
    "spend_with_dep_stop": "расход с депозитами выше порога",
    "regs_no_dep_stop": "регистрации без депозитов",
    "frequency_stop": "выгорание аудитории (стоп по частоте)",
    "frequency_warning": "выгорание аудитории (предупреждение по частоте)",
    "frequency_anomaly": "аномалия частоты показов",
}


def _build_cache_key(
    rule_name: str,
    stage: str,
    metrics: dict[str, Any],
    thresholds: dict[str, Any],
    offer_code: str | None,
) -> str:
    """Строит ключ кэша из параметров запроса.

    Метрики и пороги округляются до 2 знаков, чтобы незначительные колебания
    не порождали новые запросы к AI.
    """

    def _round_val(v: Any) -> Any:
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return v

    rounded_metrics = {k: _round_val(v) for k, v in sorted(metrics.items())}
    rounded_thresholds = {k: _round_val(v) for k, v in sorted(thresholds.items())}

    payload = {
        "rule": rule_name,
        "stage": stage,
        "metrics": rounded_metrics,
        "thresholds": rounded_thresholds,
        "offer": offer_code or "",
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()[:24]


def _get_cached(key: str) -> str | None:
    """Возвращает закэшированный ответ, если не истёк TTL."""
    entry = _explain_cache.get(key)
    if entry is None:
        return None
    text, expires_at = entry
    if time.monotonic() > expires_at:
        del _explain_cache[key]
        return None
    return text


def _put_cache(key: str, text: str) -> None:
    """Сохраняет ответ в кэш с TTL 1 час."""
    _explain_cache[key] = (text, time.monotonic() + _CACHE_TTL_SECONDS)


def _build_prompt(
    rule_name: str,
    stage: str,
    metrics: dict[str, Any],
    thresholds: dict[str, Any],
    offer_context: dict[str, Any] | None,
) -> str:
    """Формирует компактный промпт для AI на русском языке."""
    rule_label = _RULE_NAMES_RU.get(rule_name, rule_name)
    stage_label = "СТОП" if stage.upper() == "STOP" else "ПРЕДУПРЕЖДЕНИЕ"

    # Собираем ключевые метрики
    metric_parts: list[str] = []
    for key, label in [
        ("spend", "Расход"),
        ("cost_per_lead", "CPL"),
        ("cost_per_registration", "CPR"),
        ("cpc", "CPC"),
        ("leads", "Лидов"),
        ("registrations", "Регистраций"),
        ("deposits", "Депозитов"),
        ("frequency", "Частота"),
        ("clicks", "Кликов"),
    ]:
        v = metrics.get(key)
        if v is not None:
            metric_parts.append(f"{label}={v}")

    # Собираем пороги
    threshold_parts: list[str] = []
    for key, label in [
        ("cpl_stop_threshold", "Порог CPL"),
        ("cpr_stop_threshold", "Порог CPR"),
        ("cpc_stop_threshold", "Порог CPC"),
        ("warning_percent_of_stop", "Уровень warning"),
    ]:
        v = thresholds.get(key)
        if v is not None:
            threshold_parts.append(f"{label}={v}")

    # Контекст оффера
    offer_parts: list[str] = []
    if offer_context:
        offer_code = offer_context.get("offer_code")
        median_cpl = offer_context.get("median_cpl")
        median_cpr = offer_context.get("median_cpr")
        if offer_code:
            offer_parts.append(f"Оффер={offer_code}")
        if median_cpl is not None:
            offer_parts.append(f"Медиана CPL оффера={median_cpl}")
        if median_cpr is not None:
            offer_parts.append(f"Медиана CPR оффера={median_cpr}")

    lines = [
        f"Правило [{stage_label}]: {rule_label}",
        f"Метрики: {', '.join(metric_parts) if metric_parts else 'нет данных'}",
    ]
    if threshold_parts:
        lines.append(f"Пороги: {', '.join(threshold_parts)}")
    if offer_parts:
        lines.append(f"Контекст: {', '.join(offer_parts)}")

    lines.append(
        "\nНапиши 1-2 предложения на русском: главная причина срабатывания и "
        "что это значит для байера. Без вводных слов, без markdown, "
        "максимум 150 символов."
    )

    return "\n".join(lines)


async def explain_alert(
    *,
    rule_name: str,
    stage: str,
    metrics: dict[str, Any],
    thresholds: dict[str, Any],
    offer_context: dict[str, Any] | None = None,
    timeout_seconds: float | None = None,
) -> str | None:
    """Генерирует короткое (1-2 предложения) объяснение почему сработало правило.

    Возвращает None если:
    - AI недоступен (нет ключей)
    - превышен timeout
    - любая ошибка провайдера

    Никогда не бросает исключений — алерт должен уйти без объяснения.
    """
    settings = get_settings()

    # Глобальный флаг отключения
    if not settings.ai_explain_alerts_enabled:
        return None

    if timeout_seconds is None:
        timeout_seconds = float(getattr(settings, "ai_explain_timeout_seconds", 8.0))

    client = get_ai_client(settings)
    if not client.is_available:
        return None

    offer_code = (offer_context or {}).get("offer_code")
    cache_key = _build_cache_key(rule_name, stage, metrics, thresholds, offer_code)

    # Проверка кэша
    cached = _get_cached(cache_key)
    if cached is not None:
        logger.debug("explain_alert: cache hit для %s/%s", rule_name, stage)
        return cached

    prompt = _build_prompt(rule_name, stage, metrics, thresholds, offer_context)

    try:
        response = await asyncio.wait_for(
            client.chat(
                messages=[{"role": "user", "content": prompt}],
                system=(
                    "Ты ассистент байера в арбитраже трафика. "
                    "Отвечай только фактом и выводом, без приветствий и вводных слов."
                ),
                max_tokens=100,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning("explain_alert: timeout (%ss) для %s/%s", timeout_seconds, rule_name, stage)
        return None
    except AIUnavailableError as exc:
        logger.warning("explain_alert: AI недоступен: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("explain_alert: ошибка провайдера: %s", exc)
        return None

    text = (response.text or "").strip()
    if not text:
        return None

    # Жёсткий лимит — 200 символов (чуть больше заявленных 150, чтобы не резать красиво)
    if len(text) > 200:
        text = text[:197].rstrip() + "…"

    _put_cache(cache_key, text)
    logger.debug("explain_alert: сгенерировано объяснение для %s/%s", rule_name, stage)
    return text
