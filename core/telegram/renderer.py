# -*- coding: utf-8 -*-
"""Форматирование TG-сообщений для алертов observer.

Pure-функции без I/O — принимают данные, возвращают (text, inline_keyboard).
Использует HTML parse_mode (это default для TelegramBotClient).
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class AlertRenderInput:
    """Минимум данных нужный чтобы отрендерить алерт."""

    fb_ad_id: str
    ad_name: str
    campaign_name: str
    adset_name: str
    offer_code: str | None
    stage: str  # 'warning' | 'stop'
    matched_rule_codes: list[str]
    metrics: dict[str, Any]
    open_state_token: str | None  # для callback кнопок


# Эмодзи маркеры — быстрый визуальный сигнал severity (русские).
_STAGE_PREFIX = {
    "warning": "⚠️ <b>ПРЕДУПРЕЖДЕНИЕ</b>",
    "stop": "🛑 <b>СТОП</b>",
}

# Человекочитаемые названия правил (коды из core/rules/evaluator.py).
_RULE_LABELS = {
    "cpc_stop": "Цена за клик (CPC) превысила порог",
    "cpl_stop": "Цена за лид (CPL) превысила порог",
    "cpr_stop": "Цена за регистрацию (CPR) превысила порог",
    "spend_no_dep_range": "Расход без депозитов достиг стоп-диапазона",
    "spend_with_dep_range": "Расход при наличии депозита превысил порог",
    "frequency_anomaly": "Частота показов превысила порог (выгорание аудитории)",
    "regs_no_dep_stop": "Есть регистрации, но нет депозитов",
}


def _rule_label(code: str) -> str:
    """Человекочитаемое название правила по коду (fallback — сам код)."""
    return _RULE_LABELS.get(code, code)


def _fmt_decimal(v: Any, precision: int = 2) -> str:
    """Decimal/float/None → '12.34' или '—'."""
    if v is None:
        return "—"
    try:
        d = v if isinstance(v, Decimal) else Decimal(str(v))
        # quantize чтобы не выводить .000000000001 шум
        if precision == 0:
            return f"{int(d):,}".replace(",", " ")
        return f"{d:.{precision}f}"
    except (ValueError, ArithmeticError):
        return str(v)


def _fmt_int(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(v):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(v)


def _fmt_money(v: Any, precision: int = 2) -> str:
    """Денежная метрика → '$0.10' или '—'."""
    s = _fmt_decimal(v, precision)
    return s if s == "—" else f"${s}"


def _escape(s: str) -> str:
    """HTML escape для безопасной вставки в TG-сообщение."""
    return html.escape(s or "", quote=False)


def render_alert_text(inp: AlertRenderInput) -> str:
    """Полный текст TG-сообщения для ПРЕДУПРЕЖДЕНИЯ/СТОП (человекочитаемый, русский)."""
    prefix = _STAGE_PREFIX.get(inp.stage, "ℹ️")

    lines = [
        f"{prefix}",
        f"<b>{_escape(inp.ad_name or 'без названия')}</b>",
    ]
    if inp.campaign_name:
        lines.append(f"Кампания: <i>{_escape(inp.campaign_name)}</i>")
    if inp.adset_name:
        lines.append(f"Адсет: <i>{_escape(inp.adset_name)}</i>")
    if inp.offer_code:
        lines.append(f"Оффер: <code>{_escape(inp.offer_code)}</code>")

    lines.append("")
    reason_header = "Причина остановки:" if inp.stage == "stop" else "Причина предупреждения:"
    lines.append(f"<b>{reason_header}</b>")
    if inp.matched_rule_codes:
        for code in inp.matched_rule_codes:
            lines.append(f"  • {_escape(_rule_label(code))}")
    else:
        lines.append("  (нет деталей)")

    lines.append("")
    lines.append("<b>Метрики:</b>")
    m = inp.metrics
    lines.append(f"  Расход: <b>{_fmt_money(m.get('spend'))}</b>")
    lines.append(
        f"  Цена клика: {_fmt_money(m.get('cpc'), 3)} · "
        f"CTR: {_fmt_decimal(m.get('ctr'), 2)}% · "
        f"CPM: {_fmt_money(m.get('cpm'))}"
    )
    lines.append(
        f"  Клики: {_fmt_int(m.get('clicks'))} · "
        f"Просмотры лендинга: {_fmt_int(m.get('landing_page_views'))} · "
        f"Лиды: {_fmt_int(m.get('leads'))}"
    )
    lines.append(
        f"  Регистрации: {_fmt_int(m.get('registrations'))} · "
        f"Депозиты: <b>{_fmt_int(m.get('deposits'))}</b>"
    )
    if m.get("frequency") is not None:
        lines.append(f"  Частота показов: {_fmt_decimal(m.get('frequency'), 2)}")

    lines.append("")
    lines.append(f"<code>ID: {_escape(inp.fb_ad_id)}</code>")

    return "\n".join(lines)


def render_inline_keyboard(inp: AlertRenderInput) -> dict | None:
    """Inline-клавиатура с кнопками действий.

    Callback data format: `<action>:<fb_ad_id>:<token>` где action одно из:
    - 'dis'   — отключить
    - 'snz'   — snooze на 2 часа
    - 'clm'   — claim (взять под контроль вручную)

    Telegram limit на callback_data = 64 bytes. Используем сокращения.
    """
    token_short = (inp.open_state_token or "")[:8]
    buttons: list[list[dict]] = []

    if inp.stage in ("warning", "stop"):
        buttons.append(
            [
                {
                    "text": "🛑 Отключить",
                    "callback_data": f"dis:{inp.fb_ad_id}:{token_short}",
                },
                {
                    "text": "💤 Снуз 2ч",
                    "callback_data": f"snz:{inp.fb_ad_id}:{token_short}",
                },
            ]
        )
    if not buttons:
        return None
    return {"inline_keyboard": buttons}


# Для совместимости с TelegramBotClient.send_message (parse_mode='Markdown' по умолчанию)
# рекомендуется передавать parse_mode='HTML' в caller'е.
DEFAULT_PARSE_MODE = "HTML"


__all__ = [
    "AlertRenderInput",
    "DEFAULT_PARSE_MODE",
    "render_alert_text",
    "render_inline_keyboard",
]
