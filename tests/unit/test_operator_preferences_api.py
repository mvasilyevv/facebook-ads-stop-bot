"""HTTP contract for the owner display preference resource."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from apps.api import deps
from apps.api.main import create_app
from core.operator.display_preferences import OperatorDisplayPreferenceSnapshot


def _client(monkeypatch) -> tuple[TestClient, list[tuple[str, int, str | None]]]:
    calls: list[tuple[str, int, str | None]] = []
    engine = object()

    async def fake_get(_engine, *, telegram_user_id: int):
        assert _engine is engine
        calls.append(("get", telegram_user_id, None))
        return OperatorDisplayPreferenceSnapshot(
            timezone_name="Europe/Kaliningrad",
            updated_at=datetime(2026, 8, 9, 10, 0, tzinfo=UTC),
        )

    async def fake_put(_engine, *, telegram_user_id: int, timezone_name: str):
        assert _engine is engine
        calls.append(("put", telegram_user_id, timezone_name))
        return OperatorDisplayPreferenceSnapshot(
            timezone_name=timezone_name,
            updated_at=datetime(2026, 8, 9, 10, 1, tzinfo=UTC),
        )

    monkeypatch.setattr(
        "apps.api.routers.v1.operator_preferences.get_operator_display_preference",
        fake_get,
    )
    monkeypatch.setattr(
        "apps.api.routers.v1.operator_preferences.put_operator_display_preference",
        fake_put,
    )

    app = create_app()
    app.dependency_overrides[deps.get_engine] = lambda: engine

    @app.middleware("http")
    async def bind_test_owner(request: Request, call_next):
        raw_owner = request.headers.get("x-test-owner")
        if raw_owner:
            request.state.operator_owner_telegram_user_id = int(raw_owner)
        return await call_next(request)

    return TestClient(app), calls


def test_get_and_put_use_the_same_authenticated_owner(monkeypatch) -> None:
    client, calls = _client(monkeypatch)
    headers = {"X-Test-Owner": "424242"}

    read = client.get("/api/operator/preferences/display", headers=headers)
    assert read.status_code == 200
    assert read.headers["cache-control"] == "private, no-store"
    assert read.json()["timezone_name"] == "Europe/Kaliningrad"

    write = client.put(
        "/api/operator/preferences/display",
        headers=headers,
        json={"timezone_name": " America/New_York "},
    )
    assert write.status_code == 200
    assert write.headers["cache-control"] == "private, no-store"
    assert write.json()["timezone_name"] == "America/New_York"
    assert calls == [
        ("get", 424242, None),
        ("put", 424242, "America/New_York"),
    ]


def test_missing_owner_and_invalid_timezone_are_canonical_api_problems(monkeypatch) -> None:
    client, calls = _client(monkeypatch)

    denied = client.get("/api/operator/preferences/display")
    assert denied.status_code == 403
    assert denied.json() == {
        "code": "forbidden",
        "message": "Не удалось подтвердить профиль владельца",
        "correlation_id": denied.headers["x-request-id"],
        "field_errors": None,
    }

    invalid = client.put(
        "/api/operator/preferences/display",
        headers={"X-Test-Owner": "424242"},
        json={"timezone_name": "Mars/Olympus"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "validation_error"
    assert invalid.json()["field_errors"] == {
        "timezone_name": ["Value error, unknown IANA timezone"]
    }
    assert calls == []


def test_preference_store_failure_is_a_sanitized_api_problem(monkeypatch) -> None:
    client, _calls = _client(monkeypatch)

    async def fail_get(_engine, *, telegram_user_id: int):
        raise SQLAlchemyError(f"postgresql://secret/internal owner={telegram_user_id}")

    monkeypatch.setattr(
        "apps.api.routers.v1.operator_preferences.get_operator_display_preference",
        fail_get,
    )

    response = client.get(
        "/api/operator/preferences/display",
        headers={"X-Test-Owner": "424242"},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"
    assert response.json()["message"] == "Сервис временно недоступен"
    assert "secret" not in response.text
