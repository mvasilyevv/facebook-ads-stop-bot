# -*- coding: utf-8 -*-
"""«Пульс кабинета» — периодический проактивный отчёт от ассистента.

Двухступенчатый контракт против спама:
1. Детерминированный pre-check (`collect_pulse_signals`) — чистый SQL. Если
   значимых сигналов нет (нет стопов, нет упавших задач, warnings ниже порога) —
   возвращает None, AI НЕ вызывается, сообщение НЕ отправляется.
2. Только при сигналах — AI-отчёт со скилом pulse_report; при недоступном AI
   уходит детерминированный фолбэк (сигнал важнее красоты).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.ai_assistant.client import AIUnavailableError, get_ai_client
from core.ai_assistant.prompts import PromptNotFoundError, load_skill
from core.ai_assistant.text import html_to_plain_text
from core.config import get_settings
from core.rules.labels import RULE_LABELS, rule_label

logger = logging.getLogger(__name__)

# Порог «warnings сами по себе»: меньше — не будим владельца из-за предупреждений.
_WARNINGS_ALONE_THRESHOLD = 3


@dataclass(frozen=True)
class PulseSignals:
    """Значимые события окна пульса (детерминированный pre-check)."""

    window_start_utc: datetime
    window_end_utc: datetime
    stop_count: int
    warning_count: int
    failed_tasks_count: int
    # Топ-3 стопнутых объявления: (ad_name, offer_code, rule_codes)
    top_stops: list[tuple[str, str | None, list[str]]] = field(default_factory=list)

    def has_signal(self) -> bool:
        """Есть ли повод для пульса: стоп, упавшая задача или шквал warnings."""
        return (
            self.stop_count > 0
            or self.failed_tasks_count > 0
            or self.warning_count >= _WARNINGS_ALONE_THRESHOLD
        )


async def collect_pulse_signals(
    engine: AsyncEngine,
    *,
    since: datetime,
    now: datetime,
) -> PulseSignals | None:
    """Собрать сигналы окна [since, now). None — сигналов нет, пульс молчит.

    alert_events партиционирована по created_at — фильтр по окну обязателен
    (partition pruning).
    """
    async with engine.connect() as conn:
        counts = (
            await conn.execute(
                text(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE stage = 'stop')    AS stops,
                        COUNT(*) FILTER (WHERE stage = 'warning') AS warnings
                    FROM alert_events
                    WHERE created_at >= :since AND created_at < :now
                    """
                ),
                {"since": since, "now": now},
            )
        ).first()
        stops = int(counts[0] or 0) if counts else 0
        warnings = int(counts[1] or 0) if counts else 0

        failed = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM task_queue
                    WHERE status = 'failed'
                      AND updated_at >= :since AND updated_at < :now
                    """
                ),
                {"since": since, "now": now},
            )
        ).scalar()

        top_rows = (
            await conn.execute(
                text(
                    """
                    SELECT a.ad_name, o.code, e.matched_rule_codes
                    FROM alert_events e
                    JOIN fb_ads a ON a.id = e.ad_id
                    JOIN fb_adsets ads ON ads.id = a.adset_id
                    JOIN fb_campaigns c ON c.id = ads.campaign_id
                    LEFT JOIN offers o ON o.id = c.offer_id
                    WHERE e.created_at >= :since AND e.created_at < :now
                      AND e.stage = 'stop'
                    ORDER BY e.created_at DESC
                    LIMIT 3
                    """
                ),
                {"since": since, "now": now},
            )
        ).all()

    signals = PulseSignals(
        window_start_utc=since,
        window_end_utc=now,
        stop_count=stops,
        warning_count=warnings,
        failed_tasks_count=int(failed or 0),
        top_stops=[
            (str(r[0] or "?"), str(r[1]) if r[1] else None, list(r[2] or [])) for r in top_rows
        ],
    )
    return signals if signals.has_signal() else None


def _facts_text(signals: PulseSignals) -> str:
    """Факты окна для промпта / детерминированного фолбэка."""
    lines = [
        f"Окно: {signals.window_start_utc:%H:%M}–{signals.window_end_utc:%H:%M} UTC",
        f"Остановлено объявлений: {signals.stop_count}, предупреждений: {signals.warning_count}, "
        f"упавших задач: {signals.failed_tasks_count}",
    ]
    for ad_name, offer, rules in signals.top_stops:
        offer_part = f" · оффер {offer}" if offer else ""
        reasons = ", ".join(rule_label(code).lower() for code in rules)
        reason_part = f" · причина: {reasons}" if reasons else ""
        lines.append(f"Остановлено объявление {ad_name}{offer_part}{reason_part}")
    return "\n".join(lines)


def _normalize_pulse_text(value: str) -> str:
    """Убрать из AI-ответа внутренние коды и машинную лексику.

    Модель иногда буквально повторяет ``1 STOP`` и ``(cpr_stop)`` из старого
    prompt/input. Пульс — операторский текст, поэтому технические коды здесь не
    несут пользы и должны быть нормализованы детерминированно.
    """
    text_value = re.sub(r"^\s*Требуется действие:\s*", "", value, flags=re.IGNORECASE)
    text_value = re.sub(
        r"\bобнаружен(?:о)?\s+1\s+STOP\b",
        "остановлено 1 объявление",
        text_value,
        flags=re.IGNORECASE,
    )
    text_value = re.sub(
        r"\b([\w.-]+)\s+\[([^\]]+)\]",
        r"\1 · оффер \2",
        text_value,
    )
    for code, label in RULE_LABELS.items():
        text_value = re.sub(
            rf"\(\s*{re.escape(code)}\s*\)",
            f"— причина: {label.lower()}",
            text_value,
            flags=re.IGNORECASE,
        )
        text_value = re.sub(
            rf"\b{re.escape(code)}\b",
            label.lower(),
            text_value,
            flags=re.IGNORECASE,
        )
    text_value = re.sub(r"\bSTOP\b", "остановка", text_value, flags=re.IGNORECASE)
    text_value = re.sub(r"\bWARNING\b", "предупреждение", text_value, flags=re.IGNORECASE)
    text_value = text_value.strip()
    return text_value[:1].upper() + text_value[1:] if text_value else ""


def _fallback_text(signals: PulseSignals, *, html: bool = True) -> str:
    """Детерминированный пульс без AI — сигнал важнее красоты."""
    header = "📟 <b>Пульс кабинета</b>\n" if html else ""
    return header + _facts_text(signals)


async def build_pulse(
    engine: AsyncEngine,
    *,
    since: datetime,
    now: datetime,
    html: bool = True,
) -> str | None:
    """Собрать текст пульса. None — сигналов нет, слать нечего.

    html=True — Telegram-формат (заголовок с <b>); html=False — чистый текст
    для веб-виджета (заголовок рисует сам виджет).
    Никогда не бросает: при ошибке AI возвращает детерминированный фолбэк.
    """
    signals = await collect_pulse_signals(engine, since=since, now=now)
    if signals is None:
        return None

    settings = get_settings()
    client = get_ai_client(settings)
    if not client.is_available:
        return _fallback_text(signals, html=html)

    try:
        system = load_skill("pulse_report")
    except PromptNotFoundError:
        system = "Напиши короткий отчёт о состоянии рекламного кабинета по фактам."
    if not html:
        system += (
            "\n\nКанал этого ответа — веб-интерфейс. Верни только plain text: "
            "без HTML-тегов и без Markdown-разметки."
        )

    try:
        response = await asyncio.wait_for(
            client.chat(
                messages=[{"role": "user", "content": _facts_text(signals)}],
                system=system,
                max_tokens=300,
            ),
            timeout=float(settings.ai_timeout_seconds),
        )
        ai_text = (response.text or "").strip()
    except (TimeoutError, asyncio.TimeoutError, AIUnavailableError) as exc:
        logger.warning("pulse: AI недоступен (%s) — детерминированный фолбэк", exc)
        return _fallback_text(signals, html=html)
    except Exception:  # noqa: BLE001
        logger.warning("pulse: ошибка провайдера — детерминированный фолбэк", exc_info=True)
        return _fallback_text(signals, html=html)

    if not html:
        # Модель может проигнорировать формат канала и вернуть Telegram HTML.
        # React намеренно рендерит ответ как текст, поэтому без очистки оператор
        # видел буквальные <b>...</b> в почасовом пульсе.
        ai_text = html_to_plain_text(ai_text)
    ai_text = _normalize_pulse_text(ai_text)
    if not ai_text:
        return _fallback_text(signals, html=html)
    return f"📟 <b>Пульс кабинета</b>\n{ai_text}" if html else ai_text


__all__ = ["PulseSignals", "build_pulse", "collect_pulse_signals"]
