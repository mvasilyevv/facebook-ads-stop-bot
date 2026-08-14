from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

import core.vision.token_refresh as refresh

NOW = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)


def _jwt(expires_at: datetime) -> str:
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": expires_at.timestamp()}).encode())
        .decode()
        .rstrip("=")
    )
    return f"header.{payload}.signature"


def _snapshot(
    *,
    token: str,
    attempted_at: datetime | None = None,
    with_credentials: bool = True,
) -> refresh._RefreshSnapshot:
    return refresh._RefreshSnapshot(
        config_id=uuid.uuid4(),
        revision=NOW - timedelta(days=10),
        token_encrypted=token,
        username_encrypted="enc-user" if with_credentials else None,
        password_encrypted="enc-password" if with_credentials else None,
        team_id_encrypted="enc-team" if with_credentials else None,
        folder_id_encrypted="enc-folder" if with_credentials else None,
        attempted_at=attempted_at,
    )


@pytest.fixture
def decrypted_secrets(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    values = {
        "enc-user": "operator-secret-user",
        "enc-password": "operator-secret-password",
        "enc-team": "secret-team-id",
        "enc-folder": "secret-folder-id",
    }
    monkeypatch.setattr(refresh, "decrypt", lambda value: values.get(value, value))
    return values


def test_token_expiration_reads_jwt_exp_and_rejects_opaque_token() -> None:
    expires_at = NOW + timedelta(days=7)

    assert refresh.token_expiration(_jwt(expires_at)) == expires_at
    assert refresh.token_expiration("opaque-token") is None


async def test_login_exchanges_user_token_for_team_token() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/users/auth"):
            assert json.loads(request.content) == {
                "username": "vision-user",
                "password": "vision-password",
            }
            return httpx.Response(200, json={"data": {"token": "personal-token"}})
        assert request.url.path.endswith("/teams/team-1/auth")
        assert request.headers["X-Token"] == "personal-token"
        return httpx.Response(200, json={"data": {"token": "team-token"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        token = await refresh.login_to_vision_cloud(
            "https://v1.empr.cloud/api/v1/",
            username="vision-user",
            password="vision-password",
            team_id="team-1",
            http_client=client,
        )

    assert token == "team-token"
    assert [request.method for request in requests] == ["POST", "GET"]


async def test_login_error_does_not_expose_response_body_or_credentials() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"password": "vision-password", "token": "response-secret-token"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(refresh.VisionCloudAuthError) as exc_info:
            await refresh.login_to_vision_cloud(
                "https://v1.empr.cloud/api/v1",
                username="vision-user",
                password="vision-password",
                http_client=client,
            )

    assert str(exc_info.value) == "Vision cloud authentication failed at user_login (HTTP 401)"
    assert "vision-password" not in str(exc_info.value)
    assert "response-secret-token" not in str(exc_info.value)


async def test_expired_token_publishes_critical_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
    decrypted_secrets: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    del decrypted_secrets
    expired = _jwt(NOW - timedelta(minutes=1))
    monkeypatch.setattr(
        refresh, "_load_refresh_snapshot", AsyncMock(return_value=_snapshot(token=expired))
    )
    monkeypatch.setattr(refresh, "_mark_refresh_attempt", AsyncMock(return_value=True))
    monkeypatch.setattr(
        refresh,
        "login_to_vision_cloud",
        AsyncMock(side_effect=refresh.VisionCloudAuthError(stage="user_login", status_code=401)),
    )
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(refresh, "notify_recurring_incident", notify)
    caplog.set_level(logging.INFO, logger=refresh.__name__)

    result = await refresh.refresh_vision_token_if_needed(
        object(),  # type: ignore[arg-type]
        vision_cloud_url="https://v1.empr.cloud/api/v1",
        now=NOW,
    )

    assert result.outcome == "failed"
    assert notify.await_count == 2
    assert {call.kwargs["severity"] for call in notify.await_args_list} == {"critical"}
    assert {call.kwargs["incident_key"] for call in notify.await_args_list} == {
        refresh.VISION_TOKEN_REFRESH_INCIDENT_KEY
    }
    visible = caplog.text + " ".join(
        str(value) for call in notify.await_args_list for value in call.kwargs.values()
    )
    for secret in (
        "operator-secret-user",
        "operator-secret-password",
        "secret-team-id",
        expired,
    ):
        assert secret not in visible


async def test_live_token_refresh_failure_is_warning(
    monkeypatch: pytest.MonkeyPatch,
    decrypted_secrets: dict[str, str],
) -> None:
    del decrypted_secrets
    current = _jwt(NOW + timedelta(days=2))
    monkeypatch.setattr(
        refresh, "_load_refresh_snapshot", AsyncMock(return_value=_snapshot(token=current))
    )
    monkeypatch.setattr(refresh, "_mark_refresh_attempt", AsyncMock(return_value=True))
    monkeypatch.setattr(
        refresh,
        "login_to_vision_cloud",
        AsyncMock(side_effect=refresh.VisionCloudAuthError(stage="team_login", status_code=503)),
    )
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(refresh, "notify_recurring_incident", notify)

    result = await refresh.refresh_vision_token_if_needed(
        object(),  # type: ignore[arg-type]
        vision_cloud_url="https://v1.empr.cloud/api/v1",
        now=NOW,
    )

    assert result.outcome == "failed"
    assert notify.await_args.kwargs["severity"] == "warning"


async def test_missing_credentials_repeat_uses_one_stable_incident_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        refresh,
        "_load_refresh_snapshot",
        AsyncMock(
            return_value=_snapshot(token=_jwt(NOW + timedelta(days=20)), with_credentials=False)
        ),
    )
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(refresh, "notify_recurring_incident", notify)

    first = await refresh.refresh_vision_token_if_needed(
        object(),  # type: ignore[arg-type]
        vision_cloud_url="https://v1.empr.cloud/api/v1",
        now=NOW,
    )
    second = await refresh.refresh_vision_token_if_needed(
        object(),  # type: ignore[arg-type]
        vision_cloud_url="https://v1.empr.cloud/api/v1",
        now=NOW + timedelta(minutes=5),
    )

    assert first.outcome == second.outcome == "missing_configuration"
    assert notify.await_count == 2
    assert {call.kwargs["incident_key"] for call in notify.await_args_list} == {
        refresh.VISION_TOKEN_REFRESH_INCIDENT_KEY
    }
    assert notify.await_args_list[0].kwargs == notify.await_args_list[1].kwargs


async def test_opaque_token_is_not_retried_more_than_daily(
    monkeypatch: pytest.MonkeyPatch,
    decrypted_secrets: dict[str, str],
) -> None:
    del decrypted_secrets
    monkeypatch.setattr(
        refresh,
        "_load_refresh_snapshot",
        AsyncMock(
            return_value=_snapshot(
                token="opaque-token",
                attempted_at=NOW - timedelta(hours=2),
            )
        ),
    )
    login = AsyncMock()
    monkeypatch.setattr(refresh, "login_to_vision_cloud", login)

    result = await refresh.refresh_vision_token_if_needed(
        object(),  # type: ignore[arg-type]
        vision_cloud_url="https://v1.empr.cloud/api/v1",
        now=NOW,
    )

    assert result.outcome == "throttled"
    login.assert_not_awaited()


async def test_success_updates_revision_and_resolves_incident(
    monkeypatch: pytest.MonkeyPatch,
    decrypted_secrets: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    del decrypted_secrets
    current = _jwt(NOW + timedelta(days=1))
    replacement = _jwt(NOW + timedelta(days=30))
    snapshot = _snapshot(token=current)
    monkeypatch.setattr(refresh, "_load_refresh_snapshot", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(refresh, "_mark_refresh_attempt", AsyncMock(return_value=True))
    monkeypatch.setattr(refresh, "login_to_vision_cloud", AsyncMock(return_value=replacement))
    store = AsyncMock(return_value=True)
    resolve = AsyncMock(return_value=True)
    monkeypatch.setattr(refresh, "_store_refreshed_token", store)
    monkeypatch.setattr(refresh, "resolve_recurring_incident", resolve)
    caplog.set_level(logging.INFO, logger=refresh.__name__)

    result = await refresh.refresh_vision_token_if_needed(
        object(),  # type: ignore[arg-type]
        vision_cloud_url="https://v1.empr.cloud/api/v1",
        now=NOW,
    )

    assert result == refresh.VisionTokenRefreshResult("refreshed", NOW + timedelta(days=30))
    assert store.await_args.kwargs == {
        "snapshot": snapshot,
        "attempted_at": NOW,
        "token": replacement,
    }
    resolve.assert_awaited_once()
    assert "refreshed in PostgreSQL" in caplog.text


class _ScalarConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.parameters: list[dict[str, object]] = []

    async def scalar(self, statement, parameters):  # noqa: ANN001
        self.statements.append(str(statement))
        self.parameters.append(parameters)
        return uuid.uuid4()


class _BeginContext:
    def __init__(self, connection: _ScalarConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _ScalarConnection:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _ScalarEngine:
    def __init__(self) -> None:
        self.connection = _ScalarConnection()

    def begin(self) -> _BeginContext:
        return _BeginContext(self.connection)


async def test_attempt_marker_does_not_invalidate_browser_readiness() -> None:
    engine = _ScalarEngine()
    snapshot = _snapshot(token="opaque-token")

    assert await refresh._mark_refresh_attempt(
        engine,  # type: ignore[arg-type]
        snapshot=snapshot,
        attempted_at=NOW,
        minimum_attempt_interval=timedelta(days=1),
    )

    update_clause = engine.connection.statements[0].split("WHERE", maxsplit=1)[0]
    assert "token_refresh_attempted_at" in update_clause
    assert "updated_at" not in update_clause


async def test_token_write_bumps_updated_at_to_invalidate_browser_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _ScalarEngine()
    snapshot = _snapshot(token="opaque-token")
    monkeypatch.setattr(refresh, "encrypt", lambda _token: "encrypted-new-token")

    assert await refresh._store_refreshed_token(
        engine,  # type: ignore[arg-type]
        snapshot=snapshot,
        attempted_at=NOW,
        token="new-token",
    )

    statement = engine.connection.statements[0]
    assert "SET x_token_encrypted" in statement
    assert "updated_at = GREATEST" in statement
    assert engine.connection.parameters[0]["token"] == "encrypted-new-token"


def test_cloud_auth_error_never_contains_response_or_credentials() -> None:
    exc = refresh.VisionCloudAuthError(stage="user_login", status_code=401)

    assert "password-value" not in str(exc)
    assert vars(exc) == {"stage": "user_login", "status_code": 401}
