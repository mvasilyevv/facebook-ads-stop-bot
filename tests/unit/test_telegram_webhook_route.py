from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from apps.api.deps import get_engine, get_settings
from apps.api.routers.v1.telegram_webhook import router


class _Result:
    rowcount = 1

    def scalar_one_or_none(self) -> int:
        return 1


class _Context:
    def __init__(self, conn: AsyncMock) -> None:
        self.conn = conn

    async def __aenter__(self) -> AsyncMock:
        return self.conn

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _Engine:
    def __init__(self) -> None:
        self.conn = AsyncMock()
        self.conn.execute.return_value = _Result()
        self.begin_calls = 0

    def begin(self) -> _Context:
        self.begin_calls += 1
        return _Context(self.conn)


def _client(secret: str) -> tuple[TestClient, _Engine]:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    engine = _Engine()
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        telegram_webhook_secret=SecretStr(secret)
    )
    return TestClient(app), engine


def test_webhook_commits_inbox_before_returning_204() -> None:
    client, engine = _client("webhook_secret-123")

    response = client.post(
        "/api/v1/integrations/telegram/webhook?bot_generation=1",
        headers={"X-Telegram-Bot-Api-Secret-Token": "webhook_secret-123"},
        json={"update_id": 12, "callback_query": {"id": "cb"}},
    )

    assert response.status_code == 204
    assert engine.begin_calls == 1
    assert engine.conn.execute.await_count == 2


def test_webhook_duplicate_update_remains_successful() -> None:
    client, engine = _client("webhook_secret-123")
    engine.conn.execute.side_effect = [_Result(), SimpleNamespace(rowcount=0)]

    response = client.post(
        "/api/v1/integrations/telegram/webhook?bot_generation=1",
        headers={"X-Telegram-Bot-Api-Secret-Token": "webhook_secret-123"},
        json={"update_id": 12},
    )

    assert response.status_code == 204


def test_webhook_rejects_wrong_secret_without_touching_database() -> None:
    client, engine = _client("webhook_secret-123")

    response = client.post(
        "/api/v1/integrations/telegram/webhook?bot_generation=1",
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        json={"update_id": 12},
    )

    assert response.status_code == 401
    assert engine.begin_calls == 0


def test_webhook_is_fail_closed_when_secret_is_unconfigured() -> None:
    client, engine = _client("")

    response = client.post(
        "/api/v1/integrations/telegram/webhook?bot_generation=1",
        json={"update_id": 12},
    )

    assert response.status_code == 503
    assert engine.begin_calls == 0
