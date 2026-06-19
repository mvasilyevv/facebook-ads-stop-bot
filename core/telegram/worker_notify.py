# -*- coding: utf-8 -*-
"""Единая best-effort точка money-нотификаций воркеров в Telegram.

Зачем: meta_api_worker/cabinet_scheduler и др. при провале денежных операций
писали только в лог. notify_owners шлёт owner-recipient'ам в ЛИЧКУ (DM-формат),
с dedup ТОЛЬКО после успешной доставки (чтобы сбой TG не «съел» алерт на TTL).

Best-effort: исключения TG/Redis ловятся и логируются — воркер не падает.
Клиент кешируется по bot_token (свежий токен подхватывается при ротации).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from core.telegram.client import TelegramBotClient
from core.telegram.service import (
    load_active_recipients,
    load_owner_recipients,
    load_telegram_config,
)

logger = logging.getLogger(__name__)

# Кеш клиента по токену: при ротации токена создаётся новый, старый отбрасывается.
_client_cache: dict[str, TelegramBotClient] = {}


def _client_for_token(bot_token: str) -> TelegramBotClient:
    client = _client_cache.get(bot_token)
    if client is None:
        client = TelegramBotClient(bot_token)
        _client_cache.clear()  # держим один токен (на ротации старый не нужен)
        _client_cache[bot_token] = client
    return client


def _reset_client_cache() -> None:
    """Только для тестов."""
    _client_cache.clear()


async def notify_owners(
    engine: AsyncEngine,
    redis: Any,
    *,
    category: str,
    text: str,
    dedup_key: str | None = None,
    dedup_ttl_seconds: int | None = None,
) -> bool:
    """Отправить money-нотификацию всем owner-recipient'ам в личку.

    Returns: True если доставлено хотя бы одному owner. Best-effort — не бросает.
    dedup_key (если задан) ставится в Redis SET NX EX ТОЛЬКО после успешной доставки.
    """
    try:
        if dedup_key and redis is not None:
            try:
                if await redis.get(dedup_key):
                    return False
            except Exception:
                logger.exception("worker_notify[%s]: ошибка чтения dedup %s", category, dedup_key)

        cfg = await load_telegram_config(engine)
        if cfg is None or not cfg.bot_token:
            logger.warning("worker_notify[%s]: нет bot_token — пропускаю", category)
            return False

        owners = await load_owner_recipients(engine)
        if not owners:
            logger.warning("worker_notify[%s]: нет owner-получателей — пропускаю", category)
            return False

        client = _client_for_token(cfg.bot_token)
        delivered = False
        for owner in owners:
            try:
                await client.send_message(chat_id=str(owner.chat_id), text=text, parse_mode="HTML")
                delivered = True
            except Exception:
                logger.exception(
                    "worker_notify[%s]: не доставлено owner chat_id=%s", category, owner.chat_id
                )

        if delivered and dedup_key and redis is not None and dedup_ttl_seconds:
            try:
                await redis.set(dedup_key, "1", nx=True, ex=dedup_ttl_seconds)
            except Exception:
                logger.exception("worker_notify[%s]: ошибка SET dedup %s", category, dedup_key)
        return delivered
    except Exception:
        logger.exception("worker_notify[%s]: неожиданная ошибка", category)
        return False


async def notify_recipients(
    engine: AsyncEngine,
    redis: Any,
    *,
    category: str,
    text: str,
    dedup_key: str | None = None,
    dedup_ttl_seconds: int | None = None,
) -> bool:
    """Money/ops-нотификация ВСЕМ активным recipients в личку. Best-effort, dedup-after-send.

    Returns: True если доставлено хотя бы одному recipient. Не бросает.
    dedup_key ставится в Redis ТОЛЬКО после успешной доставки ≥1 получателю.
    """
    try:
        if dedup_key and redis is not None:
            try:
                if await redis.get(dedup_key):
                    return False
            except Exception:
                logger.exception(
                    "notify_recipients[%s]: ошибка чтения dedup %s", category, dedup_key
                )

        cfg = await load_telegram_config(engine)
        if cfg is None or not cfg.bot_token:
            logger.warning("notify_recipients[%s]: нет bot_token", category)
            return False

        recipients = await load_active_recipients(engine)
        if not recipients:
            logger.warning("notify_recipients[%s]: нет активных recipients", category)
            return False

        client = _client_for_token(cfg.bot_token)
        delivered = False
        for r in recipients:
            try:
                await client.send_message(chat_id=str(r.chat_id), text=text, parse_mode="HTML")
                delivered = True
            except Exception:
                logger.exception(
                    "notify_recipients[%s]: не доставлено chat_id=%s", category, r.chat_id
                )

        if delivered and dedup_key and redis is not None and dedup_ttl_seconds:
            try:
                await redis.set(dedup_key, "1", nx=True, ex=dedup_ttl_seconds)
            except Exception:
                logger.exception("notify_recipients[%s]: ошибка SET dedup %s", category, dedup_key)
        return delivered
    except Exception:
        logger.exception("notify_recipients[%s]: неожиданная ошибка", category)
        return False
