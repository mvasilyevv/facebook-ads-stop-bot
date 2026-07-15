# -*- coding: utf-8 -*-
"""AI-резюме к ежедневному дайджесту.

Best-effort надстройка над SQL-дайджестом: 2-4 предложения выводов от ассистента.
Любая проблема (AI выключен/недоступен/таймаут) → None, дайджест уходит как раньше.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from core.ai_assistant.client import AIUnavailableError, get_ai_client
from core.config import get_settings

if TYPE_CHECKING:  # pragma: no cover
    from core.telegram.digest_builder import DigestPayload

logger = logging.getLogger(__name__)

_CACHE_KEY_PREFIX = "ai:digest:summary:"
_CACHE_TTL_SECONDS = 26 * 3600  # как digest:sent — перекрывает ретраи внутри суток

_SYSTEM_PROMPT = (
    "Ты ассистент байера в арбитраже трафика (гемблинг, Facebook Ads). "
    "По данным дневного дайджеста напиши 2-4 предложения по-русски: главный вывод дня, "
    "что требует внимания, что идёт хорошо. Только факты из данных, без воды и приветствий. "
    "Обычный текст без разметки, максимум 400 символов."
)


def _facts_from_payload(payload: DigestPayload) -> str:
    """Компактная сводка фактов дайджеста для промпта."""
    lines = [
        f"Окно: {payload.window_start_utc:%d.%m %H:%M}–{payload.window_end_utc:%H:%M} UTC",
        f"Алерты: warning={payload.alerts_warning_count}, stop={payload.alerts_stop_count}",
        f"Отключения: успешных={payload.disable_tasks_succeeded}, "
        f"упавших={payload.disable_tasks_failed}",
        f"Активных офферов={payload.active_offers_count}, объявлений={payload.active_ads_count}",
        f"Спенд за окно=${payload.total_spend_window_usd}",
    ]
    for ad in payload.top_ads_by_spend[:5]:
        name = getattr(ad, "ad_name", None) or getattr(ad, "fb_ad_id", "?")
        spend = getattr(ad, "spend_usd", None) or getattr(ad, "spend", "?")
        lines.append(f"Топ по спенду: {name} — ${spend}")
    return "\n".join(lines)


async def summarize_digest(
    payload: DigestPayload,
    *,
    redis_client: Any | None = None,
) -> str | None:
    """Сгенерировать AI-вывод к дайджесту. None → дайджест уходит без резюме.

    Кэш в Redis (ai:digest:summary:{дата}) защищает от повторных обращений к AI
    при ретраях отправки внутри одних суток. Никогда не бросает исключений.
    """
    settings = get_settings()
    if not settings.ai_digest_summary_enabled:
        return None

    client = get_ai_client(settings)
    if not client.is_available:
        return None

    cache_key = f"{_CACHE_KEY_PREFIX}{payload.window_end_utc:%Y-%m-%d}"
    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                return str(cached)
        except Exception:  # noqa: BLE001
            logger.debug("digest_summary: Redis-кэш недоступен (не критично)")

    try:
        response = await asyncio.wait_for(
            client.chat(
                messages=[{"role": "user", "content": _facts_from_payload(payload)}],
                system=_SYSTEM_PROMPT,
                max_tokens=250,
            ),
            timeout=float(settings.ai_timeout_seconds),
        )
    except (TimeoutError, asyncio.TimeoutError):
        logger.warning("digest_summary: таймаут AI — дайджест уйдёт без резюме")
        return None
    except AIUnavailableError as exc:
        logger.warning("digest_summary: AI недоступен: %s", exc)
        return None
    except Exception:  # noqa: BLE001
        logger.warning("digest_summary: ошибка провайдера", exc_info=True)
        return None

    text = (response.text or "").strip()
    if not text:
        return None
    if len(text) > 600:
        text = text[:597].rstrip() + "…"

    if redis_client is not None:
        try:
            await redis_client.set(cache_key, text, ex=_CACHE_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            pass
    return text


__all__ = ["summarize_digest"]
