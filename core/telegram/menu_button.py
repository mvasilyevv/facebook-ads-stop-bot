# -*- coding: utf-8 -*-
"""Синхронизация Telegram Menu Button с актуальным URL Mini App.

Telegram хранит как default-кнопку бота, так и переопределения для конкретных
личных чатов. Поэтому обновления только default scope недостаточно: старый URL,
заданный через BotFather, может продолжать использоваться уже подключённым
recipient'ом. Синхронизируем оба уровня best-effort.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncEngine

from core.config import get_settings
from core.telegram.client import TelegramBotClient
from core.telegram.service import load_active_recipients
from core.telegram.web_app_url import load_web_app_url

logger = logging.getLogger(__name__)


async def resolve_menu_button_url(
    engine: AsyncEngine,
    *,
    explicit_url: str | None = None,
) -> str | None:
    """Разрешает URL: explicit -> system_config -> WEB_APP_URL из окружения."""
    raw = explicit_url
    if raw is None:
        raw = await load_web_app_url(engine)
    if not raw:
        raw = get_settings().web_app_url

    cleaned = (raw or "").strip()
    if not cleaned.startswith("https://"):
        return None
    return cleaned


async def sync_menu_buttons(
    engine: AsyncEngine,
    client: TelegramBotClient,
    *,
    explicit_url: str | None = None,
    chat_ids: Iterable[int] | None = None,
    include_default: bool = True,
) -> bool:
    """Обновляет default-кнопку и кнопки активных личных чатов.

    Если ``chat_ids`` не переданы, загружает всех активных recipients. Ошибки
    Telegram/БД логируются и не должны останавливать poller или onboarding.
    Возвращает True, только если все запрошенные обновления прошли успешно.
    """
    try:
        url = await resolve_menu_button_url(engine, explicit_url=explicit_url)
    except Exception:
        logger.warning("menu button: не удалось разрешить Web App URL", exc_info=True)
        return False
    if url is None:
        logger.info("menu button: Web App URL не настроен — синхронизация пропущена")
        return False

    all_ok = True
    if include_default:
        try:
            await client.set_chat_menu_button(web_app_url=url)
        except Exception:
            all_ok = False
            logger.warning("menu button: не удалось обновить default scope", exc_info=True)

    if chat_ids is None:
        try:
            recipients = await load_active_recipients(engine)
            resolved_chat_ids = [recipient.chat_id for recipient in recipients]
        except Exception:
            logger.warning("menu button: не удалось загрузить recipients", exc_info=True)
            return False
    else:
        resolved_chat_ids = [int(chat_id) for chat_id in chat_ids]

    updated = 0
    for chat_id in dict.fromkeys(resolved_chat_ids):
        try:
            await client.set_chat_menu_button(web_app_url=url, chat_id=chat_id)
            updated += 1
        except Exception:
            all_ok = False
            logger.warning("menu button: не удалось обновить private chat", exc_info=True)

    logger.info(
        "menu button синхронизирован: default=%s private_chats=%d",
        include_default,
        updated,
    )
    return all_ok


__all__ = ["resolve_menu_button_url", "sync_menu_buttons"]
