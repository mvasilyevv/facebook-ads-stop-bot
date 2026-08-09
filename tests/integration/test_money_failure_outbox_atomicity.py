# -*- coding: utf-8 -*-
"""Atomic terminal money-task -> durable notification outbox contract."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

import core.telegram.notifications as notifications
from core.tasks import create_task, mark_failed, mark_succeeded
from core.tasks.queue import claim_browser_ready_task

pytestmark = pytest.mark.usefixtures(
    "fresh_browser_readiness",
    "authoritative_telegram_config",
)


async def _owner(pg_engine, *, marker: str) -> uuid.UUID:
    recipient_id = uuid.uuid4()
    telegram_id = 8_000_000_000 + int(marker[:7], 16)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (id, chat_id, telegram_user_id, username, role)
                VALUES (:id, :telegram_id, :telegram_id, :username, 'owner')
                """
            ),
            {
                "id": recipient_id,
                "telegram_id": telegram_id,
                "username": f"money_atomic_{marker}",
            },
        )
    return recipient_id


async def _cleanup(pg_engine, *, task_ids: list[int], recipient_id: uuid.UUID) -> None:
    async with pg_engine.begin() as conn:
        dedupe_keys = [
            f"task:{task_id}:{suffix}"
            for task_id in task_ids
            for suffix in ("executing", "failed", "unknown", "partial")
        ]
        if dedupe_keys:
            await conn.execute(
                text(
                    "DELETE FROM notification_events WHERE dedupe_key = ANY(CAST(:keys AS text[]))"
                ),
                {"keys": dedupe_keys},
            )
        await conn.execute(
            text("DELETE FROM task_queue WHERE id = ANY(CAST(:ids AS bigint[]))"),
            {"ids": task_ids},
        )
        await conn.execute(
            text("DELETE FROM telegram_recipients WHERE id = :recipient_id"),
            {"recipient_id": recipient_id},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "task_type",
        "mutation_kind",
        "mutation_params",
        "expected_title",
        "expected_risk",
    ),
    [
        (
            "meta_api_mutation",
            "pause_ad",
            {},
            "Пауза не подтверждена",
            "Объявление может продолжать тратить бюджет",
        ),
        (
            "meta_api_mutation",
            "activate_ad",
            {},
            "Денежное действие не подтверждено",
            "Фактическое состояние Meta требует ручной проверки",
        ),
        (
            "meta_api_mutation",
            "bulk_status_change",
            {"action": "activate"},
            "Денежное действие не подтверждено",
            "Фактическое состояние Meta требует ручной проверки",
        ),
    ],
)
async def test_uncorrelated_money_failure_commits_one_durable_delivery(
    pg_engine,
    task_type: str,
    mutation_kind: str | None,
    mutation_params: dict[str, str],
    expected_title: str,
    expected_risk: str,
) -> None:
    marker = uuid.uuid4().hex
    recipient_id = await _owner(pg_engine, marker=marker)
    task_ids: list[int] = []
    try:
        payload = (
            {
                "mutation_kind": mutation_kind,
                "target_id": f"ad-{marker}",
                "ad_account_id": "123",
                "params": mutation_params,
            }
            if mutation_kind is not None
            else {"fb_ad_id": f"ad-{marker}"}
        )
        task_id = await create_task(
            pg_engine,
            task_type=task_type,
            idempotency_key=f"money-atomic-manual-{marker}",
            payload=payload,
            requested_by="operator:web:42",
        )
        assert task_id is not None
        task_ids.append(task_id)
        claim = await claim_browser_ready_task(
            pg_engine,
            task_type=task_type,
            lanes=("bulk" if mutation_kind == "bulk_status_change" else "interactive",),
        )
        assert claim.task is not None and claim.task.id == task_id
        fence = {
            "lease_owner": claim.task.lease_owner,
            "lease_token": claim.task.lease_token,
        }

        assert await mark_failed(pg_engine, task_id=task_id, error="meta rejected", **fence)
        assert not await mark_failed(pg_engine, task_id=task_id, error="late duplicate", **fence)

        async with pg_engine.connect() as conn:
            task_status = await conn.scalar(
                text("SELECT status FROM task_queue WHERE id = :task_id"),
                {"task_id": task_id},
            )
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT e.event_type, e.incident_id, e.facts, d.state
                        FROM notification_events e
                        JOIN notification_deliveries d ON d.event_id = e.id
                        WHERE e.dedupe_key = :dedupe_key
                          AND d.recipient_id = :recipient_id
                        """
                    ),
                    {
                        "dedupe_key": f"task:{task_id}:failed",
                        "recipient_id": recipient_id,
                    },
                )
            ).all()

        assert task_status == "failed"
        assert len(rows) == 1
        assert rows[0].event_type == "action_failed"
        assert rows[0].incident_id is None
        assert rows[0].facts["title"] == expected_title
        assert rows[0].facts["risk"] == expected_risk
        assert rows[0].state == "pending"
    finally:
        await _cleanup(pg_engine, task_ids=task_ids, recipient_id=recipient_id)


@pytest.mark.asyncio
async def test_notification_enqueue_failure_rolls_back_terminal_task_transition(
    pg_engine,
    monkeypatch,
) -> None:
    marker = uuid.uuid4().hex
    recipient_id = await _owner(pg_engine, marker=marker)
    task_ids: list[int] = []
    try:
        task_id = await create_task(
            pg_engine,
            task_type="meta_api_mutation",
            idempotency_key=f"money-atomic-rollback-{marker}",
            payload={
                "mutation_kind": "pause_ad",
                "target_id": f"ad-{marker}",
                "ad_account_id": "123",
                "params": {},
            },
            requested_by="operator:web:42",
        )
        assert task_id is not None
        task_ids.append(task_id)
        claim = await claim_browser_ready_task(
            pg_engine,
            task_type="meta_api_mutation",
            lanes=("interactive",),
        )
        assert claim.task is not None and claim.task.id == task_id

        async def fail_before_outbox_commit(*args, **kwargs):
            raise RuntimeError("simulated crash at notification boundary")

        original_enqueue = notifications.enqueue_notification_in_transaction
        monkeypatch.setattr(
            notifications,
            "enqueue_notification_in_transaction",
            fail_before_outbox_commit,
        )
        with pytest.raises(RuntimeError, match="notification boundary"):
            await mark_failed(
                pg_engine,
                task_id=task_id,
                error="meta rejected",
                lease_owner=claim.task.lease_owner,
                lease_token=claim.task.lease_token,
            )
        monkeypatch.setattr(
            notifications,
            "enqueue_notification_in_transaction",
            original_enqueue,
        )

        async with pg_engine.connect() as conn:
            task_status = await conn.scalar(
                text("SELECT status FROM task_queue WHERE id = :task_id"),
                {"task_id": task_id},
            )
            event_count = await conn.scalar(
                text("SELECT COUNT(*) FROM notification_events WHERE dedupe_key = :key"),
                {"key": f"task:{task_id}:failed"},
            )
        assert task_status == "running"
        assert event_count == 0

        assert await mark_failed(
            pg_engine,
            task_id=task_id,
            error="meta rejected",
            lease_owner=claim.task.lease_owner,
            lease_token=claim.task.lease_token,
        )
    finally:
        await _cleanup(pg_engine, task_ids=task_ids, recipient_id=recipient_id)


@pytest.mark.asyncio
async def test_partial_bulk_alert_is_committed_with_success_status(pg_engine) -> None:
    marker = uuid.uuid4().hex
    recipient_id = await _owner(pg_engine, marker=marker)
    task_ids: list[int] = []
    try:
        task_id = await create_task(
            pg_engine,
            task_type="meta_api_mutation",
            idempotency_key=f"money-atomic-partial-{marker}",
            payload={
                "mutation_kind": "bulk_status_change",
                "target_id": f"batch-{marker}",
                "ad_account_id": "123",
                "params": {"action": "pause"},
            },
            requested_by="operator:web:42",
        )
        assert task_id is not None
        task_ids.append(task_id)
        claim = await claim_browser_ready_task(
            pg_engine,
            task_type="meta_api_mutation",
            lanes=("bulk",),
        )
        assert claim.task is not None and claim.task.id == task_id
        assert await mark_succeeded(
            pg_engine,
            task_id=task_id,
            result={"outcome": "CONFIRMED", "succeeded": 2, "failed": 1},
            lease_owner=claim.task.lease_owner,
            lease_token=claim.task.lease_token,
        )

        async with pg_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT q.status, e.event_type, e.incident_id
                        FROM task_queue q
                        JOIN notification_events e ON e.dedupe_key = :dedupe_key
                        WHERE q.id = :task_id
                        """
                    ),
                    {"task_id": task_id, "dedupe_key": f"task:{task_id}:partial"},
                )
            ).one()
        assert (row.status, row.event_type, row.incident_id) == (
            "succeeded",
            "action_failed",
            None,
        )
    finally:
        await _cleanup(pg_engine, task_ids=task_ids, recipient_id=recipient_id)


@pytest.mark.asyncio
async def test_correlated_failure_emits_only_incident_lifecycle_event(pg_engine) -> None:
    marker = uuid.uuid4().hex
    recipient_id = await _owner(pg_engine, marker=marker)
    task_ids: list[int] = []
    incident_id = uuid.uuid4()
    correlation_id = uuid.uuid4()
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO incidents
                        (id, incident_key, resource_type, resource_id, severity,
                         status, title, correlation_id)
                    VALUES
                        (:id, :incident_key, 'fb_ad', :resource_id, 'critical',
                         'open', 'CPL выше stop', :correlation_id)
                    """
                ),
                {
                    "id": incident_id,
                    "incident_key": f"money-atomic:{marker}",
                    "resource_id": f"ad-{marker}",
                    "correlation_id": correlation_id,
                },
            )
        task_id = await create_task(
            pg_engine,
            task_type="meta_api_mutation",
            idempotency_key=f"money-atomic-incident-{marker}",
            payload={
                "mutation_kind": "pause_ad",
                "target_id": f"ad-{marker}",
                "ad_account_id": "123",
                "params": {},
            },
            requested_by="bot_auto_stop",
            correlation_id=correlation_id,
        )
        assert task_id is not None
        task_ids.append(task_id)
        claim = await claim_browser_ready_task(
            pg_engine,
            task_type="meta_api_mutation",
            lanes=("money",),
        )
        assert claim.task is not None and claim.task.id == task_id
        assert await mark_failed(
            pg_engine,
            task_id=task_id,
            error="meta rejected",
            lease_owner=claim.task.lease_owner,
            lease_token=claim.task.lease_token,
        )

        async with pg_engine.connect() as conn:
            failed_events = (
                await conn.execute(
                    text(
                        """
                        SELECT event_type, incident_id
                        FROM notification_events
                        WHERE dedupe_key = :dedupe_key
                        """
                    ),
                    {"dedupe_key": f"task:{task_id}:failed"},
                )
            ).all()
        assert [(row.event_type, row.incident_id) for row in failed_events] == [
            ("action_failed", incident_id)
        ]
    finally:
        await _cleanup(pg_engine, task_ids=task_ids, recipient_id=recipient_id)
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM incidents WHERE id = :incident_id"),
                {"incident_id": incident_id},
            )
