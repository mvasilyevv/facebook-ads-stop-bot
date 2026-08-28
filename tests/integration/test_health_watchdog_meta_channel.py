# -*- coding: utf-8 -*-
"""Marketing API probe -> durable incident/notification integration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from apps.health_watchdog.main import (
    BROWSER_CONTRACT_VERSION,
    META_CHANNEL_INCIDENT_KEY,
    check_meta_api_channel,
)
from core.vision.channel_config import VisionChannelConfiguration


@dataclass
class FakeMetaClient:
    responses: list[dict]
    calls: int = 0

    async def check_health(
        self,
        *,
        full_probe: bool = False,
        expected_profile_id: str = "",
    ) -> dict:
        assert full_probe is True
        assert expected_profile_id == "vision-profile-1"
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


_OK_PROBE = {
    "healthy": True,
    "probe_performed": True,
    "probe_ok": True,
    "probe_status_code": 200,
    "probe_detail": "ok",
    "browser_contract_version": BROWSER_CONTRACT_VERSION,
    "vision_profile_id": "vision-profile-1",
}
_DOWN_PROBE = {
    "healthy": False,
    "probe_performed": True,
    "probe_ok": False,
    "probe_status_code": 0,
    "probe_detail": "probe_network_down",
}


class _NoopBrowserFence:
    """Harmless fence double for probe-only tests that intentionally have no DB."""

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def __aenter__(self) -> _NoopBrowserFence:
        return self

    async def assert_held(self) -> None:
        pass

    async def __aexit__(self, *_args) -> bool:
        return False


@pytest.fixture(autouse=True)
def _scanning_enabled(monkeypatch):
    from core.observer import queries as observer_queries

    monkeypatch.setattr(
        observer_queries,
        "load_observer_config",
        AsyncMock(return_value={"is_scanning_enabled": True}),
    )
    monkeypatch.setattr(
        "apps.health_watchdog.main._load_canonical_vision_profile_id",
        AsyncMock(return_value="vision-profile-1"),
    )
    # Канал настроен: эти тесты про поведение живой пробы, а тишину
    # ненастроенного канала проверяет test_health_watchdog_unconfigured_channel.
    monkeypatch.setattr(
        "apps.health_watchdog.main.load_vision_channel_configuration",
        AsyncMock(
            return_value=VisionChannelConfiguration(
                has_token=True,
                profile_id="vision-profile-1",
            )
        ),
    )


@pytest.fixture
def mock_browser_fence(monkeypatch):
    import apps.health_watchdog.main as watchdog

    monkeypatch.setattr(watchdog, "BrowserOperationFence", _NoopBrowserFence)


@pytest.mark.asyncio
async def test_probe_down_reaches_durable_notifier(mock_browser_fence) -> None:
    meta = FakeMetaClient([_DOWN_PROBE])
    notify = AsyncMock(return_value=True)

    with patch("apps.health_watchdog.main.notify_recurring_incident", notify):
        alerted = await check_meta_api_channel(meta, engine=MagicMock())

    assert alerted is True
    assert notify.await_args.kwargs["event_type"] == "meta_channel_unavailable"
    assert notify.await_args.kwargs["severity"] == "critical"
    assert notify.await_args.kwargs["incident_key"] == META_CHANNEL_INCIDENT_KEY


@pytest.mark.asyncio
async def test_probe_reuses_durable_event_key(mock_browser_fence) -> None:
    meta = FakeMetaClient([_DOWN_PROBE, _DOWN_PROBE])
    notify = AsyncMock(return_value=True)

    with patch("apps.health_watchdog.main.notify_recurring_incident", notify):
        first = await check_meta_api_channel(meta, engine=MagicMock())
        second = await check_meta_api_channel(meta, engine=MagicMock())

    assert first is True
    assert second is True
    assert notify.await_count == 2
    assert {call.kwargs["incident_key"] for call in notify.await_args_list} == {
        META_CHANNEL_INCIDENT_KEY
    }


@pytest.mark.asyncio
async def test_probe_ok_does_not_notify(mock_browser_fence) -> None:
    notify = AsyncMock(return_value=True)
    resolve = AsyncMock(return_value=True)

    with (
        patch("apps.health_watchdog.main.notify_recurring_incident", notify),
        patch("apps.health_watchdog.main.resolve_recurring_incident", resolve),
    ):
        alerted = await check_meta_api_channel(
            FakeMetaClient([_OK_PROBE]),
            engine=MagicMock(),
        )

    assert alerted is False
    notify.assert_not_awaited()
    resolve.assert_awaited_once()
    assert resolve.await_args.kwargs["incident_key"] == META_CHANNEL_INCIDENT_KEY


@pytest.mark.asyncio
async def test_probe_recovery_then_failure_notifies_again(mock_browser_fence) -> None:
    meta = FakeMetaClient([_DOWN_PROBE, _OK_PROBE, _DOWN_PROBE])
    notify = AsyncMock(return_value=True)
    resolve = AsyncMock(return_value=True)

    with (
        patch("apps.health_watchdog.main.notify_recurring_incident", notify),
        patch("apps.health_watchdog.main.resolve_recurring_incident", resolve),
    ):
        results = [
            await check_meta_api_channel(meta, engine=MagicMock()),
            await check_meta_api_channel(meta, engine=MagicMock()),
            await check_meta_api_channel(meta, engine=MagicMock()),
        ]

    assert results == [True, False, True]
    assert notify.await_count == 2
    resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_exception_is_treated_as_down(mock_browser_fence) -> None:
    class BoomClient:
        async def check_health(
            self,
            *,
            full_probe: bool = False,
            expected_profile_id: str = "",
        ) -> dict:
            raise RuntimeError("gRPC boom")

    notify = AsyncMock(return_value=True)
    with patch("apps.health_watchdog.main.notify_recurring_incident", notify):
        alerted = await check_meta_api_channel(BoomClient(), engine=MagicMock())

    assert alerted is True
    notify.assert_awaited_once()


async def _cleanup_repeated_outage_resources(
    pg_engine,
    *,
    incident_key: str,
    recipient_id: uuid.UUID,
) -> None:
    """Delete the random test graph without relying on a completed readback."""
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                DELETE FROM telegram_message_slots
                WHERE incident_id IN (
                    SELECT id FROM incidents WHERE incident_key = :key
                )
                """
            ),
            {"key": incident_key},
        )
        await conn.execute(
            text(
                """
                DELETE FROM notification_deliveries
                WHERE event_id IN (
                    SELECT event.id
                    FROM notification_events event
                    JOIN incidents incident ON incident.id = event.incident_id
                    WHERE incident.incident_key = :key
                )
                """
            ),
            {"key": incident_key},
        )
        await conn.execute(
            text(
                """
                DELETE FROM notification_events
                WHERE incident_id IN (
                    SELECT id FROM incidents WHERE incident_key = :key
                )
                """
            ),
            {"key": incident_key},
        )
        await conn.execute(
            text("DELETE FROM incidents WHERE incident_key = :key"),
            {"key": incident_key},
        )
        await conn.execute(
            text("DELETE FROM telegram_recipients WHERE id = :id"),
            {"id": recipient_id},
        )


@pytest.mark.asyncio
async def test_repeated_outage_commits_one_event_and_delivery(
    pg_engine,
    monkeypatch,
    authoritative_telegram_config,
) -> None:
    """Two probe ticks collapse transactionally in PostgreSQL."""
    import apps.health_watchdog.main as watchdog

    incident_key = f"test:meta:{uuid.uuid4()}"
    monkeypatch.setattr(watchdog, "META_CHANNEL_INCIDENT_KEY", incident_key)
    recipient_id = uuid.uuid4()
    suffix = uuid.uuid4().int % 1_000_000_000

    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients (id, chat_id, telegram_user_id, role)
                    VALUES (:id, :chat_id, :user_id, 'owner')
                    """
                ),
                {
                    "id": recipient_id,
                    "chat_id": 7_000_000_000 + suffix,
                    "user_id": 6_000_000_000 + suffix,
                },
            )

        meta = FakeMetaClient([_DOWN_PROBE, _DOWN_PROBE])
        assert await check_meta_api_channel(meta, engine=pg_engine) is True
        assert await check_meta_api_channel(meta, engine=pg_engine) is True

        async with pg_engine.connect() as conn:
            incident_ids = list(
                await conn.scalars(
                    text("SELECT id FROM incidents WHERE incident_key = :key"),
                    {"key": incident_key},
                )
            )
            event_ids = list(
                await conn.scalars(
                    text(
                        """
                        SELECT event.id
                        FROM notification_events event
                        JOIN incidents incident ON incident.id = event.incident_id
                        WHERE incident.incident_key = :key
                        """
                    ),
                    {"key": incident_key},
                )
            )
            deliveries = await conn.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM notification_deliveries
                    WHERE recipient_id = :recipient_id
                      AND event_id = ANY(CAST(:event_ids AS uuid[]))
                    """
                ),
                {"recipient_id": recipient_id, "event_ids": event_ids},
            )

        assert len(incident_ids) == 1
        assert len(event_ids) == 1
        assert deliveries == 1
    finally:
        await _cleanup_repeated_outage_resources(
            pg_engine,
            incident_key=incident_key,
            recipient_id=recipient_id,
        )


@pytest.mark.asyncio
async def test_repeated_outage_cleanup_survives_intermediate_failure(
    pg_engine,
    monkeypatch,
    authoritative_telegram_config,
) -> None:
    import apps.health_watchdog.main as watchdog

    incident_key = f"test:meta:cleanup:{uuid.uuid4()}"
    monkeypatch.setattr(watchdog, "META_CHANNEL_INCIDENT_KEY", incident_key)
    recipient_id = uuid.uuid4()
    suffix = uuid.uuid4().int % 1_000_000_000
    incident_id = None
    event_id = None
    with pytest.raises(RuntimeError, match="simulated intermediate readback failure"):
        try:
            async with pg_engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        INSERT INTO telegram_recipients
                            (id, chat_id, telegram_user_id, role)
                        VALUES (:id, :chat_id, :user_id, 'owner')
                        """
                    ),
                    {
                        "id": recipient_id,
                        "chat_id": 7_500_000_000 + suffix,
                        "user_id": 6_500_000_000 + suffix,
                    },
                )
            assert (
                await check_meta_api_channel(
                    FakeMetaClient([_DOWN_PROBE]),
                    engine=pg_engine,
                )
                is True
            )
            async with pg_engine.connect() as conn:
                incident_id = await conn.scalar(
                    text("SELECT id FROM incidents WHERE incident_key = :key"),
                    {"key": incident_key},
                )
                event_id = await conn.scalar(
                    text("SELECT id FROM notification_events WHERE incident_id = :incident_id"),
                    {"incident_id": incident_id},
                )
            raise RuntimeError("simulated intermediate readback failure")
        finally:
            await _cleanup_repeated_outage_resources(
                pg_engine,
                incident_key=incident_key,
                recipient_id=recipient_id,
            )

    assert incident_id is not None
    assert event_id is not None
    async with pg_engine.connect() as conn:
        residue = (
            await conn.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM incidents WHERE id = :incident_id),
                        (SELECT COUNT(*) FROM notification_events WHERE id = :event_id),
                        (SELECT COUNT(*) FROM telegram_recipients WHERE id = :recipient_id)
                    """
                ),
                {
                    "incident_id": incident_id,
                    "event_id": event_id,
                    "recipient_id": recipient_id,
                },
            )
        ).one()
    assert tuple(residue) == (0, 0, 0)


@pytest.mark.asyncio
async def test_probe_recovery_closes_generation_and_next_failure_reopens(
    pg_engine,
    monkeypatch,
) -> None:
    import apps.health_watchdog.main as watchdog

    incident_key = f"test:meta:lifecycle:{uuid.uuid4()}"
    monkeypatch.setattr(watchdog, "META_CHANNEL_INCIDENT_KEY", incident_key)
    meta = FakeMetaClient([_DOWN_PROBE, _OK_PROBE, _DOWN_PROBE])

    try:
        assert await check_meta_api_channel(meta, engine=pg_engine) is True
        assert await check_meta_api_channel(meta, engine=pg_engine) is False
        assert await check_meta_api_channel(meta, engine=pg_engine) is True

        async with pg_engine.connect() as conn:
            generations = (
                await conn.execute(
                    text(
                        """
                        SELECT generation, status
                        FROM incidents
                        WHERE incident_key = :key
                        ORDER BY generation
                        """
                    ),
                    {"key": incident_key},
                )
            ).all()
            lifecycle_events = await conn.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM notification_events event
                    JOIN incidents incident ON incident.id = event.incident_id
                    WHERE incident.incident_key = :key
                    """
                ),
                {"key": incident_key},
            )

        assert generations == [(1, "resolved"), (2, "open")]
        assert lifecycle_events == 3
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    DELETE FROM notification_deliveries
                    WHERE event_id IN (
                        SELECT event.id
                        FROM notification_events event
                        JOIN incidents incident ON incident.id = event.incident_id
                        WHERE incident.incident_key = :key
                    )
                    """
                ),
                {"key": incident_key},
            )
            await conn.execute(
                text(
                    """
                    DELETE FROM notification_events
                    WHERE incident_id IN (SELECT id FROM incidents WHERE incident_key = :key)
                    """
                ),
                {"key": incident_key},
            )
            await conn.execute(
                text("DELETE FROM incidents WHERE incident_key = :key"),
                {"key": incident_key},
            )
