# -*- coding: utf-8 -*-
"""Telegram-бот: /start, /help, /app, /set.

Все настройки, статистика и управление объявлениями — в Mini-App.
Telegram-бот обрабатывает:
  - inline-кнопки disable/snooze/claim/enable_reco на алертах
  - команды /start, /help, /app, /set
  - авторизацию через код (/start КОД)
"""

from __future__ import annotations

import html
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from core.db import get_session_factory
from core.domain import AlertStage, AlertState, DisableTaskStatus
from core.enable_recommendations.service import promote_recommendation_to_enable_task
from core.models import (
    AdSnapshot,
    AlertEvent,
    AlertSnooze,
    DisableTask,
    FbAd,
    FbAdset,
    TelegramInvite,
    TelegramRecipient,
    TelegramSettings,
)
from core.settings_queries import get_or_create_observer_settings
from core.telegram.client import TelegramAPIError, TelegramBotClient
from core.telegram.delivery import (
    TelegramAdMessageContext,
    broadcast_disable_task_queue_message,
    broadcast_enable_task_queue_message,
)
from core.telegram.messaging import safe_edit_or_send_message
from core.telegram.renderer import TelegramAlertItem, build_ad_identity_lines, render_alert_message
from core.telegram.service import (
    get_or_create_telegram_settings,
    is_owner_role,
    is_private_chat,
    is_supergroup_chat,
    load_web_app_url,
    resolve_telegram_access,
)

logger = logging.getLogger(__name__)

AUTH_REQUIRED_TEXT = (
    "🔒 Вы ещё не авторизованы в Telegram-контуре. Отправьте команду <code>/start ВАШ_КОД</code>."
)
OWNER_ONLY_TEXT = "⛔ Это действие доступно только владельцу Telegram-контура."
PRIVATE_CHAT_ONLY_TEXT = "🧭 Бот работает только в личных сообщениях или в привязанном чате."
CONTROL_TOPIC_ONLY_TEXT = "🧭 Команды доступны только в авторизованном чате."
CONTROL_TOPIC_CALLBACK_TEXT = "Авторизуйтесь для использования"
WRONG_GROUP_TEXT = "🔒 Этот чат не привязан к рабочему Telegram-контуру."
AUTH_CODE_CONTROL_ONLY_TEXT = "🔒 Код активации нужно отправлять в привязанный чат."


# ==========================================
# Утилиты — snapshot helpers
# ==========================================


def _snapshot_ad_name(snap: AdSnapshot) -> str:
    """Получает ad_name через JOIN fb_ad → FbAd."""
    if snap.fb_ad is not None:
        return snap.fb_ad.ad_name or ""
    return ""


def _snapshot_campaign_name(snap: AdSnapshot) -> str | None:
    """Получает campaign_name через JOIN fb_ad → adset → campaign."""
    fb_ad = snap.fb_ad
    if fb_ad is not None and fb_ad.adset is not None and fb_ad.adset.campaign is not None:
        return fb_ad.adset.campaign.campaign_name
    return None


def _snapshot_adset_name(snap: AdSnapshot) -> str | None:
    """Получает adset_name через JOIN fb_ad → adset."""
    fb_ad = snap.fb_ad
    if fb_ad is not None and fb_ad.adset is not None:
        return fb_ad.adset.adset_name
    return None


def _snapshot_offer_id(snap: AdSnapshot) -> object | None:
    """Получает offer_id через JOIN fb_ad → adset → campaign."""
    fb_ad = snap.fb_ad
    if fb_ad is not None and fb_ad.adset is not None and fb_ad.adset.campaign is not None:
        return fb_ad.adset.campaign.offer_id
    return None


def _snapshot_offer_code(snap: AdSnapshot) -> str | None:
    """Получает offer_code через JOIN fb_ad → adset → campaign."""
    fb_ad = snap.fb_ad
    if fb_ad is not None and fb_ad.adset is not None and fb_ad.adset.campaign is not None:
        return fb_ad.adset.campaign.offer_code
    return None


def _snapshot_joinedload_options() -> list:
    """Возвращает опции eager loading для получения имён через цепочку."""
    return [joinedload(AdSnapshot.fb_ad).joinedload(FbAd.adset).joinedload(FbAdset.campaign)]


# ==========================================
# Утилиты — прочие
# ==========================================


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
    return None


def _can_manage_settings(access) -> bool:
    """Проверяет, что пользователь может менять глобальные настройки."""
    return access is not None and is_owner_role(access.role)


def _is_control_topic(message_thread_id: int | None, access) -> bool:
    """Всегда True — forum-topic режим удалён, все сообщения принимаются."""
    return access is not None


async def _guard_control_topic(
    client: TelegramBotClient,
    *,
    message_thread_id: int | None,
    access,
    callback_query_id: str,
) -> bool:
    """Проверяет доступ. Возвращает True если НЕТ доступа."""
    if _is_control_topic(message_thread_id, access):
        return False
    await client.answer_callback_query(callback_query_id, text=CONTROL_TOPIC_CALLBACK_TEXT)
    return True


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


async def _load_telegram_settings_row():
    """Загружает текущую строку Telegram-настроек из БД."""
    factory = get_session_factory()
    async with factory() as session:
        return await session.scalar(
            select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
        )


# ==========================================
# Alert helpers
# ==========================================


async def _load_latest_alert_event_for_incident(
    session,
    *,
    fb_ad_id: str,
    incident_key: str,
    stage: AlertStage | None = None,
):
    """Возвращает последнее alert-событие по объявлению и incident."""
    ad_id_subq = select(FbAd.id).where(FbAd.fb_ad_id == fb_ad_id).scalar_subquery()
    stmt = select(AlertEvent).where(AlertEvent.ad_id == ad_id_subq)
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
        dict.fromkeys((snapshot.stop_rule_codes or []) + (snapshot.warning_rule_codes or []))
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
        campaign_name=_snapshot_campaign_name(snapshot),
        adset_name=_snapshot_adset_name(snapshot),
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
            select(AdSnapshot)
            .options(*_snapshot_joinedload_options())
            .where(AdSnapshot.open_state_token == snapshot_token)
        )
        if ad is None:
            ad = await session.scalar(
                select(AdSnapshot)
                .options(*_snapshot_joinedload_options())
                .where(AdSnapshot.fb_ad_id == snapshot_token)
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
                else []
            )
        )

        note_until = datetime.now(UTC) + timedelta(minutes=minutes)
        web_app_url = await load_web_app_url()
        message = render_alert_message(
            stage=stage,
            items=[
                TelegramAlertItem(
                    snapshot_id=incident_key,
                    fb_ad_id=ad.fb_ad_id,
                    ad_name=_snapshot_ad_name(ad),
                    campaign_name=_snapshot_campaign_name(ad),
                    adset_name=_snapshot_adset_name(ad),
                    offer_code=_snapshot_offer_code(ad),
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
            web_app_url=web_app_url,
        )
        return message.text, message.reply_markup


def _back_button(target: str = "start") -> dict:
    """Кнопка возврата."""
    labels = {
        "start": "◀️ CONTROL",
        "alerts": "◀️ К алертам",
    }
    return {
        "inline_keyboard": [
            [{"text": labels.get(target, "◀️ Назад"), "callback_data": f"cmd:{target}"}]
        ]
    }


# ==========================================
# Подтверждение отключения (legacy)
# ==========================================


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
            select(AdSnapshot)
            .options(*_snapshot_joinedload_options())
            .where(AdSnapshot.open_state_token == snapshot_token)
        )
        if ad is None:
            ad = await session.scalar(
                select(AdSnapshot)
                .options(*_snapshot_joinedload_options())
                .where(AdSnapshot.fb_ad_id == snapshot_token)
            )

    if ad is None:
        return "❌ Объявление не найдено", _back_button("alerts")

    lines = ["🛑 <b>Подтвердите создание задачи на отключение</b>", ""]
    lines.extend(
        build_ad_identity_lines(
            campaign_name=_snapshot_campaign_name(ad),
            adset_name=_snapshot_adset_name(ad),
            ad_name=_snapshot_ad_name(ad),
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


# ==========================================
# Команды /start, /help, /app
# ==========================================


async def _render_start(
    client: TelegramBotClient,
    *,
    chat_id: str,
    message_thread_id: int | None,
) -> None:
    """Главное меню — приветствие и кнопка открыть приложение."""
    from core.telegram.service import load_web_app_url

    web_app_url = await load_web_app_url()
    text = (
        "👋 <b>FB Stop Bot</b>\n\n"
        "Все настройки, объявления и действия — в приложении.\n\n"
        "Команды: /app, /help"
    )
    markup = None
    if web_app_url and web_app_url.startswith("https://"):
        # url-кнопка работает в группах (web_app — только в ЛС, иначе BUTTON_TYPE_INVALID).
        markup = {
            "inline_keyboard": [
                [
                    {"text": "🚀 Открыть приложение", "url": web_app_url},
                ]
            ]
        }
    await _send_current_topic_message(
        client,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        text=text,
        reply_markup=markup,
    )


async def _render_help(
    client: TelegramBotClient,
    *,
    chat_id: str,
    message_thread_id: int | None,
) -> None:
    """Справка по командам бота."""
    text = (
        "<b>Команды</b>\n\n"
        "/start — главное меню\n"
        "/app — открыть приложение\n"
        "/ask &lt;вопрос&gt; — спросить AI-помощника\n"
        "/bind_thread &lt;WARNING|STOP|ENABLE|OPS|GENERAL&gt; — привязать текущий форумный топик к стриму\n"
        "/init_topics — создать недостающие форумные топики (WARNING/STOP/ENABLE/OPS) и привязать их\n"
        "/help — эта справка\n\n"
        "Все настройки, статистика, отключение объявлений и снуз — в Mini-App. "
        "Откройте приложение из меню слева от поля ввода или командой /app."
    )
    await _send_current_topic_message(
        client,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        text=text,
    )


_BIND_THREAD_STREAMS = ("WARNING", "STOP", "ENABLE", "OPS", "GENERAL")
_BIND_THREAD_USAGE = (
    "❌ Использование: <code>/bind_thread WARNING|STOP|ENABLE|OPS|GENERAL</code>\n"
    "Команда должна быть отправлена из нужного форумного топика."
)


async def _handle_bind_thread(
    client: TelegramBotClient,
    *,
    chat_id: str,
    chat_type: str | None,
    message_thread_id: int | None,
    access,
    parts: list[str],
) -> None:
    """Привязывает текущий форумный топик к выбранному стриму уведомлений."""
    if not _can_manage_settings(access):
        await _send_current_topic_message(
            client,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=OWNER_ONLY_TEXT,
        )
        return

    if not is_supergroup_chat(chat_type) or message_thread_id is None:
        await _send_current_topic_message(
            client,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=(
                "❌ Команду <code>/bind_thread</code> нужно вызывать из конкретного "
                "форумного топика супергруппы."
            ),
        )
        return

    if len(parts) < 2:
        await _send_current_topic_message(
            client,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=_BIND_THREAD_USAGE,
        )
        return

    stream_arg = parts[1].strip().upper()
    if stream_arg not in _BIND_THREAD_STREAMS:
        await _send_current_topic_message(
            client,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=_BIND_THREAD_USAGE,
        )
        return

    column_name = f"thread_id_{stream_arg.lower()}"
    factory = get_session_factory()
    async with factory() as session:
        settings_row = await get_or_create_telegram_settings(session)
        setattr(settings_row, column_name, int(message_thread_id))
        await session.commit()

    await _send_current_topic_message(
        client,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        text=(
            f"✅ Топик привязан как <b>{stream_arg}</b> "
            f"(thread_id=<code>{message_thread_id}</code>)."
        ),
    )


# Целевые форумные топики: stream-ключ → (имя топика, цвет иконки).
# Цвета — из официального списка Bot API createForumTopic.icon_color.
_INIT_TOPICS_SPEC: tuple[tuple[str, str, int], ...] = (
    ("warning", "⚠️ WARNING", 0xF1A30B),
    ("stop", "🛑 STOP", 0xFB6F5F),
    ("enable", "▶️ ENABLE", 0x6FB9F0),
    ("ops", "🛠 OPS", 0x8EEE98),
)


async def _handle_init_topics(
    client: TelegramBotClient,
    *,
    chat_id: str,
    chat_type: str | None,
    message_thread_id: int | None,
    access,
) -> None:
    """Создаёт недостающие форумные топики и привязывает их к стримам.

    Существующие привязки не трогает — пересоздание топиков не делает.
    General (thread_id=1) проставляется автоматически, если ещё не задан.
    """
    if not _can_manage_settings(access):
        await _send_current_topic_message(
            client,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=OWNER_ONLY_TEXT,
        )
        return

    if not is_supergroup_chat(chat_type):
        await _send_current_topic_message(
            client,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text="❌ Команду <code>/init_topics</code> можно вызывать только в супергруппе.",
        )
        return

    factory = get_session_factory()
    created: list[tuple[str, int]] = []
    kept: list[tuple[str, int]] = []
    errors: list[str] = []

    async with factory() as session:
        settings_row = await get_or_create_telegram_settings(session)

        # General — всегда thread_id=1 в форумах. Привязываем, если ещё пуст.
        if settings_row.thread_id_general is None:
            settings_row.thread_id_general = 1

        for stream_key, topic_name, icon_color in _INIT_TOPICS_SPEC:
            column = f"thread_id_{stream_key}"
            current = getattr(settings_row, column, None)
            if current is not None:
                kept.append((stream_key.upper(), int(current)))
                continue
            try:
                result = await client.create_forum_topic(
                    chat_id=chat_id,
                    name=topic_name,
                    icon_color=icon_color,
                )
            except TelegramAPIError as exc:
                logger.exception("createForumTopic failed for %s", stream_key)
                errors.append(f"{stream_key.upper()}: {exc.description or 'ошибка API'}")
                continue
            new_thread_id = int(result.get("message_thread_id") or 0)
            if new_thread_id <= 0:
                errors.append(f"{stream_key.upper()}: пустой message_thread_id в ответе")
                continue
            setattr(settings_row, column, new_thread_id)
            created.append((stream_key.upper(), new_thread_id))

        await session.commit()

    lines: list[str] = ["<b>Инициализация форумных топиков</b>"]
    if created:
        lines.append("\n✅ Созданы и привязаны:")
        for name, tid in created:
            lines.append(f"  • {name} — thread_id=<code>{tid}</code>")
    if kept:
        lines.append("\nℹ️ Уже были привязаны (не пересоздавались):")
        for name, tid in kept:
            lines.append(f"  • {name} — thread_id=<code>{tid}</code>")
    if errors:
        lines.append("\n⚠️ Ошибки:")
        for err in errors:
            lines.append(f"  • {err}")
        lines.append(
            "\nПроверьте, что бот — администратор супергруппы с правом <b>can_manage_topics</b>."
        )
    if not created and not errors and not kept:
        lines.append("\nНечего делать.")

    await _send_current_topic_message(
        client,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        text="\n".join(lines),
    )


async def _cmd_app(
    client: TelegramBotClient,
    *,
    chat_id: str,
    message_thread_id: int | None,
) -> None:
    """Команда /app — открыть мини-приложение."""
    from core.telegram.service import load_web_app_url

    url = await load_web_app_url()
    if not url:
        await _send_current_topic_message(
            client,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text="📱 Mini-app не настроена. Задайте WEB_APP_URL в .env или настройках.",
        )
        return

    # url-кнопка работает и в группах, и в ЛС (web_app — только в ЛС).
    markup = {
        "inline_keyboard": [
            [
                {
                    "text": "🚀 Открыть приложение",
                    "url": url,
                }
            ]
        ]
    }
    await _send_current_topic_message(
        client,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        text="📱 <b>FB Stop Bot — приложение</b>",
        reply_markup=markup,
    )


# ==========================================
# Команда /ask — AI-помощник
# ==========================================


async def _cmd_ask(
    client: TelegramBotClient,
    *,
    chat_id: str,
    message_thread_id: int | None,
    question: str,
    tg_user_id: str,
) -> None:
    """Команда /ask <вопрос> — one-shot запрос к AI с tools."""
    if not question:
        await _send_current_topic_message(
            client,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text="❓ Использование: <code>/ask твой вопрос</code>",
        )
        return

    from core.ai_assistant.chat import (
        ChatMessage,
        ChatRateLimitedError,
        ChatSession,
    )
    from core.ai_assistant.client import AIUnavailableError

    try:
        session = ChatSession(allow_tools=True)
        result = await session.ask(
            [ChatMessage(role="user", content=question)],
            client_key=f"tg:{tg_user_id}",
        )
    except ChatRateLimitedError as exc:
        await _send_current_topic_message(
            client,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=f"⏳ {html.escape(str(exc))}",
        )
        return
    except AIUnavailableError as exc:
        await _send_current_topic_message(
            client,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=f"🤖 AI недоступен: {html.escape(str(exc))}",
        )
        return
    except Exception:
        logger.exception("Ошибка /ask")
        await _send_current_topic_message(
            client,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text="🤖 Внутренняя ошибка AI-помощника.",
        )
        return

    answer = result.answer.strip() or "(пустой ответ)"
    suffix = ""
    if result.tool_calls:
        names = ", ".join(t.name for t in result.tool_calls)
        suffix = f"\n\n<i>Использованы инструменты: {html.escape(names)}</i>"
    await _send_current_topic_message(
        client,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        text=f"🤖 {answer}{suffix}",
    )


# ==========================================
# Команда /digest — ручной запрос daily digest
# ==========================================


async def _cmd_digest(
    client: TelegramBotClient,
    *,
    chat_id: str,
    message_thread_id: int | None,
) -> None:
    """Команда /digest — немедленно отправляет daily digest за вчера."""
    from core.db import get_session_factory
    from core.telegram.digest import render_digest_message
    from core.telegram.digest_queries import get_digest_data

    try:
        factory = get_session_factory()
        async with factory() as session:
            data = await get_digest_data(session, now=datetime.now(UTC))
        text = render_digest_message(data)
    except Exception:
        logger.exception("Ошибка при формировании /digest")
        await _send_current_topic_message(
            client,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text="❌ Не удалось получить данные дайджеста. Попробуйте позже.",
        )
        return

    await _send_current_topic_message(
        client,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        text=text,
    )


# ==========================================
# Маршрутизация
# ==========================================

COMMAND_HANDLERS = {
    "start": _render_start,
    "help": _render_help,
    # /app маршрутизируется отдельно ниже (он принимает другие аргументы)
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
            await client.answer_callback_query(cq["id"], text="Контур не активирован")
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

        if data.startswith("enable_reco:task:"):
            await client.answer_callback_query(cq["id"])
            if not _can_manage_settings(access):
                await _send_current_topic_message(
                    client,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    text=OWNER_ONLY_TEXT,
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

        if data.startswith("disable_confirm:"):
            await client.answer_callback_query(cq["id"])
            if not _can_manage_settings(access):
                await _send_current_topic_message(
                    client,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    text=OWNER_ONLY_TEXT,
                )
                return
            # Формат callback_data:
            #   новый:  disable_confirm:{open_state_token}:{fb_ad_id}
            #   legacy: disable_confirm:{open_state_token | fb_ad_id}
            confirm_parts = data.split(":")
            snapshot_token = confirm_parts[1] if len(confirm_parts) > 1 else ""
            fb_ad_id_hint = confirm_parts[2] if len(confirm_parts) > 2 else ""
            # Stale-проверка: callback_token должен соответствовать активному
            # snapshot.open_state_token. Если в новой схеме известен fb_ad_id —
            # проверяем строго: токен инцидента не должен отличаться от текущего.
            if fb_ad_id_hint:
                is_fresh = await _validate_alert_token(fb_ad_id=fb_ad_id_hint, token=snapshot_token)
                if not is_fresh:
                    await client.answer_callback_query(
                        cq["id"], text="Кнопка устарела, обновите алерт"
                    )
                    return
            execute_callback = (
                f"disable_execute:{snapshot_token}:{message_id}:{fb_ad_id_hint}"
                if fb_ad_id_hint
                else f"disable_execute:{snapshot_token}:{message_id}"
            )
            text, markup = await _render_disable_confirm(
                snapshot_token=snapshot_token,
                confirm_callback=execute_callback,
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
            if not _can_manage_settings(access):
                await _send_current_topic_message(
                    client,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    text=OWNER_ONLY_TEXT,
                )
                return
            # Формат callback_data:
            #   новый:  disable_execute:{open_state_token}:{origin_message_id}:{fb_ad_id}
            #   legacy: disable_execute:{snapshot_token}:{origin_message_id?}
            parts = data.split(":")
            snapshot_token = parts[1] if len(parts) > 1 else ""
            origin_message_id = None
            if len(parts) > 2 and parts[2]:
                try:
                    origin_message_id = int(parts[2])
                except ValueError:
                    origin_message_id = None
            fb_ad_id_hint = parts[3] if len(parts) > 3 else ""
            if fb_ad_id_hint:
                # Новая схема: токен инцидента должен совпадать со снапшотом.
                is_fresh = await _validate_alert_token(fb_ad_id=fb_ad_id_hint, token=snapshot_token)
                if not is_fresh:
                    await client.answer_callback_query(
                        cq["id"], text="Кнопка устарела, обновите алерт"
                    )
                    return
            else:
                # Legacy callback без fb_ad_id — фиксируем в логе для отладки.
                logger.info(
                    "disable_execute legacy callback (без fb_ad_id): token=%s",
                    snapshot_token,
                )
            task_info = await _create_disable_task(
                snapshot_token=snapshot_token,
                tg_user_id=tg_user_id,
                username=username,
                callback_token=snapshot_token if fb_ad_id_hint else None,
                fb_ad_id_hint=fb_ad_id_hint or None,
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

        # Новый формат: snooze:<fb_ad_id>:<minutes>:<token> (4 части после split ":"))
        if data.startswith("snooze:") and len(data.split(":")) == 4:
            parts = data.split(":")
            fb_ad_id_snz = parts[1]
            try:
                minutes_snz = int(parts[2])
            except ValueError:
                await client.answer_callback_query(cq["id"], text="Кнопка устарела")
                return
            token_snz = parts[3]
            snz_valid = await _validate_alert_token(fb_ad_id=fb_ad_id_snz, token=token_snz)
            if not snz_valid:
                await client.answer_callback_query(cq["id"], text="Кнопка устарела")
                return
            snooze_ok = await _create_alert_snooze(
                fb_ad_id=fb_ad_id_snz,
                minutes=minutes_snz,
                tg_user_id=tg_user_id,
            )
            if snooze_ok:
                until_dt = datetime.now(UTC) + timedelta(minutes=minutes_snz)
                until_str = until_dt.strftime("%H:%M")
                await client.answer_callback_query(cq["id"], text="Снуз поставлен")
                # Отдельное plain-text сообщение со статусом — оригинальный алерт
                # не трогаем, поэтому неэкранированные символы в имени объявления
                # (например `<` или `&`) не ломают разметку.
                try:
                    await client.send_message(
                        chat_id=chat_id,
                        message_thread_id=message_thread_id,
                        text=f"😴 Снуз до {until_str} (UTC)",
                        parse_mode=None,
                    )
                except Exception:
                    logger.debug("Не удалось отправить уведомление о snooze")
                logger.info(
                    "AlertSnooze: %s мин для %s (запросил @%s)",
                    minutes_snz,
                    fb_ad_id_snz,
                    username,
                )
            else:
                await client.answer_callback_query(cq["id"], text="Не удалось поставить снуз")
            return

        if data.startswith("claim:"):
            parts = data.split(":")
            if len(parts) < 3:
                await client.answer_callback_query(cq["id"], text="Кнопка устарела")
                return
            fb_ad_id_clm = parts[1]
            token_clm = parts[2]
            clm_valid = await _validate_alert_token(fb_ad_id=fb_ad_id_clm, token=token_clm)
            if not clm_valid:
                await client.answer_callback_query(cq["id"], text="Кнопка устарела")
                return
            claimed_ok = await _claim_alert(fb_ad_id=fb_ad_id_clm, token=token_clm)
            if claimed_ok:
                await client.answer_callback_query(cq["id"], text="Алерт снят")
                suffix = f"@{username}" if username else tg_user_id
                # Отдельное plain-text сообщение со статусом — оригинальный
                # алерт не трогаем, чтобы избежать parse-ошибок HTML на
                # неэкранированных символах в имени объявления.
                try:
                    await client.send_message(
                        chat_id=chat_id,
                        message_thread_id=message_thread_id,
                        text=(
                            f"✅ Алерт снят пользователем {suffix}, объявление продолжает работать."
                        ),
                        parse_mode=None,
                    )
                except Exception:
                    logger.debug("Не удалось отправить уведомление о claim")
                logger.info("Claim алерта %s (запросил @%s)", fb_ad_id_clm, username)
            else:
                await client.answer_callback_query(cq["id"], text="Кнопка устарела")
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

    parts = text_in.split()
    cmd = parts[0].lstrip("/").split("@")[0].lower()

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
            text=AUTH_REQUIRED_TEXT,
        )
        return

    if cmd == "set" and len(parts) >= 3:
        if not _can_manage_settings(access):
            await _send_current_topic_message(
                client,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                text=OWNER_ONLY_TEXT,
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

    if cmd == "bind_thread":
        await _handle_bind_thread(
            client,
            chat_id=chat_id,
            chat_type=chat_type,
            message_thread_id=message_thread_id,
            access=access,
            parts=parts,
        )
        return

    if cmd == "init_topics":
        await _handle_init_topics(
            client,
            chat_id=chat_id,
            chat_type=chat_type,
            message_thread_id=message_thread_id,
            access=access,
        )
        return

    if cmd == "app":
        await _cmd_app(client, chat_id=chat_id, message_thread_id=message_thread_id)
        return

    if cmd == "digest":
        await _cmd_digest(client, chat_id=chat_id, message_thread_id=message_thread_id)
        return

    if cmd == "ask":
        question = text_in[len("/ask") :].strip()
        await _cmd_ask(
            client,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            question=question,
            tg_user_id=tg_user_id,
        )
        return

    handler = COMMAND_HANDLERS.get(cmd)
    if handler is not None:
        await handler(client, chat_id=chat_id, message_thread_id=message_thread_id)
        return

    # Неизвестная команда
    await _send_current_topic_message(
        client,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        text="Команда не поддерживается. Откройте приложение: /app",
    )


# ==========================================
# Вспомогательные функции БД
# ==========================================


async def _update_observer_setting(**kwargs: int) -> None:
    """Обновляет настройки Observer в БД."""
    factory = get_session_factory()
    async with factory() as session:
        obs = await get_or_create_observer_settings(session)
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


async def _validate_alert_token(*, fb_ad_id: str, token: str) -> bool:
    """Проверяет, что snapshot с fb_ad_id имеет open_state_token == token.

    Возвращает True если токен валиден (соответствует активному инциденту).
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            snapshot = await session.scalar(
                select(AdSnapshot).where(
                    AdSnapshot.fb_ad_id == fb_ad_id,
                    AdSnapshot.open_state_token == token,
                )
            )
            return snapshot is not None
    except Exception:
        logger.exception("Ошибка при валидации alert-токена %s/%s", fb_ad_id, token)
        return False


async def _create_alert_snooze(*, fb_ad_id: str, minutes: int, tg_user_id: str) -> bool:
    """Создаёт запись AlertSnooze в БД.

    Возвращает True при успехе.
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            snooze = AlertSnooze(
                fb_ad_id=fb_ad_id,
                snoozed_until=datetime.now(UTC) + timedelta(minutes=minutes),
                created_by_telegram_user_id=tg_user_id,
            )
            session.add(snooze)
            await session.commit()
            return True
    except Exception:
        logger.exception("Ошибка при создании AlertSnooze для %s", fb_ad_id)
        return False


async def _claim_alert(*, fb_ad_id: str, token: str) -> bool:
    """Переводит AlertEvent/AdSnapshot в состояние CLAIMED по fb_ad_id+token.

    Возвращает True при успехе, False если токен устарел или запись не найдена.
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            snapshot = await session.scalar(
                select(AdSnapshot).where(
                    AdSnapshot.fb_ad_id == fb_ad_id,
                    AdSnapshot.open_state_token == token,
                    AdSnapshot.alert_state.in_([AlertState.WARNING_SENT, AlertState.STOP_SENT]),
                )
            )
            if snapshot is None:
                return False
            snapshot.alert_state = AlertState.CLAIMED
            await session.commit()
            return True
    except Exception:
        logger.exception("Ошибка при claim алерта %s", fb_ad_id)
        return False


async def _snooze_alert(snapshot_token: str, minutes: int) -> tuple[str | None, bool]:
    """Устанавливает snoozed_until на N минут для объявления по токену или fb_ad_id.

    Returns:
        (ad_name, applied): ad_name если объявление найдено, applied=True если snooze применён
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            ad = await session.scalar(
                select(AdSnapshot)
                .options(*_snapshot_joinedload_options())
                .where(AdSnapshot.open_state_token == snapshot_token)
            )
            if ad is None:
                ad = await session.scalar(
                    select(AdSnapshot)
                    .options(*_snapshot_joinedload_options())
                    .where(AdSnapshot.fb_ad_id == snapshot_token)
                )
            if ad is None:
                return None, False
            ad_name = _snapshot_ad_name(ad)
            if ad.alert_state == AlertState.STOP_SENT:
                logger.info(
                    "Снузер для STOP-алерта %s недоступен — отключение уже запущено",
                    snapshot_token,
                )
                return ad_name, False
            ad.snoozed_until = datetime.now(UTC) + timedelta(minutes=minutes)
            await session.commit()
            return ad_name, True
    except Exception:
        logger.exception("Ошибка при установке снузера для %s", snapshot_token)
        return None, False


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

            if row.auth_code and secrets.compare_digest(row.auth_code or "", auth_code or ""):
                row.chat_id = chat_id
                row.owner_telegram_user_id = telegram_user_id
                row.owner_username = username
                row.owner_first_name = first_name
                row.is_authorized = True
                row.auth_code = ""
                await session.commit()
                await _send_current_topic_message(
                    chat_id=chat_id,
                    client=client,
                    message_thread_id=message_thread_id,
                    text=(
                        f"✅ <b>Авторизация прошла успешно!</b>\n\n"
                        f"Добро пожаловать, {html.escape(first_name or username or 'пользователь')}!\n"
                        "Telegram-контур подключён.\n"
                        "Рабочее меню находится в topic <b>CONTROL</b>. "
                        "Используйте /start для главного меню."
                    ),
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
                await _send_current_topic_message(
                    client=client,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    text=(
                        f"✅ <b>Вы добавлены как получатель уведомлений!</b>\n\n"
                        f"Добро пожаловать, {html.escape(first_name or username or 'пользователь')}!\n"
                        "Теперь вы будете получать алерты AdGuard FB Bot.\n"
                        "Общее меню доступно в topic <b>CONTROL</b> командой /start."
                    ),
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
    callback_token: str | None = None,
    fb_ad_id_hint: str | None = None,
) -> dict | None:
    """Создаёт DisableTask в БД по токену снэпшота или fb_ad_id.

    Args:
        snapshot_token: токен из callback (open_state_token или fb_ad_id legacy).
        callback_token: явный токен инцидента из callback_data (новая схема).
            Используется в idempotency_key вместо текущего snapshot.open_state_token,
            чтобы повторные клики по одной и той же кнопке не плодили дубль задач,
            даже если observer успел открыть новый incident.
        fb_ad_id_hint: явный fb_ad_id из новой схемы callback_data — позволяет
            искать снапшот напрямую, минуя fallback по open_state_token.

    Returns:
        dict с fb_ad_id, ad_name, incident_key и контекстом сообщения; None если снэпшот не найден
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            snapshot = None
            # Новая схема: ищем снэпшот по явному fb_ad_id из callback_data.
            if fb_ad_id_hint:
                result = await session.execute(
                    select(AdSnapshot)
                    .options(*_snapshot_joinedload_options())
                    .where(AdSnapshot.fb_ad_id == fb_ad_id_hint)
                )
                snapshot = result.scalar_one_or_none()

            if snapshot is None:
                # Legacy: ищем по open_state_token
                result = await session.execute(
                    select(AdSnapshot)
                    .options(*_snapshot_joinedload_options())
                    .where(AdSnapshot.open_state_token == snapshot_token)
                )
                snapshot = result.scalar_one_or_none()

            if snapshot is None:
                # Пробуем по fb_ad_id (legacy callback, где snapshot_token == fb_ad_id)
                result = await session.execute(
                    select(AdSnapshot)
                    .options(*_snapshot_joinedload_options())
                    .where(AdSnapshot.fb_ad_id == snapshot_token)
                )
                snapshot = result.scalar_one_or_none()

            if snapshot is None:
                logger.warning("Снэпшот не найден по токену %s", snapshot_token)
                return None

            # Если есть явный callback_token из новой схемы — используем его как
            # incident_key. Это защищает от создания дубля при retry-кликах после
            # того, как observer открыл новый incident на том же snapshot.
            if callback_token:
                stable_open_state_token = callback_token
            else:
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
                    DisableTask.ad_id == snapshot.ad_id,
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
            ad_name = _snapshot_ad_name(snapshot)
            if existing_active_task is not None:
                return {
                    "fb_ad_id": snapshot.fb_ad_id,
                    "ad_name": ad_name,
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
                    "ad_name": ad_name,
                    "created_new": False,
                    "incident_key": stable_open_state_token,
                    "message_context": message_context,
                }

            snapshot.alert_state = AlertState.CLAIMED

            task = DisableTask(
                ad_id=snapshot.ad_id,
                snapshot_id=snapshot.id,
                offer_id=_snapshot_offer_id(snapshot),
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
                        "ad_name": ad_name,
                        "created_new": False,
                        "incident_key": stable_open_state_token,
                        "message_context": message_context,
                    }
                raise

            return {
                "fb_ad_id": snapshot.fb_ad_id,
                "ad_name": ad_name,
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
