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
