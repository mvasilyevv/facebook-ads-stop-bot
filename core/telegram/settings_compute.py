# -*- coding: utf-8 -*-
"""Pure-хелперы для вычисления runtime-полей Telegram-конфига.

Все функции принимают ORM-модель TelegramConfig (или None, если строки ещё нет)
и дополнительные зависимости (Redis, httpx), возвращают готовые значения для API-ответа.
Не обращаются к БД напрямую — только читают переданные объекты.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# Redis-ключ и TTL для кэша имени бота.
_BOT_USERNAME_CACHE_KEY = "tg:bot_username"
_BOT_USERNAME_CACHE_TTL = 3600  # секунд

# Порог «живости» poller'а в секундах.
_POLLER_ONLINE_THRESHOLD_SECONDS = 60


def compute_is_authorized(config: object | None) -> bool:
    """Возвращает True, если bot_token_encrypted заполнен.

    Args:
        config: ORM-объект TelegramConfig или None.
    """
    if config is None:
        return False
    token = getattr(config, "bot_token_encrypted", None)
    return bool(token)


async def compute_poller_status(config: object | None) -> str:
    """Возвращает 'ONLINE' если poller обновил heartbeat не позднее 60 секунд назад.

    Args:
        config: ORM-объект TelegramConfig или None.
    """
    if config is None:
        return "OFFLINE"
    heartbeat = getattr(config, "poller_heartbeat_at", None)
    if heartbeat is None:
        return "OFFLINE"
    # poller_heartbeat_at может быть timezone-aware или naive.
    now = datetime.now(UTC)
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=UTC)
    delta = now - heartbeat
    if delta <= timedelta(seconds=_POLLER_ONLINE_THRESHOLD_SECONDS):
        return "ONLINE"
    return "OFFLINE"


async def compute_bot_username(config: object | None, redis: object) -> str | None:
    """Возвращает username бота через кэш Redis или запрос к Telegram getMe.

    Порядок:
    1. Если config None или токен отсутствует — None.
    2. Проверяет кэш Redis по ключу 'tg:bot_username'.
    3. При кэш-miss — расшифровывает токен, вызывает /getMe, кэширует результат.
    4. При любой ошибке httpx — возвращает None (не пробрасывает).

    Args:
        config: ORM-объект TelegramConfig или None.
        redis: Redis async-клиент (redis.asyncio.Redis).
    """
    if not compute_is_authorized(config):
        return None

    # Пробуем кэш.
    try:
        cached = await redis.get(_BOT_USERNAME_CACHE_KEY)
        if cached:
            return cached
    except Exception as exc:
        logger.warning("Не удалось прочитать кэш bot_username из Redis: %s", exc)

    # Расшифровываем токен и запрашиваем /getMe.
    try:
        from core.crypto import decrypt

        enc = getattr(config, "bot_token_encrypted", None)
        if not enc:
            return None
        token = decrypt(enc)
        if not token:
            return None
    except Exception as exc:
        logger.warning("Не удалось расшифровать bot_token_encrypted: %s", exc)
        return None

    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            resp.raise_for_status()
            data = resp.json()
        username: str | None = data.get("result", {}).get("username")
        if username:
            # Кэшируем результат.
            try:
                await redis.set(_BOT_USERNAME_CACHE_KEY, username, ex=_BOT_USERNAME_CACHE_TTL)
            except Exception as exc:
                logger.warning("Не удалось сохранить bot_username в кэш Redis: %s", exc)
        return username
    except Exception as exc:
        logger.warning("Ошибка запроса getMe к Telegram API: %s", exc)
        return None


def compute_auth_deep_link(bot_username: str | None) -> str | None:
    """Возвращает deep-link для авторизации через Telegram-бота.

    Формат: https://t.me/{username}?start=auth

    Args:
        bot_username: username бота без @, или None.
    """
    if not bot_username:
        return None
    return f"https://t.me/{bot_username}?start=auth"


def compute_activation_command() -> str:
    """Возвращает статическую команду активации бота.

    Команда, которую нужно отправить боту для регистрации получателя.
    """
    return "/start auth"
