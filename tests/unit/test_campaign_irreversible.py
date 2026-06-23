# -*- coding: utf-8 -*-
"""Unit: необратимый контур создания кампании (CRIT-1 + HIGH-2 + HIGH-3).

Money-дубль кампании закрывается на четырёх рубежах, здесь — все, что проверяются
без БД:

1. CRIT-1: reconcile_stuck_running НЕ уводит в 'retrying' задачи task_type='campaign_create'
   (необратимое создание; retry = дубль). SQL-guard безусловный + IRREVERSIBLE_TASK_TYPES.
2. HIGH-3: reconciler зовёт fail_stuck_campaign_create (зависший campaign_create → failed)
   ПЕРЕД reconcile, при count>0 шлёт алерт (как для meta create/duplicate).
3. HIGH-2: classify_execution_error — сбой ПОСЛЕ инициации POST campaign (ack-lost,
   ответ потерян) НЕ transient, а partial; падение ДО любого POST с transient-причиной
   остаётся transient.
4. worker re-claim: run в creating / с created_meta_ids → process_one_task НЕ исполняет
   повторно (mark_failed + run failed), execute_campaign_spec не зовётся.

Поведение SQL на реальном Postgres (stuck campaign_create → failed, retry других типов)
проверяет integration-тест test_campaign_creator_worker.py / test_reconciler_irreversible_db.py.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest

import apps.reconciler_worker.worker as rw
from apps.campaign_creator_worker import LoadedRun
from core.campaign_builder.execute import (
    CampaignExecutionError,
    PartialCreateError,
    classify_execution_error,
)
from core.meta_api.errors import PermanentError, TemporaryError
from core.tasks.queue import IRREVERSIBLE_TASK_TYPES, reconcile_stuck_running

# ====================== CRIT-1: reconcile исключает campaign_create ======================


# Контракт: campaign_create — необратимый task_type, единый источник.
def test_campaign_create_in_irreversible_task_types() -> None:
    assert "campaign_create" in IRREVERSIBLE_TASK_TYPES


# reconcile_stuck_running строит SQL с безусловным NOT IN guard по необратимым task_type
# и биндит набор — даже без exclude_kinds (meta) campaign_create исключён всегда.
@pytest.mark.asyncio
async def test_reconcile_excludes_campaign_create_unconditionally(monkeypatch) -> None:
    captured: dict = {}

    class _FakeResult:
        rowcount = 0

    class _FakeConn:
        async def execute(self, stmt, params):
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _FakeResult()

    class _FakeBegin:
        async def __aenter__(self):
            return _FakeConn()

        async def __aexit__(self, *a):
            return False

    class _FakeEngine:
        def begin(self):
            return _FakeBegin()

    # Без exclude_kinds (meta) — проверяем именно безусловный guard по task_type.
    await reconcile_stuck_running(_FakeEngine(), exclude_kinds=None)

    sql = captured["sql"]
    assert "task_type NOT IN" in sql
    # campaign_create передан в expanding-биндинг irrev_types.
    assert "campaign_create" in captured["params"]["irrev_types"]


# С exclude_kinds (meta) оба guard'а сосуществуют: и task_type NOT IN, и meta-mutation.
@pytest.mark.asyncio
async def test_reconcile_keeps_both_guards_with_exclude_kinds(monkeypatch) -> None:
    captured: dict = {}

    class _FakeResult:
        rowcount = 0

    class _FakeConn:
        async def execute(self, stmt, params):
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _FakeResult()

    class _FakeBegin:
        async def __aenter__(self):
            return _FakeConn()

        async def __aexit__(self, *a):
            return False

    class _FakeEngine:
        def begin(self):
            return _FakeBegin()

    await reconcile_stuck_running(
        _FakeEngine(), exclude_kinds=frozenset({"create_campaign", "duplicate_campaign"})
    )
    sql = captured["sql"]
    assert "task_type NOT IN" in sql
    assert "meta_api_mutation" in sql
    assert captured["params"]["exclude_kinds"]


# ====================== HIGH-3: reconciler заводит campaign_create-стак ==================


# render_campaign_create_alert: HTML + count + указание на ручную проверку Meta.
def test_render_campaign_create_alert_html() -> None:
    txt = rw.render_campaign_create_alert(2)
    assert "<b>2</b>" in txt
    assert "campaign_create" in txt
    assert "вручную" in txt


# run_once: fail_stuck_campaign_create вызывается ПЕРЕД reconcile_stuck_running и считается.
@pytest.mark.asyncio
async def test_run_once_calls_fail_campaign_create_before_reconcile(monkeypatch) -> None:
    order: list[str] = []

    async def fake_fail_campaign(engine):
        order.append("campaign")
        return 0

    async def fake_reconcile(engine):
        order.append("reconcile")
        return 0

    monkeypatch.setattr(rw, "fail_irreversible_stuck", AsyncMock(return_value=0))
    monkeypatch.setattr(rw, "fail_stuck_campaign_create", fake_fail_campaign)
    monkeypatch.setattr(rw, "reconcile_stuck_running", fake_reconcile)
    monkeypatch.setattr(rw, "cancel_old_drafts", AsyncMock(return_value=0))

    counts = await rw.run_once(object())

    assert order.index("campaign") < order.index("reconcile")
    assert counts["campaign_create_failed"] == 0


# run_once: при campaign_create_failed>0 шлётся алерт.
@pytest.mark.asyncio
async def test_run_once_alerts_when_campaign_create_failed(monkeypatch) -> None:
    monkeypatch.setattr(rw, "fail_irreversible_stuck", AsyncMock(return_value=0))
    monkeypatch.setattr(rw, "fail_stuck_campaign_create", AsyncMock(return_value=3))
    monkeypatch.setattr(rw, "reconcile_stuck_running", AsyncMock(return_value=0))
    monkeypatch.setattr(rw, "cancel_old_drafts", AsyncMock(return_value=0))
    alert_spy = AsyncMock()
    monkeypatch.setattr(rw, "_maybe_alert_campaign_create", alert_spy)

    counts = await rw.run_once(object())

    assert counts["campaign_create_failed"] == 3
    alert_spy.assert_awaited_once()
    assert alert_spy.await_args.args[1] == 3


# run_once: при campaign_create_failed==0 алерт НЕ шлётся.
@pytest.mark.asyncio
async def test_run_once_no_campaign_alert_when_zero(monkeypatch) -> None:
    monkeypatch.setattr(rw, "fail_irreversible_stuck", AsyncMock(return_value=0))
    monkeypatch.setattr(rw, "fail_stuck_campaign_create", AsyncMock(return_value=0))
    monkeypatch.setattr(rw, "reconcile_stuck_running", AsyncMock(return_value=0))
    monkeypatch.setattr(rw, "cancel_old_drafts", AsyncMock(return_value=0))
    alert_spy = AsyncMock()
    monkeypatch.setattr(rw, "_maybe_alert_campaign_create", alert_spy)

    await rw.run_once(object())
    alert_spy.assert_not_awaited()


# _maybe_alert_campaign_create: count<=0 — мгновенный no-op без обращения к БД.
@pytest.mark.asyncio
async def test_maybe_alert_campaign_zero_noop() -> None:
    await rw._maybe_alert_campaign_create(object(), 0)


# ====================== HIGH-2: classify учитывает irreversible_attempted ===============


# Сбой ПОСЛЕ инициации POST campaign (irreversible_attempted) → НЕ transient (permanent),
# даже если причина — TemporaryError. Защита от requeue → дубль.
def test_classify_irreversible_attempted_not_transient() -> None:
    err = CampaignExecutionError("ack lost")
    err.irreversible_attempted = True
    err.__cause__ = TemporaryError("vision flapped")
    # transient-причина, но POST уже инициирован → не transient.
    assert classify_execution_error(err) == "permanent"


# PartialCreateError по определению несёт irreversible_attempted=True и классифицируется partial.
def test_partial_create_error_is_irreversible_and_partial() -> None:
    exc = PartialCreateError(
        "ack lost",
        created_ids={"campaigns": [], "adsets": [], "creatives": [], "ads": []},
        failed_step="creating",
    )
    assert exc.irreversible_attempted is True
    assert classify_execution_error(exc) == "partial"


# Падение ДО любого POST (irreversible_attempted=False) с transient-причиной → transient.
# Чистый retry безопасен: объект гарантированно не создан.
def test_classify_pre_post_transient_stays_transient() -> None:
    err = CampaignExecutionError("vision down before POST")
    err.__cause__ = TemporaryError("no vision")
    assert err.irreversible_attempted is False
    assert classify_execution_error(err) == "transient"


# Permanent-причина (например, валидация) без attempted → permanent.
def test_classify_pre_post_permanent() -> None:
    err = CampaignExecutionError("bad config")
    err.__cause__ = PermanentError("rejected")
    assert classify_execution_error(err) == "permanent"


# ====================== worker re-claim: run в работе → НЕ переисполняем =================


class _SpyClient:
    """Любой Meta-вызов = провал инварианта (повторного залива быть не должно)."""

    def __init__(self) -> None:
        self.calls: list = []

    async def execute_graph_call(self, **kw):
        self.calls.append(kw)
        raise AssertionError("execute_graph_call не должен вызываться при re-claim run в работе")


def _make_task():
    from core.tasks.queue import Task

    return Task(
        id=42,
        task_type="campaign_create",
        status="running",
        idempotency_key="idem-x",
        payload={"run_id": "run-1"},
        attempt_count=1,
        max_attempts=5,
        requested_by="reconciler",
    )


# Re-claim run в статусе 'creating' → process_one_task НЕ зовёт execute, mark_failed + run failed.
@pytest.mark.asyncio
async def test_worker_skips_run_in_creating(monkeypatch) -> None:
    import apps.campaign_creator_worker.main as worker

    monkeypatch.setattr(
        worker,
        "load_run",
        AsyncMock(return_value=LoadedRun(id="run-1", config={}, status="creating")),
    )
    # created_meta_ids неважен — статус creating уже триггерит guard.
    monkeypatch.setattr(worker, "_run_has_created_meta_ids", AsyncMock(return_value=False))
    exec_spy = AsyncMock()
    monkeypatch.setattr(worker, "execute_campaign_spec", exec_spy)
    finalize_spy = AsyncMock()
    monkeypatch.setattr(worker, "finalize_run_failed", finalize_spy)
    mark_failed_spy = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "mark_failed", mark_failed_spy)

    client = _SpyClient()
    await worker.process_one_task(object(), _make_task(), client=client, uploader=object())

    exec_spy.assert_not_awaited()  # повторного залива нет
    assert client.calls == []
    finalize_spy.assert_awaited_once()  # run уведён в failed
    mark_failed_spy.assert_awaited_once()  # task уведён в failed (НЕ retry)


# Re-claim run в нетерминальном статусе, но с created_meta_ids → тоже НЕ переисполняем.
@pytest.mark.asyncio
async def test_worker_skips_run_with_created_ids(monkeypatch) -> None:
    import apps.campaign_creator_worker.main as worker

    # status queued (как будто свежий), но created_meta_ids уже есть (zombie дописал).
    monkeypatch.setattr(
        worker,
        "load_run",
        AsyncMock(return_value=LoadedRun(id="run-1", config={}, status="queued")),
    )
    monkeypatch.setattr(worker, "_run_has_created_meta_ids", AsyncMock(return_value=True))
    exec_spy = AsyncMock()
    monkeypatch.setattr(worker, "execute_campaign_spec", exec_spy)
    monkeypatch.setattr(worker, "finalize_run_failed", AsyncMock())
    monkeypatch.setattr(worker, "mark_failed", AsyncMock(return_value=True))

    client = _SpyClient()
    await worker.process_one_task(object(), _make_task(), client=client, uploader=object())

    exec_spy.assert_not_awaited()
    assert client.calls == []


# Терминальный 'failed' run — старый guard остаётся (быстрый no-op, execute не зовётся).
@pytest.mark.asyncio
async def test_worker_skips_terminal_failed_run(monkeypatch) -> None:
    import apps.campaign_creator_worker.main as worker

    monkeypatch.setattr(
        worker,
        "load_run",
        AsyncMock(return_value=LoadedRun(id="run-1", config={}, status="failed")),
    )
    has_created_spy = AsyncMock(return_value=False)
    monkeypatch.setattr(worker, "_run_has_created_meta_ids", has_created_spy)
    exec_spy = AsyncMock()
    monkeypatch.setattr(worker, "execute_campaign_spec", exec_spy)
    monkeypatch.setattr(worker, "mark_failed", AsyncMock(return_value=True))

    client = _SpyClient()
    await worker.process_one_task(object(), _make_task(), client=client, uploader=object())

    exec_spy.assert_not_awaited()
    # Терминальная ветка отрабатывает раньше — лишний запрос created_meta_ids не делается.
    has_created_spy.assert_not_awaited()


# build_uniquification_plan сигнатура неизменна (execute.py её зовёт — кросс-контракт).
def test_build_uniquification_plan_signature_stable() -> None:
    from core.campaign_builder.uniquify import build_uniquification_plan

    sig = inspect.signature(build_uniquification_plan)
    params = list(sig.parameters)
    # cfg, block, concepts, copies — публичный контракт исполнителя (execute.py зовёт).
    assert params[:3] == ["cfg", "block", "concepts"]
    assert "copies" in sig.parameters
