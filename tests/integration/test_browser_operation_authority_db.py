from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.meta_api.client import (
    BROWSER_CONTRACT_VERSION,
    MetaApiClient,
    graph_operation_binding,
)
from core.meta_api.errors import SessionUnavailableError
from core.meta_api.operation_authority import (
    BrowserCapabilityConsume,
    BrowserCapabilityConsumeDeniedError,
    consume_pending_browser_capability,
)
from core.tasks.browser_fence import (
    BrowserMaintenanceCapabilityConsume,
    BrowserMaintenanceCapabilityConsumeDeniedError,
    consume_browser_maintenance_capability,
)
from core.tasks.queue import (
    claim_browser_ready_task,
    create_task,
    request_task_cancel,
)

_KEY_PREFIX = "browser-operation-authority-db-"
pytestmark = pytest.mark.usefixtures("fresh_browser_readiness")
_STATUS_OPERATION = graph_operation_binding(
    method="POST",
    endpoint="/987654321",
    query_params={"status": "PAUSED"},
    body_json="",
)
_STATUS_GRAPH_SEMANTICS = {
    "graph_method": "POST",
    "graph_endpoint": "/987654321",
    "graph_query_params": {"status": "PAUSED"},
    "graph_body_json": "",
}


@pytest_asyncio.fixture(autouse=True)
async def clean_operation_authority_tasks(pg_engine):
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM task_queue WHERE idempotency_key LIKE :prefix"),
            {"prefix": f"{_KEY_PREFIX}%"},
        )
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM task_queue WHERE idempotency_key LIKE :prefix"),
            {"prefix": f"{_KEY_PREFIX}%"},
        )


@pytest.mark.asyncio
async def test_capability_signing_rechecks_live_database_fence_and_cancel(
    pg_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "BROWSER_OPERATION_CAPABILITY_SECRET",
        "operation-authority-integration-" + ("s" * 48),
    )
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"{_KEY_PREFIX}{uuid.uuid4().hex}",
        payload={
            "mutation_kind": "pause_ad",
            "target_id": "987654321",
            "ad_account_id": "123",
            "params": {},
        },
        requested_by="bot_auto_stop",
        lane="money",
    )
    claim = await claim_browser_ready_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
        lease_seconds=60,
    )
    assert claim.task is not None
    assert claim.task.id == task_id
    assert claim.task.lease_owner is not None

    client = MetaApiClient(
        session_id="session-exact",
        operation_engine=pg_engine,
    )
    client._stub = SimpleNamespace(
        CheckMetaApiHealth=AsyncMock(
            return_value=SimpleNamespace(
                healthy=True,
                browser_contract_version=BROWSER_CONTRACT_VERSION,
                session_id="session-exact",
                vision_profile_id="profile-exact",
            )
        )
    )
    with client.operation_authority(
        caller="autopause",
        task_id=task_id,
        lease_owner=claim.task.lease_owner,
        lease_token=claim.task.lease_token,
        vision_profile_id="profile-exact",
    ):
        authorization = await client.prepare_operation_authorization(
            rpc="execute_graph_call",
            operation=_STATUS_OPERATION,
            ad_account_id="123",
            **_STATUS_GRAPH_SEMANTICS,
        )
        assert authorization["task_id"] == task_id
        assert authorization["lease_token"] == claim.task.lease_token
        consume = BrowserCapabilityConsume(
            browser_contract_version=BROWSER_CONTRACT_VERSION,
            rpc="execute_graph_call",
            operation=_STATUS_OPERATION,
            session_id=authorization["session_id"],
            vision_profile_id=authorization["vision_profile_id"],
            ad_account_id="123",
            caller=authorization["authorized_caller"],
            task_id=authorization["task_id"],
            lease_owner=uuid.UUID(authorization["lease_owner"]),
            lease_token=authorization["lease_token"],
            expires_at_epoch=authorization["capability_expires_at"],
            nonce=authorization["capability_nonce"],
        )
        tampered = BrowserCapabilityConsume(
            browser_contract_version=consume.browser_contract_version,
            rpc=consume.rpc,
            operation=graph_operation_binding(
                method="POST",
                endpoint="/987654321",
                query_params={"status": "ACTIVE"},
                body_json="",
            ),
            session_id=consume.session_id,
            vision_profile_id=consume.vision_profile_id,
            ad_account_id=consume.ad_account_id,
            caller=consume.caller,
            task_id=consume.task_id,
            lease_owner=consume.lease_owner,
            lease_token=consume.lease_token,
            expires_at_epoch=consume.expires_at_epoch,
            nonce=consume.nonce,
        )
        with pytest.raises(BrowserCapabilityConsumeDeniedError):
            await consume_pending_browser_capability(pg_engine, tampered)
        await consume_pending_browser_capability(pg_engine, consume)
        async with pg_engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT consumed_at, browser_contract_version "
                    "FROM browser_operation_capability_uses "
                    "WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            )
            persisted = result.mappings().one()
        assert persisted["consumed_at"] is not None
        assert persisted["browser_contract_version"] == BROWSER_CONTRACT_VERSION

        # Simulate a fresh browser process: no process-local replay state is
        # consulted, and the durable PostgreSQL CAS still denies the second send.
        with pytest.raises(
            BrowserCapabilityConsumeDeniedError,
            match="already consumed",
        ):
            await consume_pending_browser_capability(pg_engine, consume)

        async with pg_engine.begin() as conn:
            await conn.execute(
                text("UPDATE task_queue SET deadline_at = NULL WHERE id = :task_id"),
                {"task_id": task_id},
            )
        with pytest.raises(SessionUnavailableError, match="stale, cancelled, expired"):
            await client.prepare_operation_authorization(
                rpc="execute_graph_call",
                operation=_STATUS_OPERATION,
                ad_account_id="123",
                **_STATUS_GRAPH_SEMANTICS,
            )
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE task_queue "
                    "SET deadline_at = clock_timestamp() + interval '30 seconds' "
                    "WHERE id = :task_id"
                ),
                {"task_id": task_id},
            )

        pending_before_cancel = await client.prepare_operation_authorization(
            rpc="execute_graph_call",
            operation=_STATUS_OPERATION,
            ad_account_id="123",
            **_STATUS_GRAPH_SEMANTICS,
        )
        pending_consume = BrowserCapabilityConsume(
            browser_contract_version=BROWSER_CONTRACT_VERSION,
            rpc="execute_graph_call",
            operation=_STATUS_OPERATION,
            session_id=pending_before_cancel["session_id"],
            vision_profile_id=pending_before_cancel["vision_profile_id"],
            ad_account_id="123",
            caller=pending_before_cancel["authorized_caller"],
            task_id=pending_before_cancel["task_id"],
            lease_owner=uuid.UUID(pending_before_cancel["lease_owner"]),
            lease_token=pending_before_cancel["lease_token"],
            expires_at_epoch=pending_before_cancel["capability_expires_at"],
            nonce=pending_before_cancel["capability_nonce"],
        )
        assert await request_task_cancel(
            pg_engine,
            task_id=task_id,
            reason="integration cancellation",
        )
        with pytest.raises(BrowserCapabilityConsumeDeniedError):
            await consume_pending_browser_capability(pg_engine, pending_consume)
        with pytest.raises(SessionUnavailableError, match="stale, cancelled, expired"):
            await client.prepare_operation_authorization(
                rpc="execute_graph_call",
                operation=_STATUS_OPERATION,
                ad_account_id="123",
                **_STATUS_GRAPH_SEMANTICS,
            )


@pytest.mark.asyncio
async def test_maintenance_capability_consume_is_durable_across_processes(
    pg_engine,
) -> None:
    owner = uuid.uuid4().hex
    nonce = uuid.uuid4().hex
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM system_config WHERE key = 'browser_maintenance'"))
            await conn.execute(
                text(
                    """
                    INSERT INTO system_config (key, value, description)
                    VALUES (
                      'browser_maintenance',
                      jsonb_build_object(
                        'owner', CAST(:owner AS text),
                        'expires_at', clock_timestamp() + interval '45 seconds'
                      ),
                      'Disposable integration maintenance lease'
                    )
                    """
                ),
                {"owner": owner},
            )
            expires_at_epoch = int(
                await conn.scalar(text("SELECT extract(epoch FROM clock_timestamp())::bigint + 30"))
            )

        capability = BrowserMaintenanceCapabilityConsume(
            profile_id="profile-integration",
            owner=owner,
            expires_at_epoch=expires_at_epoch,
            nonce=nonce,
        )
        await consume_browser_maintenance_capability(pg_engine, capability)

        async with pg_engine.connect() as conn:
            consumed = (
                await conn.execute(
                    text(
                        """
                        SELECT
                          value->>'consumed_capability_nonce',
                          value->>'consumed_capability_profile_id'
                        FROM system_config
                        WHERE key = 'browser_maintenance'
                        """
                    )
                )
            ).one()
        assert consumed == (nonce, "profile-integration")

        # A restarted browser-agent has no shared process memory, so this
        # second value object proves replay protection lives in PostgreSQL.
        replay_after_restart = BrowserMaintenanceCapabilityConsume(
            profile_id="profile-integration",
            owner=owner,
            expires_at_epoch=expires_at_epoch,
            nonce=nonce,
        )
        with pytest.raises(
            BrowserMaintenanceCapabilityConsumeDeniedError,
            match="already consumed",
        ):
            await consume_browser_maintenance_capability(
                pg_engine,
                replay_after_restart,
            )
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    DELETE FROM system_config
                    WHERE key = 'browser_maintenance'
                      AND value->>'owner' = :owner
                    """
                ),
                {"owner": owner},
            )
