# -*- coding: utf-8 -*-
"""Unit-тесты подключения autostop-алерта в meta_api_worker.process_one_task.

Сценарий: auto-stop pause_ad ловит «канал мёртв» (code=-2) → воркер дёргает CRITICAL-
детектор; успех auto-stop сбрасывает счётчик; не-autostop фейл алерт НЕ триггерит.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as meta
from core.meta_api.errors import TemporaryError


def _autostop_task(**over) -> SimpleNamespace:
    base = dict(
        id=42,
        task_type="meta_api_mutation",
        payload={"mutation_kind": "pause_ad", "target_id": "123"},
        attempt_count=5,
        max_attempts=72,
        requested_by="bot_auto_stop",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _alert_ctx(tg=None):  # noqa: ARG001 — tg больше не используется в AutostopAlertContext
    return meta.AutostopAlertContext(
        engine=object(),
        threshold=3,
        window_seconds=1800,
        dedup_ttl_seconds=1800,
    )


# auto-stop pause_ad + code=-2 (канал мёртв) → CRITICAL-детектор вызван с ad_id и ошибкой
@pytest.mark.asyncio
async def test_autostop_channel_down_triggers_alert(monkeypatch) -> None:
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    err = TemporaryError("Failed to fetch", code=-2)
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(side_effect=err))
    monkeypatch.setattr(meta, "requeue_task", AsyncMock(return_value=True))
    spy_alert = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "maybe_alert_autostop_channel_down", spy_alert)
    redis = AsyncMock()

    await meta.process_one_task(
        object(), _autostop_task(), client=AsyncMock(), redis_client=redis, alert_ctx=_alert_ctx()
    )

    spy_alert.assert_awaited_once()
    kwargs = spy_alert.await_args.kwargs
    assert kwargs["fb_ad_id"] == "123"
    assert kwargs["exc"] is err
    assert kwargs["threshold"] == 3


# Успех auto-stop → счётчик подряд-фейлов сбрасывается (канал ожил)
@pytest.mark.asyncio
async def test_autostop_success_resets_counter(monkeypatch) -> None:
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(return_value={"success": True}))
    monkeypatch.setattr(meta, "mark_task_succeeded", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "sync_fsm_after_mutation", AsyncMock())
    spy_reset = AsyncMock()
    monkeypatch.setattr(meta, "record_autostop_success", spy_reset)
    redis = AsyncMock()

    await meta.process_one_task(
        object(), _autostop_task(), client=AsyncMock(), redis_client=redis, alert_ctx=_alert_ctx()
    )

    spy_reset.assert_awaited_once()


# Не-autostop pause_ad (ручной) с тем же отказом канала → CRITICAL-детектор НЕ вызывается
@pytest.mark.asyncio
async def test_non_autostop_failure_does_not_alert(monkeypatch) -> None:
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        meta, "execute_mutation", AsyncMock(side_effect=TemporaryError("Failed to fetch", code=-2))
    )
    monkeypatch.setattr(meta, "requeue_task", AsyncMock(return_value=True))
    spy_alert = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "maybe_alert_autostop_channel_down", spy_alert)
    redis = AsyncMock()

    await meta.process_one_task(
        object(),
        _autostop_task(requested_by="user"),
        client=AsyncMock(),
        redis_client=redis,
        alert_ctx=_alert_ctx(),
    )

    spy_alert.assert_not_awaited()
