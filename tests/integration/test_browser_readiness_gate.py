from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.browser.circuit_breaker import AsyncCircuitBreaker
from core.meta_api.browser_readiness import (
    BrowserReadinessObservation,
    VisionReadinessIdentity,
    load_vision_readiness_identity,
    persist_browser_readiness,
    probe_and_publish_browser_readiness,
)
from core.meta_api.client import MetaApiClient
from core.meta_api.errors import BrowserReadinessRejectedError
from core.tasks.browser_fence import BrowserExclusiveMaintenance
from core.tasks.queue import (
    claim_browser_ready_task,
    claim_next_task,
    create_task,
    release_after_browser_readiness_rejection,
)

_KEY_PREFIX = "browser-readiness-gate-"


@pytest_asyncio.fixture(autouse=True)
async def clean_browser_readiness_gate(pg_engine):
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM task_queue WHERE idempotency_key LIKE :prefix"),
            {"prefix": f"{_KEY_PREFIX}%"},
        )
        await conn.execute(text("DELETE FROM browser_channel_readiness"))
        await conn.execute(text("DELETE FROM vision_config"))
        await conn.execute(text("DELETE FROM system_config WHERE key = 'browser_maintenance'"))
        await conn.execute(text("DELETE FROM browser_operation_leases"))
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM task_queue WHERE idempotency_key LIKE :prefix"),
            {"prefix": f"{_KEY_PREFIX}%"},
        )
        await conn.execute(text("DELETE FROM browser_channel_readiness"))
        await conn.execute(text("DELETE FROM vision_config"))
        await conn.execute(text("DELETE FROM system_config WHERE key = 'browser_maintenance'"))
        await conn.execute(text("DELETE FROM browser_operation_leases"))


async def _seed_config(pg_engine, *, profile_id: str = "profile-ready"):
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO vision_config (
                  x_token_encrypted,
                  profile_id,
                  singleton_key
                )
                VALUES ('synthetic-not-read', :profile_id, 'default')
                """
            ),
            {"profile_id": profile_id},
        )
    identity = await load_vision_readiness_identity(pg_engine)
    assert identity is not None
    return identity


async def _seed_task(pg_engine) -> int:
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
        requested_by="test",
        lane="money",
    )
    assert task_id is not None
    return task_id


def _ready(identity: VisionReadinessIdentity) -> BrowserReadinessObservation:
    return BrowserReadinessObservation(
        state="ready",
        reason_code="ready",
        observed_contract_version=5,
        observed_profile_id=identity.profile_id,
        observed_session_id="session-ready",
    )


@pytest.mark.asyncio
async def test_claim_requires_fresh_exact_readiness_without_burning_attempt(
    pg_engine,
) -> None:
    identity = await _seed_config(pg_engine)
    task_id = await _seed_task(pg_engine)

    # Even a legacy/generic caller is delegated to the gated SQL and cannot
    # claim without durable evidence.
    missing = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
        lease_seconds=60,
    )
    assert missing.task is None

    await persist_browser_readiness(
        pg_engine,
        identity=identity,
        observation=BrowserReadinessObservation(
            state="incompatible",
            reason_code="browser_contract_incompatible",
            observed_contract_version=4,
            observed_profile_id=identity.profile_id,
            observed_session_id="session-v4",
        ),
        writer_instance=uuid.uuid4(),
    )
    incompatible = await claim_browser_ready_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
        lease_seconds=60,
    )
    assert incompatible.task is None

    async with pg_engine.connect() as conn:
        state = (
            await conn.execute(
                text(
                    """
                    SELECT status, attempt_count, lease_owner
                    FROM task_queue
                    WHERE id = :task_id
                    """
                ),
                {"task_id": task_id},
            )
        ).one()
    assert state.status == "pending"
    assert state.attempt_count == 0
    assert state.lease_owner is None


@pytest.mark.asyncio
async def test_fresh_v5_claim_is_atomic_and_returns_bound_profile(pg_engine) -> None:
    identity = await _seed_config(pg_engine)
    task_id = await _seed_task(pg_engine)
    assert await persist_browser_readiness(
        pg_engine,
        identity=identity,
        observation=_ready(identity),
        writer_instance=uuid.uuid4(),
        ttl_seconds=6,
    )

    claims = await asyncio.gather(
        *(
            claim_browser_ready_task(
                pg_engine,
                task_type="meta_api_mutation",
                lanes=("money",),
                worker_id=uuid.uuid4(),
                lease_seconds=60,
            )
            for _ in range(5)
        )
    )
    won = [claim for claim in claims if claim.task is not None]
    assert len(won) == 1
    assert won[0].task is not None
    assert won[0].task.id == task_id
    assert won[0].browser_profile_id == identity.profile_id
    assert won[0].browser_readiness_generation == 1
    async with pg_engine.connect() as conn:
        lease_is_fresh = await conn.scalar(
            text(
                """
                SELECT lease_expires_at > clock_timestamp()
                FROM task_queue
                WHERE id = :task_id
                """
            ),
            {"task_id": task_id},
        )
    assert lease_is_fresh is True


@pytest.mark.asyncio
async def test_stale_config_and_maintenance_invalidate_claim_evidence(
    pg_engine,
) -> None:
    identity = await _seed_config(pg_engine)
    await _seed_task(pg_engine)
    assert await persist_browser_readiness(
        pg_engine,
        identity=identity,
        observation=_ready(identity),
        writer_instance=uuid.uuid4(),
        ttl_seconds=6,
    )

    async with BrowserExclusiveMaintenance(
        pg_engine,
        operation_kind="readiness_test",
        drain_seconds=1,
    ):
        assert not await persist_browser_readiness(
            pg_engine,
            identity=identity,
            observation=_ready(identity),
            writer_instance=uuid.uuid4(),
            ttl_seconds=6,
        )
        async with pg_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT state, readiness_expires_at
                        FROM browser_channel_readiness
                        WHERE channel = 'meta_api'
                        """
                    )
                )
            ).one()
        assert row.state == "maintenance"
        assert row.readiness_expires_at is None

    blocked = await claim_browser_ready_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
        lease_seconds=60,
    )
    assert blocked.task is None

    assert await persist_browser_readiness(
        pg_engine,
        identity=identity,
        observation=_ready(identity),
        writer_instance=uuid.uuid4(),
        ttl_seconds=6,
    )
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE vision_config
                SET updated_at = clock_timestamp() + interval '1 microsecond'
                WHERE id = :config_id
                """
            ),
            {"config_id": identity.config_id},
        )
    stale_config = await claim_browser_ready_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
        lease_seconds=60,
    )
    assert stale_config.task is None


@pytest.mark.asyncio
async def test_observation_age_and_order_are_not_refreshed_by_lock_wait(
    pg_engine,
) -> None:
    identity = await _seed_config(pg_engine)
    async with pg_engine.connect() as conn:
        observed_now = await conn.scalar(text("SELECT clock_timestamp()"))
    assert observed_now is not None

    assert not await persist_browser_readiness(
        pg_engine,
        identity=identity,
        observation=BrowserReadinessObservation(
            state="unavailable",
            reason_code="newer_unavailable",
            observed_contract_version=5,
            observed_profile_id=identity.profile_id,
            observed_session_id="newer-session",
            observed_at=observed_now,
        ),
        writer_instance=uuid.uuid4(),
        ttl_seconds=6,
    )
    assert not await persist_browser_readiness(
        pg_engine,
        identity=identity,
        observation=BrowserReadinessObservation(
            state="ready",
            reason_code="ready",
            observed_contract_version=5,
            observed_profile_id=identity.profile_id,
            observed_session_id="older-session",
            observed_at=observed_now - timedelta(seconds=1),
        ),
        writer_instance=uuid.uuid4(),
        ttl_seconds=6,
    )
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT state, observed_session_id
                    FROM browser_channel_readiness
                    WHERE channel = 'meta_api'
                    """
                )
            )
        ).one()
    assert (row.state, row.observed_session_id) == (
        "unavailable",
        "newer-session",
    )

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM browser_channel_readiness"))
    assert not await persist_browser_readiness(
        pg_engine,
        identity=identity,
        observation=BrowserReadinessObservation(
            state="ready",
            reason_code="ready",
            observed_contract_version=5,
            observed_profile_id=identity.profile_id,
            observed_session_id="expired-session",
            observed_at=observed_now - timedelta(seconds=10),
        ),
        writer_instance=uuid.uuid4(),
        ttl_seconds=6,
    )
    async with pg_engine.connect() as conn:
        assert await conn.scalar(text("SELECT COUNT(*) FROM browser_channel_readiness")) == 0

    async with pg_engine.connect() as conn:
        delayed_observed_at = await conn.scalar(text("SELECT clock_timestamp()"))
    assert delayed_observed_at is not None
    lock_conn = await pg_engine.connect()
    lock_tx = await lock_conn.begin()
    delayed_publish: asyncio.Task[bool] | None = None
    try:
        await lock_conn.execute(
            text(
                """
                SELECT pg_advisory_xact_lock(
                  hashtext('fb-agent'),
                  hashtext('browser-maintenance')
                )
                """
            )
        )
        delayed_publish = asyncio.create_task(
            persist_browser_readiness(
                pg_engine,
                identity=identity,
                observation=BrowserReadinessObservation(
                    state="ready",
                    reason_code="ready",
                    observed_contract_version=5,
                    observed_profile_id=identity.profile_id,
                    observed_session_id="lock-delayed-session",
                    observed_at=delayed_observed_at,
                ),
                writer_instance=uuid.uuid4(),
                ttl_seconds=2,
            )
        )
        await asyncio.sleep(2.1)
    finally:
        await lock_tx.rollback()
        await lock_conn.close()
    assert delayed_publish is not None
    assert not await delayed_publish
    async with pg_engine.connect() as conn:
        assert await conn.scalar(text("SELECT COUNT(*) FROM browser_channel_readiness")) == 0


@pytest.mark.asyncio
async def test_exact_live_rejection_cas_closes_gate_without_attempt_burn(
    pg_engine,
    monkeypatch,
) -> None:
    identity = await _seed_config(pg_engine)
    task_id = await _seed_task(pg_engine)
    assert await persist_browser_readiness(
        pg_engine,
        identity=identity,
        observation=_ready(identity),
        writer_instance=uuid.uuid4(),
        ttl_seconds=6,
    )
    claim = await claim_browser_ready_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
        lease_seconds=60,
    )
    assert claim.task is not None
    assert claim.browser_readiness_generation is not None
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET external_started_at = clock_timestamp()
                WHERE id = :task_id
                """
            ),
            {"task_id": task_id},
        )

    class Stub:
        async def CheckMetaApiHealth(self, _request, **kwargs):
            assert kwargs["timeout"] > 0
            return SimpleNamespace(
                browser_contract_version=4,
                session_id="stale-session",
                vision_profile_id=identity.profile_id,
                healthy=True,
            )

    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", "s" * 64)
    client = MetaApiClient(operation_engine=pg_engine)
    client._stub = Stub()  # noqa: SLF001
    with client.operation_authority(
        caller="autopause",
        task_id=task_id,
        lease_owner=claim.task.lease_owner,
        lease_token=claim.task.lease_token,
        vision_profile_id=identity.profile_id,
        browser_readiness_generation=claim.browser_readiness_generation,
    ):
        with pytest.raises(
            BrowserReadinessRejectedError,
            match="contract is incompatible",
        ):
            await client.prepare_operation_authorization(
                rpc="upload_image",
                operation="upload_image",
                ad_account_id="123",
            )
    assert (
        await release_after_browser_readiness_rejection(
            pg_engine,
            task=claim.task,
            error="exact live identity rejected",
            target_lock_key="987654321",
        )
        == "retrying"
    )

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT task.status,
                           task.attempt_count,
                           task.external_started_at,
                           readiness.state,
                           readiness.readiness_expires_at,
                           readiness.generation
                    FROM task_queue AS task
                    JOIN browser_channel_readiness AS readiness
                      ON readiness.channel = 'meta_api'
                    WHERE task.id = :task_id
                    """
                ),
                {"task_id": task_id},
            )
        ).one()
    assert row.status == "retrying"
    assert row.attempt_count == 0
    assert row.external_started_at is None
    assert row.state == "unavailable"
    assert row.readiness_expires_at is None
    assert row.generation == claim.browser_readiness_generation + 1
    blocked = await claim_browser_ready_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
        lease_seconds=60,
    )
    assert blocked.task is None


@pytest.mark.asyncio
async def test_presend_circuit_open_cas_closes_gate_and_requeues_without_burn(
    pg_engine,
    monkeypatch,
) -> None:
    identity = await _seed_config(pg_engine)
    task_id = await _seed_task(pg_engine)
    assert await persist_browser_readiness(
        pg_engine,
        identity=identity,
        observation=_ready(identity),
        writer_instance=uuid.uuid4(),
        ttl_seconds=6,
    )
    claim = await claim_browser_ready_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
        lease_seconds=60,
    )
    assert claim.task is not None
    assert claim.browser_readiness_generation is not None
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET external_started_at = clock_timestamp()
                WHERE id = :task_id
                """
            ),
            {"task_id": task_id},
        )

    class Stub:
        def __init__(self) -> None:
            self.ExecuteGraphCallV5 = AsyncMock()

        async def CheckMetaApiHealth(self, _request, **kwargs):
            assert kwargs["timeout"] > 0
            return SimpleNamespace(
                browser_contract_version=5,
                session_id="session-ready",
                vision_profile_id=identity.profile_id,
                healthy=True,
            )

    breaker = AsyncCircuitBreaker(
        "meta-api-presend-test",
        failure_threshold=1,
        recovery_timeout=60,
    )
    await breaker.record_failure(RuntimeError("synthetic transport outage"))
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", "s" * 64)
    stub = Stub()
    client = MetaApiClient(
        session_id="session-ready",
        operation_engine=pg_engine,
        circuit_breaker=breaker,
    )
    client._stub = stub  # noqa: SLF001
    with client.operation_authority(
        caller="autopause",
        task_id=task_id,
        lease_owner=claim.task.lease_owner,
        lease_token=claim.task.lease_token,
        vision_profile_id=identity.profile_id,
        browser_readiness_generation=claim.browser_readiness_generation,
    ):
        with pytest.raises(
            BrowserReadinessRejectedError,
            match="before dispatch",
        ):
            await client.execute_graph_call(
                method="POST",
                endpoint="/987654321",
                query_params={"status": "PAUSED"},
                ad_account_id="123",
            )
    stub.ExecuteGraphCallV5.assert_not_awaited()

    assert (
        await release_after_browser_readiness_rejection(
            pg_engine,
            task=claim.task,
            error="local circuit open before dispatch",
            target_lock_key="987654321",
        )
        == "retrying"
    )
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT task.status,
                           task.attempt_count,
                           task.external_started_at,
                           readiness.state,
                           readiness.reason_code,
                           readiness.readiness_expires_at,
                           readiness.generation
                    FROM task_queue AS task
                    JOIN browser_channel_readiness AS readiness
                      ON readiness.channel = 'meta_api'
                    WHERE task.id = :task_id
                    """
                ),
                {"task_id": task_id},
            )
        ).one()
    assert row.status == "retrying"
    assert row.attempt_count == 0
    assert row.external_started_at is None
    assert row.state == "unavailable"
    assert row.reason_code == "presend_circuit_open"
    assert row.readiness_expires_at is None
    assert row.generation == claim.browser_readiness_generation + 1


@pytest.mark.asyncio
async def test_probe_writer_uses_token_only_exact_profile_and_db_time(
    pg_engine,
) -> None:
    identity = await _seed_config(pg_engine)

    class Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def check_health(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "healthy": True,
                "browser_contract_version": 5,
                "vision_profile_id": identity.profile_id,
                "session_id": "session-from-probe",
                "detail": "ok",
            }

    client = Client()
    assert await probe_and_publish_browser_readiness(
        pg_engine,
        client,
        writer_instance=uuid.uuid4(),
        ttl_seconds=6,
    )
    assert client.calls == [
        {
            "full_probe": False,
            "expected_profile_id": identity.profile_id,
        }
    ]
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT
                      state,
                      observed_at <= clock_timestamp() AS observed_not_future,
                      readiness_expires_at > clock_timestamp() AS still_fresh
                    FROM browser_channel_readiness
                    WHERE channel = 'meta_api'
                    """
                )
            )
        ).one()
    assert row.state == "ready"
    assert row.observed_not_future is True
    assert row.still_fresh is True
