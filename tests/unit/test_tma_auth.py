# -*- coding: utf-8 -*-
"""Unit-тесты для модуля core.auth.tma."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse

import pytest

from core.auth.tma import (
    InvalidInitDataError,
    issue_session_token,
    validate_init_data,
    verify_session_token,
)

FAKE_BOT_TOKEN = "fake_bot_token_12345"


def _build_init_data(user_id: int, bot_token: str, auth_date: int | None = None) -> str:
    """Вспомогательная функция: строит валидный initData для тестов."""
    if auth_date is None:
        auth_date = int(time.time())

    user = json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":"))
    fields = {
        "auth_date": str(auth_date),
        "user": user,
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()

    fields["hash"] = computed_hash
    return urllib.parse.urlencode(fields)


# Проверяем, что корректный initData проходит валидацию
def test_valid_init_data_passes():
    init_data = _build_init_data(user_id=123456, bot_token=FAKE_BOT_TOKEN)
    result = validate_init_data(init_data, FAKE_BOT_TOKEN)
    assert result["user"]["id"] == 123456
    assert "auth_date" in result


# Проверяем отклонение при неверном хэше
def test_invalid_hash_rejected():
    init_data = _build_init_data(user_id=123456, bot_token=FAKE_BOT_TOKEN)
    # Портим подпись подменой последнего символа
    tampered = init_data[:-1] + ("0" if init_data[-1] != "0" else "1")
    with pytest.raises(InvalidInitDataError, match="хэш"):
        validate_init_data(tampered, FAKE_BOT_TOKEN)


# Проверяем отклонение при устаревшем auth_date (старше суток)
def test_expired_init_data_rejected():
    old_ts = int(time.time()) - 90000  # более суток назад
    init_data = _build_init_data(user_id=123456, bot_token=FAKE_BOT_TOKEN, auth_date=old_ts)
    with pytest.raises(InvalidInitDataError, match="истёк"):
        validate_init_data(init_data, FAKE_BOT_TOKEN, max_age_seconds=86400)


# Проверяем, что issue → verify возвращает тот же telegram_user_id
def test_session_token_roundtrip():
    secret = "test_secret_key_abc"
    ttl = 3600
    token = issue_session_token("987654", ttl, secret)
    payload = verify_session_token(token, secret, ttl)
    assert payload["telegram_user_id"] == "987654"
