# -*- coding: utf-8 -*-
"""Ручная сверка неизвестного исхода: отдельный факт поверх UNKNOWN.

Инварианты, которые фиксируют эти тесты:

* закрытие требует выбора наблюдения, а не кнопки «ок»;
* исход задачи не переписывается на CONFIRMED — внешний результат так и остался
  неизвестным, и врать об этом нельзя;
* событие журнала несёт автора и время и создаётся в той же транзакции;
* повторная сверка тем же наблюдением не создаёт второго события;
* «всё ещё активен» не закрывает вопрос;
* сверенная терминальная задача перестаёт быть вечным барьером — иначе после
  неё авто-стоп для этого объявления мёртв навсегда.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError as PydanticValidationError

import apps.api.routers.v1.operator as operator_router
import core.commands.service as service_module
import core.operator.queries as queries
import core.tasks.queue as task_queue
from core.commands.service import _target_barrier
from core.tasks.action_reason import automation_stopped_reason
from core.tasks.queue import (
    MANUAL_REVIEW_CLOSING_OBSERVATIONS,
    MANUAL_REVIEW_OBSERVATIONS,
    ManualReviewNotApplicableError,
    ManualReviewTaskNotFoundError,
    record_manual_reconciliation,
)

_CORRELATION_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
_REVIEWED_AT = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


class _Rows:
    """Последовательность ответов на execute() внутри одной транзакции."""

    def __init__(self, *rows: object) -> None:
        self._rows = list(rows)

    def first(self):  # pragma: no cover - подменяется в _connection
        raise AssertionError("unused")


def _result(row: object | None):
    obj = MagicMock()
    obj.first = MagicMock(return_value=row)
    obj.one = MagicMock(return_value=row)
    obj.rowcount = 1 if row is not None else 0
    return obj


def _task_row(
    *,
    task_id: int = 4242,
    status: str = "failed",
    result: dict | None = None,
    observation: str | None = None,
    task_type: str = "meta_api_mutation",
):
    return SimpleNamespace(
        id=task_id,
        status=status,
        result={"outcome": "UNKNOWN", "reconciliation_exhausted": True}
        if result is None
        else result,
        correlation_id=_CORRELATION_ID,
        task_type=task_type,
        payload={"mutation_kind": "pause_ad", "target_id": "230011223344"},
        manual_review_observation=observation,
        manual_review_at=_REVIEWED_AT if observation else None,
        manual_review_by="operator:web" if observation else None,
    )


def _engine(*rows: object):
    """Движок, возвращающий заданную последовательность результатов execute()."""
    responses = [_result(row) for row in rows]
    connection = AsyncMock()
    connection.execute = AsyncMock(side_effect=responses)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = context
    return engine, connection


@pytest.fixture()
def enqueue(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(task_queue, "enqueue_notification_in_transaction", spy)
    return spy


def _statements(connection) -> list[str]:
    return [str(call.args[0]) for call in connection.execute.await_args_list]


# --------------------------------------------------------------------------- #
# Закрытие требует выбора наблюдения                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_review_without_observation_is_refused_before_any_sql(enqueue) -> None:
    engine, connection = _engine()

    with pytest.raises(ValueError):
        await record_manual_reconciliation(
            engine,
            task_id=4242,
            observation="ok",
            reviewed_by="operator:web",
        )

    connection.execute.assert_not_awaited()
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_without_author_is_refused(enqueue) -> None:
    engine, connection = _engine()

    with pytest.raises(ValueError):
        await record_manual_reconciliation(
            engine,
            task_id=4242,
            observation="stopped",
            reviewed_by="   ",
        )

    connection.execute.assert_not_awaited()
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_known_observations_are_exactly_three() -> None:
    assert MANUAL_REVIEW_OBSERVATIONS == frozenset({"stopped", "active", "missing"})
    assert MANUAL_REVIEW_CLOSING_OBSERVATIONS == frozenset({"stopped", "missing"})


# --------------------------------------------------------------------------- #
# Сверять можно только терминальный неизвестный исход                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_missing_task_is_reported_as_not_found(enqueue) -> None:
    engine, _ = _engine(None)

    with pytest.raises(ManualReviewTaskNotFoundError):
        await record_manual_reconciliation(
            engine,
            task_id=4242,
            observation="stopped",
            reviewed_by="operator:web",
        )

    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmed_task_cannot_be_manually_reviewed(enqueue) -> None:
    engine, connection = _engine(_task_row(status="succeeded", result={"outcome": "CONFIRMED"}))

    with pytest.raises(ManualReviewNotApplicableError):
        await record_manual_reconciliation(
            engine,
            task_id=4242,
            observation="stopped",
            reviewed_by="operator:web",
        )

    assert len(connection.execute.await_args_list) == 1  # только SELECT ... FOR UPDATE
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_running_task_cannot_be_manually_reviewed(enqueue) -> None:
    """Автоматика ещё работает — операторская сверка сняла бы барьер раньше времени."""
    engine, connection = _engine(
        _task_row(status="retrying", result={"outcome": "UNKNOWN", "reconcile_required": True})
    )

    with pytest.raises(ManualReviewNotApplicableError):
        await record_manual_reconciliation(
            engine,
            task_id=4242,
            observation="stopped",
            reviewed_by="operator:web",
        )

    assert len(connection.execute.await_args_list) == 1
    enqueue.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Запись факта: автор, время, отдельная ось от исхода                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_review_records_author_and_time_without_touching_outcome(enqueue) -> None:
    engine, connection = _engine(
        _task_row(),
        SimpleNamespace(manual_review_at=_REVIEWED_AT, manual_review_by="operator:web"),
    )

    recorded = await record_manual_reconciliation(
        engine,
        task_id=4242,
        observation="stopped",
        reviewed_by="operator:web",
    )

    assert recorded.was_changed is True
    assert recorded.question_closed is True
    assert recorded.observation == "stopped"
    assert recorded.reviewed_by == "operator:web"
    assert recorded.reviewed_at == _REVIEWED_AT

    update = _statements(connection)[1]
    assert "UPDATE task_queue" in update
    assert "manual_review_observation = :observation" in update
    assert "manual_review_at = NOW()" in update
    assert "manual_review_by = :reviewed_by" in update
    # Исход внешней операции остался неизвестным: ни статус, ни result не трогаем.
    assert "status" not in update
    assert "result" not in update
    assert "outcome" not in update


@pytest.mark.asyncio
async def test_review_event_carries_author_and_time(enqueue) -> None:
    engine, _ = _engine(
        _task_row(),
        SimpleNamespace(manual_review_at=_REVIEWED_AT, manual_review_by="operator:web"),
    )

    await record_manual_reconciliation(
        engine,
        task_id=4242,
        observation="stopped",
        reviewed_by="operator:web",
    )

    enqueue.assert_awaited_once()
    spec = enqueue.await_args.args[1]
    assert spec.event_type == "task_manual_review"
    assert spec.correlation_id == _CORRELATION_ID
    assert spec.dedupe_key == "task:4242:manual-review:stopped"
    rendered = " ".join([spec.facts.title, spec.facts.summary or "", *spec.facts.lines])
    assert "operator:web" in rendered
    assert "4242" in rendered


@pytest.mark.asyncio
async def test_review_never_reports_confirmed_outcome(enqueue) -> None:
    """Сверка — отдельный факт, а не подтверждение внешнего результата."""
    engine, connection = _engine(
        _task_row(),
        SimpleNamespace(manual_review_at=_REVIEWED_AT, manual_review_by="operator:web"),
    )

    await record_manual_reconciliation(
        engine,
        task_id=4242,
        observation="stopped",
        reviewed_by="operator:web",
    )

    joined = " ".join(_statements(connection))
    assert "CONFIRMED" not in joined
    assert "status = 'succeeded'" not in joined
    spec = enqueue.await_args.args[1]
    assert "подтвержд" not in (spec.facts.summary or "").lower()


# --------------------------------------------------------------------------- #
# Идемпотентность                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_repeated_same_observation_makes_no_second_event(enqueue) -> None:
    engine, connection = _engine(_task_row(observation="stopped"))

    recorded = await record_manual_reconciliation(
        engine,
        task_id=4242,
        observation="stopped",
        reviewed_by="operator:tma",
    )

    assert recorded.was_changed is False
    assert recorded.question_closed is True
    # Автор первой сверки не переписывается второй вкладкой.
    assert recorded.reviewed_by == "operator:web"
    assert recorded.reviewed_at == _REVIEWED_AT
    assert len(connection.execute.await_args_list) == 1
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_row_is_locked_before_decision(enqueue) -> None:
    """Две вкладки сериализуются на строке задачи, а не гоняются за UPDATE."""
    engine, connection = _engine(_task_row(observation="stopped"))

    await record_manual_reconciliation(
        engine,
        task_id=4242,
        observation="stopped",
        reviewed_by="operator:web",
    )

    assert "FOR UPDATE" in _statements(connection)[0]


@pytest.mark.asyncio
async def test_changed_observation_reopens_the_question(enqueue) -> None:
    """Оператор посмотрел ещё раз и увидел другое — новое наблюдение и новый след."""
    engine, _ = _engine(
        _task_row(observation="stopped"),
        SimpleNamespace(manual_review_at=_REVIEWED_AT, manual_review_by="operator:web"),
    )

    recorded = await record_manual_reconciliation(
        engine,
        task_id=4242,
        observation="active",
        reviewed_by="operator:web",
    )

    assert recorded.was_changed is True
    assert recorded.question_closed is False
    enqueue.assert_awaited_once()
    assert enqueue.await_args.args[1].dedupe_key == "task:4242:manual-review:active"


# --------------------------------------------------------------------------- #
# «Всё ещё активен» вопрос не закрывает                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_active_observation_does_not_close_the_question(enqueue) -> None:
    engine, _ = _engine(
        _task_row(),
        SimpleNamespace(manual_review_at=_REVIEWED_AT, manual_review_by="operator:web"),
    )

    recorded = await record_manual_reconciliation(
        engine,
        task_id=4242,
        observation="active",
        reviewed_by="operator:web",
    )

    assert recorded.question_closed is False
    spec = enqueue.await_args.args[1]
    assert spec.severity == "critical"
    assert spec.facts.risk


@pytest.mark.asyncio
async def test_missing_observation_closes_the_question(enqueue) -> None:
    engine, _ = _engine(
        _task_row(),
        SimpleNamespace(manual_review_at=_REVIEWED_AT, manual_review_by="operator:web"),
    )

    recorded = await record_manual_reconciliation(
        engine,
        task_id=4242,
        observation="missing",
        reviewed_by="operator:web",
    )

    assert recorded.question_closed is True


# --------------------------------------------------------------------------- #
# Обратимость: сверенная задача перестаёт быть вечным барьером                 #
# --------------------------------------------------------------------------- #


def _barrier_row(*, status: str, result: dict, observation: str | None = None):
    return SimpleNamespace(
        id=17,
        correlation_id=_CORRELATION_ID,
        status=status,
        result=result,
        completed_at=_REVIEWED_AT,
        updated_at=_REVIEWED_AT,
        action_kind="pause_ad",
        has_post_evidence=False,
        manual_review_observation=observation,
    )


def test_unreviewed_terminal_unknown_still_blocks_new_command() -> None:
    row = _barrier_row(
        status="failed",
        result={"outcome": "UNKNOWN", "reconciliation_exhausted": True},
    )

    assert _target_barrier(row) is row


def test_manually_reviewed_unknown_no_longer_blocks_new_command() -> None:
    """Иначе после терминального UNKNOWN авто-стоп для этого объявления мёртв."""
    row = _barrier_row(
        status="failed",
        result={"outcome": "UNKNOWN", "reconciliation_exhausted": True},
        observation="stopped",
    )

    assert _target_barrier(row) is None


def test_manually_reviewed_active_observation_also_allows_new_command() -> None:
    """Оператор видит объект живым — команда должна быть возможна, а не заперта."""
    row = _barrier_row(
        status="failed",
        result={"outcome": "UNKNOWN"},
        observation="active",
    )

    assert _target_barrier(row) is None


def test_manual_review_does_not_unblock_work_in_flight() -> None:
    row = _barrier_row(
        status="running",
        result={"outcome": "UNKNOWN", "reconcile_required": True},
        observation="stopped",
    )

    assert _target_barrier(row) is row


def test_barrier_query_reads_the_manual_review_column() -> None:
    """Иначе снятие барьера молча не сработает: колонки нет в строке."""
    source = inspect.getsource(service_module.CommandService._enqueue_ad_action)

    assert "task.manual_review_observation" in source


# --------------------------------------------------------------------------- #
# Оператор видит, почему автоматика больше не пытается                         #
# --------------------------------------------------------------------------- #


def test_exhausted_reconciliation_names_itself_in_operator_language() -> None:
    text_ru = automation_stopped_reason(
        {"outcome": "UNKNOWN", "reconciliation_exhausted": True},
        status="failed",
    )

    assert text_ru is not None
    assert "больше не проверяет" in text_ru


def test_stuck_campaign_create_explains_why_it_is_not_retried() -> None:
    """У зависшего залива reconcile_required стоит, а звать сверку уже некому."""
    text_ru = automation_stopped_reason(
        {
            "outcome": "UNKNOWN",
            "reconcile_required": True,
            "reason": "stuck_campaign_create_after_worker_loss",
        },
        status="failed",
    )

    assert text_ru is not None
    assert "дубль" in text_ru


def test_task_still_in_queue_is_not_called_abandoned() -> None:
    assert (
        automation_stopped_reason(
            {"outcome": "UNKNOWN", "reconcile_required": True},
            status="retrying",
        )
        is None
    )


def test_unknown_machine_code_never_reaches_the_operator() -> None:
    assert (
        automation_stopped_reason(
            {"outcome": "UNKNOWN", "reason": "some_internal_code_v2"},
            status="failed",
        )
        is None
    )


def test_confirmed_task_has_no_automation_stopped_reason() -> None:
    assert automation_stopped_reason({"outcome": "CONFIRMED"}, status="succeeded") is None


# --------------------------------------------------------------------------- #
# Проекция действия для оператора                                              #
# --------------------------------------------------------------------------- #


def _projection_row(
    *,
    status: str = "failed",
    result: dict | None = None,
    observation: str | None = None,
):
    return SimpleNamespace(
        id=4242,
        task_type="campaign_create",
        status=status,
        payload={},
        result=result if result is not None else {"outcome": "UNKNOWN"},
        requested_by="operator:web",
        last_error=None,
        created_at=_REVIEWED_AT,
        updated_at=_REVIEWED_AT,
        correlation_id=_CORRELATION_ID,
        target_label=None,
        manual_review_observation=observation,
        manual_review_at=_REVIEWED_AT if observation else None,
        manual_review_by="operator:web" if observation else None,
    )


def test_reviewed_action_still_reads_as_unknown() -> None:
    item = queries._task_item(_projection_row(observation="stopped"))

    assert item["state"] == "unknown"
    assert item["manual_review"]["observation"] == "stopped"
    assert item["manual_review"]["question_closed"] is True
    assert item["manual_review"]["by"] == "operator:web"
    assert item["manual_review"]["at"] == _REVIEWED_AT


def test_unreviewed_terminal_unknown_offers_manual_review() -> None:
    item = queries._task_item(_projection_row())

    assert item["manual_review"] is None
    assert item["manual_review_available"] is True


def test_running_task_does_not_offer_manual_review() -> None:
    item = queries._task_item(
        _projection_row(status="retrying", result={"outcome": "UNKNOWN", "reconcile_required": True})
    )

    assert item["manual_review_available"] is False
    assert item["automation_stopped_reason"] is None


def test_confirmed_action_offers_nothing_to_review() -> None:
    item = queries._task_item(_projection_row(status="succeeded", result={"outcome": "CONFIRMED"}))

    assert item["state"] == "confirmed"
    assert item["manual_review_available"] is False


def test_actions_query_selects_the_manual_review_columns() -> None:
    source = inspect.getsource(queries.fetch_operator_actions)

    assert "tq.manual_review_observation" in source
    assert "tq.manual_review_by" in source


# --------------------------------------------------------------------------- #
# Операторский эндпоинт                                                        #
# --------------------------------------------------------------------------- #


def _request(principal: str = "owner:42"):
    return SimpleNamespace(state=SimpleNamespace(operator_principal=principal))


@pytest.mark.asyncio
async def test_endpoint_records_review_without_claiming_confirmation(monkeypatch) -> None:
    record = AsyncMock(
        return_value=task_queue.ManualReconciliation(
            task_id=4242,
            observation="stopped",
            reviewed_at=_REVIEWED_AT,
            reviewed_by="owner:42",
            question_closed=True,
            was_changed=True,
            correlation_id=_CORRELATION_ID,
        )
    )
    monkeypatch.setattr(operator_router, "record_manual_reconciliation", record)

    result = await operator_router.record_operator_manual_review(
        task_id=4242,
        body=operator_router.OperatorManualReviewRequest(observation="stopped"),
        engine=object(),
        request=_request(),
        reviewed_by="untrusted-header",
    )

    assert not isinstance(result, operator_router.JSONResponse)
    # Исход внешней операции остался неизвестным.
    assert result.state == "unknown"
    assert result.manual_review.question_closed is True
    assert result.recorded is True
    # Личность — из доверенной границы, а не из заголовка браузера.
    assert record.await_args.kwargs["reviewed_by"] == "owner:42"


@pytest.mark.asyncio
async def test_endpoint_replay_is_idempotent(monkeypatch) -> None:
    record = AsyncMock(
        return_value=task_queue.ManualReconciliation(
            task_id=4242,
            observation="stopped",
            reviewed_at=_REVIEWED_AT,
            reviewed_by="owner:42",
            question_closed=True,
            was_changed=False,
            correlation_id=_CORRELATION_ID,
        )
    )
    monkeypatch.setattr(operator_router, "record_manual_reconciliation", record)

    result = await operator_router.record_operator_manual_review(
        task_id=4242,
        body=operator_router.OperatorManualReviewRequest(observation="stopped"),
        engine=object(),
        request=_request(),
    )

    assert result.recorded is False


@pytest.mark.asyncio
async def test_endpoint_refuses_task_the_system_still_works_on(monkeypatch) -> None:
    monkeypatch.setattr(
        operator_router,
        "record_manual_reconciliation",
        AsyncMock(side_effect=ManualReviewNotApplicableError("task_is_still_running")),
    )

    result = await operator_router.record_operator_manual_review(
        task_id=4242,
        body=operator_router.OperatorManualReviewRequest(observation="stopped"),
        engine=object(),
        request=_request(),
    )

    assert isinstance(result, operator_router.JSONResponse)
    assert result.status_code == 409


@pytest.mark.asyncio
async def test_endpoint_reports_missing_action(monkeypatch) -> None:
    monkeypatch.setattr(
        operator_router,
        "record_manual_reconciliation",
        AsyncMock(side_effect=ManualReviewTaskNotFoundError("4242")),
    )

    result = await operator_router.record_operator_manual_review(
        task_id=4242,
        body=operator_router.OperatorManualReviewRequest(observation="stopped"),
        engine=object(),
        request=_request(),
    )

    assert isinstance(result, operator_router.JSONResponse)
    assert result.status_code == 404


def test_endpoint_rejects_an_observation_outside_the_closed_list() -> None:
    with pytest.raises(PydanticValidationError):
        operator_router.OperatorManualReviewRequest(observation="ok")
