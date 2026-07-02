# -*- coding: utf-8 -*-
"""Unit: MID-10 heartbeat-touch долгих mutation против кражи reconciler'ом.

Долгий исполнитель (upload видео >30 мин) без освежения updated_at был бы украден
reconcile_stuck_running (running старше 30 мин → retrying по updated_at) → повторное
исполнение = дубль/двойной открут. touch_task_running освежает ИМЕННО updated_at
(поле, которое читает reconciler — writer↔reader контракт). _execute_with_touch
держит фоновый touch, пока идёт execute_mutation.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import apps.meta_api_worker.main as meta
from core.meta_api.schemas import MetaMutationPayload
from core.tasks.queue import touch_task_running


class _FakeConn:
    """Мок asyncpg conn: записывает SQL + params каждого execute."""

    def __init__(self, rowcount: int, sink: list) -> None:
        self._rowcount = rowcount
        self._sink = sink

    async def execute(self, stmt, params):
        self._sink.append((str(stmt), params))
        return SimpleNamespace(rowcount=self._rowcount)


class _FakeEngine:
    """Мок AsyncEngine.begin() как async context manager вокруг _FakeConn."""

    def __init__(self, rowcount: int = 1) -> None:
        self.calls: list = []
        self._rowcount = rowcount

    def begin(self):
        conn = _FakeConn(self._rowcount, self.calls)

        class _Ctx:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *a):
                return False

        return _Ctx()


# MID-10: touch_task_running обновляет РОВНО updated_at (поле-детектор reconciler'а),
# а не started_at/claimed_at — writer↔reader контракт с reconcile_stuck_running.
@pytest.mark.asyncio
async def test_touch_updates_updated_at_field() -> None:
    engine = _FakeEngine(rowcount=1)
    ok = await touch_task_running(engine, task_id=42)

    assert ok is True, "строка 'running' обновлена → True"
    assert len(engine.calls) == 1
    sql, params = engine.calls[0]
    # Ключевой инвариант: touch пишет updated_at.
    assert "SET updated_at = NOW()" in sql
    # НЕ трогает started_at/claimed_at (их reconciler НЕ читает).
    assert "started_at" not in sql
    assert "claimed_at" not in sql
    # Guard: только пока задача ещё 'running' (не воскрешаем закрытую).
    assert "status = 'running'" in sql
    assert params["id"] == 42


# touch не совпал (задача уже не 'running' — украдена/закрыта) → False, caller остановит цикл.
@pytest.mark.asyncio
async def test_touch_returns_false_when_not_running() -> None:
    engine = _FakeEngine(rowcount=0)
    ok = await touch_task_running(engine, task_id=7)
    assert ok is False


# _execute_with_touch: во время долгой mutation фоновый touch освежает updated_at,
# по завершении touch-таск отменяется, результат mutation возвращается как есть.
@pytest.mark.asyncio
async def test_execute_with_touch_calls_touch_during_long_mutation(monkeypatch) -> None:
    touched: list[int] = []

    async def fake_touch(engine, *, task_id):
        touched.append(task_id)
        return True

    monkeypatch.setattr(meta, "touch_task_running", fake_touch)

    # execute_mutation "долгая": спим дольше touch-интервала, чтобы touch успел сработать.
    async def slow_mutation(payload, *, client):
        await asyncio.sleep(0.05)
        return {"success": True, "modified_ids": ["1"]}

    monkeypatch.setattr(meta, "execute_mutation", slow_mutation)

    payload = MetaMutationPayload(mutation_kind="create_campaign", target_id="new")
    # Интервал touch'а сильно меньше длительности mutation → минимум один touch.
    result = await meta._execute_with_touch(
        MagicMock(), 99, payload, client=AsyncMock(), touch_interval_seconds=0.01
    )

    assert result == {"success": True, "modified_ids": ["1"]}
    assert touched, "фоновый touch должен освежить updated_at хотя бы раз за долгую mutation"
    assert all(tid == 99 for tid in touched)


# _execute_with_touch: короткая mutation завершается ДО первого touch-интервала —
# touch не вызывается (нет лишних апдейтов), результат пробрасывается.
@pytest.mark.asyncio
async def test_execute_with_touch_no_touch_for_fast_mutation(monkeypatch) -> None:
    touched: list[int] = []

    async def fake_touch(engine, *, task_id):
        touched.append(task_id)
        return True

    monkeypatch.setattr(meta, "touch_task_running", fake_touch)
    monkeypatch.setattr(
        meta, "execute_mutation", AsyncMock(return_value={"success": True, "modified_ids": []})
    )

    payload = MetaMutationPayload(mutation_kind="pause_ad", target_id="123")
    # Большой интервал → быстрая mutation закончится раньше первого touch.
    result = await meta._execute_with_touch(
        MagicMock(), 1, payload, client=AsyncMock(), touch_interval_seconds=100.0
    )

    assert result["success"] is True
    assert touched == [], "быстрая mutation не должна триггерить touch"


# _execute_with_touch: исключение mutation пробрасывается, touch-таск корректно отменяется.
@pytest.mark.asyncio
async def test_execute_with_touch_propagates_exception(monkeypatch) -> None:
    monkeypatch.setattr(meta, "touch_task_running", AsyncMock(return_value=True))

    async def failing_mutation(payload, *, client):
        raise RuntimeError("meta boom")

    monkeypatch.setattr(meta, "execute_mutation", failing_mutation)

    payload = MetaMutationPayload(mutation_kind="pause_ad", target_id="123")
    with pytest.raises(RuntimeError, match="meta boom"):
        await meta._execute_with_touch(
            MagicMock(), 1, payload, client=AsyncMock(), touch_interval_seconds=0.01
        )
