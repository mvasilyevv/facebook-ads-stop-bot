# -*- coding: utf-8 -*-
"""Финальный провал money-мутации (pause/permanent/partial) шлёт TG owner'ам."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as mw
from core.meta_api.schemas import MetaMutationPayload


def _payload(kind="pause_ad", target="12345"):
    return MetaMutationPayload(mutation_kind=kind, target_id=target, params={})


# Провал pause_ad (auto-stop) → notify_owners с money-текстом и dedup auto_stop_fail
@pytest.mark.asyncio
async def test_pause_fail_alerts_owner(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(mw, "notify_owners", spy)
    await mw._alert_money_fail(
        object(),
        AsyncMock(),
        payload=_payload(),
        requested_by="bot_auto_stop",
        error="PermanentError(code=368)",
        kind_label="pause_ad",
    )
    spy.assert_awaited_once()
    kw = spy.await_args.kwargs
    assert "12345" in kw["text"]
    assert kw["dedup_key"] == "auto_stop_fail:12345"
    assert kw["dedup_ttl_seconds"] == 3600


# Не-money-мутация (set_adset_budget) → НЕ алертим (не money-стоп)
@pytest.mark.asyncio
async def test_non_money_kind_no_alert(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(mw, "notify_owners", spy)
    await mw._alert_money_fail(
        object(),
        AsyncMock(),
        payload=_payload(kind="set_adset_budget"),
        requested_by="user",
        error="x",
        kind_label="set_adset_budget",
    )
    spy.assert_not_awaited()


# bulk_status_change (пауза) тоже является money-стопом → алертим
@pytest.mark.asyncio
async def test_bulk_pause_fail_alerts_owner(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(mw, "notify_owners", spy)
    await mw._alert_money_fail(
        object(),
        AsyncMock(),
        payload=_payload(kind="bulk_status_change", target="99999"),
        requested_by="user_manual",
        error="TokenInvalidError",
        kind_label="bulk_status_change",
    )
    spy.assert_awaited_once()
    kw = spy.await_args.kwargs
    assert "99999" in kw["text"]
    assert kw["dedup_key"] == "auto_stop_fail:99999"


# requested_by="user" (ручная пауза) → текст содержит «Пауза», не «Авто-стоп»
@pytest.mark.asyncio
async def test_manual_pause_actor_label(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(mw, "notify_owners", spy)
    await mw._alert_money_fail(
        object(),
        AsyncMock(),
        payload=_payload(),
        requested_by="user",
        error="SomeError",
        kind_label="pause_ad",
    )
    kw = spy.await_args.kwargs
    assert "Пауза" in kw["text"]
    assert "Авто-стоп" not in kw["text"]


# requested_by="bot_auto_stop" → текст содержит «Авто-стоп»
@pytest.mark.asyncio
async def test_autostop_actor_label(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(mw, "notify_owners", spy)
    await mw._alert_money_fail(
        object(),
        AsyncMock(),
        payload=_payload(),
        requested_by="bot_auto_stop",
        error="SomeError",
        kind_label="pause_ad",
    )
    kw = spy.await_args.kwargs
    assert "Авто-стоп" in kw["text"]
