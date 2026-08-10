# -*- coding: utf-8 -*-
"""Integration tests for PostgreSQL-backed Telegram settings and recipients."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import apps.api.routers.v1.settings_telegram as settings_router
from apps.api.deps import get_engine
from apps.api.main import create_app
from core.config import get_settings
from core.crypto import decrypt, encrypt
from core.telegram.gateway import (
    TelegramFailureKind,
    TelegramGatewayError,
    telegram_credential_fingerprint,
)
from core.telegram.service import (
    bootstrap_telegram_config_from_env,
    load_telegram_config,
)
from core.telegram.webhook_configuration import (
    disable_token_and_schedule_webhook_deletion,
    resolve_webhook_target,
    store_rotated_token_and_schedule_webhook,
)

_SETTINGS_BOT_TOKEN = "settings-authority-bot-token-v1"


async def _wait_for_blocked_backend(pg_engine, *, query_fragment: str) -> None:
    deadline = asyncio.get_running_loop().time() + 3.0
    while asyncio.get_running_loop().time() < deadline:
        async with pg_engine.connect() as conn:
            blocked = await conn.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_stat_activity
                        WHERE datname = current_database()
                          AND pid <> pg_backend_pid()
                          AND wait_event_type = 'Lock'
                          AND POSITION(:query_fragment IN query) > 0
                    )
                    """
                ),
                {"query_fragment": query_fragment},
            )
        if blocked:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"backend did not block on {query_fragment!r}")


async def _seed_ready_settings_bot(pg_engine) -> None:
    fingerprint = bytes.fromhex(telegram_credential_fingerprint(_SETTINGS_BOT_TOKEN))
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_config"))
        await conn.execute(
            text(
                """
                INSERT INTO telegram_config
                    (singleton_key, bot_token_encrypted, bot_token_fingerprint,
                     is_enabled, webhook_generation,
                     webhook_applied_generation, webhook_operation,
                     webhook_desired_url, webhook_state,
                     webhook_configured_at)
                VALUES
                    ('default', :encrypted, :fingerprint, TRUE, 1, 1,
                     'configure',
                     'https://app.example.test/api/v1/integrations/telegram/webhook?bot_generation=1',
                     'configured', NOW())
                """
            ),
            {
                "encrypted": encrypt(_SETTINGS_BOT_TOKEN),
                "fingerprint": fingerprint,
            },
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_client(pg_engine, monkeypatch):
    """AsyncClient connected only to the disposable PostgreSQL fixture."""
    monkeypatch.setattr(get_settings(), "frontend_origin", "https://app.example.test")
    monkeypatch.setattr(
        get_settings(),
        "telegram_webhook_secret",
        SecretStr("integration-webhook-secret"),
    )
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine

    # Очистка ДО теста: на shared-БД соседние тесты/фикстуры могли оставить config
    # (тогда no_config_returns_defaults видит чужой токен → is_authorized=True).
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_invites"))
        await conn.execute(text("DELETE FROM telegram_recipients"))
        await conn.execute(text("DELETE FROM telegram_config"))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    # Очистка таблиц после теста.
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_invites"))
        await conn.execute(text("DELETE FROM telegram_recipients"))
        await conn.execute(text("DELETE FROM telegram_config"))


# ---------------------------------------------------------------------------
# GET /settings/telegram
# ---------------------------------------------------------------------------


# Без config — авторизация и public identity не подтверждены.
@pytest.mark.asyncio
async def test_get_telegram_no_config_returns_defaults(app_client) -> None:
    resp = await app_client.get("/api/settings/telegram")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_authorized"] is False
    assert "poller_status" not in data
    assert data["bot_username"] is None
    assert data["auth_deep_link"] is None
    assert data["activation_command"] is None
    assert data["auth_invite_expires_at"] is None
    assert "chat_id" not in data


@pytest.mark.asyncio
async def test_settings_get_me_delete_wins_without_old_credential_call(
    app_client,
    pg_engine,
    monkeypatch,
) -> None:
    """A committed DELETE generation fences a snapshot before getMe starts."""
    await _seed_ready_settings_bot(pg_engine)
    fingerprint = telegram_credential_fingerprint(_SETTINGS_BOT_TOKEN)
    gateway = AsyncMock()
    gateway.credential_fingerprint = fingerprint
    monkeypatch.setattr(
        "core.telegram.gateway.TelegramHTMLGateway",
        lambda *_args, **_kwargs: gateway,
    )

    blocker = await pg_engine.connect()
    blocker_tx = await blocker.begin()
    request_task = None
    try:
        await disable_token_and_schedule_webhook_deletion(blocker)
        request_task = asyncio.create_task(app_client.get("/api/settings/telegram"))
        await _wait_for_blocked_backend(
            pg_engine,
            query_fragment="FROM telegram_config",
        )
        await blocker_tx.commit()
        response = await asyncio.wait_for(request_task, timeout=3.0)
    finally:
        if blocker_tx.is_active:
            await blocker_tx.rollback()
        if request_task is not None and not request_task.done():
            request_task.cancel()
            await asyncio.gather(request_task, return_exceptions=True)
        await blocker.close()

    assert response.status_code == 200
    assert response.json()["bot_username"] is None
    gateway.get_me.assert_not_awaited()
    gateway.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_settings_get_me_holds_authority_until_rotation_can_commit(
    app_client,
    pg_engine,
    monkeypatch,
) -> None:
    """If getMe wins, token rotation waits until the external call returns."""
    await _seed_ready_settings_bot(pg_engine)
    entered = asyncio.Event()
    release = asyncio.Event()
    fingerprint = telegram_credential_fingerprint(_SETTINGS_BOT_TOKEN)

    async def get_me() -> dict[str, object]:
        entered.set()
        await release.wait()
        return {"id": 1, "username": "settings_old_bot"}

    gateway = AsyncMock()
    gateway.credential_fingerprint = fingerprint
    gateway.get_me.side_effect = get_me
    monkeypatch.setattr(
        "core.telegram.gateway.TelegramHTMLGateway",
        lambda *_args, **_kwargs: gateway,
    )
    replacement_token = "settings-authority-bot-token-v2"
    target = resolve_webhook_target(
        frontend_origin="https://app.example.test",
        secret_token=SecretStr("integration-webhook-secret"),
    )

    async def rotate() -> None:
        async with pg_engine.begin() as conn:
            await store_rotated_token_and_schedule_webhook(
                conn,
                bot_token_encrypted=encrypt(replacement_token),
                bot_token_fingerprint=telegram_credential_fingerprint(replacement_token),
                target=target,
            )

    request_task = asyncio.create_task(app_client.get("/api/settings/telegram"))
    rotation_task = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=3.0)
        rotation_task = asyncio.create_task(rotate())
        await _wait_for_blocked_backend(
            pg_engine,
            query_fragment="SELECT webhook_generation",
        )
        release.set()
        response = await asyncio.wait_for(request_task, timeout=3.0)
        await asyncio.wait_for(rotation_task, timeout=3.0)
    finally:
        release.set()
        for task in (request_task, rotation_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (request_task, rotation_task) if task is not None),
            return_exceptions=True,
        )

    assert response.status_code == 200
    assert response.json()["bot_username"] == "settings_old_bot"
    gateway.get_me.assert_awaited_once()
    gateway.close.assert_awaited_once()
    async with pg_engine.connect() as conn:
        generation, state, stored_fingerprint = (
            await conn.execute(
                text(
                    """
                    SELECT webhook_generation, webhook_state,
                           bot_token_fingerprint
                    FROM telegram_config
                    WHERE singleton_key = 'default'
                    """
                )
            )
        ).one()
    assert generation == 2
    assert state == "pending"
    assert bytes(stored_fingerprint).hex() == telegram_credential_fingerprint(replacement_token)


@pytest.mark.asyncio
async def test_current_settings_get_me_401_opens_auth_incident(
    app_client,
    pg_engine,
    monkeypatch,
) -> None:
    await _seed_ready_settings_bot(pg_engine)
    gateway = AsyncMock()
    gateway.credential_fingerprint = telegram_credential_fingerprint(_SETTINGS_BOT_TOKEN)
    gateway.get_me.side_effect = TelegramGatewayError(
        method="getMe",
        kind=TelegramFailureKind.UNAUTHORIZED,
        error_code=401,
        description="Unauthorized",
    )
    monkeypatch.setattr(
        "core.telegram.gateway.TelegramHTMLGateway",
        lambda *_args, **_kwargs: gateway,
    )

    response = await app_client.get("/api/settings/telegram")

    assert response.status_code == 200
    assert response.json()["bot_username"] is None
    async with pg_engine.connect() as conn:
        auth_incidents = await conn.scalar(
            text(
                """
                SELECT COUNT(*) FROM incidents
                WHERE incident_key='telegram:bot-auth' AND status='open'
                """
            )
        )
    assert auth_incidents == 1
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                DELETE FROM notification_deliveries d USING notification_events e,
                    incidents i
                WHERE d.event_id=e.id AND e.incident_id=i.id
                  AND i.incident_key='telegram:bot-auth'
                """
            )
        )
        await conn.execute(
            text(
                """
                DELETE FROM notification_events e USING incidents i
                WHERE e.incident_id=i.id AND i.incident_key='telegram:bot-auth'
                """
            )
        )
        await conn.execute(text("DELETE FROM incidents WHERE incident_key='telegram:bot-auth'"))


@pytest.mark.asyncio
async def test_stale_settings_get_me_401_after_rotation_opens_no_auth_incident(
    app_client,
    pg_engine,
    monkeypatch,
) -> None:
    await _seed_ready_settings_bot(pg_engine)
    entered = asyncio.Event()
    release = asyncio.Event()
    gateway = AsyncMock()
    gateway.credential_fingerprint = telegram_credential_fingerprint(_SETTINGS_BOT_TOKEN)

    async def unauthorized_get_me():
        entered.set()
        await release.wait()
        raise TelegramGatewayError(
            method="getMe",
            kind=TelegramFailureKind.UNAUTHORIZED,
            error_code=401,
            description="Unauthorized",
        )

    gateway.get_me.side_effect = unauthorized_get_me
    monkeypatch.setattr(
        "core.telegram.gateway.TelegramHTMLGateway",
        lambda *_args, **_kwargs: gateway,
    )
    original_open = __import__(
        "core.telegram.notifications", fromlist=["open_telegram_auth_incident_in_transaction"]
    ).open_telegram_auth_incident_in_transaction
    rotation_done = asyncio.Event()

    async def delayed_open(*args, **kwargs):
        await asyncio.wait_for(rotation_done.wait(), timeout=3.0)
        return await original_open(*args, **kwargs)

    monkeypatch.setattr(
        "core.telegram.notifications.open_telegram_auth_incident_in_transaction",
        delayed_open,
    )
    replacement = "settings-after-401-rotation"

    async def rotate() -> None:
        target = resolve_webhook_target(
            frontend_origin="https://app.example.test",
            secret_token=SecretStr("integration-webhook-secret"),
        )
        async with pg_engine.begin() as conn:
            await store_rotated_token_and_schedule_webhook(
                conn,
                bot_token_encrypted=encrypt(replacement),
                bot_token_fingerprint=telegram_credential_fingerprint(replacement),
                target=target,
            )
        rotation_done.set()

    request_task = asyncio.create_task(app_client.get("/api/settings/telegram"))
    rotation_task = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=3.0)
        rotation_task = asyncio.create_task(rotate())
        release.set()
        response = await asyncio.wait_for(request_task, timeout=5.0)
        await asyncio.wait_for(rotation_task, timeout=3.0)
    finally:
        release.set()
        for task in (request_task, rotation_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (request_task, rotation_task) if task is not None),
            return_exceptions=True,
        )

    assert response.status_code == 200
    async with pg_engine.connect() as conn:
        auth_incidents = await conn.scalar(
            text("SELECT COUNT(*) FROM incidents WHERE incident_key='telegram:bot-auth'")
        )
    assert auth_incidents == 0


@pytest.mark.asyncio
async def test_menu_private_403_disables_delivery_not_owner_access(
    pg_engine,
    monkeypatch,
) -> None:
    await _seed_ready_settings_bot(pg_engine)
    recipient_id = uuid.uuid4()
    chat_id = 9_100_000_000 + uuid.uuid4().int % 100_000_000
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (id, chat_id, telegram_user_id, role)
                VALUES (:id, :chat_id, :chat_id, 'owner')
                """
            ),
            {"id": recipient_id, "chat_id": chat_id},
        )
    gateway = AsyncMock()
    gateway.credential_fingerprint = telegram_credential_fingerprint(_SETTINGS_BOT_TOKEN)
    gateway.set_chat_menu_button.side_effect = [
        None,
        TelegramGatewayError(
            method="setChatMenuButton",
            kind=TelegramFailureKind.FORBIDDEN,
            error_code=403,
            description="Forbidden",
        ),
    ]
    monkeypatch.setattr(
        "core.telegram.gateway.TelegramHTMLGateway",
        lambda *_args, **_kwargs: gateway,
    )
    try:
        synced = await settings_router._sync_bot_menu_button(
            pg_engine,
            "https://operator.example.test/tma/",
        )
        assert synced is False
        async with pg_engine.connect() as conn:
            state = (
                await conn.execute(
                    text(
                        """
                        SELECT r.role, r.revoked_at, p.is_enabled
                        FROM telegram_recipients r
                        JOIN telegram_recipient_preferences p ON p.recipient_id=r.id
                        WHERE r.id=:id
                        """
                    ),
                    {"id": recipient_id},
                )
            ).one()
        assert (state.role, state.revoked_at, state.is_enabled) == ("owner", None, False)
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE id=:id"),
                {"id": recipient_id},
            )


@pytest.mark.asyncio
async def test_menu_stale_private_403_after_rotation_does_not_disable_recipient(
    pg_engine,
    monkeypatch,
) -> None:
    await _seed_ready_settings_bot(pg_engine)
    recipient_id = uuid.uuid4()
    chat_id = 9_200_000_000 + uuid.uuid4().int % 100_000_000
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (id, chat_id, telegram_user_id, role)
                VALUES (:id, :chat_id, :chat_id, 'owner')
                """
            ),
            {"id": recipient_id, "chat_id": chat_id},
        )
    entered = asyncio.Event()
    release = asyncio.Event()
    gateway = AsyncMock()
    gateway.credential_fingerprint = telegram_credential_fingerprint(_SETTINGS_BOT_TOKEN)
    calls = 0

    async def set_menu(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        entered.set()
        await release.wait()
        raise TelegramGatewayError(
            method="setChatMenuButton",
            kind=TelegramFailureKind.FORBIDDEN,
            error_code=403,
            description="Forbidden",
        )

    gateway.set_chat_menu_button.side_effect = set_menu
    monkeypatch.setattr(
        "core.telegram.gateway.TelegramHTMLGateway",
        lambda *_args, **_kwargs: gateway,
    )
    original_gate = __import__(
        "core.telegram.outbound_authority",
        fromlist=["telegram_failure_authority_is_current"],
    ).telegram_failure_authority_is_current
    rotation_done = asyncio.Event()

    async def delayed_gate(*args, **kwargs):
        await asyncio.wait_for(rotation_done.wait(), timeout=3.0)
        return await original_gate(*args, **kwargs)

    monkeypatch.setattr(
        "core.telegram.outbound_authority.telegram_failure_authority_is_current",
        delayed_gate,
    )
    replacement = "menu-after-403-rotation"

    async def rotate() -> None:
        target = resolve_webhook_target(
            frontend_origin="https://app.example.test",
            secret_token=SecretStr("integration-webhook-secret"),
        )
        async with pg_engine.begin() as conn:
            await store_rotated_token_and_schedule_webhook(
                conn,
                bot_token_encrypted=encrypt(replacement),
                bot_token_fingerprint=telegram_credential_fingerprint(replacement),
                target=target,
            )
        rotation_done.set()

    sync_task = asyncio.create_task(
        settings_router._sync_bot_menu_button(
            pg_engine,
            "https://operator.example.test/tma/",
        )
    )
    rotation_task = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=3.0)
        rotation_task = asyncio.create_task(rotate())
        release.set()
        assert await asyncio.wait_for(sync_task, timeout=5.0) is False
        await asyncio.wait_for(rotation_task, timeout=3.0)
        async with pg_engine.connect() as conn:
            preference = await conn.scalar(
                text(
                    "SELECT is_enabled FROM telegram_recipient_preferences WHERE recipient_id=:id"
                ),
                {"id": recipient_id},
            )
        assert preference is None
    finally:
        release.set()
        for task in (sync_task, rotation_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (sync_task, rotation_task) if task is not None),
            return_exceptions=True,
        )
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE id=:id"),
                {"id": recipient_id},
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sync_result", "expected_state"),
    [(True, "synced"), (False, "incomplete")],
)
async def test_web_app_url_response_reports_menu_sync_state(
    app_client,
    monkeypatch,
    sync_result: bool,
    expected_state: str,
) -> None:
    sync = AsyncMock(return_value=sync_result)
    monkeypatch.setattr(settings_router, "_sync_bot_menu_button", sync)
    with patch(
        "apps.api.routers.v1.settings_telegram.compute_bot_username",
        new=AsyncMock(return_value=None),
    ):
        response = await app_client.put(
            "/api/settings/telegram/web-app-url",
            json={"web_app_url": "https://operator.example.test/tma/"},
        )
    assert response.status_code == 200
    assert response.json()["menu_sync_state"] == expected_state
    sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_diagnostics_describe_webhook_gateway_and_durable_queues(
    app_client, pg_engine, monkeypatch
) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_command_replies"))
        await conn.execute(text("DELETE FROM telegram_updates_inbox"))
        await conn.execute(text("DELETE FROM notification_deliveries"))
        await conn.execute(text("DELETE FROM notification_events"))
        await conn.execute(text("DELETE FROM incidents WHERE incident_key = 'telegram:bot-auth'"))
        await conn.execute(
            text(
                """
                INSERT INTO telegram_config
                    (singleton_key, bot_token_encrypted, is_enabled,
                     webhook_generation, webhook_applied_generation,
                     webhook_operation, webhook_desired_url,
                     webhook_secret_digest, webhook_state,
                     webhook_remote_url, webhook_remote_pending_update_count,
                     webhook_checked_at, webhook_configured_at)
                VALUES
                    ('default', 'encrypted-token-present', TRUE,
                     3, 3, 'configure',
                     'https://app.example.test/api/v1/integrations/telegram/webhook',
                     digest('integration-webhook-secret', 'sha256'),
                     'configured',
                     'https://app.example.test/api/v1/integrations/telegram/webhook',
                     0, NOW(), NOW())
                ON CONFLICT (singleton_key) DO UPDATE
                SET bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                    is_enabled = EXCLUDED.is_enabled,
                    webhook_generation = EXCLUDED.webhook_generation,
                    webhook_applied_generation = EXCLUDED.webhook_applied_generation,
                    webhook_operation = EXCLUDED.webhook_operation,
                    webhook_desired_url = EXCLUDED.webhook_desired_url,
                    webhook_secret_digest = EXCLUDED.webhook_secret_digest,
                    webhook_state = EXCLUDED.webhook_state,
                    webhook_remote_url = EXCLUDED.webhook_remote_url,
                    webhook_remote_pending_update_count =
                        EXCLUDED.webhook_remote_pending_update_count,
                    webhook_checked_at = EXCLUDED.webhook_checked_at,
                    webhook_configured_at = EXCLUDED.webhook_configured_at
                """
            )
        )

    response = await app_client.get("/api/settings/telegram/diagnostics")

    assert response.status_code == 200
    diagnostics = response.json()
    assert diagnostics["webhook_state"] == "configured"
    assert diagnostics["webhook_generation"] == 3
    assert diagnostics["webhook_applied_generation"] == 3
    assert diagnostics["webhook_remote_url_matches"] is True
    assert diagnostics["webhook_secret_digest_present"] is True
    assert diagnostics["webhook_remote_pending_update_count"] == 0
    assert diagnostics["gateway_state"] == "configured"
    assert diagnostics["outbox_state"] == "idle"
    assert diagnostics["last_webhook_update_at"] is None
    assert diagnostics["inbox_counts"] == {}
    assert diagnostics["delivery_counts"] == {}
    assert diagnostics["command_reply_counts"] == {}


# После PUT /token — is_authorized=True, bot_token_encrypted НЕ возвращается
@pytest.mark.asyncio
async def test_put_token_then_get_is_authorized(app_client) -> None:
    # Мокаем bot_username=None (getMe не нужен в тесте)
    with patch(
        "apps.api.routers.v1.settings_telegram.compute_bot_username",
        new=AsyncMock(return_value=None),
    ):
        resp = await app_client.put(
            "/api/settings/telegram/token",
            json={"bot_token": "1234567890:TEST_TOKEN"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_authorized"] is True
    # bot_token_encrypted не должен попасть в ответ
    assert "bot_token_encrypted" not in data


@pytest.mark.asyncio
async def test_put_token_normalizes_whitespace_before_encryption_and_fingerprint(
    app_client,
    pg_engine,
) -> None:
    normalized = "1234567890:NORMALIZED_TOKEN"
    with patch(
        "apps.api.routers.v1.settings_telegram.compute_bot_username",
        new=AsyncMock(return_value=None),
    ):
        response = await app_client.put(
            "/api/settings/telegram/token",
            json={"bot_token": f" \n\t{normalized}\r\n "},
        )

    assert response.status_code == 200
    async with pg_engine.connect() as conn:
        encrypted, stored_fingerprint = (
            await conn.execute(
                text(
                    """
                    SELECT bot_token_encrypted, bot_token_fingerprint
                    FROM telegram_config
                    WHERE singleton_key = 'default'
                    """
                )
            )
        ).one()
    assert decrypt(str(encrypted)) == normalized
    assert bytes(stored_fingerprint).hex() == telegram_credential_fingerprint(normalized)
    loaded = await load_telegram_config(pg_engine)
    assert loaded is not None
    assert loaded.bot_token == normalized


@pytest.mark.asyncio
async def test_put_token_rejects_whitespace_only(app_client) -> None:
    response = await app_client.put(
        "/api/settings/telegram/token",
        json={"bot_token": " \n\t "},
    )
    assert response.status_code == 422


# PUT /token, затем GET — is_authorized=True
@pytest.mark.asyncio
async def test_get_after_put_token_shows_authorized(app_client) -> None:
    with patch(
        "apps.api.routers.v1.settings_telegram.compute_bot_username",
        new=AsyncMock(return_value=None),
    ):
        await app_client.put(
            "/api/settings/telegram/token",
            json={"bot_token": "9876543210:ANOTHER_TOKEN"},
        )
        resp = await app_client.get("/api/settings/telegram")
    assert resp.status_code == 200
    assert resp.json()["is_authorized"] is True


# DELETE /settings/telegram — после GET is_authorized=False
@pytest.mark.asyncio
async def test_delete_telegram_clears_token(app_client) -> None:
    with patch(
        "apps.api.routers.v1.settings_telegram.compute_bot_username",
        new=AsyncMock(return_value=None),
    ):
        # Создаём токен
        await app_client.put(
            "/api/settings/telegram/token",
            json={"bot_token": "111:TOKEN"},
        )
        # Удаляем
        resp = await app_client.delete("/api/settings/telegram")
        assert resp.status_code == 200
        assert resp.json()["is_authorized"] is False

        # GET подтверждает
        resp2 = await app_client.get("/api/settings/telegram")
        assert resp2.status_code == 200
        assert resp2.json()["is_authorized"] is False


# DELETE на чистой БД оставляет DB-authoritative tombstone.
@pytest.mark.asyncio
async def test_delete_without_row_blocks_explicit_bootstrap(
    app_client, pg_engine, monkeypatch
) -> None:
    monkeypatch.setattr(
        get_settings(),
        "telegram_bot_token",
        SecretStr("123456789:ENV_TOKEN_MUST_STAY_DISABLED"),
    )

    resp = await app_client.delete("/api/settings/telegram")

    assert resp.status_code == 200
    assert resp.json()["is_authorized"] is False
    assert (
        await bootstrap_telegram_config_from_env(
            pg_engine,
            settings=get_settings(),
        )
        is False
    )
    assert await load_telegram_config(pg_engine) is None
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT bot_token_encrypted
                    FROM telegram_config
                    WHERE singleton_key = 'default'
                    """
                )
            )
        ).one()
    assert row[0] == ""


# PUT из UI перезаписывает токен, импортированный explicit migrator bootstrap.
@pytest.mark.asyncio
async def test_put_token_overrides_env_bootstrap(app_client, pg_engine, monkeypatch) -> None:
    monkeypatch.setattr(
        get_settings(),
        "telegram_bot_token",
        SecretStr("123456789:ENV_BOOTSTRAP_TOKEN"),
    )
    assert (
        await bootstrap_telegram_config_from_env(
            pg_engine,
            settings=get_settings(),
        )
        is True
    )
    bootstrapped = await load_telegram_config(pg_engine)
    assert bootstrapped is not None
    assert bootstrapped.bot_token == "123456789:ENV_BOOTSTRAP_TOKEN"

    with patch(
        "apps.api.routers.v1.settings_telegram.compute_bot_username",
        new=AsyncMock(return_value=None),
    ):
        resp = await app_client.put(
            "/api/settings/telegram/token",
            json={"bot_token": "987654321:UI_TOKEN"},
        )

    assert resp.status_code == 200
    configured = await load_telegram_config(pg_engine)
    assert configured is not None
    assert configured.bot_token == "987654321:UI_TOKEN"


# ---------------------------------------------------------------------------
# GET /settings/telegram/recipients
# ---------------------------------------------------------------------------


# GET recipients — видит только non-revoked
@pytest.mark.asyncio
async def test_get_recipients_returns_only_non_revoked(pg_engine) -> None:
    # Вставляем двух получателей: один активный, один revoked
    active_id = uuid.uuid4()
    revoked_id = uuid.uuid4()
    now = datetime.now(UTC)

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO telegram_recipients (id, chat_id, telegram_user_id, role, created_at)
                VALUES (:id1, 100, 200, 'owner', :now)
            """),
            {"id1": active_id, "now": now},
        )
        await conn.execute(
            text("""
                INSERT INTO telegram_recipients
                  (id, chat_id, telegram_user_id, role, created_at, revoked_at)
                VALUES (:id2, 101, 201, 'recipient', :now, :rev)
            """),
            {"id2": revoked_id, "now": now, "rev": now},
        )

    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/settings/telegram/recipients")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["recipients"][0]["id"] == str(active_id)

    # Очистка
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_recipients"))


# DELETE /recipients/{id} — soft-delete, revoked_at выставлен
@pytest.mark.asyncio
async def test_delete_recipient_soft_delete(pg_engine) -> None:
    r_id = uuid.uuid4()
    now = datetime.now(UTC)

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO telegram_recipients (id, chat_id, telegram_user_id, role, created_at)
                VALUES (:id, 100, 200, 'recipient', :now)
            """),
            {"id": r_id, "now": now},
        )

    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.delete(f"/api/settings/telegram/recipients/{r_id}")
        assert resp.status_code == 200

        # Проверяем, что revoked_at теперь выставлен
        async with AsyncSession(pg_engine) as session:
            row_result = await session.execute(
                text("SELECT revoked_at FROM telegram_recipients WHERE id = :id"),
                {"id": r_id},
            )
            row = row_result.first()
        assert row is not None
        assert row[0] is not None  # revoked_at заполнен

    # Очистка
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_recipients"))


@pytest.mark.asyncio
async def test_delete_last_owner_is_rejected(pg_engine) -> None:
    owner_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (id, chat_id, telegram_user_id, role)
                VALUES (:id, 102, 202, 'owner')
                """
            ),
            {"id": owner_id},
        )

    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete(f"/api/settings/telegram/recipients/{owner_id}")

        assert response.status_code == 409
        assert "последнего активного владельца" in response.json()["message"]
        async with pg_engine.connect() as conn:
            revoked_at = await conn.scalar(
                text("SELECT revoked_at FROM telegram_recipients WHERE id = :id"),
                {"id": owner_id},
            )
        assert revoked_at is None
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE id = :id"),
                {"id": owner_id},
            )


@pytest.mark.asyncio
async def test_delete_recipient_atomically_retires_pending_work_and_capabilities(
    pg_engine,
) -> None:
    recipient_id = uuid.uuid4()
    event_id = uuid.uuid4()
    action_token_id = uuid.uuid4()
    navigation_token_id = uuid.uuid4()
    chat_id = 7_700_000_001
    update_id = 7_710_000_001
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (id, chat_id, telegram_user_id, role)
                VALUES (:recipient_id, :chat_id, :chat_id, 'recipient')
                """
            ),
            {"recipient_id": recipient_id, "chat_id": chat_id},
        )
        await conn.execute(
            text(
                """
                INSERT INTO notification_events
                    (id, event_type, severity, audience, facts, dedupe_key)
                VALUES
                    (:event_id, 'test_revoke', 'warning', 'owners',
                     '{"title":"revoke"}'::jsonb, :dedupe_key)
                """
            ),
            {
                "event_id": event_id,
                "dedupe_key": f"test:recipient-revoke:{event_id}",
            },
        )
        delivery_id = (
            await conn.execute(
                text(
                    """
                    INSERT INTO notification_deliveries
                        (event_id, recipient_id, bot_generation,
                         telegram_chat_id, state)
                    VALUES (:event_id, :recipient_id, 1, :chat_id, 'pending')
                    RETURNING id
                    """
                ),
                {
                    "event_id": event_id,
                    "recipient_id": recipient_id,
                    "chat_id": chat_id,
                },
            )
        ).scalar_one()
        await conn.execute(
            text(
                """
                INSERT INTO telegram_updates_inbox
                    (bot_generation, update_id, payload, state, processed_at)
                VALUES (1, :update_id, '{}'::jsonb, 'processed', NOW())
                """
            ),
            {"update_id": update_id},
        )
        reply_id = (
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_command_replies
                        (bot_generation, update_id, ordinal, chat_id, text, state)
                    VALUES (1, :update_id, 0, :chat_id, 'pending reply', 'pending')
                    RETURNING id
                    """
                ),
                {"update_id": update_id, "chat_id": chat_id},
            )
        ).scalar_one()
        await conn.execute(
            text(
                """
                INSERT INTO telegram_action_tokens
                    (id, token_digest, delivery_id, event_id, recipient_id,
                     action_key, action_kind, target_type, target_id, expires_at)
                VALUES
                    (:id, :digest, :delivery_id, :event_id, :recipient_id,
                     'pause', 'pause_ad', 'fb_ad', 'ad-revoke-test',
                     NOW() + INTERVAL '1 hour')
                """
            ),
            {
                "id": action_token_id,
                "digest": b"a" * 32,
                "delivery_id": delivery_id,
                "event_id": event_id,
                "recipient_id": recipient_id,
            },
        )
        await conn.execute(
            text(
                """
                INSERT INTO telegram_navigation_tokens
                    (id, token_digest, recipient_id, delivery_id, event_id,
                     target_kind, target_id, expires_at)
                VALUES
                    (:id, :digest, :recipient_id, :delivery_id, :event_id,
                     'incident', 'incident-revoke-test',
                     NOW() + INTERVAL '1 hour')
                """
            ),
            {
                "id": navigation_token_id,
                "digest": b"n" * 32,
                "recipient_id": recipient_id,
                "delivery_id": delivery_id,
                "event_id": event_id,
            },
        )

    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.delete(f"/api/settings/telegram/recipients/{recipient_id}")
        assert response.status_code == 200

        async with pg_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT r.revoked_at,
                               d.state AS delivery_state,
                               d.last_error_code AS delivery_error,
                               cr.state AS reply_state,
                               cr.last_error_code AS reply_error,
                               at.revoked_at AS action_revoked_at,
                               nt.revoked_at AS navigation_revoked_at
                        FROM telegram_recipients r
                        JOIN notification_deliveries d
                          ON d.recipient_id = r.id AND d.id = :delivery_id
                        JOIN telegram_command_replies cr ON cr.id = :reply_id
                        JOIN telegram_action_tokens at ON at.id = :action_token_id
                        JOIN telegram_navigation_tokens nt
                          ON nt.id = :navigation_token_id
                        WHERE r.id = :recipient_id
                        """
                    ),
                    {
                        "recipient_id": recipient_id,
                        "delivery_id": delivery_id,
                        "reply_id": reply_id,
                        "action_token_id": action_token_id,
                        "navigation_token_id": navigation_token_id,
                    },
                )
            ).one()
        assert row.revoked_at is not None
        assert row.delivery_state == "superseded"
        assert row.delivery_error == "recipient_revoked"
        assert row.reply_state == "dead"
        assert row.reply_error == "recipient_revoked"
        assert row.action_revoked_at is not None
        assert row.navigation_revoked_at is not None
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM telegram_updates_inbox WHERE update_id = :update_id"),
                {"update_id": update_id},
            )
            await conn.execute(
                text("DELETE FROM notification_events WHERE id = :event_id"),
                {"event_id": event_id},
            )
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE id = :recipient_id"),
                {"recipient_id": recipient_id},
            )


# DELETE несуществующего получателя → 404
@pytest.mark.asyncio
async def test_delete_nonexistent_recipient_returns_404(app_client) -> None:
    fake_id = uuid.uuid4()
    resp = await app_client.delete(f"/api/settings/telegram/recipients/{fake_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /settings/telegram/recipients/invite
# ---------------------------------------------------------------------------


# POST invite — возвращает code и создаёт строку в telegram_invites
@pytest.mark.asyncio
async def test_post_invite_creates_code(pg_engine) -> None:
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/settings/telegram/recipients/invite")

    assert resp.status_code == 200
    data = resp.json()
    assert "code" in data
    assert len(data["code"]) > 0
    assert "expires_at" in data

    # Проверяем, что строка появилась в БД
    async with pg_engine.begin() as conn:
        result = await conn.execute(
            text("SELECT code FROM telegram_invites WHERE code = :code"),
            {"code": data["code"]},
        )
        assert result.first() is not None

    # Очистка
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_invites"))


# Два вызова POST invite — коды разные (secrets.token_urlsafe)
@pytest.mark.asyncio
async def test_post_invite_codes_are_unique(app_client) -> None:
    # Первый invite
    resp1 = await app_client.post("/api/settings/telegram/recipients/invite")
    assert resp1.status_code == 200
    # Второй invite
    resp2 = await app_client.post("/api/settings/telegram/recipients/invite")
    assert resp2.status_code == 200
    # Коды должны отличаться
    assert resp1.json()["code"] != resp2.json()["code"]


# Owner-ссылка содержит реальный код и повторно использует его до consume/expiry.
@pytest.mark.asyncio
async def test_owner_invite_is_idempotent_and_visible_in_settings(app_client, pg_engine) -> None:
    with patch(
        "apps.api.routers.v1.settings_telegram.compute_bot_username",
        new=AsyncMock(return_value="test_bot"),
    ):
        first = await app_client.post("/api/settings/telegram/owner-invite")
        second = await app_client.post("/api/settings/telegram/owner-invite")
        settings = await app_client.get("/api/settings/telegram")

    assert first.status_code == 200
    assert second.status_code == 200
    assert settings.status_code == 200
    invite = first.json()
    assert invite["role"] == "owner"
    assert invite["code"] == second.json()["code"]
    assert invite["activation_command"] == f"/start {invite['code']}"
    assert invite["auth_deep_link"] == f"https://t.me/test_bot?start={invite['code']}"
    assert settings.json()["activation_command"] == invite["activation_command"]
    assert settings.json()["auth_deep_link"] == invite["auth_deep_link"]
    assert settings.json()["auth_invite_expires_at"] == invite["expires_at"]

    async with pg_engine.connect() as conn:
        count = await conn.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM telegram_invites
                WHERE role = 'owner' AND used_at IS NULL AND revoked_at IS NULL
                """
            )
        )
    assert count == 1


@pytest.mark.asyncio
async def test_owner_invite_is_rejected_after_owner_activation(app_client, pg_engine) -> None:
    owner_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (id, chat_id, telegram_user_id, role)
                VALUES (:id, 99102, 99202, 'owner')
                """
            ),
            {"id": owner_id},
        )
    try:
        response = await app_client.post("/api/settings/telegram/owner-invite")

        assert response.status_code == 409
        assert "уже подключ" in response.json()["message"].lower()
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE id = :id"),
                {"id": owner_id},
            )
