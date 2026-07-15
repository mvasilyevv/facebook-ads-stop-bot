# -*- coding: utf-8 -*-
"""Unit: логический провал mutation (success=False) НЕ метится succeeded (R3, HIGH).

Batch-конверт Graph API даёт HTTP 200, но пер-саб ошибки лежат в теле. Handler
(bulk_status_change при полном отказе Meta, duplicate_campaign при провале copy/rename)
возвращает dict без exception, в котором success=False (или для bulk succeeded==0 &
failed>0). Раньше process_one_task после execute_mutation БЕЗУСЛОВНО звал
mark_task_succeeded и не читал result['success'] → bulk-стоп при полном отказе метился
succeeded, money-fail DM (только в except) не уходил, объявления тратили бюджет.

Фикс: после execute_mutation проверять is_mutation_success(result) — провал → mark_failed
+ money-fail алерт (тем же путём, что except-ветка). Partial bulk (часть failed) →
succeeded + FSM-sync по modified_ids + money-fail DM.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as meta


def _task(kind: str, tid: int = 1, requested_by: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        id=tid,
        task_type="meta_api_mutation",
        payload={"mutation_kind": kind, "target_id": "100"},
        requested_by=requested_by,
        attempt_count=0,
        max_attempts=5,
    )


@pytest.fixture
def _patched(monkeypatch):
    """Сканирование включено + owner-фильтр выключен → доходим до execute_mutation.

    Спаим mark_task_succeeded/mark_task_failed/sync_fsm/_alert_money_fail, чтобы проверить,
    какая ветка отработала без живой БД.
    """
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(
        meta,
        "check_mutation_ownership",
        AsyncMock(return_value=SimpleNamespace(allowed=True, not_found=False, reason="")),
    )
    spy_succeed = AsyncMock(return_value=True)
    spy_fail = AsyncMock(return_value=True)
    spy_fsm = AsyncMock()
    spy_alert = AsyncMock()
    monkeypatch.setattr(meta, "mark_task_succeeded", spy_succeed)
    monkeypatch.setattr(meta, "mark_task_failed", spy_fail)
    monkeypatch.setattr(meta, "sync_fsm_after_mutation", spy_fsm)
    monkeypatch.setattr(meta, "_alert_money_fail", spy_alert)
    return SimpleNamespace(succeed=spy_succeed, fail=spy_fail, fsm=spy_fsm, alert=spy_alert)


# ====================== is_mutation_success хелпер ======================


# success=False → провал
def test_is_mutation_success_false() -> None:
    assert meta.is_mutation_success({"success": False}) is False


# bulk: succeeded==0 & failed>0 при success=True → всё равно провал
def test_is_mutation_success_bulk_full_fail() -> None:
    assert meta.is_mutation_success({"success": True, "succeeded": 0, "failed": 3}) is False


# bulk partial: часть применилась → успех (succeeded>0)
def test_is_mutation_success_bulk_partial() -> None:
    assert meta.is_mutation_success({"success": True, "succeeded": 2, "failed": 1}) is True


# обычный success=True без bulk-полей → успех
def test_is_mutation_success_plain_true() -> None:
    assert meta.is_mutation_success({"success": True, "modified_ids": ["1"]}) is True


# None / не-dict трактуем как успех (handler не вернул структуру — не наша забота)
def test_is_mutation_success_non_dict() -> None:
    assert meta.is_mutation_success(None) is True


# ====================== process_one_task маршрутизация ======================


# bulk полный отказ Meta (success=True, succeeded=0, failed=3) → mark_failed, НЕ succeeded
@pytest.mark.asyncio
async def test_bulk_full_fail_marks_failed_not_succeeded(monkeypatch, _patched) -> None:
    result = {
        "success": True,
        "modified_ids": [],
        "succeeded": 0,
        "failed": 3,
        "sub_results": [],
    }
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(return_value=result))
    await meta.process_one_task(
        object(), _task("bulk_status_change", requested_by="bot_auto_stop"), client=AsyncMock()
    )
    _patched.fail.assert_awaited_once()
    _patched.succeed.assert_not_awaited()
    # money-fail DM ушёл (как в except-ветке)
    _patched.alert.assert_awaited_once()


# duplicate_campaign success=False (copy создан, rename упал) → mark_failed, НЕ succeeded
@pytest.mark.asyncio
async def test_duplicate_success_false_marks_failed(monkeypatch, _patched) -> None:
    result = {"success": False, "error": "rename failed", "modified_ids": []}
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(return_value=result))
    await meta.process_one_task(object(), _task("duplicate_campaign"), client=AsyncMock())
    _patched.fail.assert_awaited_once()
    _patched.succeed.assert_not_awaited()


# Контраст: bulk partial (succeeded=2, failed=1) → succeeded + FSM-sync + money-fail DM
@pytest.mark.asyncio
async def test_bulk_partial_succeeds_but_alerts(monkeypatch, _patched) -> None:
    result = {
        "success": True,
        "modified_ids": ["1", "2"],
        "succeeded": 2,
        "failed": 1,
        "sub_results": [],
    }
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(return_value=result))
    await meta.process_one_task(
        object(), _task("bulk_status_change", requested_by="bot_auto_stop"), client=AsyncMock()
    )
    _patched.succeed.assert_awaited_once()
    _patched.fail.assert_not_awaited()
    _patched.fsm.assert_awaited_once()  # FSM-sync по modified_ids
    _patched.alert.assert_awaited_once()  # money-fail DM о частичном провале


# Контраст: полный успех (success=True, succeeded=N, failed=0) → succeeded, без алерта
@pytest.mark.asyncio
async def test_full_success_no_alert(monkeypatch, _patched) -> None:
    result = {
        "success": True,
        "modified_ids": ["1", "2", "3"],
        "succeeded": 3,
        "failed": 0,
        "sub_results": [],
    }
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(return_value=result))
    await meta.process_one_task(
        object(), _task("bulk_status_change", requested_by="bot_auto_stop"), client=AsyncMock()
    )
    _patched.succeed.assert_awaited_once()
    _patched.fail.assert_not_awaited()
    _patched.alert.assert_not_awaited()


# Контраст: обычный pause_ad success=True → succeeded, без алерта
@pytest.mark.asyncio
async def test_pause_ad_success(monkeypatch, _patched) -> None:
    result = {"success": True, "modified_ids": ["100"]}
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(return_value=result))
    await meta.process_one_task(object(), _task("pause_ad"), client=AsyncMock())
    _patched.succeed.assert_awaited_once()
    _patched.fail.assert_not_awaited()
    _patched.alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_curator_grace_is_applied_before_terminal_success(monkeypatch, _patched) -> None:
    """Crash after Meta activate leaves retryable task until grace is installed."""
    events: list[str] = []

    async def apply_grace(*args, **kwargs):
        events.append("grace")

    async def mark_succeeded(*args, **kwargs):
        events.append("succeeded")
        return True

    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(return_value={"success": True, "modified_ids": ["100"]}),
    )
    monkeypatch.setattr(meta, "_apply_enable_grace_after_success", apply_grace)
    monkeypatch.setattr(meta, "mark_task_succeeded", mark_succeeded)

    await meta.process_one_task(object(), _task("activate_ad"), client=AsyncMock())

    assert events == ["grace", "succeeded"]
