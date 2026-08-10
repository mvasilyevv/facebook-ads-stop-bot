"""PostgreSQL contracts for durable Telegram webhook configuration."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import text

import core.telegram.webhook_configuration as webhook_configuration
from core.crypto import encrypt
from core.telegram.gateway import (
    TelegramFailureKind,
    TelegramGatewayError,
    telegram_credential_fingerprint,
)
from core.telegram.webhook_configuration import (
    TelegramWebhookRemoteSnapshot,
    bind_webhook_generation,
    claim_webhook_configuration,
    disable_token_and_schedule_webhook_deletion,
    mark_webhook_configuration_failure,
    mark_webhook_configuration_success,
    process_one_webhook_configuration,
    resolve_webhook_target,
    store_rotated_token_and_schedule_webhook,
)

_ORIGIN = "https://operator.example.test"
_SECRET = "durable-webhook-secret"


@pytest_asyncio.fixture(autouse=True)
async def _clean_telegram_config(pg_engine):
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM notification_deliveries"))
        await conn.execute(text("DELETE FROM notification_events"))
        await conn.execute(text("DELETE FROM incidents WHERE incident_key = 'telegram:bot-auth'"))
        await conn.execute(text("DELETE FROM telegram_config"))
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM notification_deliveries"))
        await conn.execute(text("DELETE FROM notification_events"))
        await conn.execute(text("DELETE FROM incidents WHERE incident_key = 'telegram:bot-auth'"))
        await conn.execute(text("DELETE FROM telegram_config"))


class _Gateway:
    mode = "success"
    remote_url = ""
    tokens: list[str] = []
    set_calls: list[dict[str, object]] = []
    delete_calls = 0
    close_calls = 0
    get_info_calls = 0
    set_entered: asyncio.Event | None = None
    set_release: asyncio.Event | None = None

    def __init__(self, token: str) -> None:
        self.tokens.append(token)
        self.credential_fingerprint = telegram_credential_fingerprint(token)

    async def set_webhook(self, **kwargs: object) -> None:
        self.set_calls.append(kwargs)
        if self.set_entered is not None:
            self.set_entered.set()
        if self.set_release is not None:
            await self.set_release.wait()
        if self.mode == "rate_limited":
            raise TelegramGatewayError(
                method="setWebhook",
                kind=TelegramFailureKind.RATE_LIMITED,
                error_code=429,
                retry_after=137,
                description="flood control",
            )
        if self.mode == "unauthorized":
            raise TelegramGatewayError(
                method="setWebhook",
                kind=TelegramFailureKind.UNAUTHORIZED,
                error_code=401,
                description="Unauthorized",
            )

    async def delete_webhook(self, **_kwargs: object) -> None:
        type(self).delete_calls += 1

    async def get_webhook_info(self) -> dict[str, object]:
        type(self).get_info_calls += 1
        return {
            "url": self.remote_url,
            "pending_update_count": 0,
            "max_connections": 40,
            "allowed_updates": ["message", "callback_query"],
        }

    async def close(self) -> None:
        type(self).close_calls += 1

    @classmethod
    def reset(cls) -> None:
        cls.mode = "success"
        cls.remote_url = ""
        cls.tokens = []
        cls.set_calls = []
        cls.delete_calls = 0
        cls.close_calls = 0
        cls.get_info_calls = 0
        cls.set_entered = None
        cls.set_release = None


def _patch_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.telegram.webhook_configuration as module

    _Gateway.reset()
    monkeypatch.setattr(module, "TelegramHTMLGateway", _Gateway)
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            frontend_origin=_ORIGIN,
            telegram_webhook_secret=SecretStr(_SECRET),
        ),
    )


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


@pytest.mark.asyncio
async def test_rate_limit_is_durable_and_full_retry_after_then_remote_confirmation(
    pg_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(monkeypatch)
    target = resolve_webhook_target(
        frontend_origin=_ORIGIN,
        secret_token=SecretStr(_SECRET),
    )
    async with pg_engine.begin() as conn:
        await store_rotated_token_and_schedule_webhook(
            conn,
            bot_token_encrypted=encrypt("bot-token-v1"),
            bot_token_fingerprint=telegram_credential_fingerprint("bot-token-v1"),
            target=target,
        )

    _Gateway.mode = "rate_limited"
    assert await process_one_webhook_configuration(
        pg_engine,
        worker_id="webhook-test-rate-limit",
    )

    async with pg_engine.connect() as conn:
        retry = (
            await conn.execute(
                text(
                    """
                    SELECT webhook_state, webhook_attempt_count,
                           webhook_last_error_code,
                           EXTRACT(EPOCH FROM
                               (webhook_scheduled_at - updated_at)) AS delay
                    FROM telegram_config
                    WHERE singleton_key = 'default'
                    """
                )
            )
        ).one()
    assert retry.webhook_state == "retry"
    assert retry.webhook_attempt_count == 1
    assert retry.webhook_last_error_code == "telegram_rate_limited"
    assert float(retry.delay) == pytest.approx(137.0, abs=0.001)

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE telegram_config
                SET webhook_scheduled_at = NOW()
                WHERE singleton_key = 'default'
                """
            )
        )
    _Gateway.mode = "success"
    generation_url = bind_webhook_generation(target.url, 1)
    _Gateway.remote_url = generation_url
    assert await process_one_webhook_configuration(
        pg_engine,
        worker_id="webhook-test-retry",
    )

    async with pg_engine.connect() as conn:
        configured = (
            await conn.execute(
                text(
                    """
                    SELECT webhook_state, webhook_generation,
                           webhook_applied_generation, webhook_remote_url,
                           webhook_remote_pending_update_count,
                           webhook_checked_at, webhook_configured_at,
                           webhook_last_error_code
                    FROM telegram_config
                    WHERE singleton_key = 'default'
                    """
                )
            )
        ).one()
    assert configured.webhook_state == "configured"
    assert configured.webhook_applied_generation == configured.webhook_generation
    assert configured.webhook_remote_url == generation_url
    assert configured.webhook_remote_pending_update_count == 0
    assert configured.webhook_checked_at is not None
    assert configured.webhook_configured_at is not None
    assert configured.webhook_last_error_code is None
    assert _Gateway.tokens == ["bot-token-v1", "bot-token-v1"]
    assert _Gateway.set_calls[-1] == {
        "url": generation_url,
        "secret_token": _SECRET,
        "drop_pending_updates": False,
    }


@pytest.mark.asyncio
async def test_current_webhook_401_atomically_opens_auth_incident(
    pg_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(monkeypatch)
    token = "webhook-current-401-token"
    target = resolve_webhook_target(
        frontend_origin=_ORIGIN,
        secret_token=SecretStr(_SECRET),
    )
    async with pg_engine.begin() as conn:
        await store_rotated_token_and_schedule_webhook(
            conn,
            bot_token_encrypted=encrypt(token),
            bot_token_fingerprint=telegram_credential_fingerprint(token),
            target=target,
        )

    _Gateway.mode = "unauthorized"
    assert await process_one_webhook_configuration(
        pg_engine,
        worker_id="webhook-current-401",
    )

    async with pg_engine.connect() as conn:
        config = (
            await conn.execute(
                text(
                    """
                    SELECT webhook_state, webhook_last_error_code
                    FROM telegram_config
                    WHERE singleton_key = 'default'
                    """
                )
            )
        ).one()
        incident_count = await conn.scalar(
            text(
                """
                SELECT COUNT(*) FROM incidents
                WHERE incident_key = 'telegram:bot-auth'
                  AND status = 'open'
                """
            )
        )
        event_count = await conn.scalar(
            text(
                """
                SELECT COUNT(*) FROM notification_events e
                JOIN incidents i ON i.id = e.incident_id
                WHERE i.incident_key = 'telegram:bot-auth'
                """
            )
        )
    assert (config.webhook_state, config.webhook_last_error_code) == (
        "failed",
        "telegram_unauthorized",
    )
    assert incident_count == 1
    assert event_count == 1


@pytest.mark.asyncio
async def test_stale_webhook_401_cas_rolls_back_provisional_auth_incident(pg_engine) -> None:
    old_token = "webhook-stale-401-token"
    replacement_token = "webhook-replacement-token"
    target = resolve_webhook_target(
        frontend_origin=_ORIGIN,
        secret_token=SecretStr(_SECRET),
    )
    async with pg_engine.begin() as conn:
        await store_rotated_token_and_schedule_webhook(
            conn,
            bot_token_encrypted=encrypt(old_token),
            bot_token_fingerprint=telegram_credential_fingerprint(old_token),
            target=target,
        )
    claim = await claim_webhook_configuration(pg_engine, worker_id="stale-401-claim")
    assert claim is not None
    async with pg_engine.begin() as conn:
        await store_rotated_token_and_schedule_webhook(
            conn,
            bot_token_encrypted=encrypt(replacement_token),
            bot_token_fingerprint=telegram_credential_fingerprint(replacement_token),
            target=target,
        )

    finalized = await mark_webhook_configuration_failure(
        pg_engine,
        claim=claim,
        error_code="telegram_unauthorized",
        error_detail="Telegram rejected webhook credentials",
        retry_after=None,
        retryable=False,
        authentication_failure=True,
        credential_fingerprint=telegram_credential_fingerprint(old_token),
    )

    assert finalized is False
    async with pg_engine.connect() as conn:
        generation = await conn.scalar(
            text("SELECT webhook_generation FROM telegram_config WHERE singleton_key='default'")
        )
        incident_count = await conn.scalar(
            text("SELECT COUNT(*) FROM incidents WHERE incident_key='telegram:bot-auth'")
        )
        event_count = await conn.scalar(
            text(
                """
                SELECT COUNT(*) FROM notification_events e
                JOIN incidents i ON i.id = e.incident_id
                WHERE i.incident_key='telegram:bot-auth'
                """
            )
        )
    assert generation == claim.generation + 1
    assert incident_count == 0
    assert event_count == 0


@pytest.mark.asyncio
async def test_rotation_fences_stale_claim_and_delete_clears_token_only_after_remote_proof(
    pg_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(monkeypatch)
    target = resolve_webhook_target(
        frontend_origin=_ORIGIN,
        secret_token=SecretStr(_SECRET),
    )
    async with pg_engine.begin() as conn:
        await store_rotated_token_and_schedule_webhook(
            conn,
            bot_token_encrypted=encrypt("bot-token-v1"),
            bot_token_fingerprint=telegram_credential_fingerprint("bot-token-v1"),
            target=target,
        )
    stale = await claim_webhook_configuration(
        pg_engine,
        worker_id="stale-webhook-worker",
    )
    assert stale is not None and stale.generation == 1

    async with pg_engine.begin() as conn:
        await store_rotated_token_and_schedule_webhook(
            conn,
            bot_token_encrypted=encrypt("bot-token-v2"),
            bot_token_fingerprint=telegram_credential_fingerprint("bot-token-v2"),
            target=target,
        )
    assert not await mark_webhook_configuration_success(
        pg_engine,
        claim=stale,
        remote=TelegramWebhookRemoteSnapshot(
            url=bind_webhook_generation(target.url, 1),
            pending_update_count=0,
            last_error_at=None,
            last_error_message=None,
            max_connections=40,
            allowed_updates=("message", "callback_query"),
        ),
    )

    _Gateway.remote_url = bind_webhook_generation(target.url, 2)
    assert await process_one_webhook_configuration(
        pg_engine,
        worker_id="current-webhook-worker",
    )
    async with pg_engine.begin() as conn:
        await disable_token_and_schedule_webhook_deletion(conn)
        pending_delete = (
            await conn.execute(
                text(
                    """
                    SELECT is_enabled, bot_token_encrypted, webhook_state,
                           webhook_operation, webhook_generation
                    FROM telegram_config
                    WHERE singleton_key = 'default'
                    """
                )
            )
        ).one()
    assert pending_delete.is_enabled is False
    assert pending_delete.bot_token_encrypted != ""
    assert pending_delete.webhook_state == "pending"
    assert pending_delete.webhook_operation == "delete"
    assert pending_delete.webhook_generation == 3

    _Gateway.remote_url = ""
    assert await process_one_webhook_configuration(
        pg_engine,
        worker_id="delete-webhook-worker",
    )
    async with pg_engine.connect() as conn:
        deleted = (
            await conn.execute(
                text(
                    """
                    SELECT is_enabled, bot_token_encrypted, webhook_state,
                           webhook_applied_generation, webhook_generation,
                           webhook_remote_url
                    FROM telegram_config
                    WHERE singleton_key = 'default'
                    """
                )
            )
        ).one()
    assert deleted.is_enabled is False
    assert deleted.bot_token_encrypted == ""
    assert deleted.webhook_state == "unconfigured"
    assert deleted.webhook_applied_generation == deleted.webhook_generation == 3
    assert deleted.webhook_remote_url == ""
    assert _Gateway.tokens == ["bot-token-v2", "bot-token-v2"]
    assert _Gateway.delete_calls == 1


@pytest.mark.asyncio
async def test_webhook_rotation_before_external_boundary_makes_zero_bot_api_calls(
    pg_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(monkeypatch)
    target = resolve_webhook_target(
        frontend_origin=_ORIGIN,
        secret_token=SecretStr(_SECRET),
    )
    async with pg_engine.begin() as conn:
        await store_rotated_token_and_schedule_webhook(
            conn,
            bot_token_encrypted=encrypt("bot-token-v1"),
            bot_token_fingerprint=telegram_credential_fingerprint("bot-token-v1"),
            target=target,
        )
    stale = await claim_webhook_configuration(
        pg_engine,
        worker_id="stale-before-external-boundary",
    )
    assert stale is not None
    async with pg_engine.begin() as conn:
        await store_rotated_token_and_schedule_webhook(
            conn,
            bot_token_encrypted=encrypt("bot-token-v2"),
            bot_token_fingerprint=telegram_credential_fingerprint("bot-token-v2"),
            target=target,
        )

    monkeypatch.setattr(
        webhook_configuration,
        "claim_webhook_configuration",
        AsyncMock(return_value=stale),
    )
    assert await process_one_webhook_configuration(
        pg_engine,
        worker_id="stale-operation-runner",
    )
    assert _Gateway.set_calls == []
    assert _Gateway.delete_calls == 0
    assert _Gateway.get_info_calls == 0


@pytest.mark.asyncio
async def test_webhook_external_operation_wins_and_blocks_rotation(
    pg_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(monkeypatch)
    target = resolve_webhook_target(
        frontend_origin=_ORIGIN,
        secret_token=SecretStr(_SECRET),
    )
    async with pg_engine.begin() as conn:
        await store_rotated_token_and_schedule_webhook(
            conn,
            bot_token_encrypted=encrypt("bot-token-v1"),
            bot_token_fingerprint=telegram_credential_fingerprint("bot-token-v1"),
            target=target,
        )

    _Gateway.remote_url = bind_webhook_generation(target.url, 1)
    _Gateway.set_entered = asyncio.Event()
    _Gateway.set_release = asyncio.Event()

    async def rotate() -> None:
        async with pg_engine.begin() as conn:
            await store_rotated_token_and_schedule_webhook(
                conn,
                bot_token_encrypted=encrypt("bot-token-v2"),
                bot_token_fingerprint=telegram_credential_fingerprint("bot-token-v2"),
                target=target,
            )

    operation_task = asyncio.create_task(
        process_one_webhook_configuration(
            pg_engine,
            worker_id="current-operation-runner",
        )
    )
    rotation_task = None
    try:
        assert _Gateway.set_entered is not None
        await asyncio.wait_for(_Gateway.set_entered.wait(), timeout=3.0)
        rotation_task = asyncio.create_task(rotate())
        await _wait_for_blocked_backend(
            pg_engine,
            query_fragment="SELECT webhook_generation",
        )
        assert _Gateway.set_release is not None
        _Gateway.set_release.set()
        assert await operation_task
        await rotation_task
    finally:
        if _Gateway.set_release is not None:
            _Gateway.set_release.set()
        for task in (operation_task, rotation_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (operation_task, rotation_task) if task is not None),
            return_exceptions=True,
        )

    assert len(_Gateway.set_calls) == 1
    assert _Gateway.get_info_calls == 1
    async with pg_engine.connect() as conn:
        generation, state = (
            await conn.execute(
                text(
                    "SELECT webhook_generation, webhook_state "
                    "FROM telegram_config WHERE singleton_key = 'default'"
                )
            )
        ).one()
    assert generation == 2
    assert state == "pending"
