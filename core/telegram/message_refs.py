# -*- coding: utf-8 -*-
"""Хранилище delivery-ref Telegram-сообщений по независимым потокам."""

from __future__ import annotations

import uuid as _uuid

from sqlalchemy import select

from core.db import get_session_factory
from core.domain import AlertStage, TelegramNotificationStream
from core.models import FbAd, TelegramMessageRef


def normalize_incident_key(incident_key: str | None) -> str:
    """Приводит incident key к каноническому виду для хранения."""
    return (incident_key or "").strip()


def stream_for_alert_stage(stage: AlertStage) -> TelegramNotificationStream:
    """Возвращает поток Telegram для стадии алерта."""
    if stage == AlertStage.STOP:
        return TelegramNotificationStream.STOP
    return TelegramNotificationStream.WARNING


async def _resolve_ad_id(session, fb_ad_id: str) -> _uuid.UUID | None:
    """Находит UUID записи fb_ads по fb_ad_id строке."""
    return await session.scalar(select(FbAd.id).where(FbAd.fb_ad_id == fb_ad_id))  # type: ignore[no-any-return]


async def load_message_refs_by_chat(
    *,
    fb_ad_id: str,
    incident_key: str | None,
    stream_kind: TelegramNotificationStream,
) -> dict[str, int]:
    """Возвращает последние message_id по chat_id для конкретного потока."""
    normalized_incident_key = normalize_incident_key(incident_key)
    factory = get_session_factory()
    async with factory() as session:
        ad_id = await _resolve_ad_id(session, fb_ad_id)
        if ad_id is None:
            return {}
        result = await session.execute(
            select(TelegramMessageRef.telegram_chat_id, TelegramMessageRef.telegram_message_id)
            .where(
                TelegramMessageRef.ad_id == ad_id,
                TelegramMessageRef.incident_key == normalized_incident_key,
                TelegramMessageRef.stream_kind == stream_kind,
            )
            .order_by(TelegramMessageRef.updated_at.desc(), TelegramMessageRef.created_at.desc())
        )
        refs: dict[str, int] = {}
        for chat_id, message_id in result.all():
            if chat_id and message_id and chat_id not in refs:
                refs[chat_id] = int(message_id)
        return refs


async def upsert_message_ref(
    *,
    chat_id: str,
    message_id: int,
    fb_ad_id: str,
    incident_key: str | None,
    stream_kind: TelegramNotificationStream,
) -> None:
    """Создаёт или обновляет delivery-ref Telegram-сообщения."""
    normalized_incident_key = normalize_incident_key(incident_key)
    factory = get_session_factory()
    async with factory() as session:
        ad_id = await _resolve_ad_id(session, fb_ad_id)
        if ad_id is None:
            return
        ref = await session.scalar(
            select(TelegramMessageRef).where(
                TelegramMessageRef.telegram_chat_id == chat_id,
                TelegramMessageRef.ad_id == ad_id,
                TelegramMessageRef.incident_key == normalized_incident_key,
                TelegramMessageRef.stream_kind == stream_kind,
            )
        )
        if ref is None:
            ref = TelegramMessageRef(
                ad_id=ad_id,
                telegram_chat_id=chat_id,
                telegram_message_id=message_id,
                incident_key=normalized_incident_key,
                stream_kind=stream_kind,
            )
            session.add(ref)
        else:
            ref.telegram_message_id = message_id
        await session.commit()
