# -*- coding: utf-8 -*-
"""Рендер daily digest в HTML-строку для Telegram (parse_mode=HTML).

Pure-функция без I/O — принимает `DigestPayload`, возвращает строку.
"""

from __future__ import annotations

import html
from decimal import Decimal
from typing import Any

from core.telegram.digest_builder import DigestPayload, TopAdRow


def _escape(s: str | None) -> str:
    """HTML-escape для безопасной вставки строк."""
    return html.escape(s or "", quote=False)


def _fmt_money(value: Decimal | None) -> str:
    """Форматирует Decimal как сумму в USD: 1234.5 → '$1 234.50'."""
    if value is None:
        return "—"
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
    except (ArithmeticError, ValueError):
        return "—"
    # Округляем до 2 знаков, разделитель тысяч — неразрывный пробел.
    quantized = d.quantize(Decimal("0.01"))
    int_part, _, frac_part = f"{quantized:.2f}".partition(".")
    sign = ""
    if int_part.startswith("-"):
        sign = "-"
        int_part = int_part[1:]
    rev = int_part[::-1]
    chunks = [rev[i : i + 3] for i in range(0, len(rev), 3)]
    grouped = " ".join(chunks)[::-1]
    return f"{sign}${grouped}.{frac_part}"


def _fmt_decimal(value: Decimal | None, precision: int = 3) -> str:
    """Decimal/None → '0.123' либо '—'."""
    if value is None:
        return "—"
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
    except (ArithmeticError, ValueError):
        return "—"
    return f"{d:.{precision}f}"


def _fmt_int(value: Any) -> str:
    """int/None → '1 234' с неразрывным пробелом, '—' для None."""
    if value is None:
        return "—"
    try:
        n = int(value)
    except (TypeError, ValueError):
        return str(value)
    if n == 0:
        return "0"
    rev = str(abs(n))[::-1]
    chunks = [rev[i : i + 3] for i in range(0, len(rev), 3)]
    grouped = " ".join(chunks)[::-1]
    return ("-" if n < 0 else "") + grouped


def _fmt_date_utc(payload: DigestPayload) -> str:
    """Заголовочная дата окна — день начала окна в UTC."""
    return payload.window_start_utc.strftime("%Y-%m-%d")


def _render_top_ad_line(idx: int, row: TopAdRow) -> str:
    """Одна строка из топа: <code>OFFER</code> · spend · CPC · CPL · название."""
    offer = f"<code>{_escape(row.offer_code)}</code>" if row.offer_code else "—"
    name = _escape(row.ad_name) or "(без названия)"
    if len(name) > 60:
        name = name[:57] + "..."
    return (
        f"  {idx}. {offer} · "
        f"spend <b>{_fmt_money(row.spend_usd)}</b> · "
        f"CPC {_fmt_decimal(row.cpc, 3)} · "
        f"CPL {_fmt_decimal(row.cost_per_lead, 2)} · "
        f"<i>{name}</i>"
    )


def render_digest(payload: DigestPayload) -> str:
    """Полный HTML-текст digest для отправки в Telegram.

    Лейаут: заголовок → алерты → топ-5 spend → disable-метрики → итоги.
    Если за окно вообще не было spend и алертов — отдельный «тихий» блок.
    """
    lines: list[str] = []

    lines.append(f"📊 <b>Daily digest — {_fmt_date_utc(payload)}</b>")
    lines.append(f"<i>окно 24ч до {payload.window_end_utc.strftime('%Y-%m-%d %H:%M UTC')}</i>")
    lines.append("")

    has_activity = (
        payload.alerts_warning_count
        or payload.alerts_stop_count
        or payload.top_ads_by_spend
        or payload.total_spend_24h_usd > 0
    )

    # Алерты
    lines.append("🔔 <b>Алерты:</b>")
    lines.append(
        f"  ⚠️ WARNING: <b>{_fmt_int(payload.alerts_warning_count)}</b>"
        f"  ·  🛑 STOP: <b>{_fmt_int(payload.alerts_stop_count)}</b>"
    )
    lines.append("")

    # Топ-5 spend
    lines.append("🏆 <b>Топ-5 по spend:</b>")
    if payload.top_ads_by_spend:
        for idx, row in enumerate(payload.top_ads_by_spend, 1):
            lines.append(_render_top_ad_line(idx, row))
    else:
        lines.append("  (нет данных за окно)")
    lines.append("")

    # Disable tasks
    lines.append("🔧 <b>Отключения:</b>")
    lines.append(
        f"  ✅ успешно: <b>{_fmt_int(payload.disable_tasks_succeeded)}</b>"
        f"  ·  ❌ с ошибкой: <b>{_fmt_int(payload.disable_tasks_failed)}</b>"
    )
    lines.append("")

    # Итоги
    lines.append("📈 <b>Итого:</b>")
    lines.append(f"  spend 24ч: <b>{_fmt_money(payload.total_spend_24h_usd)}</b>")
    lines.append(
        f"  активных офферов: <b>{_fmt_int(payload.active_offers_count)}</b>"
        f"  ·  активных ads (normal): <b>{_fmt_int(payload.active_ads_count)}</b>"
    )

    if not has_activity:
        lines.append("")
        lines.append("😴 За окно не было активности.")

    return "\n".join(lines)


__all__ = ["render_digest"]
