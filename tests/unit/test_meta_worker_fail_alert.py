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


# ===== Тесты ветки exhausted ValueError / unknown Exception в process_one_task =====

from types import SimpleNamespace  # noqa: E402


def _task(attempt_count=10, max_attempts=10, **kwargs):
    """Задача с исчерпанными ретраями (attempt_count == max_attempts)."""
    base = dict(
        id=99,
        task_type="meta_api_mutation",
        payload={"mutation_kind": "pause_ad", "target_id": "777"},
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        requested_by="bot_auto_stop",
        next_retry_at=None,
        last_error=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _mock_ownership(allowed=True):
    return SimpleNamespace(allowed=allowed, not_found=False, reason="", foreign_ids=[])


# pause_ad с ValueError (исчерпаны ретраи) → _alert_money_fail / notify_owners вызван
@pytest.mark.asyncio
async def test_pause_ad_value_error_exhausted_alerts(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(mw, "notify_owners", spy)
    monkeypatch.setattr(mw, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(mw, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(mw, "check_mutation_ownership", AsyncMock(return_value=_mock_ownership()))
    # Эмулируем ValueError из postprocess (не _IRREVERSIBLE_KINDS → requeue)
    monkeypatch.setattr(mw, "execute_mutation", AsyncMock(side_effect=ValueError("bad parse")))
    # Ретраи исчерпаны → requeue_task вернёт False (нет попыток)
    monkeypatch.setattr(mw, "requeue_task", AsyncMock(return_value=False))

    await mw.process_one_task(object(), _task(), client=AsyncMock(), redis_client=AsyncMock())

    spy.assert_awaited_once()
    kw = spy.await_args.kwargs
    assert "777" in kw["text"]


# pause_ad с произвольным Exception (unknown, исчерпаны ретраи) → _alert_money_fail / notify_owners вызван
@pytest.mark.asyncio
async def test_pause_ad_unknown_exception_exhausted_alerts(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(mw, "notify_owners", spy)
    monkeypatch.setattr(mw, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(mw, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(mw, "check_mutation_ownership", AsyncMock(return_value=_mock_ownership()))
    # Неклассифицированная ошибка → попадает в `except Exception`
    monkeypatch.setattr(mw, "execute_mutation", AsyncMock(side_effect=RuntimeError("unexpected")))
    # Ретраи исчерпаны → requeue_task вернёт False
    monkeypatch.setattr(mw, "requeue_task", AsyncMock(return_value=False))

    await mw.process_one_task(object(), _task(), client=AsyncMock(), redis_client=AsyncMock())

    spy.assert_awaited_once()
    kw = spy.await_args.kwargs
    assert "777" in kw["text"]
