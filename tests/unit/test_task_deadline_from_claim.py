# -*- coding: utf-8 -*-
"""Инварианты дедлайна задачи (#219).

20.08.2026 залив пролежал в очереди, пока канал браузера был недоступен, и
сгорел по дедлайну, ни разу не обратившись к Meta: окно в 1800 секунд
отсчитывалось от постановки в очередь, а ожидание готовности его съедало.
Сама просроченная строка при этом осталась ``pending`` — оператор видел
«в очереди» у мёртвой задачи.

Здесь зафиксировано три инварианта:

1. Окно на работу отсчитывается от ЗАХВАТА задачи воркером — для каждой
   полосы, а не только для money. Ожидание готовности канала его не тратит.
2. Задача, пролежавшая дольше предельного срока ожидания, закрывается
   терминально: ничего не уходило во внешнюю систему → исход ``REJECTED`` и
   причина, называющая именно ожидание в очереди.
3. Закрытие не зависит от отдельного подметальщика: потребитель очереди,
   которому нечего забрать, закрывает свои просроченные задачи сам.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.tasks.queue as task_queue
from core.commands.campaign_runs import resume_unavailable_reason
from core.tasks.queue import (
    TASK_LANES,
    claim_browser_ready_task,
    claim_next_task,
)

_CLAIM_STATEMENTS = {
    "generic": (task_queue._CLAIM_SQL, "task_queue"),
    "browser_ready": (task_queue._BROWSER_READY_CLAIM_SQL, "task"),
}


def _recording_engine() -> tuple[MagicMock, list[tuple[str, dict]]]:
    """Движок, у которого claim ничего не находит, с журналом операторов."""
    statements: list[tuple[str, dict]] = []

    async def execute(statement, params=None):
        statements.append((str(statement), dict(params or {})))
        return SimpleNamespace(
            first=lambda: None,
            all=lambda: [],
            rowcount=0,
        )

    connection = SimpleNamespace(execute=execute)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = context
    return engine, statements


# ============ 1. окно исполнения начинается в момент захвата ============


@pytest.mark.parametrize("name", sorted(_CLAIM_STATEMENTS))
def test_claim_restarts_the_execution_window_for_every_lane(name: str) -> None:
    """Захват задаёт окно заново; дедлайн постановки в очередь не переносится."""
    statement, alias = _CLAIM_STATEMENTS[name]
    sql = str(statement)

    # Ни одна ветка не оставляет задаче остаток окна, съеденный ожиданием.
    assert f"ELSE {alias}.deadline_at" not in sql
    for lane in sorted(TASK_LANES):
        assert f":{lane}_deadline_seconds" in sql, (
            f"полоса {lane} не получает собственного окна исполнения при захвате"
        )


def test_execution_window_is_defined_for_every_lane() -> None:
    """Окно исполнения известно для каждой полосы, а не только для money."""
    params = task_queue._execution_window_params()
    windows = {
        name.removesuffix("_deadline_seconds"): value
        for name, value in params.items()
        if name.endswith("_deadline_seconds")
    }

    assert set(windows) == set(TASK_LANES)
    assert all(int(value) > 0 for value in windows.values())
    # Полчаса на залив: длинная работа не должна упираться в общий дефолт.
    assert windows["bulk"] == 30 * 60


@pytest.mark.parametrize("name", sorted(_CLAIM_STATEMENTS))
def test_claim_never_opens_a_window_longer_than_its_own_lease(name: str) -> None:
    """Задача обязана успеть закрыть себя под тем же фенсом, что и открыла.

    Терминальная запись требует живого lease. Окно, способное пережить аренду,
    оставило бы задачу running: воркер залива аренду не продлевает.
    """
    sql = str(_CLAIM_STATEMENTS[name][0])

    assert "GREATEST(" in sql
    assert ":lease_seconds" in sql
    assert ":finalize_headroom_seconds" in sql
    assert task_queue._FINALIZE_HEADROOM_SECONDS > 0


def test_queue_wait_limit_is_separate_from_the_execution_window() -> None:
    """Предел ожидания в очереди — отдельная величина, не окно исполнения."""
    assert "money" not in task_queue._LANE_QUEUE_WAIT_SECONDS
    assert set(task_queue._LANE_QUEUE_WAIT_SECONDS) <= set(TASK_LANES)


# ============ 2. просроченная задача закрывается честно ============


def test_overdue_queue_wait_is_rejected_not_unknown() -> None:
    """Ничего не уходило в Meta → REJECTED и причина про ожидание в очереди."""
    sql = str(task_queue._EXPIRE_OVERDUE_TASKS_SQL)

    assert "'queue_wait_limit_exceeded'" in sql
    assert "'REJECTED'" in sql
    # Двусмысленный исход остаётся UNKNOWN: там внешняя граница уже пройдена.
    assert "external_started_at IS NOT NULL" in sql
    assert "'UNKNOWN'" in sql
    # Money-полоса не выбрасывается из очереди подметальщиком.
    assert "lane <> 'money'" in sql


def test_operator_reads_the_queue_wait_reason_in_his_own_words() -> None:
    """Причина закрытия залива названа словами оператора, а не кодом."""
    reason = task_queue._campaign_run_terminal_reason(
        "failed",
        {"outcome": "REJECTED", "reason": "queue_wait_limit_exceeded"},
    )

    assert "очеред" in reason.lower()
    assert "queue_wait_limit_exceeded" not in reason


def test_queue_wait_rejection_keeps_the_launch_restartable(tmp_path, monkeypatch) -> None:
    """Отказ до отправки не запрещает повторный залив: ничего не создано."""
    upload_dir = tmp_path / "upload-219"
    upload_dir.mkdir()
    (upload_dir / "a.jpg").write_bytes(b"image")
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(tmp_path))

    reason = resume_unavailable_reason(
        run_status="failed",
        run_config={
            "account": {
                "act_id": "123",
                "page_id": "100",
                "pixel_id": "200",
                "timezone_name": "Europe/Kaliningrad",
                "currency": "EUR",
                "currency_exponent": 2,
                "account_context_observed_at": "2026-07-29T12:00:00+00:00",
            },
            "offer_code": "GH_CR",
            "destination_link": "https://example.com",
            "start_date": "2026-07-30",
            "budget": {
                "level": "campaign",
                "currency": "EUR",
                "daily_amount": "50.00",
                "bid_strategy": "COST_CAP",
                "bid_amount": "1.50",
            },
            "targeting": {"countries": ["DE"]},
            "campaigns": [
                {
                    "key": "static",
                    "name": "{offer}",
                    "adsets": [{"name": "s1", "dir": "static/a1", "glob": "*"}],
                    "concept_refs": ["a.jpg"],
                }
            ],
            "creo_root": "upload-219",
        },
        created_meta_ids={},
        task={
            "task_status": "failed",
            "task_result": {
                "outcome": "REJECTED",
                "reason": "queue_wait_limit_exceeded",
                "reconcile_required": False,
            },
            "external_started_at": None,
            "cancel_requested_at": None,
        },
    )

    assert reason is None


# ============ 3. закрытие не ждёт отдельного подметальщика ============


@pytest.mark.asyncio
async def test_idle_browser_claim_closes_its_own_overdue_tasks() -> None:
    """Пустой claim закрывает просроченные задачи своего типа и полос."""
    engine, statements = _recording_engine()

    claim = await claim_browser_ready_task(
        engine,
        task_type="campaign_create",
        lanes=("bulk",),
    )

    assert claim.task is None
    expiries = [(sql, params) for sql, params in statements if "queue_wait_limit_exceeded" in sql]
    assert len(expiries) == 1, "просроченная задача осталась бы pending"
    sql, params = expiries[0]
    assert params == {"tt": "campaign_create", "lanes": ["bulk"]}
    assert "task_type = :tt" in sql
    # Полосы подставляются раскрывающимся bindparam: чужие задачи не заденет.
    assert "lane IN (" in sql


@pytest.mark.asyncio
async def test_idle_generic_claim_closes_its_own_overdue_tasks() -> None:
    """То же для общего claim: подметание принадлежит потребителю очереди."""
    engine, statements = _recording_engine()

    claim = await claim_next_task(
        engine,
        task_type="observer_scan",
        lanes=("interactive", "background"),
    )

    assert claim.task is None
    expiries = [params for sql, params in statements if "queue_wait_limit_exceeded" in sql]
    assert expiries == [{"tt": "observer_scan", "lanes": ["interactive", "background"]}]


@pytest.mark.asyncio
async def test_successful_claim_does_not_sweep() -> None:
    """Когда задача забрана, лишних записей в очередь claim не делает."""
    claimed = {
        "id": 41,
        "task_type": "campaign_create",
        "status": "running",
        "idempotency_key": "campaign:run:1",
        "payload": {"run_id": "11111111-1111-1111-1111-111111111111"},
        "attempt_count": 0,
        "max_attempts": 1,
        "requested_by": "operator",
        "last_error": None,
        "created_at": None,
        "external_started_at": None,
        "result": None,
        "lane": "bulk",
        "priority": 20,
        "available_at": None,
        "deadline_at": None,
        "lease_owner": None,
        "lease_token": 1,
        "lease_expires_at": None,
        "cancel_requested_at": None,
        "cancel_reason": None,
        "correlation_id": None,
        "browser_profile_id": "profile-1",
        "browser_session_id": "session-1",
        "browser_readiness_generation": 7,
    }
    row = SimpleNamespace(_mapping=claimed, **claimed)
    statements: list[str] = []

    async def execute(statement, params=None):
        statements.append(str(statement))
        return SimpleNamespace(
            first=lambda: row if "UPDATE task_queue" in str(statement) else None,
            all=lambda: [],
            rowcount=1,
        )

    connection = SimpleNamespace(execute=execute)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = context

    claim = await claim_browser_ready_task(
        engine,
        task_type="campaign_create",
        lanes=("bulk",),
    )

    assert claim.task is not None
    assert not [sql for sql in statements if "queue_wait_limit_exceeded" in sql]
