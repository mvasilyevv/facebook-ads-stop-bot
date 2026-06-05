# -*- coding: utf-8 -*-
"""Форматирование TG-сообщений для алертов observer (минимал-стиль).

Pure-функции без I/O — принимают данные, возвращают (text, inline_keyboard).
HTML parse_mode (default для TelegramBotClient передаётся явно в dispatcher).

Минимал-формат: заголовок (stage · оффер), кампания, причина(ы) с фактическим
значением и порогом, одна строка ключевых метрик. Без дубля ad_name/adset и без
технического ID-хвоста.
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


# Заголовок по stage.
_STAGE_HEAD = {
    "warning": "⚠️ ПРЕДУПРЕЖДЕНИЕ",
    "stop": "🛑 СТОП",
}

# code правила → (короткая подпись, единица значения/порога).
_RULE_SHORT: dict[str, tuple[str, str]] = {
    "cpc_stop": ("CPC", "money"),
    "cpl_stop": ("CPL", "money"),
    "cpr_stop": ("CPR", "money"),
    "spend_no_dep_range": ("Расход/CPA", "percent"),
    "spend_with_dep_range": ("Расход/CPA", "percent"),
    "frequency_anomaly": ("Частота", "ratio"),
    "regs_no_dep_stop": ("Рег без деп", "count"),
}

# Fallback-подписи (когда нет _hits с числами — старые события).
_RULE_LABELS = {
    "cpc_stop": "CPC превысил порог",
    "cpl_stop": "CPL превысил порог",
    "cpr_stop": "CPR превысил порог",
    "spend_no_dep_range": "Расход без депозитов в стоп-зоне",
    "spend_with_dep_range": "Расход при депозите превысил порог",
    "frequency_anomaly": "Частота показов: выгорание аудитории",
    "regs_no_dep_stop": "Регистрации есть, депозитов нет",
}


def _escape(s: str) -> str:
    """HTML escape для безопасной вставки в TG-сообщение."""
    return html.escape(s or "", quote=False)


def _to_decimal(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        return v if isinstance(v, Decimal) else Decimal(str(v))
    except (ValueError, ArithmeticError):
        return None


def _fmt_money(v: Any) -> str:
    d = _to_decimal(v)
    return f"${d:.2f}" if d is not None else "—"


def _fmt_decimal(v: Any, precision: int = 2) -> str:
    d = _to_decimal(v)
    return f"{d:.{precision}f}" if d is not None else "—"


def _fmt_int(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(v):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(v)


def _fmt_unit(v: Any, unit: str) -> str:
    """Значение/порог в нужной единице: money/percent/ratio/count."""
    d = _to_decimal(v)
    if d is None:
        return "—"
    if unit == "money":
        return f"${d:.2f}"
    if unit == "percent":
        return f"{d:.0f}%"
    if unit == "ratio":
        return f"{d:.2f}"
    if unit == "count":
        return f"{int(d)}"
    return f"{d}"


def _format_hit(hit: dict[str, Any]) -> str:
    """Одно сработавшее правило → 'CPL $9.56 (стоп $3.00)'."""
    code = str(hit.get("code") or "")
    short, unit = _RULE_SHORT.get(code, (code or "правило", "raw"))
    value = _fmt_unit(hit.get("value"), unit)
    threshold = _fmt_unit(hit.get("threshold"), unit)
    return f"{short} {value} (стоп {threshold})"


def _reason_lines(inp: AlertRenderInput) -> list[str]:
    """Строки причины: из _hits с числами; fallback — текстовые подписи по кодам."""
    hits = [
        h
        for h in (inp.metrics.get("_hits") or [])
        if isinstance(h, dict) and str(h.get("stage")) == inp.stage
    ]
    if hits:
        return [_escape(_format_hit(h)) for h in hits]
    if inp.matched_rule_codes:
        return [_escape(_RULE_LABELS.get(c, c)) for c in inp.matched_rule_codes]
    return ["сработало стоп-правило"]


def _metrics_line(m: dict[str, Any]) -> str:
    """Ключевые метрики одной строкой: расход · деп · рег · клики · CTR."""
    parts = [
        _fmt_money(m.get("spend")),
        f"деп {_fmt_int(m.get('deposits'))}",
        f"рег {_fmt_int(m.get('registrations'))}",
        f"клики {_fmt_int(m.get('clicks'))}",
    ]
    if m.get("ctr") is not None:
        parts.append(f"CTR {_fmt_decimal(m.get('ctr'), 2)}%")
    return " · ".join(parts)


def render_alert_text(inp: AlertRenderInput) -> str:
    """Минимал-текст TG-сообщения для ПРЕДУПРЕЖДЕНИЯ/СТОП (русский, HTML)."""
    head = _STAGE_HEAD.get(inp.stage, "ℹ️ АЛЕРТ")
    title = inp.offer_code or inp.ad_name or "без названия"

    lines = [f"<b>{_escape(head)} · {_escape(title)}</b>"]
    if inp.campaign_name:
        lines.append(f"<i>{_escape(inp.campaign_name)}</i>")

    lines.append("")
    lines.extend(_reason_lines(inp))
    lines.append(_metrics_line(inp.metrics))

    return "\n".join(lines)


def render_inline_keyboard(inp: AlertRenderInput) -> dict | None:
    """Inline-клавиатура с кнопками действий.

    Callback data format: `<action>:<fb_ad_id>:<token>` где action одно из:
    - 'dis'   — отключить
    - 'snz'   — snooze на 2 часа

    Telegram limit на callback_data = 64 bytes. Используем сокращения (token[:8]).
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


# Caller (alert_dispatcher) передаёт parse_mode='HTML' явно.
DEFAULT_PARSE_MODE = "HTML"


__all__ = [
    "AlertRenderInput",
    "DEFAULT_PARSE_MODE",
    "render_alert_text",
    "render_inline_keyboard",
]
