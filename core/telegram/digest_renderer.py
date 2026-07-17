# -*- coding: utf-8 -*-
"""Рендер daily digest как Telegram Rich Message.

Pure-функция без I/O — принимает `DigestPayload`, возвращает Rich HTML.

Лейаут:
  <h1>📊 Дайджест · 2026-05-26</h1>
  <footer>окно 24ч ...</footer>
  <table>сводка и топ-5</table>

Числа, выравнивание и экранирование — через core.telegram.format.
"""

from __future__ import annotations

from core.telegram import format as fmt
from core.telegram.digest_builder import DigestPayload, TopAdRow

# Максимальная длина названия объявления в компактной таблице.
_AD_NAME_MAX = 24


def _fmt_date_utc(payload: DigestPayload) -> str:
    """Заголовочная дата окна — день начала окна в UTC."""
    return payload.window_start_utc.strftime("%Y-%m-%d")


def _top_table(rows: list[TopAdRow]) -> str:
    """Топ-объявления по spend → нативная Rich Message таблица."""
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
        fmt.heading(f"📊 Дайджест · {_fmt_date_utc(payload)}", 1),
        fmt.footer(f"Окно 24ч до {payload.window_end_utc.strftime('%Y-%m-%d %H:%M UTC')}"),
        fmt.divider(),
        fmt.heading("Сводка", 3),
    ]

    # Активность считаем по алертам и реальному spend; непустой топ из нулей —
    # НЕ активность (урок бага дайджеста 06-02), иначе «тихий» блок прячется.
    has_activity = bool(
        payload.alerts_warning_count
        or payload.alerts_stop_count
        or payload.total_spend_window_usd > 0
    )

    lines.append(
        fmt.kv_grid(
            [
                [
                    (
                        "🔔 Алерты",
                        f"⚠️ {fmt.num(payload.alerts_warning_count)} · "
                        f"🛑 {fmt.num(payload.alerts_stop_count)}",
                    )
                ],
                [
                    (
                        "🔧 Отключения",
                        f"✅ {fmt.num(payload.disable_tasks_succeeded)} · "
                        f"❌ {fmt.num(payload.disable_tasks_failed)}",
                    )
                ],
                [("💵 Спенд", fmt.money(payload.total_spend_window_usd))],
                [
                    ("Офферы", fmt.num(payload.active_offers_count)),
                    ("Объявления", fmt.num(payload.active_ads_count)),
                ],
            ]
        )
    )
    lines.append(fmt.divider())

    # Топ-5 spend.
    lines.append(fmt.heading("🏆 Топ-5 по spend", 3))
    if payload.top_ads_by_spend:
        lines.append(_top_table(payload.top_ads_by_spend))
    else:
        lines.append(fmt.footer("Нет данных за окно"))

    if not has_activity:
        lines.append(fmt.details("😴 Тихое окно", "За окно не было активности."))

    return "\n".join(lines)


__all__ = ["render_digest"]
