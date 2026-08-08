# -*- coding: utf-8 -*-
"""Утилиты аутентификации Telegram Mini App (TMA).

Реализует:
- Проверку initData по алгоритму Telegram WebApp HMAC.
- Выдачу и проверку сессионных токенов через itsdangerous.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import urllib.parse


class InvalidInitDataError(Exception):
    """initData невалиден: неверный хэш или истёк срок действия."""


def validate_init_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: int = 86400,
) -> dict:
    """Проверяет initData Telegram WebApp по алгоритму HMAC-SHA256.

    Возвращает распарсенный словарь данных (user уже десериализован из JSON).
    Выбрасывает InvalidInitDataError при неверном хэше или истёкшем сроке.
    """
    parsed = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    d = dict(parsed)
    received_hash = d.pop("hash", None)

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))

    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not secrets.compare_digest(computed, received_hash or ""):
        raise InvalidInitDataError("Неверный хэш initData")

    auth_date = int(d.get("auth_date", "0"))
    if auth_date == 0 or (time.time() - auth_date) > max_age_seconds:
        raise InvalidInitDataError("initData истёк или auth_date отсутствует")

    if "user" in d:
        d["user"] = json.loads(d["user"])

    return d


def issue_session_token(
    telegram_user_id: str,
    ttl_seconds: int,
    secret: str,
    *,
    bot_generation: int,
) -> str:
    """Выдаёт подписанный сессионный токен для пользователя Telegram.

    Использует itsdangerous URLSafeTimedSerializer.
    """
    from itsdangerous import URLSafeTimedSerializer  # type: ignore[import-not-found]

    serializer = URLSafeTimedSerializer(secret, salt="fb-agent-tma")
    if bot_generation <= 0:
        raise ValueError("bot_generation must be positive")
    payload = {
        "telegram_user_id": telegram_user_id,
        "bot_generation": int(bot_generation),
    }
    return serializer.dumps(payload)


def verify_session_token(token: str, secret: str, max_age: int) -> dict:
    """Проверяет сессионный токен и возвращает payload.

    Выбрасывает InvalidInitDataError при невалидном или истёкшем токене.
    """
    from itsdangerous import (  # type: ignore[import-not-found]
        BadSignature,
        SignatureExpired,
        URLSafeTimedSerializer,
    )

    serializer = URLSafeTimedSerializer(secret, salt="fb-agent-tma")
    try:
        payload = serializer.loads(token, max_age=max_age)
    except SignatureExpired as exc:
        raise InvalidInitDataError("Сессионный токен истёк") from exc
    except BadSignature as exc:
        raise InvalidInitDataError("Неверная подпись токена") from exc

    return payload
