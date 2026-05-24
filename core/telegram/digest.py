# -*- coding: utf-8 -*-
"""Формирование текста daily digest для Telegram."""

from __future__ import annotations

from decimal import Decimal


def render_digest_message(data: dict, tz: str = "Europe/Moscow") -> str:
    """Рендерит HTML-сообщение daily digest.

    Args:
        data: Словарь из get_digest_data (top_offers, wasted_alerts, new_offers, totals, date_str).
        tz:   Имя временной зоны (для отображения).

    Returns:
        HTML-строка для отправки в Telegram (parse_mode=HTML).
    """
    date_str = data.get("date_str", "—")
    top_offers: list[dict] = data.get("top_offers", [])
    wasted_alerts: int = data.get("wasted_alerts", 0)
    new_offers: list[str] = data.get("new_offers", [])
    totals: dict = data.get("totals", {})

    total_spend = totals.get("spend", Decimal("0"))

    lines: list[str] = []

    # Заголовок
    lines.append(f"📊 <b>Daily digest — {date_str}</b>")
    lines.append("")

    # Нет активности за день
    if not top_offers and total_spend == 0:
        lines.append("😴 Активности за день не было.")
        _append_footer(lines, wasted_alerts, new_offers)
        return "\n".join(lines)

    # Топ офферов
    if top_offers:
        lines.append("🏆 <b>Топ офферов по spend:</b>")
        for i, offer in enumerate(top_offers, 1):
            code = offer.get("code", "?")
            spend = offer.get("spend", Decimal("0"))
            leads = offer.get("leads", 0)
            deps = offer.get("deps", 0)
            delta_pct = offer.get("delta_pct")

            # Форматируем delta
            if delta_pct is not None:
                arrow = "▲" if delta_pct >= 0 else "▼"
                delta_str = f" ({arrow} {abs(delta_pct):.0f}% vs вчера)"
            else:
                delta_str = ""

            spend_str = f"${float(spend):.0f}" if spend >= 1 else f"${float(spend):.2f}"
            lines.append(
                f"{i}. <b>{code}</b> · spend {spend_str}{delta_str} · {leads} lead · {deps} dep"
            )
        lines.append("")

    # Сводка дня
    lines.append("💰 <b>Итого за день:</b>")
    spend_total = float(total_spend)
    spend_fmt = f"${spend_total:.0f}" if spend_total >= 1 else f"${spend_total:.2f}"
    total_leads = totals.get("leads", 0)
    total_deps = totals.get("deps", 0)
    lines.append(f"spend {spend_fmt} · {total_leads} lead · {total_deps} dep")
    lines.append("")

    _append_footer(lines, wasted_alerts, new_offers)

    return "\n".join(lines)


def _append_footer(lines: list[str], wasted_alerts: int, new_offers: list[str]) -> None:
    """Добавляет блоки «алёрты впустую» и «новые офферы» если есть данные."""
    if wasted_alerts > 0:
        noun = _alerts_noun(wasted_alerts)
        lines.append(f"🤷 {wasted_alerts} {noun} проигнорированы вручную")

    if new_offers:
        codes_str = ", ".join(new_offers)
        word = "оффер" if len(new_offers) == 1 else ("оффера" if len(new_offers) < 5 else "офферов")
        lines.append(f"🆕 +{len(new_offers)} новых {word}: {codes_str}")


def _alerts_noun(n: int) -> str:
    """Склонение слова «алёрт» для числа n."""
    if 11 <= (n % 100) <= 14:
        return "алёртов"
    rem = n % 10
    if rem == 1:
        return "алёрт"
    if 2 <= rem <= 4:
        return "алёрта"
    return "алёртов"
