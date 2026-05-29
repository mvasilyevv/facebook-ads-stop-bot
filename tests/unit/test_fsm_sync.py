# -*- coding: utf-8 -*-
"""Unit: FSM-синхронизация после Marketing API mutation (core/meta_api/fsm_sync.py).

Проверяем маршрутизацию mutation_kind → нужный reset, разбор обеих форм bulk-params
и best-effort (ошибка reset не пробрасывается). reset-функции мокаются — БД не нужна.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.meta_api.fsm_sync import _resolve_bulk_ad_toggle, sync_fsm_after_mutation
from core.meta_api.schemas import MetaMutationPayload


def _payload(
    kind: str, target_id: str = "23847001", params: dict | None = None
) -> MetaMutationPayload:
    return MetaMutationPayload(
        mutation_kind=kind,
        target_id=target_id,
        params=params or {},
        ad_account_id=None,
    )


# ====================== _resolve_bulk_ad_toggle ======================


# Сокращённая форма (drafts/autostart): action=pause → disable по всем ad_ids
def test_resolve_bulk_short_pause() -> None:
    ids, is_enable = _resolve_bulk_ad_toggle({"ad_ids": ["1", "2"], "action": "pause"})
    assert ids == ["1", "2"]
    assert is_enable is False


# Сокращённая форма: action=activate → enable
def test_resolve_bulk_short_activate() -> None:
    ids, is_enable = _resolve_bulk_ad_toggle({"ad_ids": ["3"], "action": "activate"})
    assert ids == ["3"]
    assert is_enable is True


# Сокращённая форма с мусорным action → не трогаем (пустой список)
def test_resolve_bulk_short_unknown_action_noop() -> None:
    ids, _ = _resolve_bulk_ad_toggle({"ad_ids": ["1"], "action": "delete"})
    assert ids == []


# Полная форма object_type=ad + PAUSED → disable
def test_resolve_bulk_full_ad_paused() -> None:
    ids, is_enable = _resolve_bulk_ad_toggle(
        {"object_ids": ["9", "10"], "status": "PAUSED", "object_type": "ad"}
    )
    assert ids == ["9", "10"]
    assert is_enable is False


# Полная форма object_type=ad + ACTIVE → enable
def test_resolve_bulk_full_ad_active() -> None:
    ids, is_enable = _resolve_bulk_ad_toggle(
        {"object_ids": ["9"], "status": "ACTIVE", "object_type": "ad"}
    )
    assert ids == ["9"]
    assert is_enable is True


# Полная форма object_type=campaign → НЕ трогаем ad_alert_state (нет такого state у кампаний)
def test_resolve_bulk_full_campaign_skipped() -> None:
    ids, _ = _resolve_bulk_ad_toggle(
        {"object_ids": ["9"], "status": "PAUSED", "object_type": "campaign"}
    )
    assert ids == []


# Полная форма object_type=adset → тоже пропускаем
def test_resolve_bulk_full_adset_skipped() -> None:
    ids, _ = _resolve_bulk_ad_toggle(
        {"object_ids": ["9"], "status": "PAUSED", "object_type": "adset"}
    )
    assert ids == []


# ====================== sync_fsm_after_mutation: маршрутизация ======================


# pause_ad → вызывается disable-reset c target_id, enable-reset НЕ вызывается
@pytest.mark.asyncio
async def test_sync_pause_ad_calls_disable_reset(monkeypatch) -> None:
    disable_mock = AsyncMock(return_value=True)
    enable_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "core.meta_api.fsm_sync.reset_alert_state_after_disable_succeeded", disable_mock
    )
    monkeypatch.setattr(
        "core.meta_api.fsm_sync.reset_alert_state_after_enable_succeeded", enable_mock
    )

    await sync_fsm_after_mutation(object(), _payload("pause_ad", target_id="555"))

    disable_mock.assert_awaited_once()
    assert disable_mock.await_args.kwargs["fb_ad_id"] == "555"
    enable_mock.assert_not_awaited()


# activate_ad → вызывается enable-reset, disable-reset НЕ вызывается
@pytest.mark.asyncio
async def test_sync_activate_ad_calls_enable_reset(monkeypatch) -> None:
    disable_mock = AsyncMock(return_value=True)
    enable_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "core.meta_api.fsm_sync.reset_alert_state_after_disable_succeeded", disable_mock
    )
    monkeypatch.setattr(
        "core.meta_api.fsm_sync.reset_alert_state_after_enable_succeeded", enable_mock
    )

    await sync_fsm_after_mutation(object(), _payload("activate_ad", target_id="777"))

    enable_mock.assert_awaited_once()
    assert enable_mock.await_args.kwargs["fb_ad_id"] == "777"
    disable_mock.assert_not_awaited()


# bulk activate → enable-reset вызывается для каждого ad_id
@pytest.mark.asyncio
async def test_sync_bulk_activate_calls_enable_per_id(monkeypatch) -> None:
    disable_mock = AsyncMock(return_value=True)
    enable_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "core.meta_api.fsm_sync.reset_alert_state_after_disable_succeeded", disable_mock
    )
    monkeypatch.setattr(
        "core.meta_api.fsm_sync.reset_alert_state_after_enable_succeeded", enable_mock
    )

    await sync_fsm_after_mutation(
        object(),
        _payload(
            "bulk_status_change",
            target_id="bulk:3",
            params={"ad_ids": ["1", "2", "3"], "action": "activate"},
        ),
    )

    assert enable_mock.await_count == 3
    called_ids = {c.kwargs["fb_ad_id"] for c in enable_mock.await_args_list}
    assert called_ids == {"1", "2", "3"}
    disable_mock.assert_not_awaited()


# pause_campaign / set_adset_budget / create_campaign — ad_alert_state НЕ трогаем
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["pause_campaign", "activate_campaign", "set_adset_budget", "create_campaign"]
)
async def test_sync_non_ad_kinds_noop(monkeypatch, kind: str) -> None:
    disable_mock = AsyncMock(return_value=True)
    enable_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "core.meta_api.fsm_sync.reset_alert_state_after_disable_succeeded", disable_mock
    )
    monkeypatch.setattr(
        "core.meta_api.fsm_sync.reset_alert_state_after_enable_succeeded", enable_mock
    )

    await sync_fsm_after_mutation(object(), _payload(kind))

    disable_mock.assert_not_awaited()
    enable_mock.assert_not_awaited()


# Ошибка в reset не должна пробрасываться (best-effort, succeeded-контракт задачи важнее)
@pytest.mark.asyncio
async def test_sync_swallows_reset_error(monkeypatch) -> None:
    async def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr("core.meta_api.fsm_sync.reset_alert_state_after_disable_succeeded", _boom)

    # Не должно бросить наружу.
    await sync_fsm_after_mutation(object(), _payload("pause_ad"))
