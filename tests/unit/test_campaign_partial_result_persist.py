# -*- coding: utf-8 -*-
"""Персист created_ids partial-провала в task_queue.result (MID-24).

У 8 failed campaign_create (26-27.06) result был NULL — id осиротевших объектов
Meta жили только в ротируемых логах. Теперь PartialCreateError-ветка пишет их
и в task_queue.result (кроме campaign_run.created_meta_ids).

Живой провал 20.08: ветка ack_lost строила статическую строку и не сохраняла
исходное исключение НИГДЕ (ни в last_error, ни в result, ни в логе) —
``_failure_diagnostics`` закрывает это для всех терминальных веток, которым
причины не хватало (ack_lost, external_result_ambiguous, CreatorTaskFenceLost).
"""

from __future__ import annotations

import inspect
import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.campaign_creator_worker.main import _failure_diagnostics, _persist_partial_created_ids
from core.meta_api.errors import (
    AmbiguousResultError,
    BrowserOperationRejectedError,
    PermanentError,
)
from core.tasks.irreversible_control import CreatorTaskFenceLost
from core.tasks.queue import Task


def _task(task_id: int) -> Task:
    now = datetime.now(UTC)
    return Task(
        id=task_id,
        task_type="campaign_create",
        status="running",
        idempotency_key=f"campaign-{task_id}",
        payload={"run_id": "run-1"},
        attempt_count=0,
        max_attempts=5,
        requested_by="test",
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane="bulk",
        priority=0,
        available_at=now,
        deadline_at=now + timedelta(minutes=30),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000042"),
        lease_token=11,
        lease_expires_at=now + timedelta(minutes=30),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )


def _fake_engine():
    """engine.begin() как async context manager с мок-conn."""
    conn = AsyncMock()
    conn.execute.return_value = MagicMock(rowcount=1)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin = MagicMock(return_value=ctx)
    return engine, conn


# UPDATE уходит с guard status='running' и полным JSON (partial_fail/step/created_ids)
@pytest.mark.asyncio
async def test_persist_writes_result_json_with_running_guard():
    engine, conn = _fake_engine()
    created = {"campaigns": ["c1"], "adsets": ["s1", "s2"], "ads": []}

    await _persist_partial_created_ids(
        engine, task=_task(42), created_ids=created, failed_step="creating"
    )

    conn.execute.assert_awaited_once()
    sql = str(conn.execute.await_args.args[0])
    params = conn.execute.await_args.args[1]
    assert "SET result" in sql
    assert "status = 'running'" in sql  # не затираем result чужой терминальной задачи
    assert "lease_owner = :lease_owner" in sql
    assert "lease_token = :lease_token" in sql
    assert params["id"] == 42
    payload = json.loads(params["r"])
    assert payload["outcome"] == "UNKNOWN"
    assert payload["manual_review_required"] is True
    assert payload["partial_fail"] is True
    assert payload["failed_step"] == "creating"
    assert payload["created_ids"] == created


# Сбой записи (БД упала) → warning, БЕЗ исключения наружу (mark_failed важнее)
@pytest.mark.asyncio
async def test_persist_failure_is_swallowed(caplog):
    engine = MagicMock()
    engine.begin = MagicMock(side_effect=ConnectionError("db down"))

    with caplog.at_level("WARNING"):
        await _persist_partial_created_ids(
            engine, task=_task(7), created_ids={"campaigns": []}, failed_step="creating"
        )

    assert any("created_ids" in r.getMessage() for r in caplog.records)


# Контракт ветки PartialCreateError: checkpoint пишется до атомарного terminal finalize.
def test_partial_branch_persists_before_terminal_finalize():
    import apps.campaign_creator_worker.main as m

    src = inspect.getsource(m._execute_run)
    branch = src.split("except PartialCreateError")[1].split("except Exception")[0]
    assert "_persist_partial_created_ids" in branch
    assert branch.index("_persist_partial_created_ids") < branch.index("finalize_run_failed")


# ====================== _failure_diagnostics: причина без утечки ======================
#
# Инвариант: по записи упавшего прогона видно, ЧТО именно отказало, без чтения
# исходников — и наоборот, никогда не через repr(exc)/str(exc), потому что
# MetaApiError.__repr__ несёт fbtrace_id и полный Graph-текст (core/meta_api/errors.py).


ALLOWED_DIAGNOSTIC_KEYS = {
    "exception_class",
    "reason_code",
    "code",
    "subcode",
    "endpoint",
    "stage",
    "failed_step",
}


# КРИТИЧНО: fbtrace_id и «сырой» Graph-текст ни при каком составе цепочки причин
# не должны попасть в структурную запись — только именованные безопасные поля.
def test_failure_diagnostics_never_leaks_fbtrace_or_raw_meta_text():
    secret_trace = "AY-SECRET-FBTRACE-9f31c2"
    raw_meta_text = "raw Graph response with account token XYZ-DO-NOT-LEAK and PII"
    exc = PermanentError(
        raw_meta_text,
        code=100,
        subcode=33,
        endpoint="/act_123/campaigns",
        fbtrace_id=secret_trace,
    )
    # Подтверждаем сначала саму находку: repr() у MetaApiError её несёт.
    assert secret_trace in repr(exc)
    assert raw_meta_text in repr(exc)

    diagnostics = _failure_diagnostics(exc, stage="creating", failed_step="creating_campaign")

    assert set(diagnostics) <= ALLOWED_DIAGNOSTIC_KEYS
    serialized = json.dumps(diagnostics)
    assert secret_trace not in serialized
    assert raw_meta_text not in serialized
    assert diagnostics["exception_class"] == "PermanentError"
    assert diagnostics["code"] == 100
    assert diagnostics["subcode"] == 33
    assert diagnostics["endpoint"] == "/act_123/campaigns"
    assert diagnostics["stage"] == "creating"
    assert diagnostics["failed_step"] == "creating_campaign"


# ack-lost — реальная форма цепочки: PartialCreateError оборачивает исходную причину
# через `raise ... from cause` (core/campaign_builder/execute.py). Диагностика обязана
# дойти до неё, а не остановиться на обёртке воркера.
def test_failure_diagnostics_walks_cause_chain_to_the_real_reason():
    cause = AmbiguousResultError("Graph API error code=None subcode=None", code=-2)
    try:
        raise RuntimeError("ack_lost (step=creating)") from cause
    except RuntimeError as wrapper:
        diagnostics = _failure_diagnostics(wrapper, stage="creating", failed_step="creating")

    assert diagnostics["exception_class"] == "AmbiguousResultError"
    assert diagnostics["code"] == -2
    assert diagnostics["stage"] == "creating"
    assert diagnostics["failed_step"] == "creating"


# reason_code называет причину только на семье BrowserOperationRejectedError — тот же
# словарь, что строит человеческий текст карточки инцидента (core/meta_api/errors.py).
def test_failure_diagnostics_picks_up_browser_rejection_reason_code():
    cause = BrowserOperationRejectedError(
        "срок действия разрешения на операцию истёк",
        reason_code="capability_expired",
        endpoint="/act_123/adsets",
    )
    try:
        raise RuntimeError("wrapped") from cause
    except RuntimeError as wrapper:
        diagnostics = _failure_diagnostics(wrapper)

    assert diagnostics["reason_code"] == "capability_expired"
    assert diagnostics["exception_class"] == "BrowserOperationRejectedError"
    assert diagnostics["endpoint"] == "/act_123/adsets"


# CreatorTaskFenceLost не оборачивает Graph-причину (лизу потерял этот воркер, не Meta) —
# запись остаётся структурной (класс + стадия), а не пустой.
def test_failure_diagnostics_on_fence_lost_has_class_and_stage_only():
    exc = CreatorTaskFenceLost("creator task 9 lost lease before campaign_create")

    diagnostics = _failure_diagnostics(exc, stage="uploading")

    assert diagnostics == {"exception_class": "CreatorTaskFenceLost", "stage": "uploading"}


# Циклический __cause__ (искусственный, для защиты от бесконечного цикла) не должен
# зависать — тот же приём защиты, что и у _proven_pre_dispatch_rejection в main.py.
def test_failure_diagnostics_tolerates_cause_cycle():
    exc = RuntimeError("a")
    exc.__cause__ = exc  # искусственный цикл

    diagnostics = _failure_diagnostics(exc)

    assert diagnostics["exception_class"] == "RuntimeError"


# ====================== терминальные ветки собирают причину ======================
#
# Живой провал 20.08: ack_lost и external_result_ambiguous печатали текст без причины,
# обе ветки CreatorTaskFenceLost — тем более. Проверяем по исходнику, что каждая
# терминальная ветка теперь строит _failure_diagnostics и ни одна не зовёт repr(exc).


def test_ack_lost_branch_builds_structural_diagnostics_without_repr():
    import apps.campaign_creator_worker.main as m

    src = inspect.getsource(m._execute_run)
    branch = src.split("except PartialCreateError")[1].split("except Exception")[0]
    ack_lost_branch = branch.split('reason = "ack_lost_nothing_confirmed"')[1]
    assert "_failure_diagnostics(exc" in ack_lost_branch
    assert "{exc!r}" not in ack_lost_branch


def test_external_result_ambiguous_branch_builds_structural_diagnostics_without_repr():
    import apps.campaign_creator_worker.main as m

    src = inspect.getsource(m._execute_run)
    branch = src.split("if control.external_started and not _is_proven_pre_dispatch(exc):")[
        1
    ].split("kind = classify_execution_error(exc)")[0]
    assert "_failure_diagnostics(exc" in branch
    assert "{exc!r}" not in branch


def test_creator_task_fence_lost_branches_build_structural_diagnostics():
    import apps.campaign_creator_worker.main as m

    process_src = inspect.getsource(m.process_one_task)
    execute_src = inspect.getsource(m._execute_run)
    fence_lost_branches = [
        block.split("except ")[0]
        for src in (process_src, execute_src)
        for block in src.split("except CreatorTaskFenceLost as exc:")[1:]
    ]
    assert len(fence_lost_branches) == 3  # process_one_task ×1, _execute_run ×2
    for branch in fence_lost_branches:
        assert "_failure_diagnostics(exc" in branch


# task_result и error всех четырёх веток несут diagnostics/причину — не только лог.
def test_campaign_unknown_result_carries_diagnostics_when_given():
    import apps.campaign_creator_worker.main as m

    task = _task(3)
    diagnostics = {"exception_class": "AmbiguousResultError", "code": -2}

    result = m._campaign_unknown_result(
        task, run_id="run-1", reason="ack_lost_nothing_confirmed", diagnostics=diagnostics
    )
    assert result["diagnostics"] == diagnostics

    # Без диагностики ключа в результате нет — «неизвестно» не превращается в {}.
    unmarked = m._campaign_unknown_result(task, run_id="run-1", reason="absolute_deadline_exceeded")
    assert "diagnostics" not in unmarked
