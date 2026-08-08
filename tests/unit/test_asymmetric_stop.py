# -*- coding: utf-8 -*-
"""Unit-тесты асимметричного стоп-флага (is_scanning_enabled как глобальный стоп).

Семантика: пауза сканирования глушит ВКЛЮЧАЮЩИЕ/тратящие действия (autostart-activate,
enable-toggle, активирующие mutations, enable-рекомендации), но РАЗРЕШАЕТ выключающие
(disable-toggle, pause_*/bulk pause) — они снижают риск открута. Проверяем каждый гейт
в изоляции (monkeypatch модульного load_scanning_enabled, без БД).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.cabinet_scheduler.main as cab
import apps.enable_recommendation_worker.main as ereco
import apps.meta_api_worker.main as meta
from core.meta_api.schemas import MetaMutationPayload
from core.observer.queries import load_scanning_enabled
from core.tasks.queue import Task

# ====================== Фейк engine для load_scanning_enabled ======================


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeConn:
    def __init__(self, row):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *args, **kwargs):
        return _FakeResult(self._row)


class _FakeEngine:
    """Минимальный async-engine: connect() → async-context conn с фиксированной строкой."""

    def __init__(self, row):
        self._row = row

    def connect(self):
        return _FakeConn(self._row)


# ====================== load_scanning_enabled ======================


# Флаг True в БД → автоматика разрешена
@pytest.mark.asyncio
async def test_load_scanning_enabled_true() -> None:
    assert await load_scanning_enabled(_FakeEngine((True,))) is True


# Флаг False в БД → глобальный стоп активен
@pytest.mark.asyncio
async def test_load_scanning_enabled_false() -> None:
    assert await load_scanning_enabled(_FakeEngine((False,))) is False


# Нет строки observer_config → fail-safe False (чистая установка не сканирует сама)
@pytest.mark.asyncio
async def test_load_scanning_enabled_no_row_defaults_false() -> None:
    assert await load_scanning_enabled(_FakeEngine(None)) is False


# ====================== _is_activating_mutation (классификатор) ======================


# pause_ad — выключающая, на паузе разрешена
def test_is_activating_pause_ad_false() -> None:
    p = MetaMutationPayload(ad_account_id="123", mutation_kind="pause_ad", target_id="1")
    assert meta._is_activating_mutation(p) is False


# activate_ad — включающая, на паузе откладывается
def test_is_activating_activate_ad_true() -> None:
    p = MetaMutationPayload(ad_account_id="123", mutation_kind="activate_ad", target_id="1")
    assert meta._is_activating_mutation(p) is True


# bulk_status_change с action=pause — выключающая
def test_is_activating_bulk_pause_false() -> None:
    p = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="bulk_status_change",
        target_id="bulk:1",
        params={"action": "pause", "ad_ids": ["1"]},
    )
    assert meta._is_activating_mutation(p) is False


# bulk_status_change с action=activate — включающая (главный путь autostart)
def test_is_activating_bulk_activate_true() -> None:
    p = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="bulk_status_change",
        target_id="bulk:1",
        params={"action": "activate", "ad_ids": ["1"]},
    )
    assert meta._is_activating_mutation(p) is True


# duplicate_adset_structure — не выключающая → на паузе откладывается (money-safe)
def test_is_activating_duplicate_true() -> None:
    p = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="duplicate_adset_structure",
        target_id="new",
    )
    assert meta._is_activating_mutation(p) is True


# ====================== cabinet_scheduler ======================


# На паузе run_one_tick ничего не делает: не читает autostart-конфиг, не создаёт
# задачу и не ставит durable observer scan.
@pytest.mark.asyncio
async def test_cabinet_tick_paused_does_nothing(monkeypatch) -> None:
    monkeypatch.setattr(cab, "load_scanning_enabled", AsyncMock(return_value=False))
    spy_cfg = AsyncMock()
    spy_create = AsyncMock()
    spy_scan = AsyncMock()
    monkeypatch.setattr(cab, "read_autostart_config", spy_cfg)
    monkeypatch.setattr(cab, "create_mutation_task", spy_create)
    monkeypatch.setattr(cab, "enqueue_observer_scan", spy_scan)

    now = datetime(2026, 6, 5, 6, 0, tzinfo=timezone.utc)
    summary = await cab.run_one_tick(engine=object(), now=now)

    assert summary["outcome"] == "scanning_paused"
    spy_cfg.assert_not_awaited()
    spy_create.assert_not_awaited()
    spy_scan.assert_not_awaited()


# При включённом сканировании гейт не мешает — управление уходит дальше в обычный путь
@pytest.mark.asyncio
async def test_cabinet_tick_enabled_proceeds(monkeypatch) -> None:
    monkeypatch.setattr(cab, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(cab, "read_autostart_config", AsyncMock(return_value={"enabled": False}))
    now = datetime(2026, 6, 5, 6, 0, tzinfo=timezone.utc)
    summary = await cab.run_one_tick(engine=object(), now=now)

    assert summary["outcome"] == "disabled"


# ====================== meta_api_worker (process_one_task) ======================


def _meta_task(task_id: int, payload: dict, *, requested_by: str = "test") -> Task:
    now = datetime.now(timezone.utc)
    return Task(
        id=task_id,
        task_type="meta_api_mutation",
        status="running",
        idempotency_key=f"meta:{task_id}",
        payload=payload,
        attempt_count=0,
        max_attempts=5,
        requested_by=requested_by,
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane="money",
        priority=0,
        available_at=now,
        deadline_at=now + timedelta(seconds=30),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000106"),
        lease_token=6,
        lease_expires_at=now + timedelta(minutes=1),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )


# activate_ad на паузе → requeue (отложено), execute_mutation НЕ вызывается
@pytest.mark.asyncio
async def test_meta_activate_paused_requeued(monkeypatch) -> None:
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=False))
    spy_requeue = AsyncMock(return_value=True)
    spy_exec = AsyncMock()
    monkeypatch.setattr(meta, "requeue_task", spy_requeue)
    monkeypatch.setattr(meta, "execute_mutation", spy_exec)

    task = _meta_task(
        10,
        {"mutation_kind": "activate_ad", "target_id": "123", "ad_account_id": "456"},
    )
    await meta.process_one_task(object(), task, client=AsyncMock())

    spy_requeue.assert_awaited_once()
    spy_exec.assert_not_awaited()


# bulk activate (главный путь autostart) на паузе → requeue, не исполняется
@pytest.mark.asyncio
async def test_meta_bulk_activate_paused_requeued(monkeypatch) -> None:
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=False))
    spy_requeue = AsyncMock(return_value=True)
    spy_exec = AsyncMock()
    monkeypatch.setattr(meta, "requeue_task", spy_requeue)
    monkeypatch.setattr(meta, "execute_mutation", spy_exec)

    task = _meta_task(
        11,
        {
            "mutation_kind": "bulk_status_change",
            "target_id": "autostart:1",
            "ad_account_id": "456",
            "params": {"action": "activate", "ad_ids": ["1"]},
        },
    )
    await meta.process_one_task(object(), task, client=AsyncMock())

    spy_requeue.assert_awaited_once()
    spy_exec.assert_not_awaited()


# pause_ad на паузе ВСЁ РАВНО исполняется (выключающая mutation), requeue не вызывается
@pytest.mark.asyncio
async def test_meta_pause_paused_executes(monkeypatch) -> None:
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=False))
    # owner-гейт идёт ПОСЛЕ асимметричного стопа: pause проходит паузу и доходит до
    # owner-проверки — пустой owner_tag пропускает (фильтр выключен).
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    spy_requeue = AsyncMock()
    spy_exec = AsyncMock(return_value={"success": True, "modified_ids": ["123"]})
    monkeypatch.setattr(meta, "requeue_task", spy_requeue)
    monkeypatch.setattr(meta, "execute_mutation", spy_exec)
    monkeypatch.setattr(meta, "mark_task_succeeded", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "sync_fsm_after_mutation", AsyncMock())
    monkeypatch.setattr(meta, "_preflight_task_control", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "mark_external_call_started", AsyncMock(return_value=True))

    task = _meta_task(
        12,
        {"mutation_kind": "pause_ad", "target_id": "123", "ad_account_id": "456"},
    )
    await meta.process_one_task(object(), task, client=AsyncMock())

    spy_exec.assert_awaited_once()
    spy_requeue.assert_not_awaited()


@pytest.mark.asyncio
async def test_meta_pause_cancelled_before_external_call_never_executes(monkeypatch) -> None:
    """Tracker выигрывает гонку до external_started_at — Meta вызова нет."""
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(
        meta,
        "load_meta_snapshot_freshness",
        AsyncMock(return_value=SimpleNamespace(fresh=True)),
    )
    monkeypatch.setattr(meta, "mark_external_call_started", AsyncMock(return_value=False))
    monkeypatch.setattr(meta, "_preflight_task_control", AsyncMock(return_value=None))
    spy_exec = AsyncMock(return_value={"success": True, "modified_ids": ["123"]})
    monkeypatch.setattr(meta, "execute_mutation", spy_exec)

    task = _meta_task(
        120,
        {"mutation_kind": "pause_ad", "target_id": "123", "ad_account_id": "456"},
        requested_by="bot_auto_stop",
    )
    task.idempotency_key = "auto:pause_ad:123:token"
    engine = object()
    await meta.process_one_task(engine, task, client=AsyncMock())

    meta.mark_external_call_started.assert_awaited_once_with(
        engine,
        task_id=120,
        target_lock_key="123",
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000106"),
        lease_token=6,
    )
    spy_exec.assert_not_awaited()


# ====================== enable_recommendation_worker (run_once) ======================


# На паузе run_once пропускает цикл: fetch_candidates не вызывается, в counts есть метка
@pytest.mark.asyncio
async def test_enable_reco_paused_skips(monkeypatch) -> None:
    monkeypatch.setattr(ereco, "load_scanning_enabled", AsyncMock(return_value=False))
    spy_fetch = AsyncMock()
    monkeypatch.setattr(ereco, "fetch_candidates", spy_fetch)

    out = await ereco.run_once(object())

    assert out.get("skipped_paused") == 1
    assert out["candidates"] == 0
    spy_fetch.assert_not_awaited()
