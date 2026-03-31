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
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from core.db import get_session_factory
from core.domain import AlertStage, AlertState, DisableTaskStatus
from core.enable_recommendations.service import promote_recommendation_to_enable_task
from core.live_batch import load_live_batch_bounds as load_live_batch_bounds_shared
from core.models import (
    AdSnapshot,
    AlertEvent,
    DisableTask,
    ObserverSettings,
    Offer,
    TelegramInvite,
    TelegramRecipient,
    TelegramSettings,
)
from core.telegram.client import TelegramBotClient
from core.telegram.delivery import (
    TelegramAdMessageContext,
    broadcast_disable_task_queue_message,
    broadcast_enable_task_queue_message,
)
from core.telegram.messaging import safe_edit_or_send_message
from core.telegram.renderer import TelegramAlertItem, build_ad_identity_lines, render_alert_message
from core.telegram.service import (
    FORUM_SUPERGROUP_CHAT_ID,
    get_or_create_telegram_settings,
    is_forum_delivery_mode,
    is_owner_role,
    is_private_chat,
    resolve_telegram_access,
)

logger = logging.getLogger(__name__)

LIVE_BATCH_WINDOW = timedelta(minutes=5)
AUTH_REQUIRED_TEXT = (
    "🔒 Вы ещё не авторизованы в Telegram-контуре. "
    "Откройте topic <b>CONTROL</b> в группе AdGuard FB Bot и отправьте команду "
    "<code>/start ВАШ_КОД</code>."
)
OWNER_ONLY_TEXT = "⛔ Это действие доступно только владельцу Telegram-контура."
PRIVATE_CHAT_ONLY_TEXT = (
    "🧭 Telegram-контур перенесён в группу <b>AdGuard FB Bot</b>. "
    "Откройте topic <b>CONTROL</b> и работайте оттуда."
)
CONTROL_TOPIC_ONLY_TEXT = "🧭 Общее меню и команды доступны только в topic <b>CONTROL</b>."
CONTROL_TOPIC_CALLBACK_TEXT = "Откройте topic CONTROL"
WRONG_GROUP_TEXT = "🔒 Этот чат не привязан к рабочему Telegram-контуру."
AUTH_CODE_CONTROL_ONLY_TEXT = "🔒 Код активации нужно отправлять только в topic <b>CONTROL</b>."

# Иконки состояний объявлений
_STATE_ICONS: dict[AlertState, str] = {
    AlertState.EARLY_SIGNAL_SENT: "🔎",
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
    "early_outbound_ctr_signal": "Слабый CTR исходящих кликов",
    "early_lpv_ratio_signal": "Слабая доходимость до лендинга",
    "early_cost_per_lpv_signal": "Дорогой просмотр лендинга",
}


# ==========================================
# Утилиты
# ==========================================


def _key_metric_str(ad) -> str:
    """Возвращает строку с ключевой метрикой срабатывающего правила.

    Показывает CPC/CPL/CPR вместо расхода, если правило связано с этими метриками.
    """
    rules = ad.stop_rule_codes or ad.warning_rule_codes or ad.early_signal_rule_codes or []
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
    if code == "early_outbound_ctr_signal":
        if ad.outbound_ctr is not None:
            return f"CTR исх. {ad.outbound_ctr:.2f}%"
        return f"расход ${ad.spend:.2f}"
    if code == "early_lpv_ratio_signal":
        if ad.outbound_clicks:
            return f"LPV {ad.landing_page_views}/{ad.outbound_clicks}"
        return f"LPV {ad.landing_page_views}"
    if code == "early_cost_per_lpv_signal":
        if ad.cost_per_landing_page_view is not None:
            return f"Цена LPV ${ad.cost_per_landing_page_view:.2f}"
        return f"LPV {ad.landing_page_views}"
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
        "start": "◀️ CONTROL",
        "more": "◀️ CONTROL",
        "ads": "◀️ К объявлениям",
        "alerts": "◀️ К алертам",
        "disabled": "◀️ К отключённым",
    }
    return {
        "inline_keyboard": [
            [{"text": labels.get(target, "◀️ Назад"), "callback_data": f"cmd:{target}"}]
        ]
    }


async def _load_current_live_batch_bounds(session) -> tuple[datetime | None, datetime | None]:
    """Возвращает границы текущего живого батча сканирования."""
    return await load_live_batch_bounds_shared(session, window=LIVE_BATCH_WINDOW)


async def _load_telegram_settings_row():
    """Загружает текущую строку Telegram-настроек из БД."""
    factory = get_session_factory()
    async with factory() as session:
        return await session.scalar(
            select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
        )


def _auth_required_text() -> str:
    """Сообщение для неавторизованного пользователя."""
    return AUTH_REQUIRED_TEXT


def _owner_only_text() -> str:
    """Сообщение для действий только владельца."""
    return OWNER_ONLY_TEXT


def _private_chat_only_text() -> str:
    """Сообщение для старого личного чата."""
    return PRIVATE_CHAT_ONLY_TEXT


def _can_manage_settings(access) -> bool:
    """Проверяет, что пользователь может менять глобальные настройки."""
    return access is not None and is_owner_role(access.role)


def _control_topic_only_text() -> str:
    """Сообщение о том, что общие команды работают только в CONTROL."""
    return CONTROL_TOPIC_ONLY_TEXT


def _is_control_topic(message_thread_id: int | None, access) -> bool:
    """Проверяет, что команда пришла в CONTROL topic."""
    if access is None:
        return False
    return access.control_topic_id is not None and message_thread_id == access.control_topic_id


def _status_topic_notice(stream_name: str) -> str:
    """Короткая подсказка, где искать дальнейший статус."""
    return f"ℹ️ Дальнейший статус смотрите в topic <b>{html.escape(stream_name)}</b>."


async def _safe_edit_current_message(
    client: TelegramBotClient,
    *,
    chat_id: str,
    message_id: int,
    message_thread_id: int | None = None,
    text: str,
    reply_markup: dict | None = None,
) -> None:
    """Безопасно редактирует текущее сообщение с fallback на отправку нового."""
    await safe_edit_or_send_message(
        client,
        chat_id=chat_id,
        message_id=message_id,
        message_thread_id=message_thread_id,
        text=text,
        reply_markup=reply_markup,
    )


async def _send_current_topic_message(
    client: TelegramBotClient,
    *,
    chat_id: str,
    message_thread_id: int | None = None,
    text: str,
    reply_markup: dict | None = None,
) -> None:
    """Отправляет новое сообщение в текущий чат или topic."""
    await client.send_message(
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        text=text,
        reply_markup=reply_markup,
    )


def _disable_task_idempotency_key(*, fb_ad_id: str, incident_key: str) -> str:
    """Строит стабильный ключ идемпотентности для ручной задачи отключения."""
    return f"manual:{fb_ad_id}:{incident_key}"


def _normalize_snooze_minutes(value: int) -> int:
    """Нормализует длительность snooze и поддерживает legacy callback `:3`."""
    if value == 3:
        return 180
    return value


def _alert_stage_from_state(state: AlertState) -> AlertStage | None:
    """Возвращает стадию алерта по текущему состоянию snapshot."""
    if state == AlertState.STOP_SENT:
        return AlertStage.STOP
    if state == AlertState.WARNING_SENT:
        return AlertStage.WARNING
    if state == AlertState.EARLY_SIGNAL_SENT:
        return AlertStage.EARLY_SIGNAL
    return None


async def _load_latest_alert_event_for_incident(
    session,
    *,
    fb_ad_id: str,
    incident_key: str,
    stage: AlertStage | None = None,
):
    """Возвращает последнее alert-событие по объявлению и incident."""
    stmt = select(AlertEvent).where(AlertEvent.fb_ad_id == fb_ad_id)
    if incident_key:
        stmt = stmt.where(AlertEvent.telegram_group_key == incident_key)
    if stage is not None:
        stmt = stmt.where(AlertEvent.stage == stage)
    result = await session.execute(
        stmt.order_by(AlertEvent.updated_at.desc(), AlertEvent.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def _build_disable_message_context_for_snapshot(
    session, snapshot
) -> TelegramAdMessageContext:
    """Собирает контекст для STOP-сообщений по snapshot."""
    incident_key = snapshot.open_state_token or ""
    event = await _load_latest_alert_event_for_incident(
        session,
        fb_ad_id=snapshot.fb_ad_id,
        incident_key=incident_key,
        stage=AlertStage.STOP,
    )
    if event is None:
        event = await _load_latest_alert_event_for_incident(
            session,
            fb_ad_id=snapshot.fb_ad_id,
            incident_key=incident_key,
        )
    fallback_rule_codes = list(
        dict.fromkeys(
            (snapshot.stop_rule_codes or [])
            + (snapshot.warning_rule_codes or [])
            + (snapshot.early_signal_rule_codes or [])
        )
    )
    fallback_metrics = {
        "spend": str(snapshot.spend),
        "clicks": snapshot.clicks,
        "cpc": str(snapshot.cpc) if snapshot.cpc is not None else None,
        "outbound_clicks": snapshot.outbound_clicks,
        "outbound_ctr": str(snapshot.outbound_ctr) if snapshot.outbound_ctr is not None else None,
        "landing_page_views": snapshot.landing_page_views,
        "cost_per_landing_page_view": (
            str(snapshot.cost_per_landing_page_view)
            if snapshot.cost_per_landing_page_view is not None
            else None
        ),
        "cpm": str(snapshot.cpm) if snapshot.cpm is not None else None,
        "frequency": str(snapshot.frequency) if snapshot.frequency is not None else None,
        "leads": snapshot.leads,
        "cost_per_lead": (
            str(snapshot.cost_per_lead) if snapshot.cost_per_lead is not None else None
        ),
        "registrations": snapshot.registrations,
        "cost_per_registration": (
            str(snapshot.cost_per_registration)
            if snapshot.cost_per_registration is not None
            else None
        ),
        "deposits": snapshot.deposits,
    }
    return TelegramAdMessageContext(
        campaign_name=snapshot.campaign_name,
        adset_name=snapshot.adset_name,
        matched_rule_codes=list(event.matched_rule_codes or []) if event else fallback_rule_codes,
        reason_title=event.reason_title if event else None,
        reason_text=event.reason_text if event else None,
        metrics_json=dict(event.metrics_json or {}) if event else fallback_metrics,
    )


async def _render_snoozed_alert_message(
    snapshot_token: str, minutes: int
) -> tuple[str | None, dict | None]:
    """Перерисовывает исходный алерт и добавляет заметку о snooze."""
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
            return None, None

        stage = _alert_stage_from_state(ad.alert_state)
        if stage is None:
            return None, None

        incident_key = ad.open_state_token or snapshot_token
        event = await _load_latest_alert_event_for_incident(
            session,
            fb_ad_id=ad.fb_ad_id,
            incident_key=incident_key,
            stage=stage,
        )
        if event is None:
            event = await _load_latest_alert_event_for_incident(
                session,
                fb_ad_id=ad.fb_ad_id,
                incident_key=incident_key,
            )

        matched_rule_codes = (
            list(event.matched_rule_codes or [])
            if event is not None
            else (
                ad.stop_rule_codes
                if stage == AlertStage.STOP
                else ad.warning_rule_codes
                if stage == AlertStage.WARNING
                else ad.early_signal_rule_codes
            )
        )

        note_until = datetime.now(UTC) + timedelta(minutes=minutes)
        message = render_alert_message(
            stage=stage,
            items=[
                TelegramAlertItem(
                    snapshot_id=incident_key,
                    fb_ad_id=ad.fb_ad_id,
                    ad_name=ad.ad_name,
                    campaign_name=ad.campaign_name,
                    adset_name=ad.adset_name,
                    offer_code=ad.resolved_offer_code,
                    stage=stage,
                    alert_state=ad.alert_state,
                    matched_rule_codes=list(matched_rule_codes or []),
                    reason_title=event.reason_title if event else None,
                    reason_text=event.reason_text if event else None,
                    metrics_json=dict(event.metrics_json or {}) if event else {},
                )
            ],
            snooze_note=(
                "⏰ Повторные напоминания приглушены до "
                f"{note_until.astimezone().strftime('%H:%M %d.%m')}."
            ),
        )
        return message.text, message.reply_markup


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
        ad_buttons.append(
            {
                "text": f"{icon} {short}",
                "callback_data": f"ad:detail:{ad.fb_ad_id}:{prefix}",
            }
        )
    for i in range(0, len(ad_buttons), 2):
        rows.append(ad_buttons[i : i + 2])

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
    rows.append([{"text": "◀️ CONTROL", "callback_data": "cmd:start"}])

    return {"inline_keyboard": rows}


# ==========================================
# Генерация сообщений
# ==========================================


async def _render_start() -> tuple[str, dict]:
    """Короткий ops-хаб для CONTROL topic."""
    factory = get_session_factory()
    async with factory() as session:
        obs = await session.scalar(
            select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
        )
        is_scanning = obs.is_scanning_enabled if obs else False
        interval = obs.interval_seconds if obs else 90

        last_scan, batch_start = await _load_current_live_batch_bounds(session)
        if last_scan is None or batch_start is None:
            in_batch = AdSnapshot.last_observed_at.isnot(None)
        else:
            in_batch = AdSnapshot.last_observed_at >= batch_start

        active_count = (
            await session.scalar(select(func.count()).select_from(AdSnapshot).where(in_batch)) or 0
        )
        alert_count = (
            await session.scalar(
                select(func.count())
                .select_from(AdSnapshot)
                .where(
                    in_batch,
                    AdSnapshot.alert_state.in_(
                        [
                            AlertState.EARLY_SIGNAL_SENT,
                            AlertState.WARNING_SENT,
                            AlertState.STOP_SENT,
                        ]
                    ),
                )
            )
            or 0
        )
        queue_count = (
            await session.scalar(
                select(func.count())
                .select_from(DisableTask)
                .where(
                    DisableTask.status.in_(
                        [
                            DisableTaskStatus.PENDING,
                            DisableTaskStatus.RUNNING,
                            DisableTaskStatus.RETRYING,
                        ]
                    )
                )
            )
            or 0
        )

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

    lines = ["🧭 <b>CONTROL</b>", ""]
    lines.append(f"{scan_icon} Сканирование: <b>{scanning_str}</b> · {scan_str}")
    if stale:
        lines.append("⚠️ Данные устарели, проверьте observer.")
    lines.extend(
        [
            f"📋 Активных объявлений: <b>{active_count}</b>",
            f"⚠️ Активных сигналов: <b>{alert_count}</b>",
            f"⏳ Задач в очереди: <b>{queue_count}</b>",
            "",
            "Выберите рабочий раздел:",
        ]
    )

    text = "\n".join(lines)
    alerts_btn = f"Алерты ({alert_count})" if alert_count > 0 else "Алерты"
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "Сегодня", "callback_data": "cmd:today"},
                {"text": alerts_btn, "callback_data": "cmd:alerts"},
            ],
            [
                {"text": "Задачи", "callback_data": "cmd:tasks"},
                {"text": "Объявления", "callback_data": "cmd:ads"},
            ],
            [
                {"text": "Настройки", "callback_data": "cmd:settings"},
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

        last_scan, batch_start = await _load_current_live_batch_bounds(session)
        if last_scan is None or batch_start is None:
            in_batch = AdSnapshot.last_observed_at.isnot(None)
        else:
            in_batch = AdSnapshot.last_observed_at >= batch_start

        total_ads = (
            await session.scalar(select(func.count()).select_from(AdSnapshot).where(in_batch)) or 0
        )
        ads_in_warning = (
            await session.scalar(
                select(func.count())
                .select_from(AdSnapshot)
                .where(in_batch, AdSnapshot.alert_state == AlertState.WARNING_SENT)
            )
            or 0
        )
        ads_in_early_signal = (
            await session.scalar(
                select(func.count())
                .select_from(AdSnapshot)
                .where(in_batch, AdSnapshot.alert_state == AlertState.EARLY_SIGNAL_SENT)
            )
            or 0
        )
        ads_in_stop = (
            await session.scalar(
                select(func.count())
                .select_from(AdSnapshot)
                .where(in_batch, AdSnapshot.alert_state == AlertState.STOP_SENT)
            )
            or 0
        )
        ads_disabled = (
            await session.scalar(
                select(func.count())
                .select_from(AdSnapshot)
                .where(in_batch, AdSnapshot.delivery_status == "OFF")
            )
            or 0
        )
        total_spend_val = await session.scalar(select(func.sum(AdSnapshot.spend)).where(in_batch))
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
        f"📊 <b>Сегодня</b>\n\n"
        f"{status_icon} Сканирование: {'включено' if is_scanning else 'выключено'}\n"
        f"🕐 Последний скан: {last_scan_str}"
        f"{stale_warning}\n\n"
        f"📋 Активных объявлений: <b>{total_ads}</b>\n"
        f"🔎 Ранних сигналов: <b>{ads_in_early_signal}</b>\n"
        f"⚠️ Предупреждений: <b>{ads_in_warning}</b>\n"
        f"🛑 Стоп-алертов: <b>{ads_in_stop}</b>\n"
        f"🚫 Отключено: <b>{ads_disabled}</b>\n"
        f"💰 Расход: <b>{total_spend}</b>\n\n"
        f"⏱ Интервал: {interval} сек\n"
        f"📉 Порог предупреждения: {warning_pct}% от стопа"
    )
    return text, _back_button()


async def _render_more() -> tuple[str, dict]:
    """Второй уровень CONTROL-меню с редко используемыми разделами."""
    text = (
        "➕ <b>Ещё</b>\n\n"
        "Здесь лежат вторичные разделы Telegram-контура.\n"
        "Основная работа по объявлениям остаётся на первом экране CONTROL."
    )
    return text, {
        "inline_keyboard": [
            [
                {"text": "🚫 Отключённые", "callback_data": "cmd:disabled"},
                {"text": "📐 Правила", "callback_data": "cmd:rules"},
            ],
            [
                {"text": "🎯 Офферы", "callback_data": "cmd:offers"},
                {"text": "❓ Помощь", "callback_data": "cmd:help"},
            ],
            [{"text": "◀️ CONTROL", "callback_data": "cmd:start"}],
        ]
    }


async def _render_ads(page: int = 0) -> tuple[str, dict]:
    """Активные объявления, сгруппированные по кампании → адсету. Страница = 1 адсет."""
    factory = get_session_factory()
    async with factory() as session:
        last_scan, batch_start = await _load_current_live_batch_bounds(session)
        if last_scan is None or batch_start is None:
            return "📋 <b>Объявления</b>\n\nПока нет данных. Запустите observer.", _back_button()
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
            ad.alert_state
            in {AlertState.EARLY_SIGNAL_SENT, AlertState.WARNING_SENT, AlertState.STOP_SENT}
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
        rules = [
            _RULE_LABELS.get(c, c)
            for c in (
                ad.stop_rule_codes or ad.warning_rule_codes or ad.early_signal_rule_codes or []
            )
        ]
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
        last_scan, batch_start = await _load_current_live_batch_bounds(session)
        if last_scan is None or batch_start is None:
            return "⚠️ <b>Алерты</b>\n\nПока нет данных.", _back_button()
        result = await session.execute(
            select(AdSnapshot)
            .where(
                AdSnapshot.alert_state.in_(
                    [
                        AlertState.EARLY_SIGNAL_SENT,
                        AlertState.WARNING_SENT,
                        AlertState.STOP_SENT,
                    ]
                ),
                AdSnapshot.last_observed_at >= batch_start,
            )
            .order_by(AdSnapshot.campaign_name, AdSnapshot.adset_name, AdSnapshot.spend.desc())
        )
        all_ads = result.scalars().all()

    if not all_ads:
        return "⚠️ <b>Сигналы и алерты</b>\n\nАктивных сигналов нет — всё в порядке.", _back_button()

    groups = _group_by_adset(all_ads)
    total_pages = len(groups)
    page = min(max(0, page), total_pages - 1)
    campaign_name, adset_name, ads = groups[page]

    lines = [f"⚠️ <b>Сигналы и алерты</b> ({page + 1}/{total_pages})\n"]
    if campaign_name:
        lines.append(f"📁 {html.escape(campaign_name)}")
    if adset_name:
        lines.append(f"  └ {html.escape(adset_name)}\n")

    for ad in ads:
        icon = _STATE_ICONS.get(ad.alert_state, "⚠️")
        rules = [
            _RULE_LABELS.get(c, c)
            for c in (
                ad.stop_rule_codes or ad.warning_rule_codes or ad.early_signal_rule_codes or []
            )
        ]
        rule_str = f" · {rules[0]}" if rules else ""
        metric_str = _key_metric_str(ad)
        lines.append(
            f"{icon} {html.escape(ad.ad_name)} · <b>{html.escape(metric_str)}</b>{html.escape(rule_str)}"
        )
        if getattr(ad, "reason_title", None):
            lines.append(f"   🧭 {html.escape(str(ad.reason_title))}")
        if getattr(ad, "reason_text", None):
            lines.append(f"   Причина: {html.escape(str(ad.reason_text))}")
        if getattr(ad, "reason_title", None) or getattr(ad, "reason_text", None):
            lines.append("")

    return "\n".join(lines), _ads_keyboard(ads, page, total_pages, has_alerts=True, prefix="alerts")


async def _render_ad_detail(fb_ad_id: str, source: str = "ads") -> tuple[str, dict]:
    """Детальный вид одного объявления: полные метрики + кнопки управления."""
    factory = get_session_factory()
    async with factory() as session:
        ad = await session.scalar(select(AdSnapshot).where(AdSnapshot.fb_ad_id == fb_ad_id))

    if not ad:
        text = "❌ Объявление не найдено"
        return text, _back_button(source)

    cpc_str = f"${ad.cpc:.2f}" if ad.cpc else "—"
    cpl_str = f" · CPL: ${ad.cost_per_lead:.2f}" if ad.cost_per_lead else ""
    cpr_str = f" · CPR: ${ad.cost_per_registration:.2f}" if ad.cost_per_registration else ""
    outbound_ctr_str = f"{ad.outbound_ctr:.2f}%" if ad.outbound_ctr is not None else "—"
    cost_per_lpv_str = (
        f"${ad.cost_per_landing_page_view:.2f}"
        if ad.cost_per_landing_page_view is not None
        else "—"
    )
    cpm_str = f"${ad.cpm:.2f}" if ad.cpm is not None else "—"
    frequency_str = f"{ad.frequency:.2f}" if ad.frequency is not None else "—"

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
        f"🌐 Исх. клики: {ad.outbound_clicks} · CTR исх.: {outbound_ctr_str}",
        f"🧪 LPV: {ad.landing_page_views} · Цена LPV: {cost_per_lpv_str}",
        f"📈 CPM: {cpm_str} · Частота: {frequency_str}",
        "",
    ]
    if ad.last_observed_at:
        lines.append(f"🕐 Обновлено: {ad.last_observed_at.astimezone().strftime('%H:%M %d.%m')}")

    state_labels = {
        AlertState.NORMAL: "✅ Норма",
        AlertState.EARLY_SIGNAL_SENT: "🔎 Ранний сигнал",
        AlertState.WARNING_SENT: "⚠️ Предупреждение",
        AlertState.STOP_SENT: "🛑 Стоп",
        AlertState.CLAIMED: "⏳ Отключение в очереди",
        AlertState.DISABLED: "🚫 Отключено",
    }
    lines.append(f"📊 Статус: {state_labels.get(ad.alert_state, str(ad.alert_state))}")

    rule_codes = list(
        dict.fromkeys(
            (ad.stop_rule_codes or [])
            + (ad.warning_rule_codes or [])
            + (ad.early_signal_rule_codes or [])
        )
    )
    if rule_codes:
        rules_str = ", ".join(_RULE_LABELS.get(c, c) for c in rule_codes)
        lines.append(f"🔍 Правила: {html.escape(rules_str)}")

    if getattr(ad, "reason_title", None):
        lines.append(f"🧭 Причина: {html.escape(str(ad.reason_title))}")
    if getattr(ad, "reason_text", None):
        lines.append(f"   {html.escape(str(ad.reason_text))}")

    text = "\n".join(lines)

    # Кнопки зависят от состояния
    action_row: list[dict] = []
    if ad.alert_state in {
        AlertState.NORMAL,
        AlertState.EARLY_SIGNAL_SENT,
        AlertState.WARNING_SENT,
    }:
        action_row = [
            {
                "text": "🛑 Создать задачу на отключение",
                "callback_data": f"ad:disable_confirm:{ad.fb_ad_id}:{source}",
            }
        ]
    elif ad.alert_state == AlertState.STOP_SENT:
        action_row = [{"text": "⚙️ Открыть задачи", "callback_data": "cmd:tasks"}]
    elif ad.alert_state == AlertState.CLAIMED:
        action_row = [{"text": "⏳ В обработке", "callback_data": "noop"}]
    elif ad.alert_state == AlertState.DISABLED:
        action_row = [
            {
                "text": "↺ Сбросить статус в боте",
                "callback_data": f"ad:enable:{ad.fb_ad_id}",
            }
        ]

    keyboard_rows = []
    if action_row:
        keyboard_rows.append(action_row)
    keyboard_rows.append([{"text": "◀️ Назад", "callback_data": f"cmd:{source}"}])

    return text, {"inline_keyboard": keyboard_rows}


async def _render_disable_confirm(
    *,
    snapshot_token: str,
    confirm_callback: str,
    cancel_callback: str,
) -> tuple[str, dict]:
    """Экран подтверждения одиночного отключения."""
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
        return "❌ Объявление не найдено", _back_button("alerts")

    lines = ["🛑 <b>Подтвердите создание задачи на отключение</b>", ""]
    lines.extend(
        build_ad_identity_lines(
            campaign_name=ad.campaign_name,
            adset_name=ad.adset_name,
            ad_name=ad.ad_name,
            fb_ad_id=ad.fb_ad_id,
        )
    )
    lines.extend(
        [
            "",
            "ℹ️ Это ещё не выключение в Facebook, а постановка задачи в очередь.",
        ]
    )
    text = "\n".join(lines)
    keyboard = {
        "inline_keyboard": [
            [{"text": "✅ Создать задачу", "callback_data": confirm_callback}],
            [{"text": "◀️ Отмена", "callback_data": cancel_callback}],
        ]
    }
    return text, keyboard


def _render_disable_task_ack_text(*, ad_name: str, fb_ad_id: str, created_new: bool) -> str:
    """Короткое подтверждение создания задачи без перехвата STOP-цепочки."""
    title = (
        "✅ <b>Задача на отключение создана</b>"
        if created_new
        else "ℹ️ <b>Задача на отключение уже была в очереди</b>"
    )
    return (
        f"{title}\n\n"
        f"📢 <b>{html.escape(ad_name)}</b>\n"
        f"🆔 <code>{html.escape(fb_ad_id)}</code>\n\n"
        f"{_status_topic_notice('STOP')}"
    )


def _render_action_cancelled_text() -> str:
    """Текст для отмены локального confirm-сообщения."""
    return "ℹ️ <b>Действие отменено</b>\n\nСоздание задачи не запускалось."


async def _ack_disable_task_messages(
    client: TelegramBotClient,
    *,
    chat_id: str,
    message_thread_id: int | None,
    current_message_id: int,
    origin_message_id: int | None,
    text: str,
) -> None:
    """Обновляет confirm и исходное сообщение коротким подтверждением."""
    await _safe_edit_current_message(
        client,
        chat_id=chat_id,
        message_id=current_message_id,
        message_thread_id=message_thread_id,
        text=text,
    )
    if origin_message_id is not None and origin_message_id != current_message_id:
        await _safe_edit_current_message(
            client,
            chat_id=chat_id,
            message_id=origin_message_id,
            message_thread_id=message_thread_id,
            text=text,
        )


async def _render_disable_all_confirm() -> tuple[str, dict]:
    """Экран подтверждения массового отключения объявлений с алертами."""
    factory = get_session_factory()
    async with factory() as session:
        last_scan, batch_start = await _load_current_live_batch_bounds(session)
        if last_scan is None or batch_start is None:
            return "✅ Нет объявлений с алертами для отключения", _back_button()
        result = await session.execute(
            select(AdSnapshot)
            .where(
                AdSnapshot.alert_state.in_(
                    [
                        AlertState.EARLY_SIGNAL_SENT,
                        AlertState.WARNING_SENT,
                        AlertState.STOP_SENT,
                    ]
                ),
                AdSnapshot.last_observed_at >= batch_start,
            )
            .order_by(AdSnapshot.spend.desc())
        )
        ads = result.scalars().all()

    if not ads:
        text = "✅ Нет объявлений с алертами для отключения"
        return text, _back_button()

    lines = [
        f"🛑 <b>Отключить все объявления с активными сигналами?</b>\n\n"
        f"Будет создано {len(ads)} задач:\n"
    ]
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
        "🔎 <b>Ранние сигналы</b> — отдельный тип уведомления до лидов:\n"
        "• слабый CTR исходящих кликов\n"
        "• слабая доходимость до лендинга\n"
        "• дорогой просмотр лендинга\n\n"
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
        return (
            "🚫 <b>Отключённые объявления</b>\n\nСписок пуст — пока ничего не отключали.",
            _back_button(),
        )

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
        rules = (
            ", ".join(
                _RULE_LABELS.get(c, c)
                for c in (
                    ad.stop_rule_codes or ad.warning_rule_codes or ad.early_signal_rule_codes or []
                )
            )
            or "—"
        )
        lines.append(f"❌ {html.escape(ad.ad_name)} · {disabled_at}\n   {html.escape(rules)}")

    return "\n".join(lines), _ads_keyboard(
        ads, page, total_pages, has_alerts=False, prefix="disabled"
    )


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
        "🔧 <b>Настройки</b>\n\n"
        f"⏱ Интервал обновления: <b>{interval} сек</b>\n"
        f"📉 Порог предупреждения: <b>{warning_pct}%</b> от стопа\n\n"
        "💡 Быстрые изменения из CONTROL:\n"
        "<code>/set interval 60</code> — интервал 60 сек\n"
        "<code>/set warning 75</code> — порог 75%"
    )
    return text, {
        "inline_keyboard": [
            [{"text": "➕ Ещё", "callback_data": "cmd:more"}],
            [{"text": "◀️ CONTROL", "callback_data": "cmd:start"}],
        ]
    }


async def _render_help() -> tuple[str, dict]:
    """Список команд."""
    text = (
        "❓ <b>Помощь</b>\n\n"
        "Все общие команды работают только в topic <b>CONTROL</b>.\n\n"
        "/start — Короткий ops-хаб\n"
        "/status — Сводка за сегодня\n"
        "/ads — Активные объявления\n"
        "/alerts — Текущие сигналы\n"
        "/tasks — Очередь задач\n"
        "/settings — Быстрые настройки\n\n"
        "/more — Второй уровень CONTROL\n\n"
        "Действия из stream-topics не перетирают друг друга:\n"
        "EARLY/WARNING живут отдельно, STOP ведёт свой lifecycle, ENABLE ведёт включения."
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
    "today": _render_status,
    "status": _render_status,
    "ads": _render_ads,
    "alerts": _render_alerts,
    "offers": _render_offers,
    "rules": _render_rules,
    "disabled": _render_disabled,
    "settings": _render_settings,
    "help": _render_help,
    "tasks": _render_tasks,
    "more": _render_more,
}


async def handle_update(client: TelegramBotClient, update: dict) -> None:
    """Обработка одного Telegram update (message или callback_query)."""

    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data", "")
        chat = cq["message"]["chat"]
        chat_id = str(chat["id"])
        chat_type = chat.get("type")
        message_id = cq["message"]["message_id"]
        message_thread_id = cq["message"].get("message_thread_id")
        user = cq.get("from", {})
        username = user.get("username", "") or ""
        tg_user_id = str(user.get("id", ""))

        if is_private_chat(chat_type):
            await client.answer_callback_query(cq["id"], text="Контур перенесён в группу")
            return
        if chat_id != FORUM_SUPERGROUP_CHAT_ID:
            await client.answer_callback_query(cq["id"], text="Этот чат не привязан")
            return

        access = await resolve_telegram_access(
            chat_id=chat_id,
            telegram_user_id=tg_user_id,
            chat_type=chat_type,
        )
        if access is None:
            await client.answer_callback_query(cq["id"], text="Нет доступа")
            return

        if data == "noop" or data.startswith("noop"):
            await client.answer_callback_query(cq["id"])
            return

        if data == "confirm_cancel":
            await client.answer_callback_query(cq["id"], text="Отменено")
            await _safe_edit_current_message(
                client,
                chat_id=chat_id,
                message_id=message_id,
                message_thread_id=message_thread_id,
                text=_render_action_cancelled_text(),
            )
            return

        if data.startswith("cmd:"):
            if not _is_control_topic(message_thread_id, access):
                await client.answer_callback_query(cq["id"], text=CONTROL_TOPIC_CALLBACK_TEXT)
                return
            await client.answer_callback_query(cq["id"])
            cmd = data.split(":")[1]
            handler = COMMAND_HANDLERS.get(cmd)
            if not handler:
                return
            text, markup = await handler()
            await _safe_edit_current_message(
                client,
                chat_id=chat_id,
                message_id=message_id,
                message_thread_id=message_thread_id,
                text=text,
                reply_markup=markup,
            )
            return

        if data.startswith("enable_reco:task:"):
            await client.answer_callback_query(cq["id"])
            if not _can_manage_settings(access):
                await _send_current_topic_message(
                    client,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    text=_owner_only_text(),
                )
                return
            recommendation_event_id = data.split(":", 2)[2]
            task_info = await _create_enable_task_from_recommendation(
                recommendation_event_id=recommendation_event_id,
                tg_user_id=tg_user_id,
                username=username,
            )
            if not task_info:
                await _safe_edit_current_message(
                    client,
                    chat_id=chat_id,
                    message_id=message_id,
                    message_thread_id=message_thread_id,
                    text="❌ Не удалось создать задачу на включение — внутренняя ошибка.",
                )
                return

            if task_info["outcome"] in {"created", "existing"}:
                await broadcast_enable_task_queue_message(
                    ad_name=task_info["ad_name"] or "Без названия",
                    fb_ad_id=task_info["fb_ad_id"] or "",
                    requested_by_username=username,
                    created_new=bool(task_info.get("created_new")),
                    incident_key=recommendation_event_id,
                )
                logger.info(
                    "Создана задача на включение из рекомендации %s для %s (запросил @%s)",
                    recommendation_event_id,
                    task_info["fb_ad_id"],
                    username,
                )
                return

            await _safe_edit_current_message(
                client,
                chat_id=chat_id,
                message_id=message_id,
                message_thread_id=message_thread_id,
                text=task_info["detail"],
            )
            return

        if data.startswith("ads:page:"):
            if not _is_control_topic(message_thread_id, access):
                await client.answer_callback_query(cq["id"], text=CONTROL_TOPIC_CALLBACK_TEXT)
                return
            await client.answer_callback_query(cq["id"])
            try:
                page = int(data.split(":")[2])
            except (IndexError, ValueError):
                await _send_current_topic_message(
                    chat_id=chat_id,
                    client=client,
                    message_thread_id=message_thread_id,
                    text="❌ Не удалось открыть страницу объявлений. Попробуйте заново.",
                )
                return
            text, markup = await _render_ads(page=page)
            await _safe_edit_current_message(
                client,
                chat_id=chat_id,
                message_id=message_id,
                message_thread_id=message_thread_id,
                text=text,
                reply_markup=markup,
            )
            return

        if data.startswith("alerts:page:"):
            if not _is_control_topic(message_thread_id, access):
                await client.answer_callback_query(cq["id"], text=CONTROL_TOPIC_CALLBACK_TEXT)
                return
            await client.answer_callback_query(cq["id"])
            try:
                page = int(data.split(":")[2])
            except (IndexError, ValueError):
                await _send_current_topic_message(
                    client=client,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    text="❌ Не удалось открыть страницу алертов. Попробуйте заново.",
                )
                return
            text, markup = await _render_alerts(page=page)
            await _safe_edit_current_message(
                client,
                chat_id=chat_id,
                message_id=message_id,
                message_thread_id=message_thread_id,
                text=text,
                reply_markup=markup,
            )
            return

        if data.startswith("disabled:page:"):
            if not _is_control_topic(message_thread_id, access):
                await client.answer_callback_query(cq["id"], text=CONTROL_TOPIC_CALLBACK_TEXT)
                return
            await client.answer_callback_query(cq["id"])
            try:
                page = int(data.split(":")[2])
            except (IndexError, ValueError):
                await _send_current_topic_message(
                    client=client,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    text="❌ Не удалось открыть страницу отключённых объявлений. Попробуйте заново.",
                )
                return
            text, markup = await _render_disabled(page=page)
            await _safe_edit_current_message(
                client,
                chat_id=chat_id,
                message_id=message_id,
                message_thread_id=message_thread_id,
                text=text,
                reply_markup=markup,
            )
            return

        if data.startswith("ad:detail:"):
            if not _is_control_topic(message_thread_id, access):
                await client.answer_callback_query(cq["id"], text=CONTROL_TOPIC_CALLBACK_TEXT)
                return
            await client.answer_callback_query(cq["id"])
            parts = data.split(":", 3)
            fb_ad_id = parts[2]
            source = parts[3] if len(parts) > 3 else "ads"
            text, markup = await _render_ad_detail(fb_ad_id, source=source)
            await _safe_edit_current_message(
                client,
                chat_id=chat_id,
                message_id=message_id,
                message_thread_id=message_thread_id,
                text=text,
                reply_markup=markup,
            )
            return

        if data.startswith("ad:disable_confirm:"):
            if not _is_control_topic(message_thread_id, access):
                await client.answer_callback_query(cq["id"], text=CONTROL_TOPIC_CALLBACK_TEXT)
                return
            await client.answer_callback_query(cq["id"])
            parts = data.split(":", 3)
            fb_ad_id = parts[2]
            source = parts[3] if len(parts) > 3 else "ads"
            text, markup = await _render_disable_confirm(
                snapshot_token=fb_ad_id,
                confirm_callback=f"ad:disable:{fb_ad_id}:{source}:{message_id}",
                cancel_callback=f"ad:detail:{fb_ad_id}:{source}",
            )
            await _safe_edit_current_message(
                client,
                chat_id=chat_id,
                message_id=message_id,
                message_thread_id=message_thread_id,
                text=text,
                reply_markup=markup,
            )
            return

        if data.startswith("ad:disable:"):
            if not _is_control_topic(message_thread_id, access):
                await client.answer_callback_query(cq["id"], text=CONTROL_TOPIC_CALLBACK_TEXT)
                return
            await client.answer_callback_query(cq["id"])
            parts = data.split(":")
            fb_ad_id = parts[2]
            origin_message_id = int(parts[4]) if len(parts) > 4 else None
            task_info = await _create_disable_task(
                snapshot_token=fb_ad_id, tg_user_id=tg_user_id, username=username
            )
            if task_info:
                ack_text = _render_disable_task_ack_text(
                    ad_name=task_info["ad_name"],
                    fb_ad_id=task_info["fb_ad_id"],
                    created_new=bool(task_info.get("created_new")),
                )
                await _ack_disable_task_messages(
                    client,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    current_message_id=message_id,
                    origin_message_id=origin_message_id,
                    text=ack_text,
                )
                await broadcast_disable_task_queue_message(
                    ad_name=task_info["ad_name"],
                    fb_ad_id=task_info["fb_ad_id"],
                    requested_by_username=username,
                    created_new=bool(task_info.get("created_new")),
                    incident_key=task_info.get("incident_key") or "",
                    context=task_info.get("message_context"),
                )
                logger.info("Создана задача на отключение: %s (запросил @%s)", fb_ad_id, username)
            else:
                await _safe_edit_current_message(
                    client,
                    chat_id=chat_id,
                    message_id=message_id,
                    message_thread_id=message_thread_id,
                    text="❌ Не удалось создать задачу — объявление не найдено",
                )
            return

        if data.startswith("ad:enable:"):
            if not _is_control_topic(message_thread_id, access):
                await client.answer_callback_query(cq["id"], text=CONTROL_TOPIC_CALLBACK_TEXT)
                return
            await client.answer_callback_query(cq["id"])
            if not _can_manage_settings(access):
                await _send_current_topic_message(
                    client=client,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    text=_owner_only_text(),
                )
                return
            fb_ad_id = data.split(":", 2)[2]
            ad_name = await _reset_ad_state(fb_ad_id)
            if ad_name:
                await _safe_edit_current_message(
                    client,
                    chat_id=chat_id,
                    message_id=message_id,
                    message_thread_id=message_thread_id,
                    text=(
                        f"✅ <b>Статус в боте сброшен</b>\n\n"
                        f"📢 {html.escape(ad_name)}\n\n"
                        "Бот снова будет мониторить это объявление.\n\n"
                        "⚠️ Для фактического включения в Facebook используйте Менеджер рекламы."
                    ),
                    reply_markup={
                        "inline_keyboard": [
                            [{"text": "◀️ К объявлениям", "callback_data": "cmd:ads"}]
                        ]
                    },
                )
                logger.info(
                    "Состояние сброшено для объявления %s (запросил @%s)", fb_ad_id, username
                )
            else:
                await _safe_edit_current_message(
                    client,
                    chat_id=chat_id,
                    message_id=message_id,
                    message_thread_id=message_thread_id,
                    text="❌ Объявление не найдено",
                )
            return

        if data == "ads:disable_all":
            if not _is_control_topic(message_thread_id, access):
                await client.answer_callback_query(cq["id"], text=CONTROL_TOPIC_CALLBACK_TEXT)
                return
            await client.answer_callback_query(cq["id"])
            if not _can_manage_settings(access):
                await _send_current_topic_message(
                    client=client,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    text=_owner_only_text(),
                )
                return
            text, markup = await _render_disable_all_confirm()
            await _safe_edit_current_message(
                client,
                chat_id=chat_id,
                message_id=message_id,
                message_thread_id=message_thread_id,
                text=text,
                reply_markup=markup,
            )
            return

        if data == "ads:disable_all:confirm":
            if not _is_control_topic(message_thread_id, access):
                await client.answer_callback_query(cq["id"], text=CONTROL_TOPIC_CALLBACK_TEXT)
                return
            await client.answer_callback_query(cq["id"])
            if not _can_manage_settings(access):
                await _send_current_topic_message(
                    client=client,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    text=_owner_only_text(),
                )
                return
            count, failed = await _execute_disable_all(tg_user_id=tg_user_id, username=username)
            result_text = f"✅ <b>Создано задач на отключение: {count}</b>"
            if failed:
                result_text += f"\n⚠️ Не удалось обработать: {failed}"
            await _safe_edit_current_message(
                client,
                chat_id=chat_id,
                message_id=message_id,
                message_thread_id=message_thread_id,
                text=result_text,
                reply_markup={
                    "inline_keyboard": [[{"text": "◀️ К алертам", "callback_data": "cmd:alerts"}]]
                },
            )
            logger.info("Массовое отключение: %s задач создано (запросил @%s)", count, username)
            return

        if data.startswith("disable_confirm:"):
            await client.answer_callback_query(cq["id"])
            snapshot_token = data.split(":", 1)[1]
            text, markup = await _render_disable_confirm(
                snapshot_token=snapshot_token,
                confirm_callback=f"disable_execute:{snapshot_token}:{message_id}",
                cancel_callback="confirm_cancel",
            )
            await _send_current_topic_message(
                client=client,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                text=text,
                reply_markup=markup,
            )
            return

        if data.startswith("disable_execute:") or data.startswith("disable:"):
            await client.answer_callback_query(cq["id"])
            parts = data.split(":")
            snapshot_token = parts[1]
            origin_message_id = int(parts[2]) if len(parts) > 2 else None
            task_info = await _create_disable_task(
                snapshot_token=snapshot_token, tg_user_id=tg_user_id, username=username
            )
            if task_info:
                ack_text = _render_disable_task_ack_text(
                    ad_name=task_info["ad_name"],
                    fb_ad_id=task_info["fb_ad_id"],
                    created_new=bool(task_info.get("created_new")),
                )
                await _ack_disable_task_messages(
                    client,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    current_message_id=message_id,
                    origin_message_id=origin_message_id,
                    text=ack_text,
                )
                await broadcast_disable_task_queue_message(
                    ad_name=task_info["ad_name"],
                    fb_ad_id=task_info["fb_ad_id"],
                    requested_by_username=username,
                    created_new=bool(task_info.get("created_new")),
                    incident_key=task_info.get("incident_key") or "",
                    context=task_info.get("message_context"),
                )
                logger.info(
                    "Создана задача на отключение: %s (запросил @%s)",
                    task_info["fb_ad_id"],
                    username,
                )
            else:
                await _safe_edit_current_message(
                    client,
                    chat_id=chat_id,
                    message_id=message_id,
                    message_thread_id=message_thread_id,
                    text="❌ Не удалось создать задачу — снэпшот не найден",
                )
            return

        if data.startswith("snooze:"):
            await client.answer_callback_query(cq["id"])
            parts = data.split(":")
            try:
                snapshot_token = parts[1]
                raw_minutes = int(parts[2]) if len(parts) > 2 else 180
                minutes = _normalize_snooze_minutes(raw_minutes)
            except (IndexError, ValueError):
                await _send_current_topic_message(
                    client=client,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    text="❌ Не удалось отложить уведомление. Попробуйте открыть алерт заново.",
                )
                return
            ad_name, applied = await _snooze_alert(snapshot_token, minutes)
            if applied:
                text, markup = await _render_snoozed_alert_message(snapshot_token, minutes)
                if text is not None:
                    await _safe_edit_current_message(
                        client,
                        chat_id=chat_id,
                        message_id=message_id,
                        message_thread_id=message_thread_id,
                        text=text,
                        reply_markup=markup,
                    )
                logger.info(
                    "Снузер на %s мин для %s (запросил @%s)",
                    minutes,
                    snapshot_token,
                    username,
                )
            elif ad_name:
                await _safe_edit_current_message(
                    client,
                    chat_id=chat_id,
                    message_id=message_id,
                    message_thread_id=message_thread_id,
                    text=(
                        f"ℹ️ <b>Отложить уведомление нельзя</b>\n\n"
                        f"📢 {html.escape(ad_name)}\n"
                        "Авто-отключение уже запущено, поэтому снуз недоступен."
                    ),
                )
            else:
                await _safe_edit_current_message(
                    client,
                    chat_id=chat_id,
                    message_id=message_id,
                    message_thread_id=message_thread_id,
                    text="❌ Объявление не найдено",
                )
            return

        await client.answer_callback_query(cq["id"])
        return

    msg = update.get("message")
    if not msg:
        return

    chat = msg["chat"]
    chat_id = str(chat["id"])
    chat_type = chat.get("type")
    message_thread_id = msg.get("message_thread_id")
    text_in = (msg.get("text") or "").strip()
    user = msg.get("from", {})
    tg_user_id = str(user.get("id", ""))

    if not text_in.startswith("/"):
        return

    if is_private_chat(chat_type):
        await _send_current_topic_message(client, chat_id=chat_id, text=_private_chat_only_text())
        return

    if chat_id != FORUM_SUPERGROUP_CHAT_ID:
        await _send_current_topic_message(client, chat_id=chat_id, text=WRONG_GROUP_TEXT)
        return

    parts = text_in.split()
    cmd = parts[0].lstrip("/").split("@")[0].lower()

    settings_row = await _load_telegram_settings_row()
    forum_mode = is_forum_delivery_mode(getattr(settings_row, "delivery_mode", None))
    control_topic_id = getattr(settings_row, "control_topic_id", None) if settings_row else None

    if forum_mode and message_thread_id != control_topic_id:
        if cmd == "start" and len(parts) >= 2:
            authorized = await _try_authorize(
                client,
                chat_id,
                parts[1].strip(),
                msg,
                message_thread_id=message_thread_id,
            )
            if authorized:
                return
        await _send_current_topic_message(
            client,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=_control_topic_only_text(),
        )
        return

    if cmd == "start" and len(parts) >= 2:
        auth_code = parts[1].strip()
        authorized = await _try_authorize(
            client,
            chat_id,
            auth_code,
            msg,
            message_thread_id=message_thread_id,
        )
        if authorized:
            return

    access = await resolve_telegram_access(
        chat_id=chat_id,
        telegram_user_id=tg_user_id,
        chat_type=chat_type,
    )
    if access is None:
        await _send_current_topic_message(
            client,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=_auth_required_text(),
        )
        return

    if not _is_control_topic(message_thread_id, access):
        await _send_current_topic_message(
            client,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=_control_topic_only_text(),
        )
        return

    if cmd == "set" and len(parts) >= 3:
        if not _can_manage_settings(access):
            await _send_current_topic_message(
                client,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                text=_owner_only_text(),
            )
            return
        param = parts[1].lower()
        try:
            value = int(parts[2])
        except ValueError:
            await _send_current_topic_message(
                client,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                text="❌ Значение должно быть числом",
            )
            return

        if param == "interval" and 10 <= value <= 600:
            await _update_observer_setting(interval_seconds=value)
            await _send_current_topic_message(
                client,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                text=f"✅ Интервал обновления: <b>{value} сек</b>",
            )
        elif param == "warning" and 50 <= value <= 99:
            await _update_observer_setting(warning_percent_of_stop=value)
            await _send_current_topic_message(
                client,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                text=f"✅ Порог предупреждения: <b>{value}%</b>",
            )
        else:
            await _send_current_topic_message(
                client=client,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                text=(
                    "❌ Неверный параметр. Используйте:\n"
                    "<code>/set interval 60</code>\n"
                    "<code>/set warning 75</code>"
                ),
            )
        return

    handler = COMMAND_HANDLERS.get(cmd, _render_help)
    text, markup = await handler()
    await _send_current_topic_message(
        client,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        text=text,
        reply_markup=markup,
    )


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
                normalized = Decimal(str(value))
                obs.warning_percent_of_stop = normalized
                obs.cpc_warning_percent_of_stop = normalized
                obs.cpl_warning_percent_of_stop = normalized
                obs.cpr_warning_percent_of_stop = normalized
            elif key == "stop_percent_of_base":
                normalized = Decimal(str(value))
                obs.stop_percent_of_base = normalized
                obs.cpc_stop_percent_of_base = normalized
                obs.cpl_stop_percent_of_base = normalized
                obs.cpr_stop_percent_of_base = normalized
            else:
                setattr(obs, key, value)
        await session.commit()


async def _snooze_alert(snapshot_token: str, minutes: int) -> tuple[str | None, bool]:
    """Устанавливает snoozed_until на N минут для объявления по токену или fb_ad_id.

    Returns:
        (ad_name, applied): ad_name если объявление найдено, applied=True если snooze применён
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
                return None, False
            if ad.alert_state == AlertState.STOP_SENT:
                logger.info(
                    "Снузер для STOP-алерта %s недоступен — отключение уже запущено",
                    snapshot_token,
                )
                return ad.ad_name, False
            ad.snoozed_until = datetime.now(UTC) + timedelta(minutes=minutes)
            await session.commit()
            return ad.ad_name, True
    except Exception:
        logger.exception("Ошибка при установке снузера для %s", snapshot_token)
        return None, False


async def _reset_ad_state(fb_ad_id: str) -> str | None:
    """Сбрасывает alert_state объявления в NORMAL (явное действие пользователя).

    Returns:
        ad_name если успешно, None если объявление не найдено
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            ad = await session.scalar(select(AdSnapshot).where(AdSnapshot.fb_ad_id == fb_ad_id))
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
        _, batch_start = await _load_current_live_batch_bounds(session)
        if batch_start is None:
            return 0, 0
        result = await session.execute(
            select(AdSnapshot).where(
                AdSnapshot.alert_state.in_(
                    [
                        AlertState.EARLY_SIGNAL_SENT,
                        AlertState.WARNING_SENT,
                        AlertState.STOP_SENT,
                    ]
                ),
                AdSnapshot.last_observed_at >= batch_start,
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
    *,
    message_thread_id: int | None = None,
) -> bool:
    """Проверяет auth_code и привязывает chat_id к боту."""
    try:
        factory = get_session_factory()
        async with factory() as session:
            row = await get_or_create_telegram_settings(session)
            user = msg.get("from", {})
            telegram_user_id = str(user.get("id", ""))
            username = user.get("username", "") or ""
            first_name = user.get("first_name", "") or ""
            now = datetime.now(UTC)
            forum_mode = is_forum_delivery_mode(getattr(row, "delivery_mode", None))

            if forum_mode and message_thread_id != row.control_topic_id:
                await _send_current_topic_message(
                    client,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    text=AUTH_CODE_CONTROL_ONLY_TEXT,
                )
                return True

            if row.auth_code and row.auth_code == auth_code:
                if forum_mode and row.chat_id and row.chat_id != chat_id:
                    await _send_current_topic_message(
                        client,
                        chat_id=chat_id,
                        message_thread_id=message_thread_id,
                        text=WRONG_GROUP_TEXT,
                    )
                    return True
                row.chat_id = chat_id
                row.owner_telegram_user_id = telegram_user_id
                row.owner_username = username
                row.owner_first_name = first_name
                row.is_authorized = True
                row.auth_code = ""
                await session.commit()
                start_text, start_markup = await _render_start()
                await _send_current_topic_message(
                    chat_id=chat_id,
                    client=client,
                    message_thread_id=message_thread_id,
                    text=(
                        f"✅ <b>Авторизация прошла успешно!</b>\n\n"
                        f"Добро пожаловать, {html.escape(first_name or username or 'пользователь')}!\n"
                        "Telegram-контур подключён.\n"
                        "Рабочее меню находится в topic <b>CONTROL</b>.\n\n"
                        f"{start_text}"
                    ),
                    reply_markup=start_markup,
                )
                return True

            invite = await session.scalar(
                select(TelegramInvite).where(TelegramInvite.code == auth_code)
            )
            if (
                invite is not None
                and invite.used_at is None
                and invite.revoked_at is None
                and invite.expires_at > now
            ):
                recipient = await session.scalar(
                    select(TelegramRecipient).where(
                        TelegramRecipient.chat_id == chat_id,
                        or_(
                            TelegramRecipient.telegram_user_id == telegram_user_id,
                            TelegramRecipient.telegram_user_id == "",
                        ),
                    )
                )
                if recipient is None:
                    recipient = TelegramRecipient(
                        chat_id=chat_id,
                        telegram_user_id=telegram_user_id,
                        username=username,
                        first_name=first_name,
                        role=invite.role,
                        is_active=True,
                    )
                    session.add(recipient)
                else:
                    recipient.telegram_user_id = telegram_user_id
                    recipient.username = username
                    recipient.first_name = first_name
                    recipient.role = invite.role
                    recipient.is_active = True

                invite.used_at = now
                await session.commit()
                start_text, start_markup = await _render_start()
                await _send_current_topic_message(
                    client=client,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    text=(
                        f"✅ <b>Вы добавлены как получатель уведомлений!</b>\n\n"
                        f"Добро пожаловать, {html.escape(first_name or username or 'пользователь')}!\n"
                        "Теперь вы будете получать алерты AdGuard FB Bot.\n"
                        "Общее меню доступно в topic <b>CONTROL</b>.\n\n"
                        f"{start_text}"
                    ),
                    reply_markup=start_markup,
                )
                return True

            active_invite_exists = await session.scalar(
                select(TelegramInvite.id)
                .where(
                    TelegramInvite.used_at.is_(None),
                    TelegramInvite.revoked_at.is_(None),
                    TelegramInvite.expires_at > now,
                )
                .limit(1)
            )
            if row.auth_code or invite is not None or active_invite_exists is not None:
                await _send_current_topic_message(
                    client=client,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    text=(
                        "❌ Код недействителен, истёк или уже использован.\n"
                        "Попросите администратора сгенерировать новый код."
                    ),
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
        dict с fb_ad_id, ad_name, incident_key и контекстом сообщения; None если снэпшот не найден
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

            stable_open_state_token = snapshot.open_state_token or uuid.uuid4().hex
            snapshot.open_state_token = stable_open_state_token
            snapshot.telegram_group_key = stable_open_state_token
            message_context = await _build_disable_message_context_for_snapshot(session, snapshot)
            idempotency_key = _disable_task_idempotency_key(
                fb_ad_id=snapshot.fb_ad_id,
                incident_key=stable_open_state_token,
            )
            existing_active_task = await session.scalar(
                select(DisableTask).where(
                    DisableTask.fb_ad_id == snapshot.fb_ad_id,
                    DisableTask.open_state_token == stable_open_state_token,
                    DisableTask.status.in_(
                        [
                            DisableTaskStatus.PENDING,
                            DisableTaskStatus.RUNNING,
                            DisableTaskStatus.RETRYING,
                        ]
                    ),
                )
            )
            if existing_active_task is not None:
                return {
                    "fb_ad_id": snapshot.fb_ad_id,
                    "ad_name": snapshot.ad_name,
                    "campaign_name": snapshot.campaign_name,
                    "adset_name": snapshot.adset_name,
                    "created_new": False,
                    "incident_key": stable_open_state_token,
                    "message_context": message_context,
                }
            existing_task = await session.scalar(
                select(DisableTask).where(DisableTask.idempotency_key == idempotency_key)
            )
            if existing_task is not None:
                return {
                    "fb_ad_id": snapshot.fb_ad_id,
                    "ad_name": snapshot.ad_name,
                    "campaign_name": snapshot.campaign_name,
                    "adset_name": snapshot.adset_name,
                    "created_new": False,
                    "incident_key": stable_open_state_token,
                    "message_context": message_context,
                }

            snapshot.alert_state = AlertState.CLAIMED

            task = DisableTask(
                snapshot_id=snapshot.id,
                offer_id=snapshot.offer_id,
                fb_ad_id=snapshot.fb_ad_id,
                ad_name=snapshot.ad_name,
                open_state_token=stable_open_state_token,
                idempotency_key=idempotency_key,
                requested_by_telegram_user_id=tg_user_id,
                requested_by_username=username,
            )
            session.add(task)
            try:
                await session.flush()
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing_task = await session.scalar(
                    select(DisableTask).where(DisableTask.idempotency_key == idempotency_key)
                )
                if existing_task is not None:
                    return {
                        "fb_ad_id": snapshot.fb_ad_id,
                        "ad_name": snapshot.ad_name,
                        "campaign_name": snapshot.campaign_name,
                        "adset_name": snapshot.adset_name,
                        "created_new": False,
                        "incident_key": stable_open_state_token,
                        "message_context": message_context,
                    }
                raise

            return {
                "fb_ad_id": snapshot.fb_ad_id,
                "ad_name": snapshot.ad_name,
                "campaign_name": snapshot.campaign_name,
                "adset_name": snapshot.adset_name,
                "created_new": True,
                "incident_key": stable_open_state_token,
                "message_context": message_context,
            }
    except Exception:
        logger.exception("Ошибка при создании DisableTask")
        return None


async def _create_enable_task_from_recommendation(
    *,
    recommendation_event_id: str,
    tg_user_id: str,
    username: str,
) -> dict | None:
    """Создаёт EnableTask по recommendation event после повторной проверки."""
    try:
        factory = get_session_factory()
        async with factory() as session:
            result = await promote_recommendation_to_enable_task(
                session,
                event_id=recommendation_event_id,
                requested_by_telegram_user_id=tg_user_id,
                requested_by_username=username,
            )
            await session.commit()
            return {
                "outcome": result.outcome,
                "fb_ad_id": result.fb_ad_id,
                "ad_name": result.ad_name,
                "created_new": result.created_new,
                "detail": result.detail,
                "task_id": result.task_id,
                "task_status": result.task_status,
            }
    except Exception:
        logger.exception(
            "Ошибка при создании EnableTask из рекомендации %s",
            recommendation_event_id,
        )
        return {
            "outcome": "error",
            "detail": "❌ Не удалось создать задачу на включение — внутренняя ошибка.",
            "created_new": False,
            "fb_ad_id": None,
            "ad_name": None,
        }
