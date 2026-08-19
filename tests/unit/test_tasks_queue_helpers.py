# -*- coding: utf-8 -*-
"""Unit: _calc_retry_available_at exponential backoff exact values.

HIGH #8 из backend_test_audit_round_8: функция _calc_retry_available_at — чистая функция
без зависимостей, не покрыта тестами. Проверяем точные значения задержки backoff:
  base=30s, max=300s, формула: min(30 * 2^attempt, 300)

1. attempt=0 → +30s
2. attempt=1 → +60s
3. attempt=2 → +120s
4. attempt=3 → +240s
5. attempt=4 → +300s (cap)
6. attempt=10 → +300s (cap стабилен)
"""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.tasks.queue as task_queue
from core.tasks.queue import (
    BROWSER_READY_CLAIM_TASK_TYPES,
    Task,
    _calc_retry_available_at,
    _returned_task_rows,
    _returned_value,
    _row_to_task,
    claim_next_task,
    create_task,
)

# Разрешённое отклонение по времени в мс (потокобезопасность datetime.now())
_TOLERANCE_SECONDS = 2


class _InsertResult:
    def first(self):
        return None


def _engine_with_connection():
    connection = SimpleNamespace(execute=AsyncMock(return_value=_InsertResult()))
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = context
    return engine, connection


def _approx_seconds(dt: datetime, *, expected: int) -> None:
    """Проверяем что dt - now() ≈ expected секунд (±допуск)."""
    delta = (dt - datetime.now(timezone.utc)).total_seconds()
    assert abs(delta - expected) <= _TOLERANCE_SECONDS, (
        f"Ожидали +{expected}s, получили +{delta:.1f}s"
    )


# attempt=0 → задержка 30 секунд
def test_backoff_attempt_0() -> None:
    """Первая попытка (attempt=0): задержка 30 секунд."""
    result = _calc_retry_available_at(0)
    _approx_seconds(result, expected=30)


# attempt=1 → задержка 60 секунд
def test_backoff_attempt_1() -> None:
    """Вторая попытка (attempt=1): задержка 60 секунд."""
    result = _calc_retry_available_at(1)
    _approx_seconds(result, expected=60)


# attempt=2 → задержка 120 секунд
def test_backoff_attempt_2() -> None:
    """Третья попытка (attempt=2): задержка 120 секунд."""
    result = _calc_retry_available_at(2)
    _approx_seconds(result, expected=120)


# attempt=3 → задержка 240 секунд
def test_backoff_attempt_3() -> None:
    """Четвёртая попытка (attempt=3): задержка 240 секунд."""
    result = _calc_retry_available_at(3)
    _approx_seconds(result, expected=240)


# attempt=4 → задержка 300 секунд (cap)
def test_backoff_attempt_4_hits_cap() -> None:
    """Пятая попытка (attempt=4): 30*16=480s > cap → должен дать ровно 300s."""
    result = _calc_retry_available_at(4)
    _approx_seconds(result, expected=300)


# attempt=10 → задержка остаётся на cap 300s
def test_backoff_attempt_10_stays_at_cap() -> None:
    """Десятая попытка: cap 300s стабилен, не растёт дальше."""
    result = _calc_retry_available_at(10)
    _approx_seconds(result, expected=300)


# Монотонность: каждая следующая попытка >= предыдущей до cap
def test_backoff_is_monotone_until_cap() -> None:
    """Задержка монотонно растёт: attempt=0,1,2,3 — каждая следующая больше или равна."""
    delays = [_calc_retry_available_at(a) for a in range(5)]
    for i in range(len(delays) - 1):
        assert delays[i] <= delays[i + 1], (
            f"Задержка не монотонна: attempt={i} ({delays[i]}) > attempt={i + 1} ({delays[i + 1]})"
        )


# Возвращает timezone-aware datetime
def test_backoff_returns_utc_aware_datetime() -> None:
    """_calc_retry_available_at возвращает timezone-aware datetime в UTC."""
    result = _calc_retry_available_at(0)
    assert result.tzinfo is not None, (
        "_calc_retry_available_at должен вернуть timezone-aware datetime"
    )
    assert result > datetime.now(timezone.utc), "available_at должен быть в будущем"


def test_task_snapshot_is_keyword_only_and_has_no_partial_constructor() -> None:
    parameters = inspect.signature(Task).parameters.values()
    assert parameters
    assert all(parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in parameters)
    optional = {
        parameter.name
        for parameter in parameters
        if parameter.default is not inspect.Parameter.empty
    }
    assert optional == {
        "browser_profile_id",
        # Сессия браузера — часть свидетельства готовности: задача обязана
        # работать в той же сессии, где готовность подтверждена (#184).
        "browser_session_id",
        "browser_readiness_generation",
    }


def test_task_row_mapping_is_mandatory() -> None:
    with pytest.raises(TypeError, match="named columns"):
        _row_to_task((1, "meta_api_mutation"))


def test_terminal_update_requires_returning_rows() -> None:
    with pytest.raises(TypeError, match="UPDATE .. RETURNING"):
        _returned_task_rows(SimpleNamespace(rowcount=1))
    with pytest.raises(TypeError, match="named columns"):
        _returned_value(SimpleNamespace(id=1), "id")


@pytest.mark.asyncio
async def test_money_deadline_starts_when_execution_is_claimed() -> None:
    """Browser queue wait must not spend the operation's 30 second budget."""
    engine, connection = _engine_with_connection()

    await create_task(
        engine,
        task_type="meta_api_mutation",
        idempotency_key="auto:pause_ad:230011223344:deadline",
        payload={"mutation_kind": "pause_ad", "target_id": "230011223344"},
        requested_by="bot_auto_stop",
    )

    insert_params = connection.execute.await_args.args[1]
    assert insert_params["lane"] == "money"
    assert insert_params["deadline_at"] is None


@pytest.mark.asyncio
async def test_money_claim_assigns_fresh_cross_runtime_deadline_after_browser_wait() -> None:
    """A >30s maintenance wait remains claimable, then execution is bounded."""
    claim_sql = str(task_queue._BROWSER_READY_CLAIM_SQL)

    assert "task.lane = 'money'" in claim_sql
    assert "WHEN task.lane = 'money' THEN" in claim_sql
    assert "make_interval(secs => :money_deadline_seconds)" in claim_sql
    assert "browser_maintenance" in claim_sql

    from core.deadlines import bind_absolute_deadline
    from core.meta_api.client import _LIVE_OPERATION_AUTHORITY_SQL, MetaApiClient
    from core.meta_api.errors import AmbiguousResultError, PreDispatchRejectedError
    from core.meta_api.operation_authority import _CONSUME_PENDING_CAPABILITY_SQL

    worker_source = inspect.getsource(
        __import__("apps.meta_api_worker.main", fromlist=["_execute_with_touch"])
    )
    assert "tq.deadline_at > clock_timestamp()" in str(_LIVE_OPERATION_AUTHORITY_SQL)
    assert "task.deadline_at > clock_timestamp()" in str(_CONSUME_PENDING_CAPABILITY_SQL)
    assert "bind_absolute_deadline(task.deadline_at)" in worker_source

    client = MetaApiClient(session_id="session-deadline")
    client._stub = SimpleNamespace()
    with bind_absolute_deadline(datetime.now(timezone.utc) - timedelta(seconds=1)):
        # Дедлайн проверяется ДО отправки, поэтому исход — доказанный отказ, а не
        # потерянный ответ: ручная сверка при нуле ушедших запросов бессмысленна.
        with pytest.raises(PreDispatchRejectedError, match="absolute deadline exhausted") as raised:
            await client.execute_graph_call(
                method="POST",
                endpoint="/230011223344",
                query_params={"status": "PAUSED"},
                ad_account_id="42",
            )
    assert not isinstance(raised.value, AmbiguousResultError)


def test_overdue_reconciler_does_not_reject_unclaimed_money_work() -> None:
    """Old pre-fix rows with an enqueue-time deadline must survive rollout."""
    source = inspect.getsource(task_queue.expire_overdue_tasks)

    assert "lane <> 'money'" in source


def test_readiness_rejection_cannot_bypass_attempt_budget() -> None:
    source = inspect.getsource(task_queue.release_after_browser_readiness_rejection)

    assert "attempt_count = CASE" in source
    assert "lane = 'money'" in source
    assert "attempt_count + 1 >= max_attempts" in source
    assert "browser_readiness_attempts_exhausted" in source
    assert "browser_readiness_reconciliation_attempts_exhausted" in source


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", sorted(BROWSER_READY_CLAIM_TASK_TYPES))
async def test_generic_claim_fails_closed_for_browser_ready_task_types(
    task_type: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every generic caller is delegated to the durable scheduling gate."""
    captured: dict[str, object] = {}

    async def gated_claim(engine, **kwargs):
        captured["engine"] = engine
        captured.update(kwargs)
        return "gated"

    monkeypatch.setattr(task_queue, "claim_browser_ready_task", gated_claim)
    engine = object()
    result = await claim_next_task(
        engine,
        task_type=task_type,
        lanes=("money",),
        lease_seconds=71,
    )

    assert result == "gated"
    assert captured == {
        "engine": engine,
        "task_type": task_type,
        "lanes": ("money",),
        "worker_id": None,
        "lease_seconds": 71,
    }


# 18.08.2026: две задачи залива закрыл подметальщик по абсолютному дедлайну, а
# campaign_run у обеих остался queued на восемнадцать часов. Оператор видел
# зависание вместо отказа; естественная реакция — запустить залив заново, а это
# money-путь: два run на одну кампанию.
@pytest.mark.asyncio
async def test_terminal_task_closes_its_campaign_run() -> None:
    class _Conn:
        def __init__(self) -> None:
            self.statements: list[tuple[str, dict]] = []

        async def execute(self, statement, params=None):
            self.statements.append((str(statement), dict(params or {})))
            return SimpleNamespace(rowcount=1, first=lambda: None, fetchall=lambda: [])

    conn = _Conn()
    run_id = str(uuid.uuid4())
    await task_queue._terminalize_campaign_run(
        conn,
        phase="failed",
        payload={"run_id": run_id},
        result={"reason": "absolute_deadline_exceeded"},
    )

    assert len(conn.statements) == 1
    sql, params = conn.statements[0]
    assert "UPDATE campaign_run" in sql
    # Уже закрытый залив не переписываем: терминальное состояние окончательно.
    assert "status NOT IN ('succeeded', 'failed', 'cancelled')" in sql
    assert params["run_id"] == run_id
    assert "Срок задачи истёк" in params["reason"]


@pytest.mark.asyncio
async def test_terminal_task_without_run_id_touches_nothing() -> None:
    class _Conn:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, statement, params=None):
            self.calls += 1
            return SimpleNamespace(rowcount=0)

    conn = _Conn()
    await task_queue._terminalize_campaign_run(
        conn, phase="failed", payload={"ad_id": "1"}, result=None
    )
    await task_queue._terminalize_campaign_run(
        conn, phase="failed", payload={"run_id": "не-uuid"}, result=None
    )
    assert conn.calls == 0


def test_terminal_task_transition_closes_the_run() -> None:
    """Закрытие залива стоит в общей точке терминализации, а не у одного вызывающего."""
    source = inspect.getsource(task_queue._transition_terminal_task)
    assert "_terminalize_campaign_run" in source


# 18.08.2026: задача залива пролежала полчаса незабранной и сгорела по дедлайну
# с текстом, называвшим следствие. Диагноз обязан называть невыполненное условие
# готовности и повторять конъюнкты гейта в том же порядке.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "expected_code"),
    [
        ({"maintenance": True}, "browser_maintenance"),
        ({"state": None}, "readiness_missing"),
        (
            {"state": "profile_mismatch", "reason_code": "vision_profile_mismatch"},
            "state:profile_mismatch",
        ),
        ({"contract": 4}, "browser_contract_incompatible"),
        ({"observed_profile": "other"}, "vision_profile_mismatch"),
        ({"has_session": False}, "browser_session_missing"),
        ({"fresh": False}, "readiness_stale"),
    ],
)
async def test_waiting_task_names_the_unmet_readiness_condition(row, expected_code) -> None:
    base = {
        "waiting": 2,
        "run_ids": ["11111111-1111-1111-1111-111111111111"],
        "expected_profile": "profile-1",
        "state": "ready",
        "reason_code": "ready",
        "contract": 5,
        "observed_profile": "profile-1",
        "has_session": True,
        "fresh": True,
        "maintenance": False,
    }
    base.update(row)

    class _Conn:
        async def execute(self, statement, params=None):
            return SimpleNamespace(first=lambda: SimpleNamespace(**base))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    engine = SimpleNamespace(connect=lambda: _Conn())
    block = await task_queue.explain_browser_claim_block(
        engine, task_type="campaign_create", lanes=("bulk",)
    )

    assert block is not None
    assert block.reason_code == expected_code
    assert block.human.startswith("Жду готовности браузера:")
    assert block.run_ids == ("11111111-1111-1111-1111-111111111111",)


@pytest.mark.asyncio
async def test_open_gate_and_empty_queue_explain_nothing() -> None:
    for row in ({"waiting": 0}, {"waiting": 3}):
        base = {
            "waiting": 1,
            "run_ids": [],
            "expected_profile": "profile-1",
            "state": "ready",
            "reason_code": "ready",
            "contract": 5,
            "observed_profile": "profile-1",
            "has_session": True,
            "fresh": True,
            "maintenance": False,
        }
        base.update(row)

        class _Conn:
            async def execute(self, statement, params=None, _row=base):
                return SimpleNamespace(first=lambda: SimpleNamespace(**_row))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        engine = SimpleNamespace(connect=lambda: _Conn())
        assert (
            await task_queue.explain_browser_claim_block(
                engine, task_type="campaign_create", lanes=("bulk",)
            )
            is None
        )
