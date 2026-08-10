"""One-shot env -> DB bootstrap must be encrypted, idempotent and non-secret."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

import core.adset_pro.credentials as credentials
import core.telegram.web_app_url as web_app_url


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
        if sql.startswith("INSERT INTO adsetpro_credentials"):
            self.engine.adset_insert_attempts += 1
            if self.engine.adset_exists:
                return _Result(None)
            self.engine.adset_exists = True
            self.engine.adset_parameters = dict(parameters)
            return _Result(("default",))
        if sql.startswith("INSERT INTO system_config"):
            self.engine.web_insert_attempts += 1
            if self.engine.web_exists:
                return _Result(None)
            self.engine.web_exists = True
            self.engine.web_parameters = dict(parameters)
            return _Result(("web_app_url",))
        raise AssertionError(f"unexpected SQL: {sql}")

    async def scalar(self, statement, parameters=None):
        sql = " ".join(str(statement).split())
        if "FROM adsetpro_credentials" in sql:
            return self.engine.adset_exists
        if "FROM system_config" in sql:
            return self.engine.web_exists
        raise AssertionError(f"unexpected SQL: {sql}")


class _Context:
    def __init__(self, engine: "_Engine") -> None:
        self.connection = _Connection(engine)

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class _Engine:
    def __init__(self, *, adset_exists: bool = False, web_exists: bool = False) -> None:
        self.adset_exists = adset_exists
        self.web_exists = web_exists
        self.adset_insert_attempts = 0
        self.web_insert_attempts = 0
        self.adset_parameters: dict[str, object] | None = None
        self.web_parameters: dict[str, object] | None = None

    def begin(self):
        return _Context(self)

    def connect(self):
        return _Context(self)


class _CredentialLoadConnection:
    async def execute(self, statement, parameters=None):
        sql = " ".join(str(statement).split())
        assert sql.startswith("SELECT api_key_encrypted, postback_secret_encrypted")
        assert parameters is None
        return _Result((None, b"encrypted::postback-only"))


class _CredentialLoadEngine:
    def connect(self):
        connection = _CredentialLoadConnection()

        class _LoadContext:
            async def __aenter__(self):
                return connection

            async def __aexit__(self, *_args):
                return False

        return _LoadContext()


@pytest.mark.asyncio
async def test_runtime_loader_keeps_postback_secret_independent_from_mcp_key(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        credentials,
        "decrypt",
        lambda value: value.removeprefix("encrypted::"),
    )

    loaded = await credentials.load_adsetpro_credentials(_CredentialLoadEngine())

    assert loaded is not None
    assert loaded.api_key == ""
    assert loaded.postback_secret == "postback-only"


@pytest.mark.asyncio
async def test_adset_bootstrap_supports_postback_only_and_never_logs_secret(
    monkeypatch,
    caplog,
) -> None:
    secret = "POSTBACK_SECRET_MUST_NOT_APPEAR"
    engine = _Engine()
    monkeypatch.setattr(credentials, "encrypt", lambda value: f"encrypted::{value}")

    with caplog.at_level(logging.INFO, logger=credentials.__name__):
        inserted = await credentials.bootstrap_adsetpro_credentials_from_env(
            engine,
            settings=SimpleNamespace(
                adsetpro_mcp_key="",
                adsetpro_postback_secret=secret,
            ),
        )

    assert inserted is True
    assert engine.adset_parameters == {
        "api_key_encrypted": None,
        "postback_secret_encrypted": f"encrypted::{secret}".encode(),
    }
    assert secret not in caplog.text
    assert f"encrypted::{secret}" not in caplog.text


@pytest.mark.asyncio
async def test_adset_bootstrap_existing_row_is_authoritative(monkeypatch) -> None:
    engine = _Engine(adset_exists=True)
    called: list[str] = []
    monkeypatch.setattr(
        credentials,
        "encrypt",
        lambda value: called.append(value) or f"encrypted::{value}",
    )

    inserted = await credentials.bootstrap_adsetpro_credentials_from_env(
        engine,
        settings=SimpleNamespace(
            adsetpro_mcp_key="new-key",
            adsetpro_postback_secret="new-secret",
        ),
    )

    assert inserted is False
    assert engine.adset_parameters is None
    assert engine.adset_insert_attempts == 0
    assert called == []


@pytest.mark.asyncio
async def test_adset_bootstrap_encryption_error_does_not_expose_input(
    monkeypatch,
    caplog,
) -> None:
    secret = "DO_NOT_LOG_THIS_VALUE"
    engine = _Engine()
    monkeypatch.setattr(
        credentials,
        "encrypt",
        lambda value: (_ for _ in ()).throw(RuntimeError(f"failed for {value}")),
    )

    with caplog.at_level(logging.ERROR, logger=credentials.__name__):
        with pytest.raises(RuntimeError, match="error_type=RuntimeError"):
            await credentials.bootstrap_adsetpro_credentials_from_env(
                engine,
                settings=SimpleNamespace(
                    adsetpro_mcp_key=secret,
                    adsetpro_postback_secret="",
                ),
            )

    assert engine.adset_insert_attempts == 0
    assert secret not in caplog.text
    assert secret not in str(caplog.records)


@pytest.mark.asyncio
async def test_web_app_url_bootstrap_writes_once_and_existing_tombstone_wins() -> None:
    url = "https://app.example.test/tma/?source=bootstrap"
    engine = _Engine()
    settings = SimpleNamespace(web_app_url=url)

    assert await web_app_url.bootstrap_web_app_url_from_env(engine, settings=settings) is True
    first_payload = json.loads(str(engine.web_parameters["value"]))
    assert first_payload == {"url": url}

    assert await web_app_url.bootstrap_web_app_url_from_env(engine, settings=settings) is False
    assert engine.web_insert_attempts == 1
    assert json.loads(str(engine.web_parameters["value"])) == {"url": url}


@pytest.mark.asyncio
async def test_web_app_url_bootstrap_rejects_unsafe_url_without_logging_it(
    caplog,
) -> None:
    unsafe = "http://user:secret@example.test/tma?token=DO_NOT_LOG"
    engine = _Engine()

    with caplog.at_level(logging.ERROR, logger=web_app_url.__name__):
        with pytest.raises(ValueError, match="valid HTTPS"):
            await web_app_url.bootstrap_web_app_url_from_env(
                engine,
                settings=SimpleNamespace(web_app_url=unsafe),
            )

    assert engine.web_insert_attempts == 0
    assert unsafe not in caplog.text
    assert "DO_NOT_LOG" not in caplog.text
