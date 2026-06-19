# -*- coding: utf-8 -*-
"""observer_worker шлёт DM при sync OFF→disabled по списку из CycleResult."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import apps.observer_worker.main as ow


# Для каждого synced_offline_disabled — notify_owners с dedup sync_offline_disabled:{id}
@pytest.mark.asyncio
async def test_notifies_for_synced_disabled(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "notify_owners", spy)
    await ow._notify_synced_disabled(object(), AsyncMock(), fb_ad_ids=["100", "200"])
    assert spy.await_count == 2
    keys = {c.kwargs["dedup_key"] for c in spy.await_args_list}
    assert keys == {"sync_offline_disabled:100", "sync_offline_disabled:200"}
    assert spy.await_args_list[0].kwargs["dedup_ttl_seconds"] == 21600


# Пустой список → ничего не шлём
@pytest.mark.asyncio
async def test_empty_no_notify(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(ow, "notify_owners", spy)
    await ow._notify_synced_disabled(object(), AsyncMock(), fb_ad_ids=[])
    spy.assert_not_awaited()
