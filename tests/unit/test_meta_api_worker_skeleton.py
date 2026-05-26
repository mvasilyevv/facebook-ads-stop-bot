# -*- coding: utf-8 -*-
"""Smoke-тест скелета apps/meta_api_worker/main.py.

Проверяет что main loop корректно запускается и shutdown'ится по сигналу
(asyncio.Event). Не тестируем execution мутаций — не реализован (Этап 5).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

# ─── Безопасные корутины вместо AsyncMock (Python 3.14 bug) ──────────────────
# AsyncMock в Python 3.14 зависает при __setattr__ внутри await — используем
# обычные async-функции как безопасную замену во всех местах.


async def _noop(*args, **kwargs):
    """Мгновенно завершается, возвращает None."""
    return None


async def _noop_reconcile(*args, **kwargs):
    """Возвращает пустой словарь reconcile."""
    return {"expired_drafts": 0, "stuck_running": 0}


async def _noop_heartbeat(*args, **kwargs):
    """Заглушка heartbeat-loop — завершается сразу (без цикла)."""
    return None


class _FakeDb:
    """Минимальный async context manager вместо AsyncSession."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def commit(self):
        return None


def _make_mock_db():
    """Создаёт экземпляр _FakeDb для использования как session factory."""
    return _FakeDb()


# ─── Тесты ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_worker_loop_starts_and_shuts_down():
    """Цикл запускается и корректно останавливается по shutdown_event."""
    shutdown_event = asyncio.Event()
    # Устанавливаем shutdown сразу — цикл должен завершиться без зависания
    shutdown_event.set()

    mock_db = _make_mock_db()
    mock_factory = MagicMock(return_value=mock_db)

    from apps.meta_api_worker.main import meta_api_worker_loop

    with (
        patch("apps.meta_api_worker.main.get_session_factory", return_value=mock_factory),
        patch("apps.meta_api_worker.main.claim_pending_task", new=_noop),
        patch("apps.meta_api_worker.main.mark_failed", new=_noop),
        patch("apps.meta_api_worker.main.reconcile_all", new=_noop_reconcile),
        patch("apps.meta_api_worker.main.update_worker_heartbeat", new=_noop),
        patch("apps.meta_api_worker.main._heartbeat_loop", new=_noop_heartbeat),
        patch("asyncio.sleep", new=_noop),
    ):
        # Должен завершиться за несколько секунд (shutdown_event уже set)
        await asyncio.wait_for(
            meta_api_worker_loop(
                shutdown_event=shutdown_event,
                poll_interval_seconds=0,
                reconcile_interval_seconds=9999,  # не запускаем reconcile в тесте
            ),
            timeout=5.0,
        )


@pytest.mark.asyncio
async def test_worker_loop_calls_reconcile():
    """Цикл вызывает reconcile_all хотя бы один раз при запуске."""
    shutdown_event = asyncio.Event()

    reconcile_calls: list = []

    async def _mock_reconcile(db):
        # После первого reconcile устанавливаем shutdown
        reconcile_calls.append(True)
        shutdown_event.set()
        return {"expired_drafts": 0, "stuck_running": 0}

    mock_db = _make_mock_db()
    mock_factory = MagicMock(return_value=mock_db)

    from apps.meta_api_worker.main import meta_api_worker_loop

    with (
        patch("apps.meta_api_worker.main.get_session_factory", return_value=mock_factory),
        patch("apps.meta_api_worker.main.claim_pending_task", new=_noop),
        patch("apps.meta_api_worker.main.mark_failed", new=_noop),
        patch("apps.meta_api_worker.main.reconcile_all", new=_mock_reconcile),
        patch("apps.meta_api_worker.main.update_worker_heartbeat", new=_noop),
        patch("apps.meta_api_worker.main._heartbeat_loop", new=_noop_heartbeat),
        patch("asyncio.sleep", new=_noop),
    ):
        await asyncio.wait_for(
            meta_api_worker_loop(
                shutdown_event=shutdown_event,
                poll_interval_seconds=0,
                reconcile_interval_seconds=0,  # запускаем reconcile сразу
            ),
            timeout=5.0,
        )

    assert len(reconcile_calls) >= 1, "reconcile_all должен быть вызван хотя бы один раз"


@pytest.mark.asyncio
async def test_worker_loop_no_tasks_stays_idle():
    """При отсутствии задач цикл не паникует и остаётся в idle-режиме."""
    shutdown_event = asyncio.Event()
    idle_count = [0]

    async def _counting_claim(db):
        idle_count[0] += 1
        if idle_count[0] >= 2:
            # После нескольких пустых проходов завершаемся
            shutdown_event.set()
        return None

    mock_db = _make_mock_db()
    mock_factory = MagicMock(return_value=mock_db)

    from apps.meta_api_worker.main import meta_api_worker_loop

    with (
        patch("apps.meta_api_worker.main.get_session_factory", return_value=mock_factory),
        patch("apps.meta_api_worker.main.claim_pending_task", new=_counting_claim),
        patch("apps.meta_api_worker.main.mark_failed", new=_noop),
        patch("apps.meta_api_worker.main.reconcile_all", new=_noop_reconcile),
        patch("apps.meta_api_worker.main.update_worker_heartbeat", new=_noop),
        patch("apps.meta_api_worker.main._heartbeat_loop", new=_noop_heartbeat),
        patch("asyncio.sleep", new=_noop),
    ):
        await asyncio.wait_for(
            meta_api_worker_loop(
                shutdown_event=shutdown_event,
                poll_interval_seconds=0,
                reconcile_interval_seconds=9999,
            ),
            timeout=5.0,
        )

    # Проверяем что несколько итераций прошли без ошибок
    assert idle_count[0] >= 2


@pytest.mark.asyncio
async def test_worker_loop_marks_task_failed_skeleton():
    """Если задача найдена — скелет помечает её как FAILED (не реализовано)."""
    shutdown_event = asyncio.Event()

    fake_task = MagicMock()
    fake_task.id = "task-uuid-123"
    fake_task.mutation_kind = "pause_ad"
    fake_task.target_id = "act_123"

    mark_failed_calls: list = []

    async def _mock_mark_failed(db, *, task_id, error_message):
        mark_failed_calls.append({"task_id": task_id, "error": error_message})
        shutdown_event.set()

    # claim возвращает задачу один раз, потом None
    call_count = [0]

    async def _claim(db):
        call_count[0] += 1
        if call_count[0] == 1:
            return fake_task
        return None

    mock_db = _make_mock_db()
    mock_factory = MagicMock(return_value=mock_db)

    from apps.meta_api_worker.main import meta_api_worker_loop

    with (
        patch("apps.meta_api_worker.main.get_session_factory", return_value=mock_factory),
        patch("apps.meta_api_worker.main.claim_pending_task", new=_claim),
        patch("apps.meta_api_worker.main.mark_failed", new=_mock_mark_failed),
        patch("apps.meta_api_worker.main.reconcile_all", new=_noop_reconcile),
        patch("apps.meta_api_worker.main.update_worker_heartbeat", new=_noop),
        patch("apps.meta_api_worker.main._heartbeat_loop", new=_noop_heartbeat),
        patch("asyncio.sleep", new=_noop),
    ):
        await asyncio.wait_for(
            meta_api_worker_loop(
                shutdown_event=shutdown_event,
                poll_interval_seconds=0,
                reconcile_interval_seconds=9999,
            ),
            timeout=5.0,
        )

    # Проверяем что mark_failed был вызван с task_id задачи
    assert len(mark_failed_calls) == 1
    assert mark_failed_calls[0]["task_id"] == "task-uuid-123"
    assert "Not implemented" in mark_failed_calls[0]["error"]
