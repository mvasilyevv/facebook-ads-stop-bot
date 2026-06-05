# -*- coding: utf-8 -*-
"""Unit-тесты асимметричного стоп-флага (is_scanning_enabled как глобальный стоп).

Семантика: пауза сканирования глушит ВКЛЮЧАЮЩИЕ/тратящие действия (autostart-activate,
enable-toggle, активирующие mutations, enable-рекомендации), но РАЗРЕШАЕТ выключающие
(disable-toggle, pause_*/bulk pause) — они снижают риск открута. Проверяем каждый гейт
в изоляции (monkeypatch модульного load_scanning_enabled, без БД).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.cabinet_scheduler.main as cab
import apps.enable_recommendation_worker.main as ereco
import apps.meta_api_worker.main as meta
from core.meta_api.schemas import MetaMutationPayload
from core.observer.queries import load_scanning_enabled

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


# Нет строки observer_config → дефолт True (автоматика включена до явного выключения)
@pytest.mark.asyncio
async def test_load_scanning_enabled_no_row_defaults_true() -> None:
    assert await load_scanning_enabled(_FakeEngine(None)) is True


# ====================== _is_activating_mutation (классификатор) ======================


# pause_ad — выключающая, на паузе разрешена
def test_is_activating_pause_ad_false() -> None:
    p = MetaMutationPayload(mutation_kind="pause_ad", target_id="1")
    assert meta._is_activating_mutation(p) is False


# pause_campaign — выключающая
def test_is_activating_pause_campaign_false() -> None:
    p = MetaMutationPayload(mutation_kind="pause_campaign", target_id="1")
    assert meta._is_activating_mutation(p) is False


# activate_ad — включающая, на паузе откладывается
def test_is_activating_activate_ad_true() -> None:
    p = MetaMutationPayload(mutation_kind="activate_ad", target_id="1")
    assert meta._is_activating_mutation(p) is True


# bulk_status_change с action=pause — выключающая
def test_is_activating_bulk_pause_false() -> None:
    p = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="bulk:1",
        params={"action": "pause", "ad_ids": ["1"]},
    )
    assert meta._is_activating_mutation(p) is False


# bulk_status_change с action=activate — включающая (главный путь autostart)
def test_is_activating_bulk_activate_true() -> None:
    p = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="bulk:1",
        params={"action": "activate", "ad_ids": ["1"]},
    )
    assert meta._is_activating_mutation(p) is True


# create_campaign — не выключающая → на паузе откладывается (money-safe)
def test_is_activating_create_campaign_true() -> None:
    p = MetaMutationPayload(mutation_kind="create_campaign", target_id="new")
    assert meta._is_activating_mutation(p) is True


# ====================== cabinet_scheduler ======================


# На паузе run_one_tick ничего не делает: не читает autostart-конфиг, не создаёт
# задачу, не дёргает Redis (ни дедуп, ни observer-trigger)
@pytest.mark.asyncio
async def test_cabinet_tick_paused_does_nothing(monkeypatch) -> None:
    monkeypatch.setattr(cab, "load_scanning_enabled", AsyncMock(return_value=False))
    spy_cfg = AsyncMock()
    spy_create = AsyncMock()
    monkeypatch.setattr(cab, "read_autostart_config", spy_cfg)
    monkeypatch.setattr(cab, "create_mutation_task", spy_create)
    redis = AsyncMock()

    now = datetime(2026, 6, 5, 6, 0, tzinfo=timezone.utc)
    summary = await cab.run_one_tick(engine=object(), redis_client=redis, now=now)

    assert summary["outcome"] == "scanning_paused"
    spy_cfg.assert_not_awaited()
    spy_create.assert_not_awaited()
    redis.set.assert_not_awaited()
    redis.publish.assert_not_awaited()


# При включённом сканировании гейт не мешает — управление уходит дальше в обычный путь
@pytest.mark.asyncio
async def test_cabinet_tick_enabled_proceeds(monkeypatch) -> None:
    monkeypatch.setattr(cab, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(cab, "read_autostart_config", AsyncMock(return_value={"enabled": False}))
    redis = AsyncMock()

    now = datetime(2026, 6, 5, 6, 0, tzinfo=timezone.utc)
    summary = await cab.run_one_tick(engine=object(), redis_client=redis, now=now)

    assert summary["outcome"] == "disabled"


# ====================== meta_api_worker (process_one_task) ======================


# activate_ad на паузе → requeue (отложено), execute_mutation НЕ вызывается
@pytest.mark.asyncio
async def test_meta_activate_paused_requeued(monkeypatch) -> None:
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=False))
    spy_requeue = AsyncMock(return_value=True)
    spy_exec = AsyncMock()
    monkeypatch.setattr(meta, "requeue_task", spy_requeue)
    monkeypatch.setattr(meta, "execute_mutation", spy_exec)

    task = SimpleNamespace(
        id=10,
        task_type="meta_api_mutation",
        payload={"mutation_kind": "activate_ad", "target_id": "123"},
        attempt_count=0,
        max_attempts=5,
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

    task = SimpleNamespace(
        id=11,
        task_type="meta_api_mutation",
        payload={
            "mutation_kind": "bulk_status_change",
            "target_id": "autostart:1",
            "params": {"action": "activate", "ad_ids": ["1"]},
        },
        attempt_count=0,
        max_attempts=5,
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
    spy_exec = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(meta, "requeue_task", spy_requeue)
    monkeypatch.setattr(meta, "execute_mutation", spy_exec)
    monkeypatch.setattr(meta, "mark_task_succeeded", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "sync_fsm_after_mutation", AsyncMock())

    task = SimpleNamespace(
        id=12,
        task_type="meta_api_mutation",
        payload={"mutation_kind": "pause_ad", "target_id": "123"},
        attempt_count=0,
        max_attempts=5,
    )
    await meta.process_one_task(object(), task, client=AsyncMock())

    spy_exec.assert_awaited_once()
    spy_requeue.assert_not_awaited()


# ====================== enable_recommendation_worker (run_once) ======================


# На паузе run_once пропускает цикл: fetch_candidates не вызывается, в counts есть метка
@pytest.mark.asyncio
async def test_enable_reco_paused_skips(monkeypatch) -> None:
    monkeypatch.setattr(ereco, "load_scanning_enabled", AsyncMock(return_value=False))
    spy_fetch = AsyncMock()
    monkeypatch.setattr(ereco, "fetch_candidates", spy_fetch)

    out = await ereco.run_once(
        object(), redis_client=None, tg_client=None, chat_id=None, thread_id=None
    )

    assert out.get("skipped_paused") == 1
    assert out["candidates"] == 0
    spy_fetch.assert_not_awaited()
