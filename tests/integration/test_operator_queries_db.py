from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocketDisconnect
from sqlalchemy import text

import apps.api.routers.ws as ws_router
import core.config
import core.db
from apps.api.routers.v1.operator import _account_meta, _analytics_sections
from apps.api.routers.v1.schemas.operator import DataState
from core.dashboard.stats_queries import fetch_daily_series
from core.operator.queries import (
    fetch_operator_actions,
    fetch_operator_ads,
    fetch_operator_incident,
    fetch_operator_incident_page,
    fetch_operator_incidents,
    fetch_operator_revision,
    fetch_operator_scan_state,
)


async def _delete_cabinet_runtime(pg_engine, account_id: str) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM cabinet_runtime WHERE ad_account_id = :account_id"),
            {"account_id": account_id},
        )


async def _ensure_operator_revision_row(pg_engine) -> None:
    """Seed the append-only cursor for metadata-created integration DBs.

    The general integration fixture intentionally uses ``Base.create_all`` and
    therefore does not execute Alembic data migrations.  Production and the
    migration acceptance suite receive this row from the safety-first baseline.
    """

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO operator_revision_events (scope, event_id)
                SELECT 'integration_seed', NULL
                WHERE NOT EXISTS (SELECT 1 FROM operator_revision_events)
                """
            )
        )


@pytest.mark.asyncio
async def test_global_operator_queries_accept_unscoped_account(pg_engine) -> None:
    """A fresh portfolio has no account filter value for PostgreSQL to infer."""

    now = datetime.now(UTC)

    account = await _account_meta(pg_engine, None)
    daily = await fetch_daily_series(
        pg_engine,
        from_dt=now - timedelta(days=7),
        to_dt=now,
        account_id=None,
    )

    assert set(account) == {"id", "name"}
    assert isinstance(daily, list)


@pytest.mark.asyncio
async def test_operator_actions_keep_retrying_ambiguity_visible(pg_engine) -> None:
    """UNKNOWN money actions must never be projected or filtered as queued."""

    idempotency_key = f"operator-unknown-{uuid.uuid4()}"
    task_id: int | None = None
    try:
        async with pg_engine.begin() as conn:
            task_id = await conn.scalar(
                text(
                    """
                    INSERT INTO task_queue (
                        task_type, status, idempotency_key, payload, result,
                        requested_by, lane, priority, available_at, deadline_at
                    )
                        VALUES (
                            'meta_api_mutation', 'retrying', :idempotency_key,
                            CAST(:payload AS JSONB), CAST(:result AS JSONB),
                        'operator:test', 'money', 100, NOW(), NOW() + INTERVAL '30 seconds'
                    )
                    RETURNING id
                    """
                ),
                {
                    "idempotency_key": idempotency_key,
                    "payload": (
                        '{"mutation_kind":"pause_ad","target_id":"ambiguous-ad",'
                        '"ad_account_id":"123"}'
                    ),
                    "result": '{"outcome":"UNKNOWN","reconcile_required":true}',
                },
            )

        unknown, _, _ = await fetch_operator_actions(
            pg_engine,
            states=("unknown",),
            limit=100,
        )
        queued, _, _ = await fetch_operator_actions(
            pg_engine,
            states=("queued",),
            limit=100,
        )

        projected = next(item for item in unknown if item["id"] == str(task_id))
        assert projected["state"] == "unknown"
        assert all(item["id"] != str(task_id) for item in queued)
    finally:
        if task_id is not None:
            async with pg_engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM task_queue WHERE id = :task_id"),
                    {"task_id": task_id},
                )


@pytest.mark.asyncio
async def test_operator_actions_filter_two_cabinets_in_the_database(pg_engine) -> None:
    suffix = uuid.uuid4().hex[:10]
    account_a = str(int(suffix[:5], 16) + 1_000_000)
    account_b = str(int(suffix[5:], 16) + 2_000_000)
    task_ids: list[int] = []
    try:
        async with pg_engine.begin() as conn:
            for account_id in (account_a, account_b):
                task_id = await conn.scalar(
                    text(
                        """
                        INSERT INTO task_queue (
                            task_type, status, idempotency_key, payload, result,
                            requested_by, lane, priority, available_at, deadline_at
                        )
                        VALUES (
                            'meta_api_mutation', 'running', :idempotency_key,
                            CAST(:payload AS JSONB), '{}'::JSONB,
                            'operator:test', 'interactive', 100,
                            NOW(), NOW() + INTERVAL '120 seconds'
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "idempotency_key": f"operator-cabinet-scope-{suffix}-{account_id}",
                        "payload": (
                            '{"mutation_kind":"pause_ad","target_id":"scope-'
                            f'{account_id}","ad_account_id":"{account_id}"}}'
                        ),
                    },
                )
                assert task_id is not None
                task_ids.append(int(task_id))

        visible, _, _ = await fetch_operator_actions(
            pg_engine,
            account_id=f"act_{account_a}",
            limit=100,
        )

        visible_ids = {item["id"] for item in visible}
        assert str(task_ids[0]) in visible_ids
        assert str(task_ids[1]) not in visible_ids
        scoped_item = next(item for item in visible if item["id"] == str(task_ids[0]))
        assert scoped_item["account_id"] == account_a
    finally:
        if task_ids:
            async with pg_engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM task_queue WHERE id = ANY(:task_ids)"),
                    {"task_ids": task_ids},
                )


@pytest.mark.asyncio
async def test_operator_ads_keep_terminal_unknown_as_active_action(pg_engine) -> None:
    """Ads remain action-blocked while a terminal Meta result is unresolved."""
    suffix = uuid.uuid4().hex[:10]
    account_id = str(int(suffix, 16) % 9_000_000_000 + 1_000_000_000)
    campaign_id, adset_id, ad_id = (uuid.uuid4() for _ in range(3))
    fb_ad_id = f"opads_unknown_{suffix}"
    now = datetime.now(UTC)
    task_id: int | None = None
    seeded = False
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO fb_campaigns (id, ad_account_id, campaign_name)
                    VALUES (:id, :account_id, :name)
                    """
                ),
                {
                    "id": campaign_id,
                    "account_id": account_id,
                    "name": f"OPADS_UNKNOWN_CMP_{suffix}",
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO fb_adsets (id, campaign_id, adset_name)
                    VALUES (:id, :campaign_id, :name)
                    """
                ),
                {
                    "id": adset_id,
                    "campaign_id": campaign_id,
                    "name": f"OPADS_UNKNOWN_SET_{suffix}",
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO fb_ads (
                        id, adset_id, fb_ad_id, ad_name, delivery_status, last_seen_at
                    )
                    VALUES (
                        :id, :adset_id, :fb_ad_id, :name, 'ACTIVE', :last_seen_at
                    )
                    """
                ),
                {
                    "id": ad_id,
                    "adset_id": adset_id,
                    "fb_ad_id": fb_ad_id,
                    "name": f"OPADS_UNKNOWN_{suffix}",
                    "last_seen_at": now,
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics (
                        id, ad_id, cycle_ts, spend, impressions, clicks
                    )
                    VALUES (gen_random_uuid(), :ad_id, :cycle_ts, 12, 100, 10)
                    """
                ),
                {"ad_id": ad_id, "cycle_ts": now},
            )
            task_id = await conn.scalar(
                text(
                    """
                    INSERT INTO task_queue (
                        task_type, status, idempotency_key, payload, result,
                        requested_by, lane, priority, available_at, completed_at
                    )
                    VALUES (
                        'meta_api_mutation', 'failed', :idempotency_key,
                        CAST(:payload AS JSONB), CAST(:result AS JSONB),
                        'operator:test', 'money', 100, NOW(), NOW()
                    )
                    RETURNING id
                    """
                ),
                {
                    "idempotency_key": f"operator-ad-unknown-{uuid.uuid4()}",
                    "payload": (
                        '{"mutation_kind":"pause_ad",'
                        f'"target_id":"{fb_ad_id}","ad_account_id":"{account_id}"'
                        "}"
                    ),
                    "result": (
                        '{"outcome":"UNKNOWN","reconcile_required":false,'
                        '"reason":"reconciliation_exhausted"}'
                    ),
                },
            )
        seeded = True

        result = await fetch_operator_ads(
            pg_engine,
            from_dt=now - timedelta(hours=1),
            to_dt=now + timedelta(seconds=1),
            account_id=account_id,
            search=None,
            delivery_status=None,
            severity=None,
            sort="updated",
            direction="desc",
            page=1,
            page_size=50,
            tracker_available=False,
        )

        row = next(item for item in result["rows"] if item["fb_ad_id"] == fb_ad_id)
        assert row["active_action"] is not None
        assert row["active_action"]["id"] == str(task_id)
        assert row["active_action"]["kind"] == "pause"
        assert row["active_action"]["state"] == "unknown"
    finally:
        if seeded:
            async with pg_engine.begin() as conn:
                if task_id is not None:
                    await conn.execute(
                        text("DELETE FROM task_queue WHERE id = :task_id"),
                        {"task_id": task_id},
                    )
                await conn.execute(text("DELETE FROM ad_metrics WHERE ad_id = :id"), {"id": ad_id})
                await conn.execute(text("DELETE FROM fb_ads WHERE id = :id"), {"id": ad_id})
                await conn.execute(text("DELETE FROM fb_adsets WHERE id = :id"), {"id": adset_id})
                await conn.execute(
                    text("DELETE FROM fb_campaigns WHERE id = :id"), {"id": campaign_id}
                )


@pytest.mark.asyncio
async def test_operator_ads_keep_confirmed_action_until_post_command_evidence(
    pg_engine,
) -> None:
    """Confirmed remains visible only until the first post-command observation."""
    suffix = uuid.uuid4().hex[:10]
    account_id = str(int(suffix, 16) % 9_000_000_000 + 1_000_000_000)
    campaign_id, adset_id, ad_id = (uuid.uuid4() for _ in range(3))
    fb_ad_id = f"opads_confirmed_{suffix}"
    initial_cycle = datetime.now(UTC) - timedelta(minutes=1)
    task_id: int | None = None
    seeded = False

    async def read_ad() -> dict[str, object]:
        payload = await fetch_operator_ads(
            pg_engine,
            from_dt=initial_cycle - timedelta(hours=1),
            to_dt=initial_cycle + timedelta(hours=1),
            account_id=account_id,
            search=None,
            delivery_status=None,
            severity=None,
            sort="updated",
            direction="desc",
            page=1,
            page_size=50,
            tracker_available=False,
        )
        return next(item for item in payload["rows"] if item["fb_ad_id"] == fb_ad_id)

    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO fb_campaigns (id, ad_account_id, campaign_name)
                    VALUES (:id, :account_id, :name)
                    """
                ),
                {
                    "id": campaign_id,
                    "account_id": account_id,
                    "name": f"OPADS_CONFIRMED_CMP_{suffix}",
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO fb_adsets (id, campaign_id, adset_name)
                    VALUES (:id, :campaign_id, :name)
                    """
                ),
                {
                    "id": adset_id,
                    "campaign_id": campaign_id,
                    "name": f"OPADS_CONFIRMED_SET_{suffix}",
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO fb_ads (
                        id, adset_id, fb_ad_id, ad_name, delivery_status, last_seen_at
                    )
                    VALUES (
                        :id, :adset_id, :fb_ad_id, :name, 'ACTIVE', NOW()
                    )
                    """
                ),
                {
                    "id": ad_id,
                    "adset_id": adset_id,
                    "fb_ad_id": fb_ad_id,
                    "name": f"OPADS_CONFIRMED_{suffix}",
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics (
                        id, ad_id, cycle_ts, spend, impressions, clicks
                    )
                    VALUES (gen_random_uuid(), :ad_id, :cycle_ts, 12, 100, 10)
                    """
                ),
                {"ad_id": ad_id, "cycle_ts": initial_cycle},
            )
            task_id = await conn.scalar(
                text(
                    """
                    INSERT INTO task_queue (
                        task_type, status, idempotency_key, payload, result,
                        requested_by, lane, priority, available_at, completed_at
                    )
                    VALUES (
                        'meta_api_mutation', 'succeeded', :idempotency_key,
                        CAST(:payload AS JSONB), '{"outcome":"CONFIRMED"}'::jsonb,
                        'operator:test', 'money', 100, NOW(), NOW()
                    )
                    RETURNING id
                    """
                ),
                {
                    "idempotency_key": f"operator-ad-confirmed-{uuid.uuid4()}",
                    "payload": (
                        '{"mutation_kind":"pause_ad",'
                        f'"target_id":"{fb_ad_id}","ad_account_id":"{account_id}"'
                        "}"
                    ),
                },
            )
            completed_at = await conn.scalar(
                text("SELECT completed_at FROM task_queue WHERE id = :task_id"),
                {"task_id": task_id},
            )
        seeded = True

        stale = await read_ad()
        assert stale["active_action"] is not None
        assert stale["active_action"]["id"] == str(task_id)  # type: ignore[index]
        assert stale["active_action"]["state"] == "confirmed"  # type: ignore[index]

        evidence_cycle = completed_at + timedelta(seconds=1)
        async with pg_engine.begin() as conn:
            # The post-command observation ends the pending-reconciliation
            # projection even when it contradicts the claimed result. The
            # CommandService owns corrective-command and incident semantics.
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics (
                        id, ad_id, cycle_ts, spend, impressions, clicks
                    )
                    VALUES (gen_random_uuid(), :ad_id, :cycle_ts, 12, 100, 10)
                    """
                ),
                {"ad_id": ad_id, "cycle_ts": evidence_cycle},
            )

        fresh_but_mismatched = await read_ad()
        assert fresh_but_mismatched["active_action"] is None

        async with pg_engine.begin() as conn:
            await conn.execute(
                text("UPDATE fb_ads SET delivery_status = 'OFF' WHERE id = :ad_id"),
                {"ad_id": ad_id},
            )

        reconciled = await read_ad()
        assert reconciled["active_action"] is None
    finally:
        if seeded:
            async with pg_engine.begin() as conn:
                if task_id is not None:
                    await conn.execute(
                        text("DELETE FROM task_queue WHERE id = :task_id"),
                        {"task_id": task_id},
                    )
                await conn.execute(text("DELETE FROM ad_metrics WHERE ad_id = :id"), {"id": ad_id})
                await conn.execute(text("DELETE FROM fb_ads WHERE id = :id"), {"id": ad_id})
                await conn.execute(text("DELETE FROM fb_adsets WHERE id = :id"), {"id": adset_id})
                await conn.execute(
                    text("DELETE FROM fb_campaigns WHERE id = :id"), {"id": campaign_id}
                )


@pytest.mark.asyncio
async def test_operator_incident_detail_is_not_limited_by_attention_feed(pg_engine) -> None:
    """Opaque deep links must resolve even when the incident ranks below the top 50."""

    incident_ids = [uuid.uuid4() for _ in range(51)]
    suffix = uuid.uuid4().hex[:10]
    now = datetime.now(UTC)
    try:
        async with pg_engine.begin() as conn:
            for index, incident_id in enumerate(incident_ids):
                await conn.execute(
                    text(
                        """
                        INSERT INTO incidents (
                            id, incident_key, resource_type, resource_id,
                            severity, status, title, opened_at
                        )
                        VALUES (
                            :id, :incident_key, 'system', :resource_id,
                            'critical', 'open', :title, :opened_at
                        )
                        """
                    ),
                    {
                        "id": incident_id,
                        "incident_key": f"operator-detail:{suffix}:{index}",
                        "resource_id": f"resource-{suffix}-{index}",
                        "title": f"Operator detail {index}",
                        "opened_at": now - timedelta(seconds=index),
                    },
                )

        attention_rows = await fetch_operator_incidents(
            pg_engine,
            account_id=None,
            limit=50,
        )
        oldest_id = incident_ids[-1]
        assert len(attention_rows) == 50
        assert all(row["id"] != oldest_id for row in attention_rows)

        detail = await fetch_operator_incident(pg_engine, incident_id=oldest_id)

        assert detail is not None
        assert detail["id"] == oldest_id
        assert detail["title"] == "Operator detail 50"
        assert detail["status"] == "open"
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM incidents WHERE id = ANY(:incident_ids)"),
                {"incident_ids": incident_ids},
            )


@pytest.mark.asyncio
async def test_operator_incident_page_filters_cabinet_and_uses_stable_uuid_order(
    pg_engine,
) -> None:
    account_id = str(int(uuid.uuid4().hex[:12], 16))
    other_account_id = str(int(uuid.uuid4().hex[:12], 16))
    incident_ids = sorted([uuid.uuid4(), uuid.uuid4()])
    excluded_ids = [uuid.uuid4(), uuid.uuid4()]
    opened_at = datetime.now(UTC).replace(microsecond=0)
    suffix = uuid.uuid4().hex[:10]
    try:
        async with pg_engine.begin() as conn:
            for index, incident_id in enumerate([*incident_ids, *excluded_ids]):
                is_visible = index < len(incident_ids)
                await conn.execute(
                    text(
                        """
                        INSERT INTO incidents (
                            id, incident_key, resource_type, resource_id,
                            ad_account_id, severity, status, title, opened_at
                        )
                        VALUES (
                            :id, :incident_key, 'ad', :resource_id,
                            :account_id, :severity, :status, :title, :opened_at
                        )
                        """
                    ),
                    {
                        "id": incident_id,
                        "incident_key": f"operator-page:{suffix}:{index}",
                        "resource_id": f"ad-{suffix}-{index}",
                        "account_id": account_id if is_visible else other_account_id,
                        "severity": "warning" if index != 3 else "critical",
                        "status": "open" if index != 2 else "resolved",
                        "title": f"Operator page {index}",
                        "opened_at": opened_at,
                    },
                )

        first_page, total = await fetch_operator_incident_page(
            pg_engine,
            account_id=account_id,
            severities=("warning",),
            statuses=("open",),
            page=1,
            page_size=1,
        )
        second_page, second_total = await fetch_operator_incident_page(
            pg_engine,
            account_id=account_id,
            severities=("warning",),
            statuses=("open",),
            page=2,
            page_size=1,
        )

        assert total == second_total == 2
        assert [row["id"] for row in first_page] == [incident_ids[0]]
        assert [row["id"] for row in second_page] == [incident_ids[1]]
        assert all(row["ad_account_id"] == account_id for row in [*first_page, *second_page])
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM incidents WHERE id = ANY(:incident_ids)"),
                {"incident_ids": [*incident_ids, *excluded_ids]},
            )


@pytest.mark.asyncio
async def test_operator_scan_state_uses_cabinet_runtime_schema(pg_engine) -> None:
    account_id = f"contract_{uuid.uuid4().hex[:16]}"
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO cabinet_runtime (
                        ad_account_id, lease_token, stage, last_progress_at,
                        last_snapshot_at, last_error_code
                    )
                    VALUES (
                        :account_id, 7, 'degraded', NOW(), NOW(),
                        'scan_source_unavailable'
                    )
                    """
                ),
                {"account_id": account_id},
            )

        state = await fetch_operator_scan_state(pg_engine)

        actor = next(item for item in state["actors"] if item["ad_account_id"] == account_id)
        assert actor["lease_token"] == 7
        assert actor["stage"] == "degraded"
        assert actor["error"] == "scan_source_unavailable"
    finally:
        await _delete_cabinet_runtime(pg_engine, account_id)


@pytest.mark.asyncio
async def test_operator_scan_state_isolates_cabinet_freshness_outcome_and_schedule(
    pg_engine,
) -> None:
    suffix = int(uuid.uuid4().hex[:12], 16)
    account_a = str(suffix % 8_000_000_000 + 1_000_000_000)
    account_b = str((suffix + 1) % 8_000_000_000 + 1_000_000_000)
    observed_at = datetime.now(UTC).replace(microsecond=0)
    scan_a_at = observed_at - timedelta(minutes=2)
    scan_b_at = observed_at - timedelta(minutes=1)
    next_a_at = observed_at + timedelta(minutes=10)
    next_b_at = observed_at + timedelta(minutes=1)
    account_ids = [account_a, account_b]

    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO cabinet_runtime (
                        ad_account_id, lease_token, stage, next_scan_at,
                        last_progress_at, last_snapshot_at, last_error_code
                    )
                    VALUES
                      (:account_a, 1, 'idle', :next_a_at, :scan_a_at, :scan_a_at, NULL),
                      (:account_b, 1, 'error', :next_b_at, :scan_b_at, :scan_b_at, 'foreign_error')
                    """
                ),
                {
                    "account_a": account_a,
                    "account_b": account_b,
                    "scan_a_at": scan_a_at,
                    "scan_b_at": scan_b_at,
                    "next_a_at": next_a_at,
                    "next_b_at": next_b_at,
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO scan_runs (
                        scan_id, started_at, finished_at, outcome, ad_account_id
                    )
                    VALUES
                      (
                        nextval('scan_runs_id_seq'), :scan_a_at, :scan_a_at,
                        'success', :account_a
                      ),
                      (
                        nextval('scan_runs_id_seq'), :scan_b_at, :scan_b_at,
                        'error', :account_b
                      )
                    """
                ),
                {
                    "account_a": account_a,
                    "account_b": account_b,
                    "scan_a_at": scan_a_at,
                    "scan_b_at": scan_b_at,
                },
            )

        state = await fetch_operator_scan_state(pg_engine, account_id=account_a)

        assert state["last_scan_at"] == scan_a_at
        assert state["last_scan_outcome"] == "success"
        assert state["next_scan_at"] == next_a_at
        assert [actor["ad_account_id"] for actor in state["actors"]] == [account_a]
        assert all(actor["error"] != "foreign_error" for actor in state["actors"])
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM scan_runs WHERE ad_account_id = ANY(CAST(:account_ids AS text[]))"
                ),
                {"account_ids": account_ids},
            )
            await conn.execute(
                text(
                    "DELETE FROM cabinet_runtime "
                    "WHERE ad_account_id = ANY(CAST(:account_ids AS text[]))"
                ),
                {"account_ids": account_ids},
            )


@pytest.mark.asyncio
async def test_operator_revision_advances_after_committed_write(pg_engine) -> None:
    account_id = f"revision_{uuid.uuid4().hex[:16]}"
    progress_at = datetime.now(UTC) + timedelta(days=30)
    snapshot_at = progress_at + timedelta(seconds=1)
    try:
        await _ensure_operator_revision_row(pg_engine)
        before_sequence, before_revision = await fetch_operator_revision(pg_engine)
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO cabinet_runtime (
                        ad_account_id, lease_token, stage,
                        last_progress_at, last_snapshot_at
                    )
                    VALUES (:account_id, 1, 'idle', :progress_at, :snapshot_at)
                    """
                ),
                {
                    "account_id": account_id,
                    "progress_at": progress_at,
                    "snapshot_at": snapshot_at,
                },
            )
            # Also exercise the append-only event path used by migration-owned
            # triggers. The public cursor itself is commit-visible PostgreSQL WAL.
            await conn.execute(
                text(
                    """
                    INSERT INTO operator_revision_events (scope, event_id)
                    VALUES ('cabinet', :event_id)
                    """
                ),
                {"event_id": account_id},
            )

        sequence, revision = await fetch_operator_revision(pg_engine)

        assert sequence > before_sequence
        assert revision != before_revision
        assert revision == f"r{sequence:x}"
    finally:
        await _delete_cabinet_runtime(pg_engine, account_id)


@pytest.mark.asyncio
async def test_operator_money_sections_are_partial_when_cabinet_timezone_unknown(
    pg_engine,
) -> None:
    suffix = uuid.uuid4().hex[:10]
    account_id = str(int(suffix, 16) % 9_000_000_000 + 1_000_000_000)
    offer_id, campaign_id, adset_id, ad_id = (uuid.uuid4() for _ in range(4))
    now = datetime.now(UTC)
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO offers (id, code, name) VALUES (:id, :code, :name)"),
                {"id": offer_id, "code": f"OPTZ_{suffix}", "name": "Operator timezone"},
            )
            await conn.execute(
                text(
                    "INSERT INTO offer_rules "
                    "(offer_id, cpa_threshold, currency, stop_percent_of_rule) "
                    "VALUES (:id, 10, 'USD', 80)"
                ),
                {"id": offer_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO fb_campaigns "
                    "(id, ad_account_id, campaign_name, offer_id) "
                    "VALUES (:id, :account_id, :name, :offer_id)"
                ),
                {
                    "id": campaign_id,
                    "account_id": account_id,
                    "name": f"OPTZ_CMP_{suffix}",
                    "offer_id": offer_id,
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO fb_adsets (id, campaign_id, adset_name) "
                    "VALUES (:id, :campaign_id, :name)"
                ),
                {"id": adset_id, "campaign_id": campaign_id, "name": f"OPTZ_SET_{suffix}"},
            )
            await conn.execute(
                text(
                    "INSERT INTO fb_ads "
                    "(id, adset_id, fb_ad_id, ad_name, first_seen_at, last_seen_at) "
                    "VALUES (:id, :adset_id, :fb_id, :name, :first_seen_at, :last_seen_at)"
                ),
                {
                    "id": ad_id,
                    "adset_id": adset_id,
                    "fb_id": f"optz_{suffix}",
                    "name": f"OPTZ_AD_{suffix}",
                    "first_seen_at": now - timedelta(seconds=2),
                    "last_seen_at": now,
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO ad_metrics "
                    "(id, ad_id, cycle_ts, spend, impressions, clicks, leads) "
                    "VALUES (gen_random_uuid(), :ad_id, :ts, 12, 100, 10, 2)"
                ),
                {"ad_id": ad_id, "ts": now - timedelta(seconds=1)},
            )
            await conn.execute(
                text(
                    "INSERT INTO meta_account_snapshot "
                    "(account_id, timezone_name, currency, currency_observed_at) "
                    "VALUES (:account_id, 'Invalid/Timezone', 'USD', NOW())"
                ),
                {"account_id": account_id},
            )

        economy, funnel, _, _, _, cabinet_days = await _analytics_sections(
            engine=pg_engine,
            account_id=account_id,
            window_name="today",
            now=now,
        )

        assert cabinet_days.timezone_known is False
        assert cabinet_days.missing_account_ids == (account_id,)
        assert economy.state == DataState.PARTIAL
        assert any(issue.code == "cabinet_timezone_unknown" for issue in economy.issues)
        assert funnel.state == DataState.PARTIAL
        assert any(issue.code == "cabinet_timezone_unknown" for issue in funnel.issues)

        recent_event_at = now - timedelta(seconds=120)
        stale_tracker_at = now - timedelta(seconds=1_200)
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE meta_account_snapshot SET timezone_name = 'UTC' "
                    "WHERE account_id = :account_id"
                ),
                {"account_id": account_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO tracker_click_state "
                    "(id, source, click_id, ad_id, fb_ad_id, attribution_status, "
                    "registration, ftd, confirmed_deposit, registration_at, last_event_at) "
                    "VALUES (gen_random_uuid(), 'adsetpro', :click_id, :ad_id, :fb_ad_id, "
                    "'matched_direct', true, false, false, :ts, :ts)"
                ),
                {
                    "click_id": f"optz_click_{suffix}",
                    "ad_id": ad_id,
                    "fb_ad_id": f"optz_{suffix}",
                    "ts": recent_event_at,
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO adsetpro_postback_events "
                    "(received_at, occurred_at, click_id, fb_ad_id, fb_ad_fk, event_type, "
                    "raw_json, signature_valid, is_duplicate, attribution_status) "
                    "VALUES (:ts, :ts, :click_id, :fb_ad_id, :ad_id, 'registration', "
                    "'{}'::jsonb, true, false, 'matched_direct')"
                ),
                {
                    "ts": recent_event_at,
                    "click_id": f"optz_postback_{suffix}",
                    "fb_ad_id": f"optz_{suffix}",
                    "ad_id": ad_id,
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO system_config (key, value, description) "
                    "VALUES ("
                    "'tracker_provider_reconciliation', "
                    "jsonb_build_object("
                    "'status', 'ok', "
                    "'checked_at', CAST(:checked_at AS text), "
                    "'window_start', CAST(:window_start AS text), "
                    "'window_end', CAST(:window_end AS text), "
                    "'drift_after', 0, "
                    "'skipped', 0"
                    "), "
                    "'Operator analytics provider audit'"
                    ") "
                    "ON CONFLICT (key) DO UPDATE SET "
                    "value = EXCLUDED.value, updated_at = NOW()"
                ),
                {
                    "checked_at": stale_tracker_at.isoformat(),
                    "window_start": (now - timedelta(days=2)).isoformat(),
                    "window_end": now.isoformat(),
                },
            )

        _, stale_funnel, _, _, _, known_cabinet_days = await _analytics_sections(
            engine=pg_engine,
            account_id=account_id,
            window_name="today",
            now=now,
        )

        assert known_cabinet_days.timezone_known is True
        assert stale_funnel.state == DataState.STALE
        assert stale_funnel.as_of == stale_tracker_at
        assert stale_funnel.freshness_seconds == 1_200
        assert any(issue.code == "funnel_source_stale" for issue in stale_funnel.issues)
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM adsetpro_postback_events WHERE fb_ad_fk = :id"),
                {"id": ad_id},
            )
            await conn.execute(
                text("DELETE FROM tracker_click_state WHERE ad_id = :id"), {"id": ad_id}
            )
            await conn.execute(
                text("DELETE FROM system_config WHERE key = 'tracker_provider_reconciliation'")
            )
            await conn.execute(text("DELETE FROM ad_metrics WHERE ad_id = :id"), {"id": ad_id})
            await conn.execute(text("DELETE FROM fb_ads WHERE id = :id"), {"id": ad_id})
            await conn.execute(text("DELETE FROM fb_adsets WHERE id = :id"), {"id": adset_id})
            await conn.execute(text("DELETE FROM fb_campaigns WHERE id = :id"), {"id": campaign_id})
            await conn.execute(
                text("DELETE FROM meta_account_snapshot WHERE account_id = :id"),
                {"id": account_id},
            )
            await conn.execute(text("DELETE FROM offers WHERE id = :id"), {"id": offer_id})


@pytest.mark.asyncio
async def test_operator_ads_severity_filter_matches_returned_row_freshness(
    pg_engine,
) -> None:
    suffix = uuid.uuid4().hex[:10]
    account_id = str(int(suffix, 16) % 9_000_000_000 + 1_000_000_000)
    campaign_id, adset_id = uuid.uuid4(), uuid.uuid4()
    fresh_ad_id, partial_ad_id, stale_ad_id, unavailable_ad_id = (uuid.uuid4() for _ in range(4))
    now = datetime.now(UTC)
    fresh_at = now - timedelta(seconds=5)
    stale_at = now - timedelta(minutes=5)
    ad_ids = (fresh_ad_id, partial_ad_id, stale_ad_id, unavailable_ad_id)
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO fb_campaigns "
                    "(id, ad_account_id, campaign_name) "
                    "VALUES (:id, :account_id, :name)"
                ),
                {
                    "id": campaign_id,
                    "account_id": account_id,
                    "name": f"OPADS_CMP_{suffix}",
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO fb_adsets (id, campaign_id, adset_name) "
                    "VALUES (:id, :campaign_id, :name)"
                ),
                {"id": adset_id, "campaign_id": campaign_id, "name": f"OPADS_SET_{suffix}"},
            )
            for label, ad_id in zip(("fresh", "partial", "stale", "missing"), ad_ids, strict=True):
                await conn.execute(
                    text(
                        "INSERT INTO fb_ads "
                        "(id, adset_id, fb_ad_id, ad_name, last_seen_at) "
                        "VALUES (:id, :adset_id, :fb_id, :name, :last_seen_at)"
                    ),
                    {
                        "id": ad_id,
                        "adset_id": adset_id,
                        "fb_id": f"opads_{label}_{suffix}",
                        "name": f"OPADS_{label}_{suffix}",
                        "last_seen_at": now,
                    },
                )
            for ad_id, cycle_ts in ((fresh_ad_id, fresh_at), (stale_ad_id, stale_at)):
                await conn.execute(
                    text(
                        "INSERT INTO ad_metrics "
                        "(id, ad_id, cycle_ts, spend, impressions, clicks) "
                        "VALUES (gen_random_uuid(), :ad_id, :cycle_ts, 12, 100, 10)"
                    ),
                    {"ad_id": ad_id, "cycle_ts": cycle_ts},
                )
            await conn.execute(
                text(
                    "INSERT INTO ad_metrics "
                    "(id, ad_id, cycle_ts, spend, impressions, clicks) "
                    "VALUES (gen_random_uuid(), :ad_id, :cycle_ts, NULL, 100, 10)"
                ),
                {"ad_id": partial_ad_id, "cycle_ts": fresh_at},
            )

        query = {
            "from_dt": now - timedelta(hours=1),
            "to_dt": now + timedelta(seconds=1),
            "account_id": account_id,
            "search": None,
            "delivery_status": None,
            "sort": "updated",
            "direction": "desc",
            "page": 1,
            "page_size": 50,
            "tracker_available": False,
        }
        all_rows = await fetch_operator_ads(pg_engine, severity=None, **query)
        assert all_rows["total"] == 4
        assert all_rows["row_state"] == "partial"
        assert all_rows["as_of"] == stale_at

        ok_rows = await fetch_operator_ads(pg_engine, severity="ok", **query)
        assert ok_rows["total"] == 1
        # A filter may narrow the visible rows, but it must never hide degraded
        # source completeness and turn the collection false-green.
        assert ok_rows["row_state"] == "partial"
        assert [(row["data_state"], row["severity"]) for row in ok_rows["rows"]] == [
            ("ready", "ok")
        ]

        unknown_rows = await fetch_operator_ads(pg_engine, severity="unknown", **query)
        assert unknown_rows["total"] == 3
        assert unknown_rows["row_state"] == "partial"
        assert {(row["data_state"], row["severity"]) for row in unknown_rows["rows"]} == {
            ("partial", "unknown"),
            ("stale", "unknown"),
            ("unavailable", "unknown"),
        }
        partial_row = next(row for row in unknown_rows["rows"] if row["data_state"] == "partial")
        assert partial_row["metrics"]["spend"] is None

        empty_offset_page = await fetch_operator_ads(
            pg_engine,
            severity=None,
            **{**query, "page": 2},
        )
        assert empty_offset_page["rows"] == []
        assert empty_offset_page["total"] == 4
        assert empty_offset_page["pages"] == 1
        assert empty_offset_page["row_state"] == "partial"
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM ad_metrics WHERE ad_id = ANY(:ad_ids)"),
                {"ad_ids": list(ad_ids)},
            )
            await conn.execute(
                text("DELETE FROM fb_ads WHERE id = ANY(:ad_ids)"),
                {"ad_ids": list(ad_ids)},
            )
            await conn.execute(text("DELETE FROM fb_adsets WHERE id = :id"), {"id": adset_id})
            await conn.execute(text("DELETE FROM fb_campaigns WHERE id = :id"), {"id": campaign_id})


class _ListenerConnection:
    def __init__(self) -> None:
        self.added = False
        self.removed = False

    async def add_listener(self, channel, callback) -> None:
        assert channel == "fb_operator_events"
        self.added = True

    async def remove_listener(self, channel, callback) -> None:
        assert channel == "fb_operator_events"
        self.removed = True


class _WebSocket:
    def __init__(self, connection: _ListenerConnection) -> None:
        self.headers = {}
        self.query_params = {}
        self.app = SimpleNamespace(state=SimpleNamespace(operator_pg_connection=connection))
        self.accepted = False
        self.closed_with: int | None = None
        self.messages: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int) -> None:
        self.closed_with = code

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)
        if len(self.messages) > 1:
            raise WebSocketDisconnect()


@pytest.mark.asyncio
async def test_operator_ws_accepts_after_real_revision_query(
    pg_engine,
    monkeypatch,
) -> None:
    await _ensure_operator_revision_row(pg_engine)
    connection = _ListenerConnection()
    websocket = _WebSocket(connection)
    monkeypatch.setattr(ws_router, "_authorize_websocket", AsyncMock(return_value=True))
    monkeypatch.setattr(ws_router, "_HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(core.db, "get_engine", lambda: pg_engine)
    monkeypatch.setattr(
        core.config,
        "get_settings",
        lambda: SimpleNamespace(database_url="postgresql+asyncpg://unused"),
    )

    await ws_router.ws_operator(websocket)  # type: ignore[arg-type]

    assert connection.added is True
    assert connection.removed is True
    assert websocket.accepted is True
    assert websocket.closed_with is None
    assert websocket.messages[0]["type"] == "snapshot_required"
    assert websocket.messages[0]["sequence"] == 1
    assert websocket.messages[0]["snapshot_revision"]


@pytest.mark.asyncio
async def test_operator_ws_closes_after_tma_owner_role_is_downgraded(
    pg_engine,
    monkeypatch,
) -> None:
    await _ensure_operator_revision_row(pg_engine)
    connection = _ListenerConnection()
    websocket = _WebSocket(connection)
    websocket.headers = {"sec-websocket-protocol": "fb-operator-v1, tma.signed.session"}
    monkeypatch.setattr(ws_router, "_authorize_websocket", AsyncMock(return_value=True))
    revalidate = AsyncMock(return_value=False)
    monkeypatch.setattr(ws_router, "_validate_tma_websocket_session", revalidate)
    monkeypatch.setattr(ws_router, "_HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(core.db, "get_engine", lambda: pg_engine)
    monkeypatch.setattr(
        core.config,
        "get_settings",
        lambda: SimpleNamespace(database_url="postgresql+asyncpg://unused"),
    )

    await ws_router.ws_operator(websocket)  # type: ignore[arg-type]

    assert websocket.accepted is True
    assert websocket.closed_with == 1008
    assert len(websocket.messages) == 1
    assert websocket.messages[0]["type"] == "snapshot_required"
    revalidate.assert_awaited_once()
    assert connection.removed is True
