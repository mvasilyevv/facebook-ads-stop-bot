# -*- coding: utf-8 -*-
"""Синхронизация Telegram Menu Button с актуальным URL Mini App.

Telegram хранит как default-кнопку бота, так и переопределения для конкретных
личных чатов. Поэтому обновления только default scope недостаточно: старый URL,
заданный через BotFather, может продолжать использоваться уже подключённым
recipient'ом. Синхронизируем оба уровня с bounded incomplete result.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import AsyncContextManager, Protocol

from sqlalchemy.ext.asyncio import AsyncEngine

from core.safe_diagnostics import safe_exception_diagnostic
from core.telegram.service import load_active_recipients
from core.telegram.web_app_url import load_web_app_url, normalize_web_app_base

logger = logging.getLogger(__name__)


class MenuButtonGateway(Protocol):
    async def set_chat_menu_button(
        self,
        *,
        web_app_url: str,
        button_text: str = "📱 Открыть",
        chat_id: int | None = None,
    ) -> None: ...


async def resolve_menu_button_url(
    engine: AsyncEngine,
    *,
    explicit_url: str | None = None,
) -> str | None:
    """Разрешает URL: explicit -> DB-authoritative system_config."""
    raw = explicit_url
    if raw is None:
        raw = await load_web_app_url(engine)

    return normalize_web_app_base(raw)


async def sync_menu_buttons(
    engine: AsyncEngine,
    client: MenuButtonGateway,
    *,
    explicit_url: str | None = None,
    chat_ids: Iterable[int] | None = None,
    include_default: bool = True,
    authorize_call: Callable[[], AsyncContextManager[bool]] | None = None,
    on_gateway_error: Callable[[int | None, Exception], Awaitable[bool]] | None = None,
    deadline_seconds: float = 10.0,
) -> bool:
    """Обновляет default-кнопку и кнопки активных личных чатов.

    Если ``chat_ids`` не переданы, загружает всех активных recipients. Ошибки
    Telegram/БД логируются и не должны останавливать webhook-worker или onboarding.
    Возвращает True, только если все запрошенные обновления прошли успешно.
    """
    try:
        url = await resolve_menu_button_url(engine, explicit_url=explicit_url)
    except Exception as exc:
        logger.warning(
            "menu button: Web App URL недоступен (%s)",
            safe_exception_diagnostic(exc),
        )
        return False
    if url is None:
        logger.info("menu button: Web App URL не настроен — синхронизация пропущена")
        return False

    if deadline_seconds <= 0:
        raise ValueError("deadline_seconds must be positive")
    deadline = asyncio.get_running_loop().time() + deadline_seconds

    async def update_scope(*, chat_id: int | None = None) -> bool | None:
        """Return None when authority was revoked and remaining calls must stop."""

        async def send() -> None:
            if chat_id is None:
                await client.set_chat_menu_button(web_app_url=url)
            else:
                await client.set_chat_menu_button(web_app_url=url, chat_id=chat_id)

        try:
            async with asyncio.timeout_at(deadline):
                if authorize_call is None:
                    await send()
                    return True
                async with authorize_call() as authorized:
                    if not authorized:
                        return None
                    await send()
                    return True
        except TimeoutError:
            logger.warning("menu button: общий deadline синхронизации исчерпан")
            return None
        except Exception as exc:
            logger.warning(
                "menu button: Telegram scope update failed (%s)",
                safe_exception_diagnostic(exc),
            )
            if on_gateway_error is not None and not await on_gateway_error(chat_id, exc):
                return None
            return False

    all_ok = True
    if include_default:
        default_result = await update_scope()
        if default_result is None:
            return False
        all_ok = default_result

    if chat_ids is None:
        try:
            recipients = await load_active_recipients(engine)
            resolved_chat_ids = [recipient.chat_id for recipient in recipients]
        except Exception as exc:
            logger.warning(
                "menu button: recipients недоступны (%s)",
                safe_exception_diagnostic(exc),
            )
            return False
    else:
        resolved_chat_ids = [int(chat_id) for chat_id in chat_ids]

    updated = 0
    for chat_id in dict.fromkeys(resolved_chat_ids):
        result = await update_scope(chat_id=chat_id)
        if result is None:
            return False
        if result:
            updated += 1
        else:
            all_ok = False

    logger.info(
        "menu button синхронизирован: default=%s private_chats=%d",
        include_default,
        updated,
    )
    return all_ok


__all__ = ["resolve_menu_button_url", "sync_menu_buttons"]
