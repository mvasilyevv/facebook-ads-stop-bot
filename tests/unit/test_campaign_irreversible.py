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

import asyncio
import inspect
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import apps.reconciler_worker.worker as rw
import core.campaign_builder.execute as campaign_execute
from apps.campaign_creator_worker import LoadedRun
from core.campaign_builder.execute import (
    CampaignExecutionError,
    PartialCreateError,
    classify_execution_error,
)
from core.meta_api.errors import (
    BrowserReadinessRejectedError,
    PermanentError,
    TemporaryError,
)
from core.tasks.irreversible_control import CreatorTaskControlAbort
from core.tasks.queue import IRREVERSIBLE_TASK_TYPES, reconcile_stuck_running


class _UnitControl:
    def __init__(self, **kwargs) -> None:
        task = kwargs.get("task")
        self.external_started = bool(
            task is not None and getattr(task, "external_started_at", None) is not None
        )

    async def check(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _no_db_task_control(monkeypatch) -> None:
    import apps.campaign_creator_worker.main as worker

    monkeypatch.setattr(worker, "CreatorTaskControl", _UnitControl)


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
        def all(self):
            return []

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
        def all(self):
            return []

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
        _FakeEngine(), exclude_kinds=frozenset({"duplicate_adset_structure"})
    )
    sql = captured["sql"]
    assert "task_type NOT IN" in sql
    assert "meta_api_mutation" in sql
    assert captured["params"]["exclude_kinds"]


# ====================== HIGH-3: reconciler заводит campaign_create-стак ==================


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

    monkeypatch.setattr(rw, "expire_overdue", AsyncMock(return_value=0))
    monkeypatch.setattr(rw, "prepare_duplicate_recovery", AsyncMock(return_value=0))
    monkeypatch.setattr(rw, "fail_duplicate_without_checkpoint", AsyncMock(return_value=0))
    monkeypatch.setattr(rw, "fail_stuck_campaign_create", fake_fail_campaign)
    monkeypatch.setattr(rw, "reconcile_stuck_running", fake_reconcile)

    counts = await rw.run_once(object())

    assert order.index("campaign") < order.index("reconcile")
    assert counts["campaign_create_failed"] == 0


def test_reconciler_has_no_post_commit_campaign_alert_path() -> None:
    assert not hasattr(rw, "_maybe_alert_campaign_create")
    assert not hasattr(rw, "render_campaign_create_alert")


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


def test_presend_readiness_rejection_overrides_local_external_boundary_marker() -> None:
    cause = BrowserReadinessRejectedError("local circuit open before browser dispatch")
    created = {"campaigns": [], "adsets": [], "creatives": [], "ads": []}

    with pytest.raises(CampaignExecutionError) as raised:
        campaign_execute._raise_for_failure(  # noqa: SLF001
            created,
            cause,
            failed_step="creating_campaign",
            campaign_create_attempted=True,
        )

    assert not isinstance(raised.value, PartialCreateError)
    assert raised.value.__cause__ is cause
    assert raised.value.irreversible_attempted is False
    assert classify_execution_error(raised.value) == "transient"


def test_readiness_rejection_after_confirmed_create_remains_partial() -> None:
    cause = BrowserReadinessRejectedError("upload rejected before its Meta dispatch")
    created = {
        "campaigns": ["campaign-1"],
        "adsets": [],
        "creatives": [],
        "ads": [],
    }

    with pytest.raises(PartialCreateError) as raised:
        campaign_execute._raise_for_failure(  # noqa: SLF001
            created,
            cause,
            failed_step="uploading",
            campaign_create_attempted=True,
        )

    assert raised.value.created_ids["campaigns"] == ["campaign-1"]
    assert raised.value.__cause__ is cause
    assert classify_execution_error(raised.value) == "partial"


# Отказ готовности браузера ДО отправки в Meta, случившийся после подтверждённого
# создания: исход не меняется (partial → UNKNOWN, ручная сверка), но причина
# больше не теряется — pre_dispatch помечает доказанный отказ до отправки.
def test_readiness_rejection_after_confirmed_create_is_marked_pre_dispatch() -> None:
    cause = BrowserReadinessRejectedError("upload rejected before its Meta dispatch")
    created = {"campaigns": ["campaign-1"], "adsets": [], "creatives": [], "ads": []}

    with pytest.raises(PartialCreateError) as raised:
        campaign_execute._raise_for_failure(  # noqa: SLF001
            created,
            cause,
            failed_step="uploading",
            campaign_create_attempted=True,
        )

    # Стена от дубля стоит на месте: partial, необратимо, requeue запрещён.
    assert classify_execution_error(raised.value) == "partial"
    assert raised.value.irreversible_attempted is True
    assert raised.value.pre_dispatch is True


# Любая другая причина при непустом created: отказ до отправки НЕ доказан → pre_dispatch=False
# (ответ Meta мог потеряться). False здесь означает «не доказано», а не «запрос ушёл».
def test_partial_without_proof_of_pre_dispatch_is_not_marked() -> None:
    cause = TemporaryError("vision flapped mid-flight")
    created = {"campaigns": ["campaign-1"], "adsets": ["adset-1"], "creatives": [], "ads": []}

    with pytest.raises(PartialCreateError) as raised:
        campaign_execute._raise_for_failure(  # noqa: SLF001
            created,
            cause,
            failed_step="creating_ads",
            campaign_create_attempted=True,
        )

    assert raised.value.pre_dispatch is False
    assert classify_execution_error(raised.value) == "partial"


# ack-lost (created пуст, POST campaign инициирован) — отказ заведомо ПОСЛЕ отправки.
def test_ack_lost_partial_is_not_marked_pre_dispatch() -> None:
    cause = TemporaryError("meta answer lost")
    created = {"campaigns": [], "adsets": [], "creatives": [], "ads": []}

    with pytest.raises(PartialCreateError) as raised:
        campaign_execute._raise_for_failure(  # noqa: SLF001
            created,
            cause,
            failed_step="creating",
            campaign_create_attempted=True,
        )

    assert raised.value.pre_dispatch is False


# Результат задачи несёт признак наружу: исход прежний (UNKNOWN + ручная сверка),
# добавлен только признак «отказ до отправки в Meta».
def test_task_result_carries_pre_dispatch_without_changing_outcome() -> None:
    import apps.campaign_creator_worker.main as worker

    result = worker._campaign_unknown_result(  # noqa: SLF001
        _make_task(),
        run_id="run-1",
        reason="partial_or_ack_lost",
        created_ids={"campaigns": ["campaign-1"]},
        failed_step="uploading",
        pre_dispatch=True,
    )

    assert result["outcome"] == "UNKNOWN"
    assert result["reconcile_required"] is True
    assert result["manual_review_required"] is True
    assert result["pre_dispatch"] is True
    # Неизвестность остаётся неизвестностью: без признака ключа в результате нет.
    unmarked = worker._campaign_unknown_result(  # noqa: SLF001
        _make_task(),
        run_id="run-1",
        reason="absolute_deadline_exceeded",
    )
    assert "pre_dispatch" not in unmarked


# Ветка PartialCreateError воркера обязана прокинуть признак в оба места, куда пишет result.
def test_partial_branch_forwards_pre_dispatch_marker() -> None:
    import apps.campaign_creator_worker.main as worker

    src = inspect.getsource(worker._execute_run)  # noqa: SLF001
    branch = src.split("except PartialCreateError")[1].split("except Exception")[0]
    assert branch.count("pre_dispatch=exc.pre_dispatch") == 2


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

    now = datetime.now(UTC)
    return Task(
        id=42,
        task_type="campaign_create",
        status="running",
        idempotency_key="idem-x",
        payload={"run_id": "run-1"},
        attempt_count=1,
        max_attempts=5,
        requested_by="reconciler",
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane="bulk",
        priority=0,
        available_at=now,
        deadline_at=now + timedelta(minutes=5),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000042"),
        lease_token=9,
        lease_expires_at=now + timedelta(minutes=30),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
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
    mark_failed_spy.assert_not_awaited()  # task закрывается атомарно вместе с run


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


@pytest.mark.asyncio
async def test_worker_rejects_preexisting_boundary_without_meta_retry(monkeypatch) -> None:
    import apps.campaign_creator_worker.main as worker

    monkeypatch.setattr(
        worker,
        "load_run",
        AsyncMock(return_value=LoadedRun(id="run-1", config={}, status="queued")),
    )
    monkeypatch.setattr(worker, "_run_has_created_meta_ids", AsyncMock(return_value=False))
    finalize = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "finalize_run_failed", finalize)
    execute = AsyncMock()
    monkeypatch.setattr(worker, "execute_campaign_spec", execute)
    task = _make_task()
    task.external_started_at = datetime.now(UTC)
    client = _SpyClient()

    await worker.process_one_task(object(), task, client=client, uploader=object())

    execute.assert_not_awaited()
    assert client.calls == []
    result = finalize.await_args.kwargs["task_result"]
    assert result["outcome"] == "UNKNOWN"
    assert result["reason"] == "preexisting_external_boundary"


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


@pytest.mark.asyncio
async def test_campaign_worker_uses_durable_gate_without_preclaim_rpc(
    monkeypatch,
) -> None:
    import apps.campaign_creator_worker.main as worker

    stop = asyncio.Event()
    task = _make_task()
    claim = AsyncMock(
        return_value=SimpleNamespace(
            queue_empty=False,
            task=task,
            browser_profile_id="campaign-profile-1",
            browser_session_id="campaign-session-1",
            browser_readiness_generation=4,
        )
    )

    async def process(*_args, **_kwargs) -> None:
        stop.set()

    monkeypatch.setattr(worker, "_claim", claim)
    monkeypatch.setattr(worker, "process_one_task", process)
    client = MagicMock()
    client.operation_authority.return_value = nullcontext()

    pinned: list[str] = []

    async def process(*_args, **_kwargs) -> None:  # noqa: F811 - фиксируем пин на момент работы
        pinned.append(client.session_id)
        stop.set()

    monkeypatch.setattr(worker, "process_one_task", process)

    await worker.task_loop(
        object(),
        stop,
        client=client,
        uploader=object(),
    )

    # #184: залив работает в той сессии, на которой подтверждена готовность,
    # а не в «самой свежей», выбранной browser-agent'ом на каждом вызове.
    assert pinned == ["campaign-session-1"]

    claim.assert_awaited_once()
    client.operation_authority.assert_called_once_with(
        caller="campaign_creator",
        task_id=task.id,
        lease_owner=task.lease_owner,
        lease_token=task.lease_token,
        vision_profile_id="campaign-profile-1",
        browser_readiness_generation=4,
    )


@pytest.mark.asyncio
async def test_cancel_winning_first_meta_boundary_makes_zero_meta_calls(monkeypatch) -> None:
    import apps.campaign_creator_worker.main as worker

    class _CancelAtBoundary(_UnitControl):
        async def begin_external(self, _operation: str) -> None:
            raise CreatorTaskControlAbort("cancel_requested", external_started=False)

    async def _execute(*_args, client, **_kwargs):
        await client.execute_graph_call(method="POST", endpoint="/act_1/campaigns")

    async def _direct(_control, operation_factory):
        return await operation_factory()

    delegate = AsyncMock()
    control = _CancelAtBoundary()
    task = _make_task()
    monkeypatch.setattr(worker, "parse_run_config", lambda _cfg: SimpleNamespace())
    monkeypatch.setattr(worker, "resolve_concepts_from_config", lambda _cfg: {})
    monkeypatch.setattr(worker, "build_campaign_spec", lambda _cfg: object())
    monkeypatch.setattr(worker, "set_run_status", AsyncMock(return_value=True))
    monkeypatch.setattr(worker, "execute_campaign_spec", _execute)
    monkeypatch.setattr(worker, "run_with_task_control", _direct)
    cancel_finalize = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "finalize_run_cancelled", cancel_finalize)
    retry = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "requeue_for_retry", retry)

    await worker._execute_run(
        object(),
        task,
        run_id="run-1",
        config={},
        client=delegate,
        uploader=AsyncMock(),
        control=control,
    )

    delegate.execute_graph_call.assert_not_awaited()
    retry.assert_not_awaited()
    cancel_finalize.assert_awaited_once()


@pytest.mark.asyncio
async def test_transient_after_campaign_boundary_is_unknown_not_retried(monkeypatch) -> None:
    import apps.campaign_creator_worker.main as worker

    class _StartedControl(_UnitControl):
        async def begin_external(self, _operation: str) -> None:
            self.external_started = True

    async def _execute(*_args, client, **_kwargs):
        await client.execute_graph_call(method="POST", endpoint="/act_1/campaigns")

    async def _direct(_control, operation_factory):
        return await operation_factory()

    from core.meta_api.errors import TemporaryError

    delegate = AsyncMock()
    delegate.execute_graph_call.side_effect = TemporaryError("response lost")
    control = _StartedControl()
    task = _make_task()
    monkeypatch.setattr(worker, "parse_run_config", lambda _cfg: SimpleNamespace())
    monkeypatch.setattr(worker, "resolve_concepts_from_config", lambda _cfg: {})
    monkeypatch.setattr(worker, "build_campaign_spec", lambda _cfg: object())
    monkeypatch.setattr(worker, "set_run_status", AsyncMock(return_value=True))
    monkeypatch.setattr(worker, "execute_campaign_spec", _execute)
    monkeypatch.setattr(worker, "run_with_task_control", _direct)
    failed = AsyncMock(return_value=True)
    retry = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "finalize_run_failed", failed)
    monkeypatch.setattr(worker, "requeue_for_retry", retry)

    await worker._execute_run(
        object(),
        task,
        run_id="run-1",
        config={},
        client=delegate,
        uploader=AsyncMock(),
        control=control,
    )

    retry.assert_not_awaited()
    result = failed.await_args.kwargs["task_result"]
    assert result["outcome"] == "UNKNOWN"
    assert result["manual_review_required"] is True


class _StartedControl(_UnitControl):
    async def begin_external(self, _operation: str) -> None:
        self.external_started = True


async def _run_until_deadline(monkeypatch, worker, *, control, task, failed, persist):
    """Гоняет _execute_run так, что абсолютный дедлайн срезает залив на середине."""

    async def _execute(*_args, client, created_sink=None, on_progress=None, **_kwargs):
        # Накопитель ведёт вызывающий; свой локальный dict уехал бы вместе с отменой.
        created = created_sink if created_sink is not None else {}
        # Первый POST переводит задачу за внешнюю границу (fenced client).
        await client.execute_graph_call(method="POST", endpoint="/act_1/campaigns")
        created.setdefault("campaigns", []).append("120001")
        created.setdefault("adsets", []).extend(["120002", "120003"])
        if on_progress is not None:
            await on_progress({"stage": "uploading"})
        # Пока execute ждёт готовности видео, дедлайн задачи истекает.
        await asyncio.sleep(30)

    async def _direct(_control, operation_factory):
        return await operation_factory()

    monkeypatch.setattr(worker, "parse_run_config", lambda _cfg: SimpleNamespace())
    monkeypatch.setattr(worker, "resolve_concepts_from_config", lambda _cfg: {})
    monkeypatch.setattr(worker, "build_campaign_spec", lambda _cfg: object())
    monkeypatch.setattr(worker, "set_run_status", AsyncMock(return_value=True))
    monkeypatch.setattr(worker, "execute_campaign_spec", _execute)
    monkeypatch.setattr(worker, "run_with_task_control", _direct)
    monkeypatch.setattr(worker, "finalize_run_failed", failed)
    monkeypatch.setattr(worker, "_persist_partial_created_ids", persist)
    monkeypatch.setattr(worker, "requeue_for_retry", AsyncMock(return_value=True))

    await worker._execute_run(
        object(),
        task,
        run_id="run-1",
        config={},
        client=AsyncMock(),
        uploader=AsyncMock(),
        control=control,
    )


# Абсолютный дедлайн отменяет корутину залива через CancelledError (BaseException), мимо
# except Exception внутри execute. Перечень уже созданного обязан дойти до оператора:
# UNKNOWN без единого id — это чистка кабинета вслепую.
@pytest.mark.asyncio
async def test_absolute_deadline_keeps_itemized_created_ids(monkeypatch) -> None:
    import apps.campaign_creator_worker.main as worker

    task = _make_task()
    task.deadline_at = datetime.now(UTC) + timedelta(milliseconds=50)
    failed = AsyncMock(return_value=True)
    persist = AsyncMock(return_value=True)

    await _run_until_deadline(
        monkeypatch,
        worker,
        control=_StartedControl(),
        task=task,
        failed=failed,
        persist=persist,
    )

    result = failed.await_args.kwargs["task_result"]
    assert result["outcome"] == "UNKNOWN"
    assert result["reason"] == "absolute_deadline_exceeded"
    assert result["created_ids"]["campaigns"] == ["120001"]
    assert result["created_ids"]["adsets"] == ["120002", "120003"]
    # Шаг падения — последняя стадия, о которой отчитался execute.
    assert result["failed_step"] == "uploading"
    assert failed.await_args.kwargs["created_meta_ids"]["adsets"] == ["120002", "120003"]
    # Осиротевшие id ложатся и в task_queue.result — разбор очереди смотрит туда.
    persist.assert_awaited_once()
    assert persist.await_args.kwargs["created_ids"]["campaigns"] == ["120001"]


# Тот же перечень нужен, когда дедлайн/отмену первым увидел контроль в БД: исход
# UNKNOWN, но чистить оператору всё равно есть что.
@pytest.mark.asyncio
async def test_control_abort_after_boundary_keeps_itemized_created_ids(monkeypatch) -> None:
    import apps.campaign_creator_worker.main as worker

    class _AbortingControl(_StartedControl):
        async def check(self) -> None:
            return None

    async def _execute(*_args, created_sink=None, **_kwargs):
        created = created_sink if created_sink is not None else {}
        created.setdefault("campaigns", []).append("120001")
        raise CreatorTaskControlAbort("cancel_requested", external_started=True)

    async def _direct(_control, operation_factory):
        return await operation_factory()

    failed = AsyncMock(return_value=True)
    persist = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "parse_run_config", lambda _cfg: SimpleNamespace())
    monkeypatch.setattr(worker, "resolve_concepts_from_config", lambda _cfg: {})
    monkeypatch.setattr(worker, "build_campaign_spec", lambda _cfg: object())
    monkeypatch.setattr(worker, "set_run_status", AsyncMock(return_value=True))
    monkeypatch.setattr(worker, "execute_campaign_spec", _execute)
    monkeypatch.setattr(worker, "run_with_task_control", _direct)
    monkeypatch.setattr(worker, "finalize_run_failed", failed)
    monkeypatch.setattr(worker, "_persist_partial_created_ids", persist)

    await worker._execute_run(
        object(),
        _make_task(),
        run_id="run-1",
        config={},
        client=AsyncMock(),
        uploader=AsyncMock(),
        control=_AbortingControl(),
    )

    result = failed.await_args.kwargs["task_result"]
    assert result["outcome"] == "UNKNOWN"
    assert result["created_ids"]["campaigns"] == ["120001"]
    persist.assert_awaited_once()
