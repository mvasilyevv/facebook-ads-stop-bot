# -*- coding: utf-8 -*-
"""Полноценный Telegram-бот: команды, кнопки, просмотр статистики, управление.

Команды:
  /start    — Главное меню с живыми счётчиками
  /status   — Детальный статус мониторинга
  /ads      — Список объявлений с переходом в детали
  /offers   — Список офферов с CPA
  /rules    — Текущие стоп-правила
  /disabled — Отключённые объявления
  /settings — Настройки бота
  /help     — Помощь

Inline-кнопки:
  - Главное меню с динамическими счётчиками алертов
  - Список объявлений: кнопка на каждое → детальный вид
  - Детали: Отключить / Включить (сброс) / В обработке
  - Отключить всех с алертами → экран подтверждения
  - "Отключить" на алертах из renderer.py (legacy, сохранён)
"""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from core.db import get_session_factory
from core.domain import AlertState
from core.models import (
    AdSnapshot,
    DisableTask,
    ObserverSettings,
    Offer,
    TelegramRecipient,
    TelegramSettings,
)
from core.telegram.client import TelegramBotClient

logger = logging.getLogger(__name__)

# Иконки состояний объявлений
_STATE_ICONS: dict[AlertState, str] = {
    AlertState.STOP_SENT: "🛑",
    AlertState.WARNING_SENT: "⚠️",
    AlertState.CLAIMED: "⏳",
    AlertState.DISABLED: "🚫",
}

# Человекочитаемые названия правил
_RULE_LABELS: dict[str, str] = {
    "cpc_stop": "Дорогой клик",
    "cpl_stop": "Дорогой лид",
    "cpr_stop": "Дорогая рега",
    "regs_no_dep_stop": "Реги без депозитов",
    "spend_no_dep_range": "Расход без депа",
    "spend_with_dep_range": "Расход с депом",
}


# ==========================================
# Утилиты
# ==========================================


def _key_metric_str(ad) -> str:
    """Возвращает строку с ключевой метрикой срабатывающего правила.

    Показывает CPC/CPL/CPR вместо расхода, если правило связано с этими метриками.
    """
    rules = ad.stop_rule_codes or ad.warning_rule_codes or []
    code = rules[0] if rules else ""
    if code == "cpc_stop":
        if ad.cpc is not None:
            return f"CPC ${ad.cpc:.2f}"
        return f"расход ${ad.spend:.2f} (нет кликов)"
    if code == "cpl_stop":
        if ad.cost_per_lead is not None:
            return f"CPL ${ad.cost_per_lead:.2f}"
        return f"расход ${ad.spend:.2f} (нет лидов)"
    if code == "cpr_stop":
        if ad.cost_per_registration is not None:
            return f"CPR ${ad.cost_per_registration:.2f}"
        return f"расход ${ad.spend:.2f} (нет рег)"
    if code == "regs_no_dep_stop":
        return f"реги: {ad.registrations}, депы: 0"
    return f"расход ${ad.spend:.2f}"


def _format_age(age_sec: int) -> str:
    """Форматирует возраст в секундах в читаемую строку."""
    if age_sec < 60:
        return "только что"
    if age_sec < 3600:
        return f"{age_sec // 60} мин назад"
    h, m = divmod(age_sec // 60, 60)
    return f"{h}ч {m}мин назад" if m else f"{h}ч назад"


def _group_by_adset(ads: list) -> list[tuple[str, str, list]]:
    """Группирует объявления по (campaign_name, adset_name), порядок как в исходном списке."""
    groups: dict[tuple[str, str], list] = {}
    for ad in ads:
        key = (ad.campaign_name or "", ad.adset_name or "")
        if key not in groups:
            groups[key] = []
        groups[key].append(ad)
    return [(c, a, grp) for (c, a), grp in groups.items()]


def _back_button(target: str = "start") -> dict:
    """Кнопка возврата."""
    labels = {
        "start": "◀️ Главное меню",
        "ads": "◀️ К объявлениям",
        "alerts": "◀️ К алертам",
        "disabled": "◀️ К отключённым",
    }
    return {"inline_keyboard": [[{"text": labels.get(target, "◀️ Назад"), "callback_data": f"cmd:{target}"}]]}


def _ads_keyboard(
    ads: list,
    page: int,
    total_pages: int,
    has_alerts: bool,
    prefix: str = "ads",
) -> dict:
    """Клавиатура списка объявлений с кнопками-переходами на детали."""
    rows: list[list[dict]] = []

    # По две кнопки на строку — каждая ведёт на детальный вид
    ad_buttons = []
    for ad in ads:
        icon = _STATE_ICONS.get(ad.alert_state, "✅")
        short = ad.ad_name[:22].rstrip()
        ad_buttons.append({
            "text": f"{icon} {short}",
            "callback_data": f"ad:detail:{ad.fb_ad_id}:{prefix}",
        })
    for i in range(0, len(ad_buttons), 2):
        rows.append(ad_buttons[i:i + 2])

    # Навигация
    nav = []
    if page > 0:
        nav.append({"text": "◀️", "callback_data": f"{prefix}:page:{page - 1}"})
    nav.append({"text": f"{page + 1}/{total_pages}", "callback_data": "noop"})
    if page < total_pages - 1:
        nav.append({"text": "▶️", "callback_data": f"{prefix}:page:{page + 1}"})
    rows.append(nav)

    # Действия
    if has_alerts:
        rows.append([{"text": "🛑 Отключить всех с алертами", "callback_data": "ads:disable_all"}])
    rows.append([{"text": "◀️ Главное меню", "callback_data": "cmd:start"}])

    return {"inline_keyboard": rows}


# ==========================================
# Генерация сообщений
# ==========================================


async def _render_start() -> tuple[str, dict]:
    """Главное меню с живыми счётчиками из БД."""
    factory = get_session_factory()
    async with factory() as session:
        obs = await session.scalar(
            select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
        )
        is_scanning = obs.is_scanning_enabled if obs else False
        interval = obs.interval_seconds if obs else 90

        last_scan = await session.scalar(select(func.max(AdSnapshot.last_observed_at)))

        if last_scan:
            batch_start = last_scan - timedelta(minutes=5)
            in_batch = AdSnapshot.last_observed_at >= batch_start
        else:
            in_batch = AdSnapshot.last_observed_at.isnot(None)

        active_count = await session.scalar(
            select(func.count()).select_from(AdSnapshot).where(in_batch)
        ) or 0
        alert_count = await session.scalar(
            select(func.count()).select_from(AdSnapshot).where(
                in_batch,
                AdSnapshot.alert_state.in_([AlertState.WARNING_SENT, AlertState.STOP_SENT]),
            )
        ) or 0

    # Свежесть данных
    if last_scan:
        age_sec = int((datetime.now(tz=UTC) - last_scan).total_seconds())
        scan_str = _format_age(age_sec)
        stale = age_sec > interval * 3
    else:
        scan_str = "нет данных"
        stale = False
        age_sec = 0

    scan_icon = "🟢" if is_scanning and not stale else ("🟡" if is_scanning else "🔴")
    scanning_str = "включено" if is_scanning else "выключено"

    text = f"🛡 <b>AdGuard FB Bot</b>\n\n{scan_icon} Сканирование {scanning_str} · {scan_str}\n"
    if stale:
        text += "⚠️ Данные устарели\n"
    text += f"📋 Активных: <b>{active_count}</b>"
    if alert_count > 0:
        text += f" · ⚠️ Алертов: <b>{alert_count}</b>"
    text += "\n\nВыберите раздел:"

    alerts_btn = f"⚠️ Алерты ({alert_count})" if alert_count > 0 else "⚠️ Алерты"
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📋 Объявления", "callback_data": "cmd:ads"},
                {"text": alerts_btn, "callback_data": "cmd:alerts"},
            ],
            [
                {"text": "🚫 Отключённые", "callback_data": "cmd:disabled"},
                {"text": "⚙️ Задачи", "callback_data": "cmd:tasks"},
            ],
            [
                {"text": "📊 Статус", "callback_data": "cmd:status"},
                {"text": "⚙️ Правила", "callback_data": "cmd:rules"},
            ],
            [
                {"text": "🔧 Настройки", "callback_data": "cmd:settings"},
            ],
        ]
    }
    return text, keyboard


async def _render_status() -> tuple[str, dict]:
    """Детальный статус мониторинга — данные из БД."""
    factory = get_session_factory()
    async with factory() as session:
        obs = await session.scalar(
            select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
        )
        interval = obs.interval_seconds if obs else 90
        warning_pct = int(obs.warning_percent_of_stop) if obs else 80
        is_scanning = obs.is_scanning_enabled if obs else False

        last_scan = await session.scalar(select(func.max(AdSnapshot.last_observed_at)))

        if last_scan:
            batch_start = last_scan - timedelta(minutes=5)
            in_batch = AdSnapshot.last_observed_at >= batch_start
        else:
            in_batch = AdSnapshot.last_observed_at.isnot(None)

        total_ads = await session.scalar(
            select(func.count()).select_from(AdSnapshot).where(in_batch)
        ) or 0
        ads_in_warning = await session.scalar(
            select(func.count()).select_from(AdSnapshot).where(
                in_batch, AdSnapshot.alert_state == AlertState.WARNING_SENT
            )
        ) or 0
        ads_in_stop = await session.scalar(
            select(func.count()).select_from(AdSnapshot).where(
                in_batch, AdSnapshot.alert_state == AlertState.STOP_SENT
            )
        ) or 0
        ads_disabled = await session.scalar(
            select(func.count()).select_from(AdSnapshot).where(
                in_batch, AdSnapshot.delivery_status == "OFF"
            )
        ) or 0
        total_spend_val = await session.scalar(
            select(func.sum(AdSnapshot.spend)).where(in_batch)
        )
        total_spend = f"${total_spend_val:.2f}" if total_spend_val else "$0.00"

    if last_scan:
        age_sec = int((datetime.now(tz=UTC) - last_scan).total_seconds())
        freshness = _format_age(age_sec)
        last_scan_str = f"{last_scan.astimezone().strftime('%H:%M %d.%m')} ({freshness})"
        stale_warning = (
            f"\n⚠️ Данные устарели — сканер не работает {freshness}\n"
            if age_sec > interval * 3
            else ""
        )
    else:
        last_scan_str = "ещё не было"
        stale_warning = ""

    status_icon = "🟢" if is_scanning else "🔴"
    text = (
        f"📊 <b>Статус мониторинга</b>\n\n"
        f"{status_icon} Сканирование: {'включено' if is_scanning else 'выключено'}\n"
        f"🕐 Последний скан: {last_scan_str}"
        f"{stale_warning}\n\n"
        f"📋 Активных объявлений: <b>{total_ads}</b>\n"
        f"⚠️ Предупреждений: <b>{ads_in_warning}</b>\n"
        f"🛑 Стоп-алертов: <b>{ads_in_stop}</b>\n"
        f"🚫 Отключено: <b>{ads_disabled}</b>\n"
        f"💰 Расход: <b>{total_spend}</b>\n\n"
        f"⏱ Интервал: {interval} сек\n"
        f"📉 Порог предупреждения: {warning_pct}% от стопа"
    )
    return text, _back_button()


async def _render_ads(page: int = 0) -> tuple[str, dict]:
    """Активные объявления, сгруппированные по кампании → адсету. Страница = 1 адсет."""
    factory = get_session_factory()
    async with factory() as session:
        last_scan = await session.scalar(select(func.max(AdSnapshot.last_observed_at)))
        if not last_scan:
            return "📋 <b>Объявления</b>\n\nПока нет данных. Запустите observer.", _back_button()

        batch_start = last_scan - timedelta(minutes=5)
        result = await session.execute(
            select(AdSnapshot)
            .where(
                AdSnapshot.delivery_status != "OFF",
                AdSnapshot.last_observed_at >= batch_start,
            )
            .order_by(AdSnapshot.campaign_name, AdSnapshot.adset_name, AdSnapshot.spend.desc())
        )
        all_ads = result.scalars().all()

        has_alerts = any(
            ad.alert_state in {AlertState.WARNING_SENT, AlertState.STOP_SENT}
            for ad in all_ads
        )

    if not all_ads:
        return "📋 <b>Объявления</b>\n\nНет активных объявлений в текущем скане.", _back_button()

    groups = _group_by_adset(all_ads)
    total_pages = len(groups)
    page = min(max(0, page), total_pages - 1)
    campaign_name, adset_name, ads = groups[page]

    lines = [f"📋 <b>Активные объявления</b> ({page + 1}/{total_pages})\n"]
    if campaign_name:
        lines.append(f"📁 {html.escape(campaign_name)}")
    if adset_name:
        lines.append(f"  └ {html.escape(adset_name)}\n")

    for ad in ads:
        icon = _STATE_ICONS.get(ad.alert_state, "✅")
        leads_str = str(ad.leads) if ad.leads else "—"
        rules = [_RULE_LABELS.get(c, c) for c in (ad.stop_rule_codes or ad.warning_rule_codes or [])]
        rule_str = f" · {rules[0]}" if rules else ""
        lines.append(
            f"{icon} {html.escape(ad.ad_name)} · <b>${ad.spend:.2f}</b>"
            f" · {leads_str} лид{html.escape(rule_str)}"
        )

    return "\n".join(lines), _ads_keyboard(ads, page, total_pages, has_alerts, prefix="ads")


async def _render_alerts(page: int = 0) -> tuple[str, dict]:
    """Алерты из текущего скана, сгруппированные по кампании → адсету. Страница = 1 адсет."""
    factory = get_session_factory()
    async with factory() as session:
        last_scan = await session.scalar(select(func.max(AdSnapshot.last_observed_at)))
        if not last_scan:
            return "⚠️ <b>Алерты</b>\n\nПока нет данных.", _back_button()

        batch_start = last_scan - timedelta(minutes=5)
        result = await session.execute(
            select(AdSnapshot)
            .where(
                AdSnapshot.alert_state.in_([AlertState.WARNING_SENT, AlertState.STOP_SENT]),
                AdSnapshot.last_observed_at >= batch_start,
            )
            .order_by(AdSnapshot.campaign_name, AdSnapshot.adset_name, AdSnapshot.spend.desc())
        )
        all_ads = result.scalars().all()

    if not all_ads:
        return "⚠️ <b>Алерты</b>\n\nАктивных алертов нет — всё в порядке.", _back_button()

    groups = _group_by_adset(all_ads)
    total_pages = len(groups)
    page = min(max(0, page), total_pages - 1)
    campaign_name, adset_name, ads = groups[page]

    lines = [f"⚠️ <b>Алерты</b> ({page + 1}/{total_pages})\n"]
    if campaign_name:
        lines.append(f"📁 {html.escape(campaign_name)}")
    if adset_name:
        lines.append(f"  └ {html.escape(adset_name)}\n")

    for ad in ads:
        icon = _STATE_ICONS.get(ad.alert_state, "⚠️")
        rules = [_RULE_LABELS.get(c, c) for c in (ad.stop_rule_codes or ad.warning_rule_codes or [])]
        rule_str = f" · {rules[0]}" if rules else ""
        metric_str = _key_metric_str(ad)
        lines.append(
            f"{icon} {html.escape(ad.ad_name)} · <b>{html.escape(metric_str)}</b>{html.escape(rule_str)}"
        )

    return "\n".join(lines), _ads_keyboard(ads, page, total_pages, has_alerts=True, prefix="alerts")


async def _render_ad_detail(fb_ad_id: str, source: str = "ads") -> tuple[str, dict]:
    """Детальный вид одного объявления: полные метрики + кнопки управления."""
    factory = get_session_factory()
    async with factory() as session:
        ad = await session.scalar(
            select(AdSnapshot).where(AdSnapshot.fb_ad_id == fb_ad_id)
        )

    if not ad:
        text = "❌ Объявление не найдено"
        return text, _back_button(source)

    cpc_str = f"${ad.cpc:.2f}" if ad.cpc else "—"
    cpl_str = f" · CPL: ${ad.cost_per_lead:.2f}" if ad.cost_per_lead else ""
    cpr_str = f" · CPR: ${ad.cost_per_registration:.2f}" if ad.cost_per_registration else ""

    lines = [f"📢 <b>{html.escape(ad.ad_name)}</b>"]
    if ad.campaign_name:
        lines.append(f"📁 {html.escape(ad.campaign_name)}")
    if ad.adset_name:
        lines.append(f"  └ {html.escape(ad.adset_name)}")
    lines += [
        "",
        f"💰 Расход: <b>${ad.spend:.2f}</b>",
        f"🖱 CPC: {cpc_str} · Кликов: {ad.clicks}",
        f"📋 Лидов: {ad.leads}{cpl_str}",
        f"📝 Рег: {ad.registrations}{cpr_str}",
        f"💵 Депозитов: {ad.deposits}",
        "",
    ]
    if ad.last_observed_at:
        lines.append(f"🕐 Обновлено: {ad.last_observed_at.astimezone().strftime('%H:%M %d.%m')}")

    state_labels = {
        AlertState.NORMAL: "✅ Норма",
        AlertState.WARNING_SENT: "⚠️ Предупреждение",
        AlertState.STOP_SENT: "🛑 Стоп",
        AlertState.CLAIMED: "⏳ Отключение в очереди",
        AlertState.DISABLED: "🚫 Отключено",
    }
    lines.append(f"📊 Статус: {state_labels.get(ad.alert_state, str(ad.alert_state))}")

    rule_codes = list(dict.fromkeys((ad.stop_rule_codes or []) + (ad.warning_rule_codes or [])))
    if rule_codes:
        rules_str = ", ".join(_RULE_LABELS.get(c, c) for c in rule_codes)
        lines.append(f"🔍 Правила: {html.escape(rules_str)}")

    text = "\n".join(lines)

    # Кнопки зависят от состояния
    action_row: list[dict] = []
    if ad.alert_state in {AlertState.NORMAL, AlertState.WARNING_SENT, AlertState.STOP_SENT}:
        action_row = [{"text": "🛑 Отключить", "callback_data": f"ad:disable:{ad.fb_ad_id}"}]
    elif ad.alert_state == AlertState.CLAIMED:
        action_row = [{"text": "⏳ В обработке", "callback_data": "noop"}]
    elif ad.alert_state == AlertState.DISABLED:
        action_row = [{"text": "✅ Включить (сброс)", "callback_data": f"ad:enable:{ad.fb_ad_id}"}]

    keyboard_rows = []
    if action_row:
        keyboard_rows.append(action_row)
    keyboard_rows.append([{"text": "◀️ Назад", "callback_data": f"cmd:{source}"}])

    return text, {"inline_keyboard": keyboard_rows}


async def _render_disable_all_confirm() -> tuple[str, dict]:
    """Экран подтверждения массового отключения объявлений с алертами."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(AdSnapshot)
            .where(AdSnapshot.alert_state.in_([AlertState.WARNING_SENT, AlertState.STOP_SENT]))
            .order_by(AdSnapshot.spend.desc())
        )
        ads = result.scalars().all()

    if not ads:
        text = "✅ Нет объявлений с алертами для отключения"
        return text, _back_button()

    lines = [f"🛑 <b>Отключить все объявления с алертами?</b>\n\nБудет создано {len(ads)} задач:\n"]
    for ad in ads[:10]:
        icon = _STATE_ICONS.get(ad.alert_state, "")
        lines.append(f"{icon} {html.escape(ad.ad_name[:45])}")
    if len(ads) > 10:
        lines.append(f"... и ещё {len(ads) - 10}")

    text = "\n".join(lines)
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Подтвердить", "callback_data": "ads:disable_all:confirm"},
                {"text": "❌ Отмена", "callback_data": "cmd:alerts"},
            ],
        ]
    }
    return text, keyboard


async def _render_offers() -> tuple[str, dict]:
    """Список офферов из БД."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Offer).order_by(Offer.name))
        offers = result.scalars().all()

    if not offers:
        text = "🎯 <b>Офферы</b>\n\nОфферы не настроены. Добавьте их через UI."
        return text, _back_button()

    lines = ["🎯 <b>Офферы</b>\n"]
    for o in offers:
        status = "✅" if o.is_active else "⏸"
        lines.append(
            f"\n{status} <b>{html.escape(o.name)}</b>\n"
            f"   Код: <code>{html.escape(o.code)}</code>\n"
            f"   CPA: ${o.cpa_amount:.2f}"
        )
    return "\n".join(lines), _back_button()


async def _render_rules() -> tuple[str, dict]:
    """Стоп-правила с актуальным порогом из БД."""
    factory = get_session_factory()
    async with factory() as session:
        obs = await session.scalar(
            select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
        )
        warning_pct = int(obs.warning_percent_of_stop) if obs else 80

    text = (
        "⚙️ <b>Стоп-правила</b>\n\n"
        "1️⃣ <b>CPC</b> — если клик > 2% CPA → стоп\n"
        "2️⃣ <b>CPL</b> — если лид > 10% CPA → стоп\n"
        "3️⃣ <b>CPR</b> — если рега > 20% CPA → стоп\n"
        "4️⃣ <b>Реги без депов</b> — если 5 рег и 0 депов → стоп\n"
        "5️⃣ <b>Расход без депа</b> — расход 50-70% CPA, нет депов → стоп\n"
        "6️⃣ <b>Расход с депом</b> — есть деп, расход 70-90% CPA → стоп\n\n"
        f"📉 Порог предупреждения: <b>{warning_pct}%</b> от стопа\n\n"
        "💡 Проценты и лимиты настраиваются через UI или индивидуально на оффер."
    )
    return text, _back_button()


async def _render_disabled(page: int = 0) -> tuple[str, dict]:
    """Отключённые объявления, сгруппированные по кампании → адсету. Страница = 1 адсет."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(AdSnapshot)
            .where(AdSnapshot.delivery_status == "OFF")
            .order_by(AdSnapshot.updated_at.desc())
        )
        all_ads = result.scalars().all()

    if not all_ads:
        return "🚫 <b>Отключённые объявления</b>\n\nСписок пуст — пока ничего не отключали.", _back_button()

    groups = _group_by_adset(all_ads)
    total_pages = len(groups)
    page = min(max(0, page), total_pages - 1)
    campaign_name, adset_name, ads = groups[page]

    lines = [f"🚫 <b>Отключённые объявления</b> ({page + 1}/{total_pages})\n"]
    if campaign_name:
        lines.append(f"📁 {html.escape(campaign_name)}")
    if adset_name:
        lines.append(f"  └ {html.escape(adset_name)}\n")

    for ad in ads:
        disabled_at = ad.updated_at.astimezone().strftime("%H:%M %d.%m") if ad.updated_at else "?"
        rules = ", ".join(_RULE_LABELS.get(c, c) for c in (ad.stop_rule_codes or [])) or "—"
        lines.append(
            f"❌ {html.escape(ad.ad_name)} · {disabled_at}\n"
            f"   {html.escape(rules)}"
        )

    return "\n".join(lines), _ads_keyboard(ads, page, total_pages, has_alerts=False, prefix="disabled")


async def _render_settings() -> tuple[str, dict]:
    """Текущие настройки из БД."""
    factory = get_session_factory()
    async with factory() as session:
        obs = await session.scalar(
            select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
        )
        interval = obs.interval_seconds if obs else 90
        warning_pct = int(obs.warning_percent_of_stop) if obs else 80

    text = (
        "🔧 <b>Настройки бота</b>\n\n"
        f"⏱ Интервал обновления: <b>{interval} сек</b>\n"
        f"📉 Порог предупреждения: <b>{warning_pct}%</b> от стопа\n\n"
        "💡 Для изменения настроек используйте Web UI или отправьте:\n"
        "<code>/set interval 60</code> — интервал 60 сек\n"
        "<code>/set warning 75</code> — порог 75%"
    )
    return text, _back_button()


async def _render_help() -> tuple[str, dict]:
    """Список команд."""
    text = (
        "❓ <b>Помощь</b>\n\n"
        "/start — Главное меню\n"
        "/status — Статус мониторинга\n"
        "/ads — Объявления с метриками\n"
        "/offers — Офферы и CPA\n"
        "/rules — Стоп-правила\n"
        "/disabled — Отключённые объявления\n"
        "/settings — Настройки\n"
        "/tasks — Очередь задач на отключение\n\n"
        "Кнопка <b>«Отключить»</b> на алертах создаёт задачу на отключение через Playwright.\n"
        "Кнопка <b>«Включить (сброс)»</b> сбрасывает состояние в боте — "
        "для реального включения в Facebook используйте Ads Manager."
    )
    return text, _back_button()


async def _render_tasks() -> tuple[str, dict]:
    """Задачи на отключение за последние 24 часа."""
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(DisableTask)
            .where(DisableTask.created_at >= cutoff)
            .order_by(DisableTask.created_at.desc())
            .limit(30)
        )
        tasks = result.scalars().all()

    if not tasks:
        return "⚙️ <b>Задачи на отключение</b>\n\nЗадач за последние 24 часа нет.", _back_button()

    _TASK_ICONS = {
        "PENDING": "⏳",
        "RUNNING": "🔄",
        "RETRYING": "🔄",
        "SUCCEEDED": "✅",
        "FAILED": "❌",
    }

    lines = ["⚙️ <b>Задачи на отключение</b> (24ч)\n"]
    now = datetime.now(UTC)
    for t in tasks:
        status_val = str(t.status)
        icon = _TASK_ICONS.get(status_val, "❓")
        name = html.escape(t.ad_name[:38].rstrip())
        extra = ""
        if status_val == "RETRYING":
            if t.next_retry_at and t.next_retry_at > now:
                secs = int((t.next_retry_at - now).total_seconds())
                extra = f" · retry через {secs}с"
            extra += f" · попытка {t.attempt_count}/{t.max_attempts}"
        elif status_val == "FAILED" and t.last_error:
            err = html.escape(t.last_error[:40])
            extra = f" · {err}"
        elif status_val == "SUCCEEDED" and t.completed_at:
            extra = f" · {t.completed_at.astimezone().strftime('%H:%M')}"
        lines.append(f"{icon} {name}{extra}")

    return "\n".join(lines), _back_button()


# ==========================================
# Маршрутизация
# ==========================================

COMMAND_HANDLERS = {
    "start": _render_start,
    "status": _render_status,
    "ads": _render_ads,
    "alerts": _render_alerts,
    "offers": _render_offers,
    "rules": _render_rules,
    "disabled": _render_disabled,
    "settings": _render_settings,
    "help": _render_help,
    "tasks": _render_tasks,
}


async def handle_update(client: TelegramBotClient, update: dict) -> None:
    """Обработка одного Telegram update (message или callback_query)."""

    # --- Callback query (кнопки) ---
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data", "")
        chat_id = str(cq["message"]["chat"]["id"])
        message_id = cq["message"]["message_id"]
        user = cq.get("from", {})
        username = user.get("username", "") or ""
        tg_user_id = str(user.get("id", ""))

        await client.answer_callback_query(cq["id"])

        # noop — игнорируем
        if data == "noop":
            return

        # Команда из кнопки: cmd:start, cmd:ads, cmd:alerts, etc.
        if data.startswith("cmd:"):
            cmd = data.split(":")[1]
            handler = COMMAND_HANDLERS.get(cmd)
            if not handler:
                return
            text, markup = await handler()
            await client.edit_message(
                chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup
            )
            return

        # Пагинация объявлений: ads:page:N
        if data.startswith("ads:page:"):
            page = int(data.split(":")[2])
            text, markup = await _render_ads(page=page)
            await client.edit_message(
                chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup
            )
            return

        # Пагинация алертов: alerts:page:N
        if data.startswith("alerts:page:"):
            page = int(data.split(":")[2])
            text, markup = await _render_alerts(page=page)
            await client.edit_message(
                chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup
            )
            return

        # Пагинация отключённых: disabled:page:N
        if data.startswith("disabled:page:"):
            page = int(data.split(":")[2])
            text, markup = await _render_disabled(page=page)
            await client.edit_message(
                chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup
            )
            return

        # Детальный вид объявления: ad:detail:{fb_ad_id}:{source}
        if data.startswith("ad:detail:"):
            parts = data.split(":", 3)
            fb_ad_id = parts[2]
            source = parts[3] if len(parts) > 3 else "ads"
            text, markup = await _render_ad_detail(fb_ad_id, source=source)
            await client.edit_message(
                chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup
            )
            return

        # Отключить из детального вида: ad:disable:{fb_ad_id}
        if data.startswith("ad:disable:"):
            fb_ad_id = data.split(":", 2)[2]
            task_info = await _create_disable_task(
                snapshot_token=fb_ad_id, tg_user_id=tg_user_id, username=username
            )
            if task_info:
                await client.edit_message(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        f"✅ <b>Задача на отключение создана</b>\n\n"
                        f"📢 {html.escape(task_info['ad_name'])}\n"
                        f"🆔 <code>{task_info['fb_ad_id']}</code>\n"
                        f"👤 Запросил: @{html.escape(username) or 'неизвестно'}\n"
                        f"⏳ Статус: в очереди"
                    ),
                    reply_markup={"inline_keyboard": [[
                        {"text": "◀️ К объявлениям", "callback_data": "cmd:ads"}
                    ]]},
                )
                logger.info("Создана задача на отключение: %s (запросил @%s)", fb_ad_id, username)
            else:
                await client.edit_message(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="❌ Не удалось создать задачу — объявление не найдено",
                )
            return

        # Включить (сброс состояния): ad:enable:{fb_ad_id}
        if data.startswith("ad:enable:"):
            fb_ad_id = data.split(":", 2)[2]
            ad_name = await _reset_ad_state(fb_ad_id)
            if ad_name:
                await client.edit_message(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        f"✅ <b>Состояние сброшено</b>\n\n"
                        f"📢 {html.escape(ad_name)}\n\n"
                        f"Бот будет снова мониторить это объявление.\n\n"
                        f"⚠️ Для фактического включения в Facebook — "
                        f"активируйте объявление в Ads Manager вручную."
                    ),
                    reply_markup={"inline_keyboard": [[
                        {"text": "◀️ К объявлениям", "callback_data": "cmd:ads"}
                    ]]},
                )
                logger.info("Состояние сброшено для объявления %s (запросил @%s)", fb_ad_id, username)
            else:
                await client.edit_message(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="❌ Объявление не найдено",
                )
            return

        # Подтверждение массового отключения: ads:disable_all
        if data == "ads:disable_all":
            text, markup = await _render_disable_all_confirm()
            await client.edit_message(
                chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup
            )
            return

        # Выполнить массовое отключение: ads:disable_all:confirm
        if data == "ads:disable_all:confirm":
            count, failed = await _execute_disable_all(tg_user_id=tg_user_id, username=username)
            result_text = f"✅ <b>Создано задач на отключение: {count}</b>"
            if failed:
                result_text += f"\n⚠️ Не удалось обработать: {failed}"
            await client.edit_message(
                chat_id=chat_id,
                message_id=message_id,
                text=result_text,
                reply_markup={"inline_keyboard": [[
                    {"text": "◀️ К алертам", "callback_data": "cmd:alerts"}
                ]]},
            )
            logger.info("Массовое отключение: %s задач создано (запросил @%s)", count, username)
            return

        # Кнопка "Отключить" из алертов renderer.py: disable:{snapshot_token}
        if data.startswith("disable:"):
            snapshot_token = data.split(":", 1)[1]
            task_info = await _create_disable_task(
                snapshot_token=snapshot_token, tg_user_id=tg_user_id, username=username
            )
            if task_info:
                await client.edit_message(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        f"✅ <b>Задача на отключение создана</b>\n\n"
                        f"📢 {html.escape(task_info['ad_name'])}\n"
                        f"🆔 <code>{task_info['fb_ad_id']}</code>\n"
                        f"👤 Запросил: @{html.escape(username) or 'неизвестно'}\n"
                        f"⏳ Статус: в очереди"
                    ),
                )
                logger.info(
                    "Создана задача на отключение: %s (запросил @%s)",
                    task_info["fb_ad_id"],
                    username,
                )
            else:
                await client.edit_message(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="❌ Не удалось создать задачу — снэпшот не найден",
                )
            return

        # Кнопка "Оставить" из алертов renderer.py: snooze:{snapshot_token}:{hours}
        if data.startswith("snooze:"):
            parts = data.split(":")
            snapshot_token = parts[1]
            hours = int(parts[2]) if len(parts) > 2 else 3
            ad_name = await _snooze_alert(snapshot_token, hours)
            if ad_name:
                await client.edit_message(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        f"✅ <b>Снузер установлен</b>\n\n"
                        f"📢 {html.escape(ad_name)}\n"
                        f"⏰ Повторный алерт через {hours}ч"
                    ),
                )
                logger.info("Снузер на %sч для %s (запросил @%s)", hours, snapshot_token, username)
            else:
                await client.edit_message(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="❌ Объявление не найдено",
                )
            return

        return

    # --- Текстовые сообщения ---
    msg = update.get("message")
    if not msg:
        return

    chat_id = str(msg["chat"]["id"])
    text_in = (msg.get("text") or "").strip()

    if not text_in.startswith("/"):
        return

    parts = text_in.split()
    cmd = parts[0].lstrip("/").split("@")[0].lower()

    # /start {auth_code} — авторизация бота
    if cmd == "start" and len(parts) >= 2:
        auth_code = parts[1].strip()
        authorized = await _try_authorize(client, chat_id, auth_code, msg)
        if authorized:
            return

    # /set — настройка параметров
    if cmd == "set" and len(parts) >= 3:
        param = parts[1].lower()
        try:
            value = int(parts[2])
        except ValueError:
            await client.send_message(chat_id=chat_id, text="❌ Значение должно быть числом")
            return

        if param == "interval" and 10 <= value <= 600:
            await _update_observer_setting(interval_seconds=value)
            await client.send_message(
                chat_id=chat_id, text=f"✅ Интервал обновления: <b>{value} сек</b>"
            )
        elif param == "warning" and 50 <= value <= 99:
            await _update_observer_setting(warning_percent_of_stop=value)
            await client.send_message(
                chat_id=chat_id, text=f"✅ Порог предупреждения: <b>{value}%</b>"
            )
        else:
            await client.send_message(
                chat_id=chat_id,
                text=(
                    "❌ Неверный параметр. Используйте:\n"
                    "<code>/set interval 60</code>\n"
                    "<code>/set warning 75</code>"
                ),
            )
        return

    handler = COMMAND_HANDLERS.get(cmd, _render_help)
    text, markup = await handler()
    await client.send_message(chat_id=chat_id, text=text, reply_markup=markup)


# ==========================================
# Вспомогательные функции БД
# ==========================================


async def _update_observer_setting(**kwargs: int) -> None:
    """Обновляет настройки Observer в БД."""
    factory = get_session_factory()
    async with factory() as session:
        obs = await session.scalar(
            select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
        )
        if not obs:
            obs = ObserverSettings(singleton_key="default")
            session.add(obs)
        for key, value in kwargs.items():
            if key == "warning_percent_of_stop":
                setattr(obs, key, Decimal(str(value)))
            else:
                setattr(obs, key, value)
        await session.commit()


async def _snooze_alert(snapshot_token: str, hours: int) -> str | None:
    """Устанавливает snoozed_until на N часов для объявления по токену или fb_ad_id.

    Returns:
        ad_name если успешно, None если объявление не найдено
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            ad = await session.scalar(
                select(AdSnapshot).where(AdSnapshot.open_state_token == snapshot_token)
            )
            if ad is None:
                ad = await session.scalar(
                    select(AdSnapshot).where(AdSnapshot.fb_ad_id == snapshot_token)
                )
            if ad is None:
                return None
            ad.snoozed_until = datetime.now(UTC) + timedelta(hours=hours)
            await session.commit()
            return ad.ad_name
    except Exception:
        logger.exception("Ошибка при установке снузера для %s", snapshot_token)
        return None


async def _reset_ad_state(fb_ad_id: str) -> str | None:
    """Сбрасывает alert_state объявления в NORMAL (явное действие пользователя).

    Returns:
        ad_name если успешно, None если объявление не найдено
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            ad = await session.scalar(
                select(AdSnapshot).where(AdSnapshot.fb_ad_id == fb_ad_id)
            )
            if ad is None:
                return None
            ad.alert_state = AlertState.NORMAL
            ad.open_state_token = None
            await session.commit()
            return ad.ad_name
    except Exception:
        logger.exception("Ошибка при сбросе состояния объявления %s", fb_ad_id)
        return None


async def _execute_disable_all(*, tg_user_id: str, username: str) -> tuple[int, int]:
    """Создаёт DisableTask для всех объявлений с алертами.

    Returns:
        (успешно создано, не удалось обработать)
    """
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(AdSnapshot).where(
                AdSnapshot.alert_state.in_([AlertState.WARNING_SENT, AlertState.STOP_SENT])
            )
        )
        ads = result.scalars().all()

    count = 0
    failed = 0
    for ad in ads:
        task_info = await _create_disable_task(
            snapshot_token=ad.fb_ad_id,
            tg_user_id=tg_user_id,
            username=username,
        )
        if task_info:
            count += 1
        else:
            failed += 1

    return count, failed


async def _try_authorize(
    client: TelegramBotClient,
    chat_id: str,
    auth_code: str,
    msg: dict,
) -> bool:
    """Проверяет auth_code и привязывает chat_id к боту."""
    try:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
            )
            row = result.scalar_one_or_none()
            if row is None:
                return False

            user = msg.get("from", {})
            username = user.get("username", "") or ""
            first_name = user.get("first_name", "") or ""

            # Первичная авторизация (основной chat_id)
            if row.auth_code and row.auth_code == auth_code:
                row.chat_id = chat_id
                row.is_authorized = True
                row.auth_code = ""
                await session.commit()
                await client.send_message(
                    chat_id=chat_id,
                    text=(
                        f"✅ <b>Авторизация прошла успешно!</b>\n\n"
                        f"Добро пожаловать, {html.escape(first_name or username or 'пользователь')}!\n"
                        f"Теперь вы будете получать уведомления AdGuard FB Bot.\n\n"
                        f"Используйте /help для списка команд."
                    ),
                )
                return True

            # Дополнительный пользователь (pending_codes)
            pending = list(row.pending_codes or [])
            if auth_code in pending:
                pending.remove(auth_code)
                row.pending_codes = pending
                await session.commit()

                existing = await session.execute(
                    select(TelegramRecipient).where(TelegramRecipient.chat_id == chat_id)
                )
                recipient = existing.scalar_one_or_none()
                if recipient is None:
                    recipient = TelegramRecipient(
                        chat_id=chat_id,
                        username=username,
                        first_name=first_name,
                        is_active=True,
                    )
                    session.add(recipient)
                else:
                    recipient.username = username
                    recipient.first_name = first_name
                    recipient.is_active = True
                await session.commit()

                await client.send_message(
                    chat_id=chat_id,
                    text=(
                        f"✅ <b>Вы добавлены как получатель уведомлений!</b>\n\n"
                        f"Добро пожаловать, {html.escape(first_name or username or 'пользователь')}!\n"
                        f"Теперь вы будете получать алерты AdGuard FB Bot."
                    ),
                )
                return True

            if row.auth_code or pending:
                await client.send_message(
                    chat_id=chat_id,
                    text="❌ Неверный код авторизации. Проверьте код в настройках UI.",
                )
                return True

    except Exception:
        logger.exception("Ошибка в _try_authorize")

    return False


async def _create_disable_task(
    *,
    snapshot_token: str,
    tg_user_id: str,
    username: str,
) -> dict | None:
    """Создаёт DisableTask в БД по токену снэпшота или fb_ad_id.

    Returns:
        dict с fb_ad_id и ad_name или None если снэпшот не найден
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            # Ищем снэпшот по open_state_token
            result = await session.execute(
                select(AdSnapshot).where(AdSnapshot.open_state_token == snapshot_token)
            )
            snapshot = result.scalar_one_or_none()
            if snapshot is None:
                # Пробуем по fb_ad_id
                result = await session.execute(
                    select(AdSnapshot).where(AdSnapshot.fb_ad_id == snapshot_token)
                )
                snapshot = result.scalar_one_or_none()

            if snapshot is None:
                logger.warning("Снэпшот не найден по токену %s", snapshot_token)
                return None

            snapshot.alert_state = AlertState.CLAIMED
            idempotency_key = f"disable:{snapshot.fb_ad_id}:{snapshot_token}"
            task = DisableTask(
                snapshot_id=snapshot.id,
                offer_id=snapshot.offer_id,
                fb_ad_id=snapshot.fb_ad_id,
                ad_name=snapshot.ad_name,
                open_state_token=snapshot_token,
                idempotency_key=idempotency_key,
                requested_by_telegram_user_id=tg_user_id,
                requested_by_username=username,
            )
            session.add(task)
            await session.commit()

            return {"fb_ad_id": snapshot.fb_ad_id, "ad_name": snapshot.ad_name}
    except Exception:
        logger.exception("Ошибка при создании DisableTask")
        return None
