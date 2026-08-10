# -*- coding: utf-8 -*-
"""Unit-тесты owner-scoping на ПУТИ ИСПОЛНЕНИЯ (защита чужих объявлений в шаренном кабинете).

Покрывают: вердикт check_mutation_ownership/check_ad_ownership по уровням целей,
bulk-семантику, NULL owner_tag (фильтр выключен), duplicate_adset_structure,
а также маршрутизацию в meta_api_worker (строгая fail-policy: чужое → fail;
своё-но-не-в-каталоге → выключающее requeue / включающее fail). Единственный канал
act — Marketing API (DOM-toggle и toggle_executor удалены).
Все тесты без БД — резолверы и БД-операции замоканы (monkeypatch).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as meta
import core.meta_api.ownership as own
from core.meta_api.ownership import OwnershipDecision, check_mutation_ownership
from core.meta_api.schemas import MetaMutationPayload
from core.tasks.queue import Task

# ====================== check_mutation_ownership: одиночные цели ======================


# pause_ad своего объявления (owner-тег в кампании) → разрешено
@pytest.mark.asyncio
async def test_mut_pause_ad_own(monkeypatch) -> None:
    monkeypatch.setattr(own, "_resolve_ad", AsyncMock(return_value=("CR2 | MV | GH", "ad1")))
    p = MetaMutationPayload(ad_account_id="123", mutation_kind="pause_ad", target_id="123")
    d = await check_mutation_ownership(object(), p, owner_tag="MV")
    assert d.allowed is True


# pause_ad ЧУЖОГО объявления → отказ, not_found=False (точно чужое)
@pytest.mark.asyncio
async def test_mut_pause_ad_foreign(monkeypatch) -> None:
    monkeypatch.setattr(own, "_resolve_ad", AsyncMock(return_value=("CR2 | ABC | GH", "ad1")))
    p = MetaMutationPayload(ad_account_id="123", mutation_kind="pause_ad", target_id="123")
    d = await check_mutation_ownership(object(), p, owner_tag="MV")
    assert d.allowed is False
    assert d.not_found is False


# pause_ad объявления, которого нет в каталоге → отказ, not_found=True (скан мог отстать)
@pytest.mark.asyncio
async def test_mut_pause_ad_not_found(monkeypatch) -> None:
    monkeypatch.setattr(own, "_resolve_ad", AsyncMock(return_value=None))
    p = MetaMutationPayload(ad_account_id="123", mutation_kind="pause_ad", target_id="123")
    d = await check_mutation_ownership(object(), p, owner_tag="MV")
    assert d.allowed is False
    assert d.not_found is True


# activate_ad чужого → отказ (включающее тоже скоупится)
@pytest.mark.asyncio
async def test_mut_activate_ad_foreign(monkeypatch) -> None:
    monkeypatch.setattr(own, "_resolve_ad", AsyncMock(return_value=("X | ABC", "a")))
    p = MetaMutationPayload(ad_account_id="123", mutation_kind="activate_ad", target_id="1")
    d = await check_mutation_ownership(object(), p, owner_tag="MV")
    assert d.allowed is False


# NULL/пустой owner_tag → разрешено всё, резолвер НЕ вызывается (фильтр выключен)
@pytest.mark.asyncio
async def test_mut_null_owner_tag_allows_without_resolve(monkeypatch) -> None:
    spy = AsyncMock()
    monkeypatch.setattr(own, "_resolve_ad", spy)
    p = MetaMutationPayload(ad_account_id="123", mutation_kind="pause_ad", target_id="1")
    assert (await check_mutation_ownership(object(), p, owner_tag=None)).allowed is True
    assert (await check_mutation_ownership(object(), p, owner_tag="   ")).allowed is True
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_mut_duplicate_adset_structure_checks_source_ad_and_generated_names(
    monkeypatch,
) -> None:
    payload = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="duplicate_adset_structure",
        target_id="draft",
        params={
            "source_ad_id": "101",
            "source_adset_id": "201",
            "campaign_names": ["CR2 | MV | duplicate 1", "CR2 | MV | duplicate 2"],
        },
    )
    resolver = AsyncMock(return_value=("CR2", "MV | source ad"))
    monkeypatch.setattr(own, "_resolve_ad", resolver)
    engine = object()

    decision = await check_mutation_ownership(engine, payload, owner_tag="MV")

    assert decision.allowed is True
    resolver.assert_awaited_once_with(engine, "101")


@pytest.mark.asyncio
async def test_mut_duplicate_adset_structure_rejects_generated_name_without_owner_tag(
    monkeypatch,
) -> None:
    payload = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="duplicate_adset_structure",
        target_id="draft",
        params={
            "source_ad_id": "101",
            "campaign_names": ["CR2 | MV | duplicate 1", "CR2 | foreign duplicate"],
        },
    )
    monkeypatch.setattr(own, "_resolve_ad", AsyncMock(return_value=("CR2 | MV", "source")))

    decision = await check_mutation_ownership(object(), payload, owner_tag="MV")

    assert decision.allowed is False
    assert "campaign_names[1]" in decision.reason


@pytest.mark.asyncio
async def test_mut_duplicate_adset_structure_rejects_foreign_source_before_names(
    monkeypatch,
) -> None:
    payload = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="duplicate_adset_structure",
        target_id="draft",
        params={
            "source_ad_id": "101",
            "campaign_names": ["CR2 | MV | duplicate"],
        },
    )
    monkeypatch.setattr(
        own,
        "_resolve_ad",
        AsyncMock(return_value=("CR2 | foreign", "source")),
    )

    decision = await check_mutation_ownership(object(), payload, owner_tag="MV")

    assert decision.allowed is False
    assert "чужая кампания" in decision.reason


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
        ad_account_id="123",
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
        ad_account_id="123",
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
        ad_account_id="123",
        mutation_kind="bulk_status_change",
        target_id="b",
        params={"ad_ids": ["1", "2"], "action": "pause"},
    )
    d = await check_mutation_ownership(object(), p, owner_tag="MV")
    assert d.allowed is False
    assert d.not_found is True
    assert "2" in d.foreign_ids


# ====================== meta_api_worker routing ======================


def _meta_task(kind: str, target: str = "1", params: dict | None = None) -> Task:
    now = datetime.now(UTC)
    payload = {"mutation_kind": kind, "target_id": target, "ad_account_id": "123"}
    if params is not None:
        payload["params"] = params
    return Task(
        id=1,
        task_type="meta_api_mutation",
        status="running",
        idempotency_key=f"meta:{kind}:1",
        payload=payload,
        attempt_count=0,
        max_attempts=5,
        requested_by="test",
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane="money",
        priority=0,
        available_at=now,
        deadline_at=now + timedelta(seconds=30),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000104"),
        lease_token=4,
        lease_expires_at=now + timedelta(minutes=1),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )


@pytest.fixture(autouse=True)
def _fenced_external_boundary(monkeypatch) -> None:
    monkeypatch.setattr(meta, "_preflight_task_control", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "mark_external_call_started", AsyncMock(return_value=True))


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
    spy_exec = AsyncMock(return_value={"success": True, "modified_ids": ["1"]})
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
    spy_exec = AsyncMock(return_value={"success": True, "modified_ids": ["1"]})
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
