# -*- coding: utf-8 -*-
"""Unit: резолв токена syntx (приоритет источников) + декод exp из JWT."""

from __future__ import annotations

import base64
import json
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.syntx.auth import decode_token_exp, resolve_syntx_token, token_days_left
from core.syntx.errors import SyntxAuthError


def _make_jwt(payload: dict) -> str:
    """Собрать JWT (без подписи по делу — нам важен только payload)."""

    def b64(d: dict) -> str:
        raw = json.dumps(d).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64(payload)}.sig"


# decode_token_exp достаёт exp как UTC-datetime.
def test_decode_token_exp() -> None:
    tok = _make_jwt({"user_id": 1, "iat": 1_000_000_000, "exp": 1_000_086_400})
    exp = decode_token_exp(tok)
    assert exp == datetime.fromtimestamp(1_000_086_400, tz=timezone.utc)


# Мусорный токен → None, без исключения.
def test_decode_token_exp_garbage() -> None:
    assert decode_token_exp("not-a-jwt") is None


# token_days_left считает дни до exp относительно now.
def test_token_days_left() -> None:
    tok = _make_jwt({"exp": 1_000_086_400})  # ровно +1 день к 1_000_000_000
    now = datetime.fromtimestamp(1_000_000_000, tz=timezone.utc)
    assert token_days_left(tok, now=now) == pytest.approx(1.0)


# Явный аргумент имеет высший приоритет (перебивает env).
def test_resolve_explicit_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNTX_AUTH_TOKEN", "from_env")
    assert resolve_syntx_token("explicit_tok") == "explicit_tok"


# Без явного — берётся env.
def test_resolve_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNTX_AUTH_TOKEN", "from_env")
    assert resolve_syntx_token() == "from_env"


# Нет ни одного источника → SyntxAuthError.
def test_resolve_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYNTX_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("core.syntx.auth._ENV_FILE", Path("/nonexistent/.env"))
    monkeypatch.setattr(
        "core.config.get_settings", lambda: types.SimpleNamespace(syntx_auth_token="")
    )
    with pytest.raises(SyntxAuthError):
        resolve_syntx_token()
