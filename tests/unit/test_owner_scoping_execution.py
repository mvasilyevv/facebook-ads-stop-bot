# -*- coding: utf-8 -*-
"""Unit-тесты owner-scoping на ПУТИ ИСПОЛНЕНИЯ (защита чужих объявлений в шаренном кабинете).

Покрывают: вердикт check_mutation_ownership/check_ad_ownership по уровням целей,
bulk-семантику, NULL owner_tag (фильтр выключен), custom_audience/create_campaign,
а также маршрутизацию в meta_api_worker и toggle_executor (строгая fail-policy:
чужое → fail; своё-но-не-в-каталоге → выключающее requeue / включающее fail).
Все тесты без БД — резолверы и БД-операции замоканы (monkeypatch).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as meta
import core.meta_api.ownership as own
import core.tasks.toggle_executor as toggle
from core.meta_api.ownership import OwnershipDecision, check_mutation_ownership
from core.meta_api.schemas import MetaMutationPayload

# ====================== check_mutation_ownership: одиночные цели ======================


# pause_ad своего объявления (owner-тег в кампании) → разрешено
@pytest.mark.asyncio
async def test_mut_pause_ad_own(monkeypatch) -> None:
    monkeypatch.setattr(own, "_resolve_ad", AsyncMock(return_value=("CR2 | MV | GH", "ad1")))
    p = MetaMutationPayload(mutation_kind="pause_ad", target_id="123")
    d = await check_mutation_ownership(object(), p, owner_tag="MV")
    assert d.allowed is True


# pause_ad ЧУЖОГО объявления → отказ, not_found=False (точно чужое)
@pytest.mark.asyncio
async def test_mut_pause_ad_foreign(monkeypatch) -> None:
    monkeypatch.setattr(own, "_resolve_ad", AsyncMock(return_value=("CR2 | ABC | GH", "ad1")))
    p = MetaMutationPayload(mutation_kind="pause_ad", target_id="123")
    d = await check_mutation_ownership(object(), p, owner_tag="MV")
    assert d.allowed is False
    assert d.not_found is False


# pause_ad объявления, которого нет в каталоге → отказ, not_found=True (скан мог отстать)
@pytest.mark.asyncio
async def test_mut_pause_ad_not_found(monkeypatch) -> None:
    monkeypatch.setattr(own, "_resolve_ad", AsyncMock(return_value=None))
    p = MetaMutationPayload(mutation_kind="pause_ad", target_id="123")
    d = await check_mutation_ownership(object(), p, owner_tag="MV")
    assert d.allowed is False
    assert d.not_found is True


# activate_ad чужого → отказ (включающее тоже скоупится)
@pytest.mark.asyncio
async def test_mut_activate_ad_foreign(monkeypatch) -> None:
    monkeypatch.setattr(own, "_resolve_ad", AsyncMock(return_value=("X | ABC", "a")))
    p = MetaMutationPayload(mutation_kind="activate_ad", target_id="1")
    d = await check_mutation_ownership(object(), p, owner_tag="MV")
    assert d.allowed is False


# pause_campaign: своя кампания → allow, чужая → reject
@pytest.mark.asyncio
async def test_mut_campaign_own_and_foreign(monkeypatch) -> None:
    monkeypatch.setattr(own, "_resolve_campaign", AsyncMock(return_value="CR2 | MV"))
    p = MetaMutationPayload(mutation_kind="pause_campaign", target_id="c1")
    assert (await check_mutation_ownership(object(), p, owner_tag="MV")).allowed is True

    monkeypatch.setattr(own, "_resolve_campaign", AsyncMock(return_value="CR2 | ABC"))
    assert (await check_mutation_ownership(object(), p, owner_tag="MV")).allowed is False


# set_adset_budget: резолв adset→campaign, чужое → reject
@pytest.mark.asyncio
async def test_mut_adset_foreign(monkeypatch) -> None:
    monkeypatch.setattr(own, "_resolve_adset", AsyncMock(return_value="X | ABC"))
    p = MetaMutationPayload(
        mutation_kind="set_adset_budget", target_id="as1", params={"daily": 5000}
    )
    d = await check_mutation_ownership(object(), p, owner_tag="MV")
    assert d.allowed is False


# set_ad_creative резолвится как ad-level
@pytest.mark.asyncio
async def test_mut_set_ad_creative_own(monkeypatch) -> None:
    monkeypatch.setattr(own, "_resolve_ad", AsyncMock(return_value=("CR2 | MV", "a")))
    p = MetaMutationPayload(
        mutation_kind="set_ad_creative", target_id="1", params={"creative_id": "9"}
    )
    assert (await check_mutation_ownership(object(), p, owner_tag="MV")).allowed is True


# NULL/пустой owner_tag → разрешено всё, резолвер НЕ вызывается (фильтр выключен)
@pytest.mark.asyncio
async def test_mut_null_owner_tag_allows_without_resolve(monkeypatch) -> None:
    spy = AsyncMock()
    monkeypatch.setattr(own, "_resolve_ad", spy)
    p = MetaMutationPayload(mutation_kind="pause_ad", target_id="1")
    assert (await check_mutation_ownership(object(), p, owner_tag=None)).allowed is True
    assert (await check_mutation_ownership(object(), p, owner_tag="   ")).allowed is True
    spy.assert_not_awaited()


# custom_audience вне owner-scope → allow, резолвер не вызван
@pytest.mark.asyncio
async def test_mut_custom_audience_allowed(monkeypatch) -> None:
    spy = AsyncMock()
    monkeypatch.setattr(own, "_resolve_ad", spy)
    p = MetaMutationPayload(
        mutation_kind="custom_audience", target_id="", params={"subtype": "CUSTOM"}
    )
    assert (await check_mutation_ownership(object(), p, owner_tag="MV")).allowed is True
    spy.assert_not_awaited()


# create_campaign: owner-тег в имени → allow; без тега → reject
@pytest.mark.asyncio
async def test_mut_create_campaign_name_scoping() -> None:
    ok = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params={"campaign": {"name": "CR2 | MV | x"}},
    )
    assert (await check_mutation_ownership(object(), ok, owner_tag="MV")).allowed is True
    bad = MetaMutationPayload(
        mutation_kind="create_campaign", target_id="new", params={"campaign": {"name": "CR2 | ABC"}}
    )
    assert (await check_mutation_ownership(object(), bad, owner_tag="MV")).allowed is False


# duplicate_campaign: источник свой → allow, чужой → reject, не найден → reject not_found
@pytest.mark.asyncio
async def test_mut_duplicate_campaign(monkeypatch) -> None:
    p = MetaMutationPayload(
        mutation_kind="duplicate_campaign", target_id="src", params={"new_name": "n"}
    )
    monkeypatch.setattr(own, "_resolve_campaign", AsyncMock(return_value="CR2 | MV"))
    assert (await check_mutation_ownership(object(), p, owner_tag="MV")).allowed is True
    monkeypatch.setattr(own, "_resolve_campaign", AsyncMock(return_value="CR2 | ABC"))
    assert (await check_mutation_ownership(object(), p, owner_tag="MV")).allowed is False
    monkeypatch.setattr(own, "_resolve_campaign", AsyncMock(return_value=None))
    d = await check_mutation_ownership(object(), p, owner_tag="MV")
    assert d.allowed is False and d.not_found is True


# ====================== check_mutation_ownership: bulk ======================


# bulk pause, все id свои → allow
@pytest.mark.asyncio
async def test_bulk_all_own(monkeypatch) -> None:
    monkeypatch.setattr(
        own,
        "_resolve_ads_batch",
        AsyncMock(return_value={"1": ("CR2 | MV", "a1"), "2": ("X | MV", "a2")}),
    )
    p = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="b",
        params={"ad_ids": ["1", "2"], "action": "pause"},
    )
    assert (await check_mutation_ownership(object(), p, owner_tag="MV")).allowed is True


# bulk pause, один id чужой → reject всей задачи, foreign содержит чужой id
@pytest.mark.asyncio
async def test_bulk_one_foreign(monkeypatch) -> None:
    monkeypatch.setattr(
        own,
        "_resolve_ads_batch",
        AsyncMock(return_value={"1": ("CR2 | MV", "a1"), "2": ("X | ABC", "a2")}),
    )
    p = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="b",
        params={"ad_ids": ["1", "2"], "action": "pause"},
    )
    d = await check_mutation_ownership(object(), p, owner_tag="MV")
    assert d.allowed is False
    assert d.not_found is False
    assert "2" in d.foreign_ids


# bulk pause, один id не в каталоге → reject not_found
@pytest.mark.asyncio
async def test_bulk_one_not_found(monkeypatch) -> None:
    monkeypatch.setattr(
        own, "_resolve_ads_batch", AsyncMock(return_value={"1": ("CR2 | MV", "a1")})
    )  # "2" отсутствует
    p = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="b",
        params={"ad_ids": ["1", "2"], "action": "pause"},
    )
    d = await check_mutation_ownership(object(), p, owner_tag="MV")
    assert d.allowed is False
    assert d.not_found is True
    assert "2" in d.foreign_ids


# bulk полная форма object_type=campaign, чужая кампания → reject
@pytest.mark.asyncio
async def test_bulk_full_form_campaign_foreign(monkeypatch) -> None:
    monkeypatch.setattr(own, "_resolve_campaigns_batch", AsyncMock(return_value={"10": "X | ABC"}))
    p = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="10",
        params={"object_ids": ["10"], "status": "PAUSED", "object_type": "campaign"},
    )
    d = await check_mutation_ownership(object(), p, owner_tag="MV")
    assert d.allowed is False
    assert "10" in d.foreign_ids


# ====================== meta_api_worker routing ======================


def _meta_task(kind: str, target: str = "1", params: dict | None = None) -> SimpleNamespace:
    payload = {"mutation_kind": kind, "target_id": target}
    if params is not None:
        payload["params"] = params
    return SimpleNamespace(
        id=1, task_type="meta_api_mutation", payload=payload, attempt_count=0, max_attempts=5
    )


# Чужое объявление → mark_task_failed, без execute и без requeue
@pytest.mark.asyncio
async def test_worker_reject_foreign(monkeypatch) -> None:
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value="MV"))
    monkeypatch.setattr(
        meta,
        "check_mutation_ownership",
        AsyncMock(
            return_value=OwnershipDecision(allowed=False, reason="чужое", foreign_ids=("1",))
        ),
    )
    spy_fail = AsyncMock(return_value=True)
    spy_requeue = AsyncMock()
    spy_exec = AsyncMock()
    monkeypatch.setattr(meta, "mark_task_failed", spy_fail)
    monkeypatch.setattr(meta, "requeue_task", spy_requeue)
    monkeypatch.setattr(meta, "execute_mutation", spy_exec)

    await meta.process_one_task(object(), _meta_task("pause_ad"), client=AsyncMock())
    spy_fail.assert_awaited_once()
    spy_requeue.assert_not_awaited()
    spy_exec.assert_not_awaited()


# Своё, но не в каталоге + ВЫКЛЮЧАЮЩЕЕ (pause) → requeue (скан догонит), без fail
@pytest.mark.asyncio
async def test_worker_not_found_deactivating_requeues(monkeypatch) -> None:
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value="MV"))
    monkeypatch.setattr(
        meta,
        "check_mutation_ownership",
        AsyncMock(return_value=OwnershipDecision(allowed=False, reason="nf", not_found=True)),
    )
    spy_fail = AsyncMock()
    spy_requeue = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "mark_task_failed", spy_fail)
    monkeypatch.setattr(meta, "requeue_task", spy_requeue)
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock())

    await meta.process_one_task(object(), _meta_task("pause_ad"), client=AsyncMock())
    spy_requeue.assert_awaited_once()
    spy_fail.assert_not_awaited()


# Своё, но не в каталоге + ВКЛЮЧАЮЩЕЕ (activate) → mark_task_failed (не requeue)
@pytest.mark.asyncio
async def test_worker_not_found_activating_fails(monkeypatch) -> None:
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value="MV"))
    monkeypatch.setattr(
        meta,
        "check_mutation_ownership",
        AsyncMock(return_value=OwnershipDecision(allowed=False, reason="nf", not_found=True)),
    )
    spy_fail = AsyncMock(return_value=True)
    spy_requeue = AsyncMock()
    monkeypatch.setattr(meta, "mark_task_failed", spy_fail)
    monkeypatch.setattr(meta, "requeue_task", spy_requeue)
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock())

    await meta.process_one_task(object(), _meta_task("activate_ad"), client=AsyncMock())
    spy_fail.assert_awaited_once()
    spy_requeue.assert_not_awaited()


# Разрешено → mutation исполняется
@pytest.mark.asyncio
async def test_worker_allowed_executes(monkeypatch) -> None:
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value="MV"))
    monkeypatch.setattr(
        meta,
        "check_mutation_ownership",
        AsyncMock(return_value=OwnershipDecision(allowed=True, reason="ok")),
    )
    spy_exec = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(meta, "execute_mutation", spy_exec)
    monkeypatch.setattr(meta, "mark_task_succeeded", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "sync_fsm_after_mutation", AsyncMock())

    await meta.process_one_task(object(), _meta_task("pause_ad"), client=AsyncMock())
    spy_exec.assert_awaited_once()


# NULL owner_tag → реальная проверка пропускает, mutation исполняется
@pytest.mark.asyncio
async def test_worker_null_owner_tag_executes(monkeypatch) -> None:
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    # check_mutation_ownership НЕ мокаем — реальная, с owner_tag=None вернёт allowed
    spy_exec = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(meta, "execute_mutation", spy_exec)
    monkeypatch.setattr(meta, "mark_task_succeeded", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "sync_fsm_after_mutation", AsyncMock())

    await meta.process_one_task(object(), _meta_task("pause_ad"), client=AsyncMock())
    spy_exec.assert_awaited_once()


# Порядок гейтов: на паузе сканирования + активирующая → асимметричный стоп СРАЗУ,
# owner-резолвер не вызывается (лишний БД-запрос не делается)
@pytest.mark.asyncio
async def test_worker_pause_gate_precedes_owner(monkeypatch) -> None:
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=False))
    spy_check = AsyncMock()
    monkeypatch.setattr(meta, "check_mutation_ownership", spy_check)
    monkeypatch.setattr(meta, "requeue_task", AsyncMock(return_value=True))

    await meta.process_one_task(object(), _meta_task("activate_ad"), client=AsyncMock())
    spy_check.assert_not_awaited()


# ====================== toggle_executor routing ======================


def _toggle_claim(fb_ad_id: str = "123") -> SimpleNamespace:
    task = SimpleNamespace(id=1, payload={"fb_ad_id": fb_ad_id}, attempt_count=0, max_attempts=5)
    return SimpleNamespace(queue_empty=False, task=task)


# disable своего ad → toggle_ad вызывается
@pytest.mark.asyncio
async def test_toggle_disable_own(monkeypatch) -> None:
    monkeypatch.setattr(toggle, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(toggle, "load_owner_tag", AsyncMock(return_value="MV"))
    monkeypatch.setattr(
        toggle,
        "check_ad_ownership",
        AsyncMock(return_value=OwnershipDecision(allowed=True, reason="ok")),
    )
    monkeypatch.setattr(toggle, "claim_next_task", AsyncMock(return_value=_toggle_claim()))
    monkeypatch.setattr(toggle, "mark_succeeded", AsyncMock(return_value=True))
    monkeypatch.setattr(toggle, "reset_alert_state_after_disable_succeeded", AsyncMock())
    gate = AsyncMock()
    gate.toggle_ad = AsyncMock(return_value={"success": True, "final_state": "off"})

    out = await toggle.execute_one_toggle_task(object(), task_type="disable", gate=gate)
    assert out == "succeeded"
    gate.toggle_ad.assert_awaited_once()


# disable ЧУЖОГО ad → mark_failed, toggle_ad НЕ вызывается
@pytest.mark.asyncio
async def test_toggle_disable_foreign(monkeypatch) -> None:
    monkeypatch.setattr(toggle, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(toggle, "load_owner_tag", AsyncMock(return_value="MV"))
    monkeypatch.setattr(
        toggle,
        "check_ad_ownership",
        AsyncMock(return_value=OwnershipDecision(allowed=False, reason="чужое")),
    )
    monkeypatch.setattr(toggle, "claim_next_task", AsyncMock(return_value=_toggle_claim()))
    spy_fail = AsyncMock(return_value=True)
    monkeypatch.setattr(toggle, "mark_failed", spy_fail)
    gate = AsyncMock()
    gate.toggle_ad = AsyncMock()

    out = await toggle.execute_one_toggle_task(object(), task_type="disable", gate=gate)
    assert out == "failed"
    spy_fail.assert_awaited_once()
    gate.toggle_ad.assert_not_awaited()


# disable своего, но не в каталоге → requeue (скан догонит), toggle НЕ вызывается
@pytest.mark.asyncio
async def test_toggle_disable_not_found_requeues(monkeypatch) -> None:
    monkeypatch.setattr(toggle, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(toggle, "load_owner_tag", AsyncMock(return_value="MV"))
    monkeypatch.setattr(
        toggle,
        "check_ad_ownership",
        AsyncMock(return_value=OwnershipDecision(allowed=False, reason="nf", not_found=True)),
    )
    monkeypatch.setattr(toggle, "claim_next_task", AsyncMock(return_value=_toggle_claim()))
    spy_requeue = AsyncMock(return_value=True)
    spy_fail = AsyncMock()
    monkeypatch.setattr(toggle, "requeue_for_retry", spy_requeue)
    monkeypatch.setattr(toggle, "mark_failed", spy_fail)
    gate = AsyncMock()
    gate.toggle_ad = AsyncMock()

    out = await toggle.execute_one_toggle_task(object(), task_type="disable", gate=gate)
    assert out == "retrying"
    spy_requeue.assert_awaited_once()
    spy_fail.assert_not_awaited()
    gate.toggle_ad.assert_not_awaited()


# enable своего, но не в каталоге → mark_failed (включающее НЕ ждёт каталог)
@pytest.mark.asyncio
async def test_toggle_enable_not_found_fails(monkeypatch) -> None:
    monkeypatch.setattr(toggle, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(toggle, "load_owner_tag", AsyncMock(return_value="MV"))
    monkeypatch.setattr(
        toggle,
        "check_ad_ownership",
        AsyncMock(return_value=OwnershipDecision(allowed=False, reason="nf", not_found=True)),
    )
    monkeypatch.setattr(toggle, "claim_next_task", AsyncMock(return_value=_toggle_claim()))
    spy_fail = AsyncMock(return_value=True)
    spy_requeue = AsyncMock()
    monkeypatch.setattr(toggle, "mark_failed", spy_fail)
    monkeypatch.setattr(toggle, "requeue_for_retry", spy_requeue)
    gate = AsyncMock()
    gate.toggle_ad = AsyncMock()

    out = await toggle.execute_one_toggle_task(object(), task_type="enable", gate=gate)
    assert out == "failed"
    spy_fail.assert_awaited_once()
    spy_requeue.assert_not_awaited()
    gate.toggle_ad.assert_not_awaited()


# NULL owner_tag → реальная проверка пропускает, toggle_ad вызывается
@pytest.mark.asyncio
async def test_toggle_null_owner_tag_executes(monkeypatch) -> None:
    monkeypatch.setattr(toggle, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(toggle, "load_owner_tag", AsyncMock(return_value=None))
    # check_ad_ownership НЕ мокаем — реальная, owner_tag=None → allowed
    monkeypatch.setattr(toggle, "claim_next_task", AsyncMock(return_value=_toggle_claim()))
    monkeypatch.setattr(toggle, "mark_succeeded", AsyncMock(return_value=True))
    monkeypatch.setattr(toggle, "reset_alert_state_after_disable_succeeded", AsyncMock())
    gate = AsyncMock()
    gate.toggle_ad = AsyncMock(return_value={"success": True, "final_state": "off"})

    out = await toggle.execute_one_toggle_task(object(), task_type="disable", gate=gate)
    assert out == "succeeded"
    gate.toggle_ad.assert_awaited_once()
