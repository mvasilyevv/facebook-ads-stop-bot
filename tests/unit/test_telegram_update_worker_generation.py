from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.telegram_update_worker import main as worker
from core.telegram.update_inbox import ClaimedTelegramUpdate


@pytest.mark.asyncio
async def test_cached_gateway_mismatch_releases_lease_without_sixty_second_stall(
    monkeypatch,
) -> None:
    claim = ClaimedTelegramUpdate(
        bot_generation=8,
        update_id=991,
        payload={"callback_query": {"id": "cb"}},
        attempt_count=1,
        lease_token=uuid.uuid4(),
    )
    monkeypatch.setattr(worker, "claim_telegram_update", AsyncMock(return_value=claim))
    monkeypatch.setattr(
        worker,
        "telegram_update_claim_is_authoritative",
        AsyncMock(return_value=True),
    )

    @asynccontextmanager
    async def denied_authority(*_args, **_kwargs):
        yield False

    monkeypatch.setattr(worker, "hold_telegram_outbound_authority", denied_authority)
    retire = AsyncMock(return_value=False)
    release = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "retire_stale_telegram_update_claim", retire)
    monkeypatch.setattr(
        worker,
        "release_telegram_update_claim_for_gateway_refresh",
        release,
    )
    handler = AsyncMock()
    monkeypatch.setattr(worker, "handle_update", handler)
    gateway = SimpleNamespace(credential_fingerprint="0" * 64)
    engine = object()

    result = await worker.process_one_update(
        engine,
        gateway=gateway,
        worker_id="stale-gateway",
    )

    assert result is None
    retire.assert_awaited_once_with(engine, claim=claim)
    release.assert_awaited_once_with(engine, claim=claim)
    handler.assert_not_awaited()


class _StopTestLoop(Exception):
    """Breaks out of run_worker's otherwise-infinite loop from inside a mock."""


@pytest.mark.asyncio
async def test_run_worker_marks_poll_success_without_a_configured_bot_token(
    monkeypatch,
) -> None:
    """Review issue #176 Л1: an unconfigured Telegram bot token is a separate,
    already-surfaced state. The loop's real recurring duty (config load, lease
    reconcile) still runs every pass regardless, and must still count as
    proof the worker is alive — not leave it permanently "not processing its
    queue" just because no token is saved yet.
    """

    async def _no_saved_config(*_args, **_kwargs):
        raise _StopTestLoop  # первый проход после отметки — этого достаточно

    monkeypatch.setattr(worker, "start_worker_metrics_server", lambda *_args: None)
    monkeypatch.setattr(worker, "load_telegram_config", _no_saved_config)
    heartbeat = AsyncMock()
    monkeypatch.setattr(worker, "record_worker_heartbeat", heartbeat)

    with pytest.raises(_StopTestLoop):
        await worker.run_worker(engine=object())

    heartbeat.assert_awaited_once()
    assert heartbeat.await_args.kwargs["poll_success"] is True
