# -*- coding: utf-8 -*-
"""Unit: Telegram runtime is DB-only; env adoption is an explicit bootstrap."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import core.telegram.service as service
from core.telegram.gateway import telegram_credential_fingerprint


def _settings(token: str) -> SimpleNamespace:
    return SimpleNamespace(
        telegram_bot_token=token,
        frontend_origin="https://app.adpulse.su",
        telegram_webhook_secret="w" * 48,
    )


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _Connection:
    def __init__(self, engine: "_Engine") -> None:
        self.engine = engine

    async def execute(self, statement, parameters=None):
        sql = " ".join(str(statement).split())
        if sql.startswith("SELECT bot_token_encrypted"):
            return _Result(self.engine.row)
        if sql.startswith("INSERT INTO telegram_config"):
            self.engine.insert_attempts += 1
            if self.engine.row is not None:
                return _Result(None)
            self.engine.insert_parameters = dict(parameters)
            self.engine.row = (
                parameters["bot_token_encrypted"],
                True,
                False,
                int(parameters["webhook_generation"]),
                datetime.now(UTC),
            )
            return _Result(("default",))
        raise AssertionError(f"unexpected SQL: {sql}")

    async def scalar(self, statement, parameters=None):
        sql = " ".join(str(statement).split())
        assert "SELECT EXISTS" in sql
        assert "FROM telegram_config" in sql
        assert parameters is None
        return self.engine.row is not None


class _Context:
    def __init__(self, engine: "_Engine") -> None:
        self.conn = _Connection(engine)

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Engine:
    def __init__(self, encrypted_token: str | None = None) -> None:
        self.row = (
            (
                encrypted_token,
                True,
                False,
                0,
                datetime.now(UTC),
            )
            if encrypted_token is not None
            else None
        )
        self.insert_attempts = 0
        self.insert_parameters: dict[str, object] | None = None

    def connect(self):
        return _Context(self)

    def begin(self):
        return _Context(self)


@pytest.fixture
def fake_crypto(monkeypatch):
    monkeypatch.setattr(service, "encrypt", lambda value: f"encrypted::{value}")
    monkeypatch.setattr(service, "decrypt", lambda value: value.removeprefix("encrypted::"))


@pytest.mark.asyncio
async def test_runtime_missing_row_fails_closed_without_reading_environment(
    monkeypatch, fake_crypto
) -> None:
    engine = _Engine()
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("runtime consulted env")),
    )

    config = await service.load_telegram_config(engine)

    assert config is None
    assert engine.row is None
    assert engine.insert_attempts == 0


@pytest.mark.asyncio
async def test_runtime_existing_database_token_is_loaded_without_environment(
    monkeypatch, fake_crypto
) -> None:
    engine = _Engine("encrypted::ui-token")
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("runtime consulted env")),
    )

    config = await service.load_telegram_config(engine)

    assert config is not None
    assert config.bot_token == "ui-token"
    assert engine.insert_attempts == 0


@pytest.mark.asyncio
async def test_runtime_existing_tombstone_fails_closed_without_environment(
    monkeypatch, fake_crypto
) -> None:
    engine = _Engine("")
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("runtime consulted env")),
    )

    config = await service.load_telegram_config(engine)

    assert config is None
    assert engine.row[0] == ""
    assert engine.insert_attempts == 0


@pytest.mark.asyncio
async def test_explicit_bootstrap_encrypts_once_without_logging_secret(
    monkeypatch, caplog, fake_crypto
) -> None:
    secret = "123456789:TOP_SECRET_BOOTSTRAP_TOKEN"
    engine = _Engine()
    monkeypatch.setattr(service, "get_settings", lambda: _settings(f"  {secret}  "))

    with caplog.at_level(logging.INFO, logger=service.__name__):
        inserted = await service.bootstrap_telegram_config_from_env(engine)

    assert inserted is True
    assert engine.row[0] == f"encrypted::{secret}"
    assert engine.insert_parameters == {
        "bot_token_encrypted": f"encrypted::{secret}",
        "bot_token_fingerprint": bytes.fromhex(telegram_credential_fingerprint(secret)),
        "webhook_generation": 1,
        "webhook_operation": "configure",
        "webhook_desired_url": (
            "https://app.adpulse.su/api/v1/integrations/telegram/webhook?bot_generation=1"
        ),
        "webhook_secret_digest": engine.insert_parameters["webhook_secret_digest"],
        "webhook_state": "pending",
    }
    assert isinstance(engine.insert_parameters["webhook_secret_digest"], bytes)
    assert engine.insert_attempts == 1
    assert secret not in caplog.text
    assert engine.row[0] not in caplog.text


@pytest.mark.asyncio
async def test_explicit_bootstrap_existing_row_or_tombstone_is_authoritative(
    monkeypatch, fake_crypto
) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("bootstrap consulted env")),
    )
    for encrypted_token in ("encrypted::ui-token", ""):
        engine = _Engine(encrypted_token)
        encrypted_inputs: list[str] = []
        monkeypatch.setattr(
            service,
            "encrypt",
            lambda value, calls=encrypted_inputs: calls.append(value) or f"encrypted::{value}",
        )

        inserted = await service.bootstrap_telegram_config_from_env(engine)

        assert inserted is False
        assert engine.insert_attempts == 0
        assert encrypted_inputs == []


@pytest.mark.asyncio
async def test_explicit_bootstrap_blank_input_stays_unconfigured(fake_crypto) -> None:
    engine = _Engine()

    inserted = await service.bootstrap_telegram_config_from_env(
        engine,
        settings=_settings("   "),
    )

    assert inserted is False
    assert engine.row is None
    assert engine.insert_attempts == 0


@pytest.mark.asyncio
async def test_explicit_bootstrap_encryption_error_is_sanitized(monkeypatch, caplog) -> None:
    secret = "123456789:SECRET_FROM_ENV"
    engine = _Engine()
    monkeypatch.setattr(
        service,
        "encrypt",
        lambda value: (_ for _ in ()).throw(RuntimeError(f"cannot encrypt {value}")),
    )

    with caplog.at_level(logging.ERROR, logger=service.__name__):
        with pytest.raises(RuntimeError, match="error_type=RuntimeError") as raised:
            await service.bootstrap_telegram_config_from_env(
                engine,
                settings=_settings(secret),
            )

    assert engine.insert_attempts == 0
    assert secret not in caplog.text
    assert "cannot encrypt" not in caplog.text
    assert secret not in str(raised.value)
