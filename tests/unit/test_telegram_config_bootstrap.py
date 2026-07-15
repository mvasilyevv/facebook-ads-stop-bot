# -*- coding: utf-8 -*-
"""Unit: безопасный bootstrap telegram_config из env на чистой БД."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import core.telegram.service as service


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
            encrypted = parameters["bot_token_encrypted"]
            self.engine.row = (encrypted, None, 0, None)
            return _Result(("default",))
        raise AssertionError(f"unexpected SQL: {sql}")


class _Context:
    def __init__(self, engine: "_Engine") -> None:
        self.conn = _Connection(engine)

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Engine:
    def __init__(self, row=None) -> None:
        self.row = row
        self.insert_attempts = 0

    def connect(self):
        return _Context(self)

    def begin(self):
        return _Context(self)


@pytest.fixture
def fake_crypto(monkeypatch):
    monkeypatch.setattr(service, "encrypt", lambda value: f"encrypted::{value}")
    monkeypatch.setattr(service, "decrypt", lambda value: value.removeprefix("encrypted::"))


@pytest.mark.asyncio
async def test_missing_row_is_bootstrapped_encrypted_without_logging_secret(
    monkeypatch, caplog, fake_crypto
) -> None:
    secret = "123456789:TOP_SECRET_BOOTSTRAP_TOKEN"
    engine = _Engine()
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(telegram_bot_token=f"  {secret}  "),
    )

    with caplog.at_level(logging.INFO, logger=service.__name__):
        config = await service.load_telegram_config(engine)

    assert config is not None
    assert config.bot_token == secret
    assert engine.row[0] == f"encrypted::{secret}"
    assert engine.row[0] != secret
    assert engine.insert_attempts == 1
    assert secret not in caplog.text
    assert engine.row[0] not in caplog.text


@pytest.mark.asyncio
async def test_existing_database_token_wins_over_env(monkeypatch, fake_crypto) -> None:
    engine = _Engine(("encrypted::ui-token", 42, 17, None))
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(telegram_bot_token="env-token"),
    )

    config = await service.load_telegram_config(engine)

    assert config is not None
    assert config.bot_token == "ui-token"
    assert config.chat_id == 42
    assert config.poller_offset == 17
    assert engine.insert_attempts == 0


@pytest.mark.asyncio
async def test_existing_empty_row_is_explicit_disable_and_blocks_env(
    monkeypatch, fake_crypto
) -> None:
    engine = _Engine(("", None, 0, None))
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(telegram_bot_token="env-token"),
    )

    config = await service.load_telegram_config(engine)

    assert config is None
    assert engine.row[0] == ""
    assert engine.insert_attempts == 0


@pytest.mark.asyncio
async def test_missing_row_and_blank_env_stays_unconfigured(monkeypatch, fake_crypto) -> None:
    engine = _Engine()
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(telegram_bot_token="   "),
    )

    config = await service.load_telegram_config(engine)

    assert config is None
    assert engine.row is None
    assert engine.insert_attempts == 0


@pytest.mark.asyncio
async def test_encryption_error_does_not_log_secret_or_exception_message(
    monkeypatch, caplog
) -> None:
    secret = "123456789:SECRET_FROM_ENV"
    engine = _Engine()
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(telegram_bot_token=secret),
    )
    monkeypatch.setattr(
        service,
        "encrypt",
        lambda value: (_ for _ in ()).throw(RuntimeError(f"cannot encrypt {value}")),
    )

    with caplog.at_level(logging.ERROR, logger=service.__name__):
        config = await service.load_telegram_config(engine)

    assert config is None
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text
    assert "cannot encrypt" not in caplog.text
