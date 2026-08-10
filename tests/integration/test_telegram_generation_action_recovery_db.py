"""Generation fencing and receipt-proven Telegram action recovery."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

import core.telegram.handlers.alerts as alerts_module
from apps.api.routers.v1.settings_telegram import delete_telegram_recipient
from core.commands.service import CommandService
from core.incidents.service import acknowledge_incident
from core.telegram.action_tokens import claim_action_token, mint_action_token
from core.telegram.handlers.alerts import _command_idempotency_key, handle_action_callback
from core.telegram.handlers.router import _dispatch_callback_query
from core.telegram.owner_roster import lock_owner_roster
from core.telegram.schemas import TelegramWebhookUpdate
from core.telegram.update_inbox import (
    TelegramIngressUnavailableError,
    claim_telegram_update,
    persist_telegram_update,
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


async def _configure_bot(conn, *, generation: int) -> None:
    await conn.execute(text("DELETE FROM telegram_config"))
    await conn.execute(
        text(
            """
            INSERT INTO telegram_config
                (singleton_key, bot_token_encrypted, is_enabled,
                 webhook_generation, webhook_applied_generation,
                 webhook_operation, webhook_desired_url,
                 webhook_state, webhook_configured_at)
            VALUES
                ('default', 'test-ciphertext', TRUE,
                 :generation, :generation, 'configure',
                 :url, 'configured', NOW())
            """
        ),
        {
            "generation": generation,
            "url": (
                "https://operator.example.test/api/v1/integrations/telegram/webhook"
                f"?bot_generation={generation}"
            ),
        },
    )


@pytest_asyncio.fixture
async def telegram_action_context(
    pg_engine,
    fb_ad_fixture,
    known_test_cabinet_timezones,
):
    recipient_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    suffix = uuid.uuid4().int % 1_000_000_000
    chat_id = 7_000_000_000 + suffix
    user_id = 8_000_000_000 + suffix
    async with pg_engine.begin() as conn:
        await _configure_bot(conn, generation=1)
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (id, chat_id, telegram_user_id, role)
                VALUES (:id, :chat_id, :user_id, 'owner')
                """
            ),
            {"id": recipient_id, "chat_id": chat_id, "user_id": user_id},
        )
        fb_ad_id = str(
            await conn.scalar(
                text("SELECT fb_ad_id FROM fb_ads WHERE id = :id"),
                {"id": fb_ad_fixture.ad_id},
            )
        )
        await conn.execute(
            text(
                """
                UPDATE fb_ads
                SET delivery_status = 'ACTIVE'
                WHERE id = :id
                """
            ),
            {"id": fb_ad_fixture.ad_id},
        )
        await conn.execute(
            text(
                """
                INSERT INTO incidents
                    (id, incident_key, generation, resource_type, resource_id,
                     severity, status, title)
                VALUES
                    (:id, :key, 1, 'ad', :fb_ad_id,
                     'critical', 'open', 'Telegram recovery test')
                """
            ),
            {
                "id": incident_id,
                "key": f"test:telegram-recovery:{incident_id}",
                "fb_ad_id": fb_ad_id,
            },
        )
    yield {
        "recipient_id": recipient_id,
        "incident_id": incident_id,
        "chat_id": chat_id,
        "user_id": user_id,
        "fb_ad_id": fb_ad_id,
    }
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                DELETE FROM command_idempotency_receipts receipt
                USING task_queue task
                WHERE task.id = receipt.task_id
                  AND task.payload->>'target_id' = :target_id
                """
            ),
            {"target_id": fb_ad_id},
        )
        await conn.execute(
            text(
                """
                DELETE FROM task_queue
                WHERE payload->>'target_id' = :target_id
                """
            ),
            {"target_id": fb_ad_id},
        )
        await conn.execute(
            text("DELETE FROM telegram_action_tokens WHERE recipient_id = :recipient_id"),
            {"recipient_id": recipient_id},
        )
        await conn.execute(
            text(
                """
                DELETE FROM notification_deliveries
                WHERE recipient_id = :recipient_id
                """
            ),
            {"recipient_id": recipient_id},
        )
        await conn.execute(
            text("DELETE FROM notification_events WHERE incident_id = :incident_id"),
            {"incident_id": incident_id},
        )
        await conn.execute(
            text("DELETE FROM incidents WHERE id = :incident_id"),
            {"incident_id": incident_id},
        )
        await conn.execute(
            text("DELETE FROM telegram_recipients WHERE id = :recipient_id"),
            {"recipient_id": recipient_id},
        )
        await conn.execute(text("DELETE FROM telegram_config"))


@pytest.mark.asyncio
async def test_bot_generation_namespaces_update_ids_and_retires_stale_inbox(
    pg_engine,
) -> None:
    update_id = 9_900_000_000 + uuid.uuid4().int % 100_000_000
    update = TelegramWebhookUpdate.model_validate({"update_id": update_id})
    try:
        async with pg_engine.begin() as conn:
            await _configure_bot(conn, generation=1)
            assert await persist_telegram_update(conn, update, bot_generation=1)
        async with pg_engine.begin() as conn:
            await _configure_bot(conn, generation=2)
            with pytest.raises(TelegramIngressUnavailableError):
                await persist_telegram_update(conn, update, bot_generation=1)
            assert await persist_telegram_update(conn, update, bot_generation=2)

        claim = await claim_telegram_update(pg_engine, worker_id="generation-test")
        assert claim is not None
        assert claim.bot_generation == 2
        assert claim.update_id == update_id
        async with pg_engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT bot_generation, state, last_error_code
                        FROM telegram_updates_inbox
                        WHERE update_id = :update_id
                        ORDER BY bot_generation
                        """
                    ),
                    {"update_id": update_id},
                )
            ).all()
        assert [(row.bot_generation, row.state) for row in rows] == [
            (1, "dead"),
            (2, "leased"),
        ]
        assert rows[0].last_error_code == "stale_bot_generation"
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM telegram_updates_inbox WHERE update_id = :update_id"),
                {"update_id": update_id},
            )
            await conn.execute(text("DELETE FROM telegram_config"))


@pytest.mark.asyncio
async def test_disable_after_claim_prevents_warm_worker_money_task(
    pg_engine,
    telegram_action_context,
) -> None:
    ctx = telegram_action_context
    async with pg_engine.begin() as conn:
        token = await mint_action_token(
            conn,
            recipient_id=ctx["recipient_id"],
            incident_id=ctx["incident_id"],
            incident_generation=1,
            action_key="pause",
            action_kind="pause_ad",
            target_type="fb_ad",
            target_id=ctx["fb_ad_id"],
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    claim = await claim_action_token(
        pg_engine,
        token_id=token.id,
        chat_id=ctx["chat_id"],
        telegram_user_id=ctx["user_id"],
        claim_key="disable-after-claim",
    )
    assert claim.status == "claimed"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE telegram_config
                SET is_enabled = FALSE,
                    webhook_generation = webhook_generation + 1,
                    webhook_operation = 'delete', webhook_state = 'pending',
                    webhook_applied_generation = NULL
                WHERE singleton_key = 'default'
                """
            )
        )

    client = AsyncMock()
    await handle_action_callback(
        engine=pg_engine,
        client=client,
        cq_id="disable-after-claim",
        raw_token=None,
        token_id=token.id,
        chat_id=ctx["chat_id"],
        telegram_user_id=ctx["user_id"],
        username="owner",
        bot_generation=1,
    )

    async with pg_engine.connect() as conn:
        task_count = await conn.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM task_queue
                WHERE payload->>'target_id' = :target_id
                  AND payload->>'mutation_kind' = 'pause_ad'
                """
            ),
            {"target_id": ctx["fb_ad_id"]},
        )
        consumed_at = await conn.scalar(
            text("SELECT consumed_at FROM telegram_action_tokens WHERE id = :id"),
            {"id": token.id},
        )
    assert task_count == 0
    assert consumed_at is None
    assert "отключ" in client.answer_callback_query.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_revoked_owner_can_only_attach_receipt_proven_money_claim(
    pg_engine,
    telegram_action_context,
) -> None:
    ctx = telegram_action_context
    async with pg_engine.begin() as conn:
        committed_token = await mint_action_token(
            conn,
            recipient_id=ctx["recipient_id"],
            incident_id=ctx["incident_id"],
            incident_generation=1,
            action_key="pause",
            action_kind="pause_ad",
            target_type="fb_ad",
            target_id=ctx["fb_ad_id"],
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        uncommitted_token = await mint_action_token(
            conn,
            recipient_id=ctx["recipient_id"],
            incident_id=ctx["incident_id"],
            incident_generation=1,
            action_key="pause-second",
            action_kind="pause_ad",
            target_type="fb_ad",
            target_id=ctx["fb_ad_id"],
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    claim_key = "receipt-proven-retry"
    claimed = await claim_action_token(
        pg_engine,
        token_id=committed_token.id,
        chat_id=ctx["chat_id"],
        telegram_user_id=ctx["user_id"],
        claim_key=claim_key,
    )
    assert claimed.status == "claimed"
    idempotency_key = _command_idempotency_key(claimed)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE telegram_action_tokens
                SET command_idempotency_key = :idempotency_key
                WHERE id = :token_id
                """
            ),
            {"token_id": committed_token.id, "idempotency_key": idempotency_key},
        )
    receipt = await CommandService(pg_engine).enqueue_ad_action(
        action_kind="pause_ad",
        fb_ad_id=ctx["fb_ad_id"],
        requested_by=f"tg:{ctx['user_id']}",
        idempotency_key=idempotency_key,
        created_by_chat_id=ctx["chat_id"],
    )
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE telegram_recipients SET revoked_at = NOW() WHERE id = :id"),
            {"id": ctx["recipient_id"]},
        )

    callback = {
        "id": claim_key,
        "data": "a:redacted",
        "_fb_action_token_id": str(committed_token.id),
        "from": {"id": ctx["user_id"], "username": "owner"},
        "message": {"chat": {"id": ctx["chat_id"], "type": "private"}},
    }
    client = AsyncMock()
    await _dispatch_callback_query(
        engine=pg_engine,
        client=client,
        cq=callback,
        bot_generation=1,
    )
    denied = AsyncMock()
    fresh_callback = {
        **callback,
        "id": "fresh-after-revoke",
        "_fb_action_token_id": str(uncommitted_token.id),
    }
    await _dispatch_callback_query(
        engine=pg_engine,
        client=denied,
        cq=fresh_callback,
        bot_generation=1,
    )

    async with pg_engine.connect() as conn:
        committed = (
            await conn.execute(
                text(
                    """
                    SELECT consumed_at, task_id
                    FROM telegram_action_tokens
                    WHERE id = :id
                    """
                ),
                {"id": committed_token.id},
            )
        ).one()
        uncommitted = (
            await conn.execute(
                text(
                    """
                    SELECT claimed_at, consumed_at
                    FROM telegram_action_tokens
                    WHERE id = :id
                    """
                ),
                {"id": uncommitted_token.id},
            )
        ).one()
        task_count = await conn.scalar(
            text(
                """
                SELECT COUNT(*) FROM task_queue
                WHERE payload->>'target_id' = :target_id
                  AND payload->>'mutation_kind' = 'pause_ad'
                """
            ),
            {"target_id": ctx["fb_ad_id"]},
        )
    assert committed.consumed_at is not None
    assert committed.task_id == receipt.task_id
    assert uncommitted.claimed_at is None
    assert uncommitted.consumed_at is None
    assert task_count == 1
    assert f"#{receipt.task_id}" in client.answer_callback_query.await_args.kwargs["text"]
    assert "доступа нет" in denied.answer_callback_query.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_incident_ack_waiting_on_incident_never_deadlocks_recipient_revoke(
    pg_engine,
    telegram_action_context,
) -> None:
    """ACK takes incident then recipient advisory; revoke can finish meanwhile."""
    ctx = telegram_action_context
    notification_recipient_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (id, chat_id, telegram_user_id, role)
                VALUES (:id, :chat_id, :user_id, 'recipient')
                """
            ),
            {
                "id": notification_recipient_id,
                "chat_id": ctx["chat_id"] + 10_000_000_000,
                "user_id": ctx["user_id"] + 10_000_000_000,
            },
        )
        token = await mint_action_token(
            conn,
            recipient_id=ctx["recipient_id"],
            incident_id=ctx["incident_id"],
            incident_generation=1,
            action_key="ack-lock-order",
            action_kind="ack_incident",
            target_type="incident",
            target_id=str(ctx["incident_id"]),
            target_payload={"generation": 1},
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

    blocker = await pg_engine.connect()
    blocker_tx = await blocker.begin()
    ack_task = None
    revoke_task = None
    client = AsyncMock()
    try:
        await blocker.execute(
            text("SELECT id FROM incidents WHERE id = :id FOR UPDATE"),
            {"id": ctx["incident_id"]},
        )
        ack_task = asyncio.create_task(
            handle_action_callback(
                engine=pg_engine,
                client=client,
                cq_id="ack-vs-revoke-lock-order",
                raw_token=None,
                token_id=token.id,
                chat_id=ctx["chat_id"],
                telegram_user_id=ctx["user_id"],
                username="owner",
                bot_generation=1,
            )
        )
        await _wait_for_blocked_backend(
            pg_engine,
            query_fragment="SELECT t.recipient_id",
        )
        revoke_task = asyncio.create_task(
            delete_telegram_recipient(str(notification_recipient_id), pg_engine)
        )
        # With the old recipient-row-first ACK order this side waited in a
        # row/advisory cycle until PostgreSQL's deadlock detector aborted one.
        await asyncio.wait_for(asyncio.shield(revoke_task), timeout=3.0)
        await blocker_tx.commit()
        await asyncio.wait_for(ack_task, timeout=3.0)
    finally:
        if blocker_tx.is_active:
            await blocker_tx.rollback()
        for task in (ack_task, revoke_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (ack_task, revoke_task) if task is not None),
            return_exceptions=True,
        )
        await blocker.close()
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE id = :id"),
                {"id": notification_recipient_id},
            )

    async with pg_engine.connect() as conn:
        incident_status = await conn.scalar(
            text("SELECT status FROM incidents WHERE id = :id"),
            {"id": ctx["incident_id"]},
        )
        token_state = (
            await conn.execute(
                text(
                    """
                    SELECT claimed_at, consumed_at
                    FROM telegram_action_tokens
                    WHERE id = :id
                    """
                ),
                {"id": token.id},
            )
        ).one()
        ack_events = await conn.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM notification_events
                WHERE incident_id = :incident_id
                  AND event_type = 'incident_acknowledged'
                """
            ),
            {"incident_id": ctx["incident_id"]},
        )
    assert incident_status == "acknowledged"
    assert token_state.claimed_at is not None
    assert token_state.consumed_at is not None
    assert ack_events == 1
    assert "принят" in client.answer_callback_query.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_single_owner_can_ack_two_incidents_concurrently(
    pg_engine,
    telegram_action_context,
) -> None:
    ctx = telegram_action_context
    second_incident_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO incidents
                    (id, incident_key, generation, resource_type, resource_id,
                     severity, status, title)
                VALUES (:id, :key, 1, 'ad', :resource_id,
                        'critical', 'open', 'Second concurrent ACK')
                """
            ),
            {
                "id": second_incident_id,
                "key": f"test:concurrent-ack:{second_incident_id}",
                "resource_id": ctx["fb_ad_id"],
            },
        )
        owner_specs = [
            (
                ctx["recipient_id"],
                ctx["incident_id"],
                ctx["chat_id"],
                ctx["user_id"],
            ),
            (
                ctx["recipient_id"],
                second_incident_id,
                ctx["chat_id"],
                ctx["user_id"],
            ),
        ]
        tokens = []
        for index, (recipient_id, incident_id, _chat_id, _user_id) in enumerate(owner_specs):
            tokens.append(
                await mint_action_token(
                    conn,
                    recipient_id=recipient_id,
                    incident_id=incident_id,
                    incident_generation=1,
                    action_key=f"ack-concurrent-{index}",
                    action_kind="ack_incident",
                    target_type="incident",
                    target_id=str(incident_id),
                    target_payload={"generation": 1},
                    expires_at=datetime.now(UTC) + timedelta(minutes=5),
                )
            )

    clients = [AsyncMock(), AsyncMock()]
    tasks = [
        asyncio.create_task(
            handle_action_callback(
                engine=pg_engine,
                client=clients[index],
                cq_id=f"concurrent-ack-{index}",
                raw_token=None,
                token_id=tokens[index].id,
                chat_id=owner_specs[index][2],
                telegram_user_id=owner_specs[index][3],
                username=f"owner-{index}",
                bot_generation=1,
            )
        )
        for index in range(2)
    ]
    try:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5.0)
        async with pg_engine.connect() as conn:
            statuses = (
                await conn.execute(
                    text(
                        """
                        SELECT id, status
                        FROM incidents
                        WHERE id = ANY(CAST(:ids AS uuid[]))
                        """
                    ),
                    {"ids": [ctx["incident_id"], second_incident_id]},
                )
            ).all()
        assert {row.status for row in statuses} == {"acknowledged"}
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    DELETE FROM notification_deliveries d
                    USING notification_events e
                    WHERE d.event_id = e.id
                      AND e.incident_id = :incident_id
                    """
                ),
                {"incident_id": second_incident_id},
            )
            await conn.execute(
                text("DELETE FROM notification_events WHERE incident_id = :id"),
                {"id": second_incident_id},
            )
            await conn.execute(
                text("DELETE FROM incidents WHERE id = :id"),
                {"id": second_incident_id},
            )


@pytest.mark.asyncio
async def test_ack_roster_snapshot_linearizes_concurrent_recipient_add(
    pg_engine,
    telegram_action_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = telegram_action_context
    added_recipient_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        token = await mint_action_token(
            conn,
            recipient_id=ctx["recipient_id"],
            incident_id=ctx["incident_id"],
            incident_generation=1,
            action_key="ack-roster-race",
            action_kind="ack_incident",
            target_type="incident",
            target_id=str(ctx["incident_id"]),
            target_payload={"generation": 1},
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

    entered = asyncio.Event()
    release = asyncio.Event()
    original_serialize = alerts_module.serialize_recipient_delivery_state_in_transaction

    async def blocked_serialize(conn, recipient_ids):
        entered.set()
        await release.wait()
        await original_serialize(conn, recipient_ids)

    monkeypatch.setattr(
        alerts_module,
        "serialize_recipient_delivery_state_in_transaction",
        blocked_serialize,
    )
    client = AsyncMock()
    ack_task = asyncio.create_task(
        handle_action_callback(
            engine=pg_engine,
            client=client,
            cq_id="ack-roster-race",
            raw_token=None,
            token_id=token.id,
            chat_id=ctx["chat_id"],
            telegram_user_id=ctx["user_id"],
            username="owner",
            bot_generation=1,
        )
    )

    async def add_recipient() -> None:
        async with pg_engine.begin() as conn:
            await lock_owner_roster(conn)
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients
                        (id, chat_id, telegram_user_id, role)
                    VALUES (:id, :chat_id, :user_id, 'recipient')
                    """
                ),
                {
                    "id": added_recipient_id,
                    "chat_id": ctx["chat_id"] + 30_000_000_000,
                    "user_id": ctx["user_id"] + 30_000_000_000,
                },
            )

    add_task = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=3.0)
        add_task = asyncio.create_task(add_recipient())
        await _wait_for_blocked_backend(
            pg_engine,
            query_fragment="pg_advisory_xact_lock(hashtext",
        )
        release.set()
        await asyncio.wait_for(ack_task, timeout=3.0)
        await asyncio.wait_for(add_task, timeout=3.0)
        async with pg_engine.connect() as conn:
            added_delivery_count = await conn.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM notification_deliveries d
                    JOIN notification_events e ON e.id=d.event_id
                    WHERE e.incident_id=:incident_id
                      AND e.event_type='incident_acknowledged'
                      AND d.recipient_id=:recipient_id
                    """
                ),
                {"incident_id": ctx["incident_id"], "recipient_id": added_recipient_id},
            )
        assert added_delivery_count == 0
    finally:
        release.set()
        for task in (ack_task, add_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (ack_task, add_task) if task is not None),
            return_exceptions=True,
        )
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE id=:id"),
                {"id": added_recipient_id},
            )


@pytest.mark.asyncio
async def test_money_status_divergence_linearizes_concurrent_recipient_revoke(
    pg_engine,
    telegram_action_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = telegram_action_context
    notification_recipient_id = uuid.uuid4()
    original = await CommandService(pg_engine).enqueue_ad_action(
        action_kind="pause_ad",
        fb_ad_id=ctx["fb_ad_id"],
        requested_by="operator:web",
        idempotency_key=f"divergence-original:{uuid.uuid4()}",
    )
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (id, chat_id, telegram_user_id, role)
                VALUES (:id, :chat_id, :user_id, 'recipient')
                """
            ),
            {
                "id": notification_recipient_id,
                "chat_id": ctx["chat_id"] + 40_000_000_000,
                "user_id": ctx["user_id"] + 40_000_000_000,
            },
        )
        completed_at = await conn.scalar(
            text(
                """
                UPDATE task_queue
                SET status='succeeded', result='{"outcome":"CONFIRMED"}'::jsonb,
                    completed_at=NOW(), updated_at=NOW()
                WHERE id=:task_id
                RETURNING completed_at
                """
            ),
            {"task_id": original.task_id},
        )
        await conn.execute(
            text(
                """
                INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend)
                SELECT :metric_id, id, :cycle_ts, 1.00
                FROM fb_ads WHERE fb_ad_id=:fb_ad_id
                """
            ),
            {
                "metric_id": uuid.uuid4(),
                "cycle_ts": completed_at + timedelta(seconds=1),
                "fb_ad_id": ctx["fb_ad_id"],
            },
        )
        token = await mint_action_token(
            conn,
            recipient_id=ctx["recipient_id"],
            incident_id=ctx["incident_id"],
            incident_generation=1,
            action_key="pause-divergence",
            action_kind="pause_ad",
            target_type="fb_ad",
            target_id=ctx["fb_ad_id"],
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

    entered = asyncio.Event()
    release = asyncio.Event()
    original_serialize = alerts_module.serialize_recipient_delivery_state_in_transaction

    async def blocked_serialize(conn, recipient_ids):
        entered.set()
        await release.wait()
        await original_serialize(conn, recipient_ids)

    monkeypatch.setattr(
        alerts_module,
        "serialize_recipient_delivery_state_in_transaction",
        blocked_serialize,
    )
    client = AsyncMock()
    action_task = asyncio.create_task(
        handle_action_callback(
            engine=pg_engine,
            client=client,
            cq_id="money-divergence-vs-revoke",
            raw_token=None,
            token_id=token.id,
            chat_id=ctx["chat_id"],
            telegram_user_id=ctx["user_id"],
            username="owner",
            bot_generation=1,
        )
    )
    revoke_task = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=3.0)
        revoke_task = asyncio.create_task(
            delete_telegram_recipient(str(notification_recipient_id), pg_engine)
        )
        await _wait_for_blocked_backend(
            pg_engine,
            query_fragment="pg_advisory_xact_lock(hashtext",
        )
        release.set()
        await asyncio.wait_for(action_task, timeout=4.0)
        await asyncio.wait_for(revoke_task, timeout=4.0)
        async with pg_engine.connect() as conn:
            task_count = await conn.scalar(
                text(
                    """
                    SELECT COUNT(*) FROM task_queue
                    WHERE payload->>'target_id'=:fb_ad_id
                    """
                ),
                {"fb_ad_id": ctx["fb_ad_id"]},
            )
            divergence_count = await conn.scalar(
                text(
                    """
                    SELECT COUNT(*) FROM incidents
                    WHERE incident_key=:key AND status='open'
                    """
                ),
                {"key": (f"meta-status-divergence:{ctx['fb_ad_id']}:{original.task_id}")},
            )
        assert task_count == 2
        assert divergence_count == 1
    finally:
        release.set()
        for task in (action_task, revoke_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (action_task, revoke_task) if task is not None),
            return_exceptions=True,
        )
        async with pg_engine.begin() as conn:
            divergence_key = f"meta-status-divergence:{ctx['fb_ad_id']}:{original.task_id}"
            await conn.execute(
                text(
                    """
                    DELETE FROM notification_deliveries d USING notification_events e,
                        incidents i
                    WHERE d.event_id=e.id AND e.incident_id=i.id
                      AND i.incident_key=:key
                    """
                ),
                {"key": divergence_key},
            )
            await conn.execute(
                text(
                    """
                    DELETE FROM notification_events e USING incidents i
                    WHERE e.incident_id=i.id AND i.incident_key=:key
                    """
                ),
                {"key": divergence_key},
            )
            await conn.execute(
                text("DELETE FROM incidents WHERE incident_key=:key"),
                {"key": divergence_key},
            )
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE id=:id"),
                {"id": notification_recipient_id},
            )


@pytest.mark.asyncio
async def test_ack_transition_and_token_consume_are_atomic_with_proven_recovery(
    pg_engine,
    telegram_action_context,
) -> None:
    ctx = telegram_action_context
    uncommitted_incident_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        token = await mint_action_token(
            conn,
            recipient_id=ctx["recipient_id"],
            incident_id=ctx["incident_id"],
            incident_generation=1,
            action_key="ack",
            action_kind="ack_incident",
            target_type="incident",
            target_id=str(ctx["incident_id"]),
            target_payload={"generation": 1},
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        await conn.execute(
            text(
                """
                INSERT INTO incidents
                    (id, incident_key, generation, resource_type, resource_id,
                     severity, status, title)
                VALUES
                    (:id, :key, 1, 'ad', :resource_id,
                     'critical', 'open', 'Uncommitted ACK')
                """
            ),
            {
                "id": uncommitted_incident_id,
                "key": f"test:uncommitted-ack:{uncommitted_incident_id}",
                "resource_id": ctx["fb_ad_id"],
            },
        )
        uncommitted_token = await mint_action_token(
            conn,
            recipient_id=ctx["recipient_id"],
            incident_id=uncommitted_incident_id,
            incident_generation=1,
            action_key="ack-uncommitted",
            action_kind="ack_incident",
            target_type="incident",
            target_id=str(uncommitted_incident_id),
            target_payload={"generation": 1},
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    claim_key = "ack-committed-recovery"
    assert (
        await claim_action_token(
            pg_engine,
            token_id=token.id,
            chat_id=ctx["chat_id"],
            telegram_user_id=ctx["user_id"],
            claim_key=claim_key,
        )
    ).status == "claimed"
    await acknowledge_incident(
        pg_engine,
        incident_id=ctx["incident_id"],
        acknowledged_by=f"tg:{ctx['user_id']}",
        expected_generation=1,
    )
    assert (
        await claim_action_token(
            pg_engine,
            token_id=uncommitted_token.id,
            chat_id=ctx["chat_id"],
            telegram_user_id=ctx["user_id"],
            claim_key="ack-without-commit",
        )
    ).status == "claimed"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE telegram_recipients SET revoked_at = NOW() WHERE id = :id"),
            {"id": ctx["recipient_id"]},
        )

    client = AsyncMock()
    await handle_action_callback(
        engine=pg_engine,
        client=client,
        cq_id=claim_key,
        raw_token=None,
        token_id=token.id,
        chat_id=ctx["chat_id"],
        telegram_user_id=ctx["user_id"],
        username="owner",
        bot_generation=1,
    )
    denied = AsyncMock()
    await handle_action_callback(
        engine=pg_engine,
        client=denied,
        cq_id="ack-without-commit",
        raw_token=None,
        token_id=uncommitted_token.id,
        chat_id=ctx["chat_id"],
        telegram_user_id=ctx["user_id"],
        username="owner",
        bot_generation=1,
    )
    async with pg_engine.connect() as conn:
        consumed_at = await conn.scalar(
            text("SELECT consumed_at FROM telegram_action_tokens WHERE id = :id"),
            {"id": token.id},
        )
        ack_events = await conn.scalar(
            text(
                """
                SELECT COUNT(*) FROM notification_events
                WHERE incident_id = :incident_id
                  AND event_type = 'incident_acknowledged'
                """
            ),
            {"incident_id": ctx["incident_id"]},
        )
        uncommitted = (
            await conn.execute(
                text(
                    """
                    SELECT i.status, t.consumed_at
                    FROM incidents i
                    JOIN telegram_action_tokens t ON t.incident_id = i.id
                    WHERE i.id = :incident_id AND t.id = :token_id
                    """
                ),
                {
                    "incident_id": uncommitted_incident_id,
                    "token_id": uncommitted_token.id,
                },
            )
        ).one()
    assert consumed_at is not None
    assert ack_events == 1
    assert client.answer_callback_query.await_args.kwargs["text"] == "Инцидент принят"
    assert uncommitted.status == "open"
    assert uncommitted.consumed_at is None
    assert "доступ отозван" in denied.answer_callback_query.await_args.kwargs["text"]
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM incidents WHERE id = :id"),
            {"id": uncommitted_incident_id},
        )
