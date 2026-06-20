# -*- coding: utf-8 -*-
"""Рендер daily digest в HTML-строку для Telegram — стиль «чистая карточка».

Pure-функция без I/O — принимает `DigestPayload`, возвращает строку (parse_mode=HTML).

Лейаут:
  📊 Дайджест · 2026-05-26               ← заголовок + дата окна
  окно 24ч до 2026-05-27 09:00 UTC
  <пусто>
  🔔 Алерты      ⚠️ 12 · 🛑 3            ← компактные сводки одной строкой
  🔧 Отключения  ✅ 4 · ❌ 1
  📈 Итого       спенд $1 234.50 · офферов 7 · ads 42
  <пусто>
  🏆 Топ-5 по spend                       ← выровненная моноширинная таблица
  <pre># Оффер Spend CPC CPL Объявление</pre>

Числа, выравнивание и экранирование — через core.telegram.format.
"""

from __future__ import annotations

from core.telegram import format as fmt
from core.telegram.digest_builder import DigestPayload, TopAdRow

# Максимальная длина названия объявления в таблице (моноширинный столбец).
_AD_NAME_MAX = 24


def _fmt_date_utc(payload: DigestPayload) -> str:
    """Заголовочная дата окна — день начала окна в UTC."""
    return payload.window_start_utc.strftime("%Y-%m-%d")


def _top_table(rows: list[TopAdRow]) -> str:
    """Топ-объявления по spend → выровненная таблица в <pre>."""
    table_rows: list[list[str]] = []
    for idx, row in enumerate(rows, 1):
        table_rows.append(
            [
                str(idx),
                row.offer_code or "—",
                fmt.money(row.spend_usd),
                fmt.dec(row.cpc, 3),
                fmt.dec(row.cost_per_lead, 2),
                fmt.truncate(row.ad_name or "(без названия)", _AD_NAME_MAX),
            ]
        )
    return fmt.table(
        ["#", "Оффер", "Spend", "CPC", "CPL", "Объявление"],
        table_rows,
        aligns=["l", "l", "r", "r", "r", "l"],
    )


def render_digest(payload: DigestPayload) -> str:
    """Полный HTML-текст digest для отправки в Telegram.

    Лейаут: заголовок → сводки (алерты/отключения/итоги) → топ-5 spend.
    Если за окно не было spend и алертов — добавляем «тихий» блок внизу.
    """
    lines: list[str] = [
        f"📊 {fmt.b('Дайджест')} · {fmt.b(_fmt_date_utc(payload))}",
        fmt.i(f"окно 24ч до {payload.window_end_utc.strftime('%Y-%m-%d %H:%M UTC')}"),
        "",
    ]

    # Активность считаем по алертам и реальному spend; непустой топ из нулей —
    # НЕ активность (урок бага дайджеста 06-02), иначе «тихий» блок прячется.
    has_activity = bool(
        payload.alerts_warning_count
        or payload.alerts_stop_count
        or payload.total_spend_window_usd > 0
    )

    # Сводки — по одной строке, метка жирным.
    lines.append(
        f"🔔 {fmt.b('Алерты')}   "
        f"⚠️ {fmt.b(fmt.num(payload.alerts_warning_count))} · "
        f"🛑 {fmt.b(fmt.num(payload.alerts_stop_count))}"
    )
    lines.append(
        f"🔧 {fmt.b('Отключения')}   "
        f"✅ {fmt.b(fmt.num(payload.disable_tasks_succeeded))} · "
        f"❌ {fmt.b(fmt.num(payload.disable_tasks_failed))}"
    )
    lines.append(
        f"📈 {fmt.b('Итого')}   "
        f"спенд {fmt.b(fmt.money(payload.total_spend_window_usd))} · "
        f"офферов {fmt.b(fmt.num(payload.active_offers_count))} · "
        f"ads {fmt.b(fmt.num(payload.active_ads_count))}"
    )
    lines.append("")

    # Топ-5 spend.
    lines.append(f"🏆 {fmt.b('Топ-5 по spend')}")
    if payload.top_ads_by_spend:
        lines.append(_top_table(payload.top_ads_by_spend))
    else:
        lines.append("(нет данных за окно)")

    if not has_activity:
        lines.append("")
        lines.append("😴 За окно не было активности.")

    return "\n".join(lines)


__all__ = ["render_digest"]
