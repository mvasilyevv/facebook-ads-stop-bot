from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.telegram.webhook_configuration import TelegramWebhookTarget

ROOT = Path(__file__).resolve().parents[2]


def _load_script() -> ModuleType:
    path = ROOT / "scripts/configure-telegram-webhook.py"
    spec = importlib.util.spec_from_file_location("configure_telegram_webhook", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, row: SimpleNamespace | None) -> None:
        self._row = row

    def first(self) -> SimpleNamespace | None:
        return self._row


class _Connection:
    def __init__(self, row: SimpleNamespace | None) -> None:
        self._row = row

    async def execute(self, _statement: object) -> _Result:
        return _Result(self._row)


class _ConnectContext:
    def __init__(self, row: SimpleNamespace | None) -> None:
        self._connection = _Connection(row)

    async def __aenter__(self) -> _Connection:
        return self._connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Engine:
    def __init__(self, row: SimpleNamespace | None) -> None:
        self._row = row

    def connect(self) -> _ConnectContext:
        return _ConnectContext(self._row)


@pytest.mark.asyncio
async def test_configurator_uses_database_authoritative_bot_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    target = TelegramWebhookTarget(
        url="https://app.example/api/v1/integrations/telegram/webhook",
        secret_token="webhook-secret-123",
        secret_digest=b"x" * 32,
    )
    engine = _Engine(
        SimpleNamespace(
            webhook_state="configured",
            webhook_generation=8,
            webhook_applied_generation=8,
            webhook_desired_url=target.url,
            webhook_remote_url=target.url,
            webhook_last_error_code=None,
        )
    )

    async def load_config(actual_engine: _Engine) -> SimpleNamespace:
        assert actual_engine is engine
        return SimpleNamespace(bot_token="database-bot-token")

    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            frontend_origin="https://app.example",
            telegram_webhook_secret="webhook-secret-123",
            telegram_bot_token="stale-env-token",
        ),
    )
    monkeypatch.setattr(module, "get_engine", lambda: engine)
    monkeypatch.setattr(module, "load_telegram_config", load_config)
    monkeypatch.setattr(module, "resolve_webhook_target", lambda **_kwargs: target)
    ensure = AsyncMock(return_value=True)
    process = AsyncMock(return_value=True)
    monkeypatch.setattr(module, "ensure_webhook_configuration_desired", ensure)
    monkeypatch.setattr(module, "process_one_webhook_configuration", process)

    await module.configure()

    ensure.assert_awaited_once_with(engine, target=target, force=True)
    process.assert_awaited_once_with(engine, worker_id="release-configurator")


@pytest.mark.asyncio
async def test_configurator_fails_when_generation_is_not_claimable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    engine = _Engine(None)
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            frontend_origin="https://app.example",
            telegram_webhook_secret="webhook-secret-123",
        ),
    )
    monkeypatch.setattr(module, "get_engine", lambda: engine)
    monkeypatch.setattr(
        module,
        "load_telegram_config",
        AsyncMock(return_value=SimpleNamespace(bot_token="database-bot-token")),
    )
    monkeypatch.setattr(
        module,
        "ensure_webhook_configuration_desired",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        module,
        "process_one_webhook_configuration",
        AsyncMock(return_value=False),
    )

    with pytest.raises(RuntimeError, match="not claimable"):
        await module.configure()


@pytest.mark.asyncio
async def test_configurator_fails_on_false_green_remote_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    target_url = "https://app.example/api/v1/integrations/telegram/webhook"
    engine = _Engine(
        SimpleNamespace(
            webhook_state="configured",
            webhook_generation=9,
            webhook_applied_generation=9,
            webhook_desired_url=target_url,
            webhook_remote_url="https://stale.example/webhook",
            webhook_last_error_code=None,
        )
    )
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            frontend_origin="https://app.example",
            telegram_webhook_secret="webhook-secret-123",
        ),
    )
    monkeypatch.setattr(module, "get_engine", lambda: engine)
    monkeypatch.setattr(
        module,
        "load_telegram_config",
        AsyncMock(return_value=SimpleNamespace(bot_token="database-bot-token")),
    )
    monkeypatch.setattr(
        module,
        "ensure_webhook_configuration_desired",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        module,
        "process_one_webhook_configuration",
        AsyncMock(return_value=True),
    )

    with pytest.raises(RuntimeError, match="not remotely confirmed"):
        await module.configure()
