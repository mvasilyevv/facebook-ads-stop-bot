# -*- coding: utf-8 -*-
"""Общий lifecycle доставки Telegram-статусов."""

from __future__ import annotations

import html
import logging
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from core.db import get_session_factory
from core.domain import (
    AlertStage,
    DisableTaskStatus,
    EnableRecommendationLevel,
    EnableTaskStatus,
    TelegramNotificationStream,
)
from core.models import AdSnapshot, AlertEvent, EnableRecommendationEvent, FbAd, FbAdset
from core.telegram.client import TelegramBotClient
from core.telegram.message_refs import (
    load_message_refs_by_chat,
    normalize_incident_key,
    upsert_message_ref,
)
from core.telegram.messaging import safe_edit_or_send_message
from core.telegram.renderer import (
    TelegramEnableRecommendationItem,
    build_ad_identity_lines,
    build_diagnosis_lines,
    build_metric_lines,
    normalize_enable_recommendation_reason,
    render_enable_recommendation_message,
)
from core.telegram.service import load_telegram_runtime_config

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class TelegramAdMessageContext:
    """Контекст объявления для lifecycle-сообщений Telegram."""

    campaign_name: str | None = None
    adset_name: str | None = None
    matched_rule_codes: list[str] = field(default_factory=list)
    reason_title: str | None = None
    reason_text: str | None = None
    metrics_json: dict = field(default_factory=dict)


def _display_username(username: str | None) -> str:
    """Возвращает имя пользователя для сообщений."""
    value = (username or "").strip()
    return f"@{html.escape(value)}" if value else "неизвестно"


def _snapshot_metrics_json(snapshot: AdSnapshot | None) -> dict:
    """Строит fallback-метрики из текущего snapshot."""
    if snapshot is None:
        return {}
    return {
        "spend": f"{snapshot.spend:.2f}",
        "clicks": snapshot.clicks,
        "cpc": f"{snapshot.cpc:.4f}" if snapshot.cpc is not None else None,
        "outbound_clicks": snapshot.outbound_clicks,
        "outbound_ctr": (
            f"{snapshot.outbound_ctr:.4f}" if snapshot.outbound_ctr is not None else None
        ),
        "landing_page_views": snapshot.landing_page_views,
        "cost_per_landing_page_view": (
            f"{snapshot.cost_per_landing_page_view:.4f}"
            if snapshot.cost_per_landing_page_view is not None
            else None
        ),
        "cpm": f"{snapshot.cpm:.4f}" if snapshot.cpm is not None else None,
        "frequency": f"{snapshot.frequency:.4f}" if snapshot.frequency is not None else None,
        "leads": snapshot.leads,
        "cost_per_lead": (
            f"{snapshot.cost_per_lead:.4f}" if snapshot.cost_per_lead is not None else None
        ),
        "registrations": snapshot.registrations,
        "cost_per_registration": (
            f"{snapshot.cost_per_registration:.4f}"
            if snapshot.cost_per_registration is not None
            else None
        ),
        "deposits": snapshot.deposits,
    }


def _build_task_message(
    *,
    title: str,
    ad_name: str,
    fb_ad_id: str,
    requested_by_username: str,
    context: TelegramAdMessageContext | None = None,
    status_line: str | None = None,
    detail: str | None = None,
    retry_line: str | None = None,
    footer: str | None = None,
) -> str:
    """Собирает lifecycle-сообщение с иерархией, контекстом и метриками."""
    lines = [title, ""]
    lines.extend(
        build_ad_identity_lines(
            campaign_name=context.campaign_name if context else None,
            adset_name=context.adset_name if context else None,
            ad_name=ad_name,
            fb_ad_id=fb_ad_id,
        )
    )
    lines.append("")

    if context:
        rule_summaries = context.metrics_json.get("rule_summaries")
        if not isinstance(rule_summaries, list):
            rule_summaries = None
        diagnosis_lines = build_diagnosis_lines(
            reason_title=context.reason_title,
            reason_text=context.reason_text,
            matched_rule_codes=context.matched_rule_codes,
            rule_summaries=rule_summaries,
        )
        if diagnosis_lines:
            lines.extend(diagnosis_lines)
            lines.append("")

        metric_lines = build_metric_lines(context.metrics_json or {})
        if metric_lines:
            lines.append("📌 <b>Ключевые метрики</b>")
            lines.extend(metric_lines)
            lines.append("")

    if status_line:
        lines.append(status_line)
    if detail:
        lines.append(f"Причина: {html.escape(detail)}")
    if retry_line:
        lines.append(retry_line)
    if footer:
        lines.append(footer)
    lines.append(f"👤 Запросил: {_display_username(requested_by_username)}")
    return "\n".join(lines).strip()


async def _load_disable_message_context(
    *,
    fb_ad_id: str,
    incident_key: str = "",
) -> TelegramAdMessageContext:
    """Загружает замороженный контекст STOP-инцидента или fallback из snapshot."""
    normalized_incident_key = normalize_incident_key(incident_key)
    factory = get_session_factory()
    async with factory() as session:
        snapshot = await session.scalar(
            select(AdSnapshot)
            .options(
                joinedload(AdSnapshot.fb_ad).joinedload(FbAd.adset).joinedload(FbAdset.campaign)
            )
            .where(AdSnapshot.fb_ad_id == fb_ad_id)
        )
        ad_id = snapshot.ad_id if snapshot else None

        event: AlertEvent | None = None
        if ad_id is not None and normalized_incident_key:
            stop_result = await session.execute(
                select(AlertEvent)
                .where(
                    AlertEvent.ad_id == ad_id,
                    AlertEvent.telegram_group_key == normalized_incident_key,
                    AlertEvent.stage == AlertStage.STOP,
                )
                .order_by(AlertEvent.updated_at.desc(), AlertEvent.created_at.desc())
                .limit(1)
            )
            event = stop_result.scalar_one_or_none()

            if event is None:
                latest_result = await session.execute(
                    select(AlertEvent)
                    .where(
                        AlertEvent.ad_id == ad_id,
                        AlertEvent.telegram_group_key == normalized_incident_key,
                    )
                    .order_by(AlertEvent.updated_at.desc(), AlertEvent.created_at.desc())
                    .limit(1)
                )
                event = latest_result.scalar_one_or_none()

        if ad_id is not None and event is None:
            latest_result = await session.execute(
                select(AlertEvent)
                .where(AlertEvent.ad_id == ad_id)
                .order_by(AlertEvent.updated_at.desc(), AlertEvent.created_at.desc())
                .limit(1)
            )
            event = latest_result.scalar_one_or_none()

    fallback_rule_codes: list[str] = []
    if snapshot is not None:
        fallback_rule_codes = list(
            dict.fromkeys(
                (snapshot.stop_rule_codes or [])
                + (snapshot.warning_rule_codes or [])
                + (snapshot.early_signal_rule_codes or [])
            )
        )

    campaign_name: str | None = None
    adset_name: str | None = None
    if snapshot is not None and snapshot.fb_ad is not None:
        fb_ad = snapshot.fb_ad
        if fb_ad.adset is not None:
            adset_name = fb_ad.adset.adset_name
            if fb_ad.adset.campaign is not None:
                campaign_name = fb_ad.adset.campaign.campaign_name

    return TelegramAdMessageContext(
        campaign_name=campaign_name,
        adset_name=adset_name,
        matched_rule_codes=list(event.matched_rule_codes or []) if event else fallback_rule_codes,
        reason_title=event.reason_title if event else None,
        reason_text=event.reason_text if event else None,
        metrics_json=dict(event.metrics_json or {}) if event else _snapshot_metrics_json(snapshot),
    )


async def _load_enable_message_context(
    *,
    fb_ad_id: str,
    incident_key: str = "",
) -> TelegramAdMessageContext:
    """Загружает контекст рекомендации/задачи на включение."""
    normalized_incident_key = normalize_incident_key(incident_key)
    recommendation_event: EnableRecommendationEvent | None = None
    event_uuid: _uuid.UUID | None = None
    if normalized_incident_key:
        try:
            event_uuid = _uuid.UUID(normalized_incident_key)
        except (TypeError, ValueError):
            event_uuid = None

    factory = get_session_factory()
    async with factory() as session:
        snapshot = await session.scalar(
            select(AdSnapshot)
            .options(
                joinedload(AdSnapshot.fb_ad).joinedload(FbAd.adset).joinedload(FbAdset.campaign)
            )
            .where(AdSnapshot.fb_ad_id == fb_ad_id)
        )
        ad_id = snapshot.ad_id if snapshot else None

        if event_uuid is not None:
            recommendation_event = await session.scalar(
                select(EnableRecommendationEvent).where(EnableRecommendationEvent.id == event_uuid)
            )

        if recommendation_event is None and ad_id is not None:
            latest_result = await session.execute(
                select(EnableRecommendationEvent)
                .where(EnableRecommendationEvent.ad_id == ad_id)
                .order_by(
                    EnableRecommendationEvent.updated_at.desc(),
                    EnableRecommendationEvent.created_at.desc(),
                )
                .limit(1)
            )
            recommendation_event = latest_result.scalar_one_or_none()

    campaign_name: str | None = None
    adset_name: str | None = None
    if snapshot is not None and snapshot.fb_ad is not None:
        fb_ad = snapshot.fb_ad
        if fb_ad.adset is not None:
            adset_name = fb_ad.adset.adset_name
            if fb_ad.adset.campaign is not None:
                campaign_name = fb_ad.adset.campaign.campaign_name

    return TelegramAdMessageContext(
        campaign_name=campaign_name,
        adset_name=adset_name,
        matched_rule_codes=(
            list(recommendation_event.matched_rule_codes or []) if recommendation_event else []
        ),
        reason_title=recommendation_event.reason_title if recommendation_event else None,
        reason_text=recommendation_event.reason_text if recommendation_event else None,
        metrics_json=(
            dict(recommendation_event.metrics_json or {})
            if recommendation_event
            else _snapshot_metrics_json(snapshot)
        ),
    )


def render_disable_task_queue_message(
    *,
    ad_name: str,
    fb_ad_id: str,
    requested_by_username: str,
    created_new: bool,
    context: TelegramAdMessageContext | None = None,
) -> str:
    """Строит сообщение о постановке задачи на отключение."""
    if created_new:
        title = "✅ <b>Создана задача на отключение</b>"
        status_line = "⏳ Статус: в очереди"
    else:
        title = "ℹ️ <b>Задача уже была в очереди</b>"
        status_line = "⏳ Статус: ожидает выполнения"
    return _build_task_message(
        title=title,
        ad_name=ad_name,
        fb_ad_id=fb_ad_id,
        requested_by_username=requested_by_username,
        context=context,
        status_line=status_line,
        footer="ℹ️ Дальнейший статус этой цепочки будет идти в STOP topic.",
    )


def render_enable_task_queue_message(
    *,
    ad_name: str,
    fb_ad_id: str,
    requested_by_username: str,
    created_new: bool,
    context: TelegramAdMessageContext | None = None,
) -> str:
    """Строит сообщение о постановке задачи на включение."""
    if created_new:
        title = "✅ <b>Создана задача на включение</b>"
        status_line = "⏳ Статус: в очереди"
    else:
        title = "ℹ️ <b>Задача на включение уже была в очереди</b>"
        status_line = "⏳ Статус: ожидает выполнения"
    return _build_task_message(
        title=title,
        ad_name=ad_name,
        fb_ad_id=fb_ad_id,
        requested_by_username=requested_by_username,
        context=context,
        status_line=status_line,
        footer="ℹ️ Дальнейший статус этой цепочки будет идти в ENABLE topic.",
    )


def render_disable_task_runtime_message(
    *,
    ad_name: str,
    fb_ad_id: str,
    requested_by_username: str,
    status: str,
    context: TelegramAdMessageContext | None = None,
    detail: str = "",
    next_retry_at: datetime | None = None,
) -> str:
    """Строит runtime-статус задачи на отключение."""
    if status == DisableTaskStatus.SUCCEEDED.value:
        return _build_task_message(
            title="✅ <b>Клик по выключению выполнен</b>",
            ad_name=ad_name,
            fb_ad_id=fb_ad_id,
            requested_by_username=requested_by_username,
            context=context,
            footer="🔎 Бот ждёт подтверждения статуса OFF в следующем скане; цепочка остаётся в STOP topic.",
        )

    if status == DisableTaskStatus.RETRYING.value:
        retry_line = None
        if next_retry_at is not None:
            seconds = max(0, int((next_retry_at - datetime.now(UTC)).total_seconds()))
            retry_line = f"🔁 Повтор через {seconds} сек"
        return _build_task_message(
            title="⚠️ <b>Задача на отключение будет повторена</b>",
            ad_name=ad_name,
            fb_ad_id=fb_ad_id,
            requested_by_username=requested_by_username,
            context=context,
            detail=detail or "Временная ошибка",
            retry_line=retry_line,
        )

    return _build_task_message(
        title="❌ <b>Задача на отключение завершилась ошибкой</b>",
        ad_name=ad_name,
        fb_ad_id=fb_ad_id,
        requested_by_username=requested_by_username,
        context=context,
        detail=detail or "Неизвестная ошибка",
    )


def render_enable_task_runtime_message(
    *,
    ad_name: str,
    fb_ad_id: str,
    requested_by_username: str,
    status: str,
    context: TelegramAdMessageContext | None = None,
    detail: str = "",
    next_retry_at: datetime | None = None,
) -> str:
    """Строит runtime-статус задачи на включение."""
    if status == EnableTaskStatus.SUCCEEDED.value:
        return _build_task_message(
            title="✅ <b>Задача на включение выполнена</b>",
            ad_name=ad_name,
            fb_ad_id=fb_ad_id,
            requested_by_username=requested_by_username,
            context=context,
            footer="ℹ️ Дальнейшие статусы этой цепочки идут в ENABLE topic.",
        )

    if status == EnableTaskStatus.RETRYING.value:
        retry_line = None
        if next_retry_at is not None:
            seconds = max(0, int((next_retry_at - datetime.now(UTC)).total_seconds()))
            retry_line = f"🔁 Повтор через {seconds} сек"
        return _build_task_message(
            title="⚠️ <b>Задача на включение будет повторена</b>",
            ad_name=ad_name,
            fb_ad_id=fb_ad_id,
            requested_by_username=requested_by_username,
            context=context,
            detail=detail or "Временная ошибка",
            retry_line=retry_line,
        )

    return _build_task_message(
        title="❌ <b>Задача на включение завершилась ошибкой</b>",
        ad_name=ad_name,
        fb_ad_id=fb_ad_id,
        requested_by_username=requested_by_username,
        context=context,
        detail=detail or "Неизвестная ошибка",
    )


async def _broadcast_message(
    *,
    fb_ad_id: str,
    incident_key: str = "",
    stream_kind: TelegramNotificationStream,
    text: str,
    reply_markup: dict | None = None,
    fallback_token: str = "",
    fallback_chat_id: str = "",
    skip_chat_id: str | None = None,
    skip_message_id: int | None = None,
) -> list[tuple[str, int]]:
    """Отправляет или обновляет сообщение в конкретном Telegram-потоке."""
    token, destinations = await load_telegram_runtime_config(
        fallback_token=fallback_token,
        fallback_chat_id=fallback_chat_id,
    )
    if not token or not destinations:
        return []

    refs_by_chat = await load_message_refs_by_chat(
        fb_ad_id=fb_ad_id,
        incident_key=incident_key,
        stream_kind=stream_kind,
    )

    delivered_refs: list[tuple[str, int]] = []
    client = TelegramBotClient(token)
    try:
        for destination in destinations:
            existing_message_id = refs_by_chat.get(destination.chat_id)
            message_thread_id = destination.thread_id_for_stream(stream_kind)
            if (
                destination.chat_id == skip_chat_id
                and existing_message_id is not None
                and existing_message_id == skip_message_id
            ):
                continue
            try:
                _, delivered_message_id = await safe_edit_or_send_message(
                    client,
                    chat_id=destination.chat_id,
                    message_id=existing_message_id,
                    message_thread_id=message_thread_id,
                    text=text,
                    reply_markup=reply_markup,
                )
                if delivered_message_id is None:
                    logger.error(
                        "Потеря message_id при доставке для %s в chat_id=%s, stream=%s",
                        fb_ad_id,
                        destination.chat_id,
                        stream_kind,
                    )
                    continue
                await upsert_message_ref(
                    chat_id=destination.chat_id,
                    message_id=delivered_message_id,
                    fb_ad_id=fb_ad_id,
                    incident_key=incident_key,
                    stream_kind=stream_kind,
                )
                delivered_refs.append((destination.chat_id, delivered_message_id))
            except Exception:
                logger.exception(
                    "Не удалось доставить Telegram-сообщение для %s в chat_id=%s, stream=%s",
                    fb_ad_id,
                    destination.chat_id,
                    stream_kind,
                )
    finally:
        await client.close()

    return delivered_refs


async def broadcast_observer_runtime_message(
    *,
    text: str,
    fallback_token: str = "",
    fallback_chat_id: str = "",
) -> None:
    """Рассылает служебное сообщение observer в CONTROL-поток или личный чат."""
    token, destinations = await load_telegram_runtime_config(
        fallback_token=fallback_token,
        fallback_chat_id=fallback_chat_id,
    )
    if not token or not destinations or not text.strip():
        return

    client = TelegramBotClient(token)
    try:
        for destination in destinations:
            message_thread_id = None
            if destination.delivery_mode == "FORUM_GROUP":
                message_thread_id = destination.control_topic_id
            try:
                await client.send_message(
                    chat_id=destination.chat_id,
                    message_thread_id=message_thread_id,
                    text=text,
                )
            except Exception:
                logger.exception(
                    "Не удалось доставить служебное Telegram-сообщение observer в chat_id=%s",
                    destination.chat_id,
                )
    finally:
        await client.close()


async def broadcast_disable_task_queue_message(
    *,
    ad_name: str,
    fb_ad_id: str,
    requested_by_username: str,
    created_new: bool,
    incident_key: str = "",
    context: TelegramAdMessageContext | None = None,
    fallback_token: str = "",
    fallback_chat_id: str = "",
    skip_chat_id: str | None = None,
    skip_message_id: int | None = None,
) -> None:
    """Рассылает queued/update-сообщение всем релевантным получателям."""
    context = context or await _load_disable_message_context(
        fb_ad_id=fb_ad_id,
        incident_key=incident_key,
    )
    text = render_disable_task_queue_message(
        ad_name=ad_name,
        fb_ad_id=fb_ad_id,
        requested_by_username=requested_by_username,
        created_new=created_new,
        context=context,
    )
    await _broadcast_message(
        fb_ad_id=fb_ad_id,
        incident_key=incident_key,
        stream_kind=TelegramNotificationStream.STOP,
        text=text,
        fallback_token=fallback_token,
        fallback_chat_id=fallback_chat_id,
        skip_chat_id=skip_chat_id,
        skip_message_id=skip_message_id,
    )


async def broadcast_enable_task_queue_message(
    *,
    ad_name: str,
    fb_ad_id: str,
    requested_by_username: str,
    created_new: bool,
    incident_key: str = "",
    context: TelegramAdMessageContext | None = None,
    fallback_token: str = "",
    fallback_chat_id: str = "",
    skip_chat_id: str | None = None,
    skip_message_id: int | None = None,
) -> None:
    """Рассылает queued/update-сообщение всем релевантным получателям."""
    context = context or await _load_enable_message_context(
        fb_ad_id=fb_ad_id,
        incident_key=incident_key,
    )
    text = render_enable_task_queue_message(
        ad_name=ad_name,
        fb_ad_id=fb_ad_id,
        requested_by_username=requested_by_username,
        created_new=created_new,
        context=context,
    )
    await _broadcast_message(
        fb_ad_id=fb_ad_id,
        incident_key=incident_key,
        stream_kind=TelegramNotificationStream.ENABLE,
        text=text,
        fallback_token=fallback_token,
        fallback_chat_id=fallback_chat_id,
        skip_chat_id=skip_chat_id,
        skip_message_id=skip_message_id,
    )


async def broadcast_disable_task_runtime_message(
    *,
    ad_name: str,
    fb_ad_id: str,
    requested_by_username: str,
    status: str,
    incident_key: str = "",
    context: TelegramAdMessageContext | None = None,
    detail: str = "",
    next_retry_at: datetime | None = None,
    fallback_token: str = "",
    fallback_chat_id: str = "",
) -> None:
    """Рассылает runtime-обновление по задаче отключения."""
    context = context or await _load_disable_message_context(
        fb_ad_id=fb_ad_id,
        incident_key=incident_key,
    )
    text = render_disable_task_runtime_message(
        ad_name=ad_name,
        fb_ad_id=fb_ad_id,
        requested_by_username=requested_by_username,
        status=status,
        context=context,
        detail=detail,
        next_retry_at=next_retry_at,
    )
    await _broadcast_message(
        fb_ad_id=fb_ad_id,
        incident_key=incident_key,
        stream_kind=TelegramNotificationStream.STOP,
        text=text,
        fallback_token=fallback_token,
        fallback_chat_id=fallback_chat_id,
    )


async def broadcast_enable_task_runtime_message(
    *,
    ad_name: str,
    fb_ad_id: str,
    requested_by_username: str,
    status: str,
    incident_key: str = "",
    context: TelegramAdMessageContext | None = None,
    detail: str = "",
    next_retry_at: datetime | None = None,
    fallback_token: str = "",
    fallback_chat_id: str = "",
) -> None:
    """Рассылает runtime-обновление по задаче включения."""
    context = context or await _load_enable_message_context(
        fb_ad_id=fb_ad_id,
        incident_key=incident_key,
    )
    text = render_enable_task_runtime_message(
        ad_name=ad_name,
        fb_ad_id=fb_ad_id,
        requested_by_username=requested_by_username,
        status=status,
        context=context,
        detail=detail,
        next_retry_at=next_retry_at,
    )
    await _broadcast_message(
        fb_ad_id=fb_ad_id,
        incident_key=incident_key,
        stream_kind=TelegramNotificationStream.ENABLE,
        text=text,
        fallback_token=fallback_token,
        fallback_chat_id=fallback_chat_id,
    )


async def broadcast_enable_recommendation_message(
    *,
    event_id,
    ad_name: str,
    fb_ad_id: str,
    campaign_name: str | None = None,
    adset_name: str | None = None,
    delivery_status: str,
    recommendation_level: str,
    matched_rule_codes: list[str],
    reason_title: str | None,
    reason_text: str | None,
    metrics_json: dict,
    fallback_token: str = "",
    fallback_chat_id: str = "",
) -> list[tuple[str, int]]:
    """Рассылает новое recommendation-сообщение всем активным получателям."""
    recommendation_level_enum = EnableRecommendationLevel(recommendation_level)
    normalized_reason_title, normalized_reason_text = normalize_enable_recommendation_reason(
        recommendation_level=recommendation_level_enum,
        reason_title=reason_title,
        reason_text=reason_text,
    )
    message = render_enable_recommendation_message(
        item=TelegramEnableRecommendationItem(
            event_id=str(event_id),
            fb_ad_id=fb_ad_id,
            ad_name=ad_name,
            campaign_name=campaign_name,
            adset_name=adset_name,
            delivery_status=delivery_status,
            recommendation_level=recommendation_level_enum,
            matched_rule_codes=matched_rule_codes,
            reason_title=normalized_reason_title,
            reason_text=normalized_reason_text,
            metrics_json=metrics_json or {},
        )
    )

    return await _broadcast_message(
        fb_ad_id=fb_ad_id,
        incident_key=str(event_id),
        stream_kind=TelegramNotificationStream.ENABLE,
        text=message.text,
        reply_markup=message.reply_markup,
        fallback_token=fallback_token,
        fallback_chat_id=fallback_chat_id,
    )
