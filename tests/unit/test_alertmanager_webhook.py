from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from apps.api.deps import get_engine, get_settings
from apps.api.routers.v1.alertmanager_webhook import router
from core.telegram.alertmanager_ingress import (
    AlertmanagerAlert,
    AlertmanagerWebhookPayload,
    normalize_alert,
)


class _Context:
    def __init__(self, conn: object) -> None:
        self.conn = conn

    async def __aenter__(self) -> object:
        return self.conn

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _Engine:
    def __init__(self) -> None:
        self.conn = object()
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
        alertmanager_webhook_secret=SecretStr(secret),
        frontend_origin="https://app.adpulse.su",
    )
    return TestClient(app), engine


def _payload(*, status: str = "firing") -> dict[str, object]:
    return {
        "version": "4",
        "status": status,
        "receiver": "durable-notification-plane",
        "alerts": [
            {
                "status": status,
                "labels": {
                    "alertname": "FBWorkerHeartbeatStale",
                    "severity": "critical",
                    "service": "worker",
                },
                "annotations": {"summary": "Worker heartbeat is stale"},
                "startsAt": "2026-07-19T10:00:00Z",
                "endsAt": "2026-07-19T10:05:00Z",
                "fingerprint": "abc123",
            }
        ],
    }


def test_webhook_commits_incident_and_outbox_before_204() -> None:
    client, engine = _client("alertmanager-secret-1234567890123456")
    persist = AsyncMock()

    with patch(
        "apps.api.routers.v1.alertmanager_webhook.persist_alertmanager_payload",
        persist,
    ):
        response = client.post(
            "/api/v1/integrations/alertmanager/webhook",
            headers={"Authorization": "Bearer alertmanager-secret-1234567890123456"},
            json=_payload(),
        )

    assert response.status_code == 204
    assert engine.begin_calls == 1
    persist.assert_awaited_once()
    assert persist.await_args.args[0] is engine.conn
    assert persist.await_args.kwargs == {"operator_public_url": "https://app.adpulse.su"}


def test_webhook_rejects_wrong_bearer_without_database_write() -> None:
    client, engine = _client("alertmanager-secret-1234567890123456")

    response = client.post(
        "/api/v1/integrations/alertmanager/webhook",
        headers={"Authorization": "Bearer wrong"},
        json=_payload(),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid Alertmanager webhook secret"}
    assert engine.begin_calls == 0


def test_webhook_is_fail_closed_without_configured_secret() -> None:
    client, engine = _client("")

    response = client.post(
        "/api/v1/integrations/alertmanager/webhook",
        json=_payload(),
    )

    assert response.status_code == 503
    assert engine.begin_calls == 0


def test_normalization_is_short_and_missing_severity_is_unknown() -> None:
    incoming = AlertmanagerAlert.model_validate(
        {
            "status": "firing",
            "labels": {"alertname": "SourceDown", "service": "observer"},
            "annotations": {"summary": "line one\n  line two"},
            "startsAt": "2026-07-19T10:00:00Z",
        }
    )

    normalized = normalize_alert(incoming)

    assert normalized.severity == "unknown"
    assert normalized.summary == "line one line two"
    assert normalized.fingerprint
    assert len(normalized.incident_key) <= 160


def test_payload_rejects_more_than_one_hundred_alerts() -> None:
    alert = _payload()["alerts"][0]
    with pytest.raises(ValidationError):
        AlertmanagerWebhookPayload.model_validate(
            {
                "version": "4",
                "status": "firing",
                "alerts": [alert] * 101,
            }
        )
