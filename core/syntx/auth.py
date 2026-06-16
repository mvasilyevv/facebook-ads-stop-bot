# -*- coding: utf-8 -*-
"""Резолв и инспекция auth-токена syntx.

syntx авторизует только заголовком `Authorization: Bearer <JWT>`. JWT лежит в
`localStorage.auth_token` залогиненного syntx (профиль recon_profile) и живёт
30 дней. Куки/сессия не нужны, голый httpx из терминала проходит (Cloudflare
по fingerprint не режет — проверено 16.06).

Приоритет источников токена: явный аргумент → env SYNTX_AUTH_TOKEN →
settings.syntx_auth_token → строка SYNTX_AUTH_TOKEN=... в .env.

Авто-рефреш (TODO, заложено на будущее): когда токен близок к exp — прочитать
свежий `localStorage.auth_token` из recon_profile через Playwright-сниппет и
переписать .env. Сейчас обновляется вручную раз в 30 дней.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from core.syntx.errors import SyntxAuthError

logger = logging.getLogger(__name__)

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_ENV_KEY = "SYNTX_AUTH_TOKEN"


def _read_token_from_dotenv() -> str | None:
    """Прочитать SYNTX_AUTH_TOKEN из .env (без зависимости от pydantic-settings)."""
    if not _ENV_FILE.exists():
        return None
    try:
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{_ENV_KEY}="):
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def resolve_syntx_token(explicit: str | None = None) -> str:
    """Вернуть токен по приоритету источников или бросить SyntxAuthError.

    settings читаем лениво и мягко: модуль core.syntx не должен падать при импорте,
    если конфиг недоступен.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.environ.get(_ENV_KEY)
    if env and env.strip():
        return env.strip()
    try:
        from core.config import get_settings

        token = getattr(get_settings(), "syntx_auth_token", "") or ""
        if token.strip():
            return token.strip()
    except Exception:  # noqa: BLE001 — конфиг опционален для CLI-сценариев
        pass
    dotenv = _read_token_from_dotenv()
    if dotenv:
        return dotenv
    raise SyntxAuthError(
        "Нет токена syntx: задай аргумент token, env SYNTX_AUTH_TOKEN, "
        "settings.syntx_auth_token или строку в .env"
    )


def decode_token_exp(token: str) -> datetime | None:
    """Достать exp (UTC) из JWT без проверки подписи. None — если не разобрать."""
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        exp = payload.get("exp")
        if exp is None:
            return None
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    except (IndexError, ValueError, binascii.Error, json.JSONDecodeError):
        return None


def token_days_left(token: str, *, now: datetime | None = None) -> float | None:
    """Сколько дней до протухания токена. None — если exp не разобран."""
    exp = decode_token_exp(token)
    if exp is None:
        return None
    moment = now or datetime.now(tz=timezone.utc)
    return (exp - moment).total_seconds() / 86400.0
