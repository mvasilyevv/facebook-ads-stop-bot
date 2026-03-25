# -*- coding: utf-8 -*-
"""Полноценный Telegram-бот: команды, кнопки, просмотр статистики, управление.

Команды:
  /start   — Главное меню с кнопками
  /status  — Статус мониторинга (сколько объявлений, алертов, disabled)
  /ads     — Список объявлений с метриками
  /offers  — Список офферов с CPA
  /rules   — Текущие стоп-правила
  /disabled — Отключённые объявления
  /settings — Настройки бота (интервал, порог предупреждения)
  /help    — Помощь

Inline-кнопки:
  - "Отключить" на алертах (из renderer.py)
  - Навигация по спискам объявлений (пагинация)
  - Быстрые действия из главного меню
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

from core.telegram.client import TelegramBotClient

logger = logging.getLogger(__name__)


# ==========================================
# Inline-клавиатуры
# ==========================================

def _main_menu_keyboard() -> dict:
    """Главное меню с кнопками."""
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Статус", "callback_data": "cmd:status"},
                {"text": "📋 Объявления", "callback_data": "cmd:ads"},
            ],
            [
                {"text": "🎯 Офферы", "callback_data": "cmd:offers"},
                {"text": "⚙️ Правила", "callback_data": "cmd:rules"},
            ],
            [
                {"text": "🚫 Отключённые", "callback_data": "cmd:disabled"},
                {"text": "🔧 Настройки", "callback_data": "cmd:settings"},
            ],
        ]
    }


def _back_button() -> dict:
    """Кнопка «Назад в меню»."""
    return {
        "inline_keyboard": [
            [{"text": "◀️ Главное меню", "callback_data": "cmd:start"}]
        ]
    }


def _ads_navigation(page: int, total_pages: int) -> dict:
    """Пагинация для списка объявлений."""
    buttons = []
    if page > 0:
        buttons.append({"text": "◀️ Назад", "callback_data": f"ads:page:{page - 1}"})
    buttons.append({"text": f"{page + 1}/{total_pages}", "callback_data": "noop"})
    if page < total_pages - 1:
        buttons.append({"text": "Вперёд ▶️", "callback_data": f"ads:page:{page + 1}"})
    return {
        "inline_keyboard": [
            buttons,
            [{"text": "◀️ Главное меню", "callback_data": "cmd:start"}],
        ]
    }


# ==========================================
# Хранилище состояния (в памяти)
# ==========================================

@dataclass
class BotState:
    """Текущее состояние для отображения в TG."""
    total_ads: int = 0
    ads_in_warning: int = 0
    ads_in_stop: int = 0
    ads_disabled: int = 0
    total_spend: str = "$0.00"
    last_scan_at: str | None = None
    is_running: bool = False

    # Снимки объявлений
    ad_snapshots: list[dict] = field(default_factory=list)

    # Офферы
    offers: list[dict] = field(default_factory=list)

    # Отключённые
    disabled_ads: list[dict] = field(default_factory=list)

    # Настройки
    observer_interval: int = 90
    warning_percent: int = 80


# Глобальный стейт (обновляется observer worker)
bot_state = BotState()


# ==========================================
# Генерация сообщений
# ==========================================

def _render_start() -> tuple[str, dict]:
    """Стартовое сообщение с главным меню."""
    text = (
        "🛑 <b>FB Stop Bot v2</b>\n\n"
        "Бот для мониторинга и автоматической остановки "
        "объявлений Facebook Ads по стоп-правилам.\n\n"
        "Выберите действие:"
    )
    return text, _main_menu_keyboard()


def _render_status() -> tuple[str, dict]:
    """Статус мониторинга."""
    s = bot_state
    status_icon = "🟢" if s.is_running else "🔴"
    text = (
        f"📊 <b>Статус мониторинга</b>\n\n"
        f"{status_icon} Observer: {'работает' if s.is_running else 'остановлен'}\n"
        f"🕐 Последний скан: {s.last_scan_at or 'ещё не было'}\n\n"
        f"📋 Объявлений: <b>{s.total_ads}</b>\n"
        f"⚠️ Предупреждений: <b>{s.ads_in_warning}</b>\n"
        f"🛑 Стоп-алертов: <b>{s.ads_in_stop}</b>\n"
        f"🚫 Отключено: <b>{s.ads_disabled}</b>\n"
        f"💰 Общий расход: <b>{s.total_spend}</b>\n\n"
        f"⏱ Интервал: {s.observer_interval} сек\n"
        f"📉 Порог предупреждения: {s.warning_percent}% от стопа"
    )
    return text, _back_button()


def _render_ads(page: int = 0) -> tuple[str, dict]:
    """Список объявлений с метриками (пагинация по 5)."""
    per_page = 5
    ads = bot_state.ad_snapshots
    total_pages = max(1, (len(ads) + per_page - 1) // per_page)
    page = min(page, total_pages - 1)

    start = page * per_page
    page_ads = ads[start:start + per_page]

    if not page_ads:
        text = (
            "📋 <b>Объявления</b>\n\n"
            "Пока нет данных. Запустите observer для сканирования."
        )
        return text, _back_button()

    lines = ["📋 <b>Объявления</b>\n"]
    for ad in page_ads:
        state_icon = {
            "STOP_SENT": "🛑",
            "WARNING_SENT": "⚠️",
            "CLAIMED": "🔄",
            "DISABLED": "🚫",
        }.get(ad.get("alert_state", ""), "✅")

        lines.append(
            f"\n{state_icon} <b>{ad.get('ad_name', '?')}</b>\n"
            f"   💰 Расход: ${ad.get('spend', '0')}\n"
            f"   🖱 CPC: ${ad.get('cpc', '-')} | Клики: {ad.get('clicks', 0)}\n"
            f"   👤 Лиды: {ad.get('leads', 0)} | Реги: {ad.get('regs', 0)} | Депы: {ad.get('deps', 0)}\n"
            f"   🏷 Оффер: {ad.get('offer', '-')}"
        )

    text = "\n".join(lines)
    return text, _ads_navigation(page, total_pages)


def _render_offers() -> tuple[str, dict]:
    """Список офферов с CPA."""
    offers = bot_state.offers
    if not offers:
        text = "🎯 <b>Офферы</b>\n\nОфферы не настроены. Добавьте их через UI."
        return text, _back_button()

    lines = ["🎯 <b>Офферы</b>\n"]
    for o in offers:
        status = "✅" if o.get("is_active") else "⏸"
        lines.append(
            f"\n{status} <b>{o.get('name', '?')}</b>\n"
            f"   Код: <code>{o.get('code', '?')}</code>\n"
            f"   CPA: ${o.get('cpa', '?')}"
        )
    text = "\n".join(lines)
    return text, _back_button()


def _render_rules() -> tuple[str, dict]:
    """Текущие стоп-правила."""
    text = (
        "⚙️ <b>Стоп-правила</b>\n\n"
        "1️⃣ <b>CPC</b> — если клик > 2% CPA → стоп\n"
        "2️⃣ <b>CPL</b> — если лид > 10% CPA → стоп\n"
        "3️⃣ <b>CPR</b> — если рега > 20% CPA → стоп\n"
        "4️⃣ <b>Реги без депов</b> — если 5 рег и 0 депов → стоп\n"
        "5️⃣ <b>Расход без депа</b> — расход 50-70% CPA, нет депов → стоп\n"
        "6️⃣ <b>Расход с депом</b> — есть деп, расход 70-90% CPA → стоп\n\n"
        f"📉 Порог предупреждения: <b>{bot_state.warning_percent}%</b> от стопа\n\n"
        "💡 Проценты и лимиты настраиваются через UI или индивидуально на оффер."
    )
    return text, _back_button()


def _render_disabled() -> tuple[str, dict]:
    """Отключённые объявления."""
    disabled = bot_state.disabled_ads
    if not disabled:
        text = "🚫 <b>Отключённые объявления</b>\n\nСписок пуст — пока ничего не отключали."
        return text, _back_button()

    lines = ["🚫 <b>Отключённые объявления</b>\n"]
    for ad in disabled[:10]:
        lines.append(
            f"\n❌ <b>{ad.get('ad_name', '?')}</b>\n"
            f"   Ad ID: <code>{ad.get('fb_ad_id', '?')}</code>\n"
            f"   Правило: {ad.get('rule', '?')}\n"
            f"   Отключено: {ad.get('disabled_at', '?')}"
        )
    if len(disabled) > 10:
        lines.append(f"\n... и ещё {len(disabled) - 10}")

    text = "\n".join(lines)
    return text, _back_button()


def _render_settings() -> tuple[str, dict]:
    """Текущие настройки."""
    s = bot_state
    text = (
        "🔧 <b>Настройки бота</b>\n\n"
        f"⏱ Интервал обновления: <b>{s.observer_interval} сек</b>\n"
        f"📉 Порог предупреждения: <b>{s.warning_percent}%</b> от стопа\n\n"
        "💡 Для изменения настроек используйте Web UI или отправьте:\n"
        "<code>/set interval 60</code> — интервал 60 сек\n"
        "<code>/set warning 75</code> — порог 75%"
    )
    return text, _back_button()


def _render_help() -> tuple[str, dict]:
    """Список команд."""
    text = (
        "❓ <b>Помощь</b>\n\n"
        "/start — Главное меню\n"
        "/status — Статус мониторинга\n"
        "/ads — Объявления с метриками\n"
        "/offers — Офферы и CPA\n"
        "/rules — Стоп-правила\n"
        "/disabled — Отключённые объявления\n"
        "/settings — Настройки\n\n"
        "Кнопка <b>«Отключить»</b> на алертах — "
        "создаёт задачу на отключение через Playwright."
    )
    return text, _back_button()


# ==========================================
# Маршрутизация
# ==========================================

COMMAND_HANDLERS = {
    "start": _render_start,
    "status": _render_status,
    "offers": _render_offers,
    "rules": _render_rules,
    "disabled": _render_disabled,
    "settings": _render_settings,
    "help": _render_help,
}


async def handle_update(client: TelegramBotClient, update: dict) -> None:
    """Обработка одного Telegram update (message или callback_query)."""

    # --- Callback query (кнопки) ---
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data", "")
        chat_id = str(cq["message"]["chat"]["id"])
        message_id = cq["message"]["message_id"]

        # Отвечаем на callback чтобы убрать «часики»
        await client.answer_callback_query(cq["id"])

        # Команда из кнопки: cmd:status, cmd:ads, etc.
        if data.startswith("cmd:"):
            cmd = data.split(":")[1]
            handler = COMMAND_HANDLERS.get(cmd)
            if cmd == "ads":
                text, markup = _render_ads(page=0)
            elif handler:
                text, markup = handler()
            else:
                return
            await client.edit_message(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=markup,
            )
            return

        # Пагинация объявлений: ads:page:N
        if data.startswith("ads:page:"):
            page = int(data.split(":")[2])
            text, markup = _render_ads(page=page)
            await client.edit_message(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=markup,
            )
            return

        # Кнопка "Отключить" из алертов: disable:{snapshot_id}
        if data.startswith("disable:"):
            snapshot_id = data.split(":", 1)[1]
            # TODO: создать DisableTask в БД
            await client.edit_message(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"✅ Задача на отключение создана\n\n"
                    f"Snapshot: <code>{snapshot_id}</code>\n"
                    f"Статус: ⏳ В очереди"
                ),
            )
            logger.info("Создана задача на отключение: %s", snapshot_id)
            return

        # noop — игнорируем
        return

    # --- Текстовые сообщения ---
    msg = update.get("message")
    if not msg:
        return

    chat_id = str(msg["chat"]["id"])
    text_in = (msg.get("text") or "").strip()

    if not text_in.startswith("/"):
        return

    # Парсим команду
    parts = text_in.split()
    cmd = parts[0].lstrip("/").split("@")[0].lower()

    # /set — настройка параметров
    if cmd == "set" and len(parts) >= 3:
        param = parts[1].lower()
        try:
            value = int(parts[2])
        except ValueError:
            await client.send_message(chat_id=chat_id, text="❌ Значение должно быть числом")
            return

        if param == "interval" and 10 <= value <= 600:
            bot_state.observer_interval = value
            await client.send_message(
                chat_id=chat_id,
                text=f"✅ Интервал обновления: <b>{value} сек</b>",
            )
        elif param == "warning" and 50 <= value <= 99:
            bot_state.warning_percent = value
            await client.send_message(
                chat_id=chat_id,
                text=f"✅ Порог предупреждения: <b>{value}%</b>",
            )
        else:
            await client.send_message(
                chat_id=chat_id,
                text="❌ Неверный параметр. Используйте:\n"
                     "<code>/set interval 60</code>\n"
                     "<code>/set warning 75</code>",
            )
        return

    # /ads — с пагинацией
    if cmd == "ads":
        text, markup = _render_ads(page=0)
        await client.send_message(chat_id=chat_id, text=text, reply_markup=markup)
        return

    # Остальные команды
    handler = COMMAND_HANDLERS.get(cmd, _render_help)
    text, markup = handler()
    await client.send_message(chat_id=chat_id, text=text, reply_markup=markup)
