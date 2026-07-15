# -*- coding: utf-8 -*-
"""Unit: H-1 — крэш-путь reconciler НЕ ретраит необратимые mutations.

Money-safety: если worker создал кампанию в Meta (create_campaign/duplicate_campaign),
но умер ДО mark_succeeded, задача застряла в 'running'. Слепой reconcile перевёл бы
её в 'retrying' → повторное создание = ДУБЛЬ кампании + двойной бюджет. Здесь
проверяем проводку: run_once сначала уводит необратимые в failed, затем requeue
ОСТАЛЬНОГО с exclude_kinds; при failed>0 шлётся алерт в ops-топик.

SQL-поведение (stuck create_campaign → failed, stuck pause_ad → retrying) проверяется
интеграционным тестом test_reconciler_irreversible_db.py на реальном Postgres.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as meta
import apps.reconciler_worker.worker as rw
from core.meta_api.schemas import IRREVERSIBLE_MUTATION_KINDS
from core.tasks.queue import fail_stuck_irreversible


@pytest.fixture(autouse=True)
def _duplicate_recovery_noop(monkeypatch):
    monkeypatch.setattr(
        rw,
        "_canonical_prepare_stuck_duplicate_recovery",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        rw,
        "_canonical_fail_stuck_duplicate_without_checkpoint",
        AsyncMock(return_value=0),
    )


# Контракт: набор необратимых kinds — единый источник, воркер использует его же (DRY)
def test_irreversible_kinds_single_source() -> None:
    assert IRREVERSIBLE_MUTATION_KINDS == frozenset(
        {"create_campaign", "duplicate_campaign", "duplicate_adset_structure"}
    )
    # meta_api_worker не должен держать собственную копию — только алиас на схему
    assert meta._IRREVERSIBLE_KINDS is IRREVERSIBLE_MUTATION_KINDS


# render_irreversible_alert: HTML + count, без проблем согласования числа
def test_render_irreversible_alert_contains_count_and_html() -> None:
    txt = rw.render_irreversible_alert(3)
    assert "<b>3</b>" in txt
    assert "Reconciler" in txt
    assert "вручную" in txt


# run_once: необратимые уводятся в failed ПЕРВЫМИ, reconcile получает exclude_kinds
@pytest.mark.asyncio
async def test_run_once_passes_irreversible_kinds(monkeypatch) -> None:
    fail_spy = AsyncMock(return_value=0)
    reconcile_spy = AsyncMock(return_value=0)
    drafts_spy = AsyncMock(return_value=0)
    monkeypatch.setattr(rw, "_canonical_fail_stuck_irreversible", fail_spy)
    monkeypatch.setattr(rw, "_canonical_reconcile_stuck_running", reconcile_spy)
    monkeypatch.setattr(rw, "_canonical_cancel_stale_drafts", drafts_spy)

    counts = await rw.run_once(object())

    # fail вызван с необратимым набором
    assert fail_spy.await_args.kwargs["mutation_kinds"] == frozenset(
        {"create_campaign", "duplicate_campaign"}
    )
    # reconcile исключает те же kinds из requeue
    assert reconcile_spy.await_args.kwargs["exclude_kinds"] is IRREVERSIBLE_MUTATION_KINDS
    assert counts["irreversible_failed"] == 0


@pytest.mark.asyncio
async def test_run_once_schedules_duplicate_recovery_before_generic_fail(monkeypatch) -> None:
    order: list[str] = []

    async def prepare(*args, **kwargs):
        order.append("prepare")
        return 1

    async def fail_without_checkpoint(*args, **kwargs):
        order.append("duplicate_without_checkpoint")
        return 0

    async def fail_generic(*args, **kwargs):
        order.append("generic")
        assert "duplicate_adset_structure" not in kwargs["mutation_kinds"]
        return 0

    monkeypatch.setattr(rw, "_canonical_prepare_stuck_duplicate_recovery", prepare)
    monkeypatch.setattr(
        rw,
        "_canonical_fail_stuck_duplicate_without_checkpoint",
        fail_without_checkpoint,
    )
    monkeypatch.setattr(rw, "_canonical_fail_stuck_irreversible", fail_generic)
    monkeypatch.setattr(rw, "_canonical_reconcile_stuck_running", AsyncMock(return_value=0))
    monkeypatch.setattr(rw, "_canonical_cancel_stale_drafts", AsyncMock(return_value=0))

    counts = await rw.run_once(object())

    assert order == ["prepare", "duplicate_without_checkpoint", "generic"]
    assert counts["duplicate_recovery_scheduled"] == 1


@pytest.mark.asyncio
async def test_run_once_immediately_alerts_when_duplicate_recovery_scheduled(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        rw,
        "_canonical_prepare_stuck_duplicate_recovery",
        AsyncMock(return_value=2),
    )
    monkeypatch.setattr(rw, "_canonical_fail_stuck_irreversible", AsyncMock(return_value=0))
    monkeypatch.setattr(rw, "_canonical_reconcile_stuck_running", AsyncMock(return_value=0))
    monkeypatch.setattr(rw, "_canonical_cancel_stale_drafts", AsyncMock(return_value=0))
    alert = AsyncMock()
    monkeypatch.setattr(rw, "_maybe_alert_duplicate_recovery", alert)
    engine = object()

    counts = await rw.run_once(engine)

    assert counts["duplicate_recovery_scheduled"] == 2
    alert.assert_awaited_once_with(engine, 2)


@pytest.mark.asyncio
async def test_duplicate_recovery_alert_has_dedicated_category_and_manual_check(
    monkeypatch,
) -> None:
    import core.telegram.worker_notify as worker_notify

    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(worker_notify, "notify_recipients", notify)
    engine = object()

    await rw._maybe_alert_duplicate_recovery(engine, 3)

    notify.assert_awaited_once()
    kwargs = notify.await_args.kwargs
    assert kwargs["category"] == "reconciler_duplicate_recovery"
    assert kwargs["dedup_key"] == "reconciler:duplicate_adset_structure:recovery_scheduled"
    assert kwargs["dedup_ttl_seconds"] == 30 * 60
    assert "PAUSE-only recovery" in kwargs["text"]
    assert "Ads Manager" in kwargs["text"]


@pytest.mark.asyncio
async def test_generic_irreversible_fail_defensively_excludes_duplicate_kind() -> None:
    class FakeResult:
        rowcount = 1

    class FakeConnection:
        def __init__(self) -> None:
            self.params = None

        async def execute(self, statement, params):
            self.params = params
            return FakeResult()

    connection = FakeConnection()

    class FakeEngine:
        def begin(self):
            class Context:
                async def __aenter__(self):
                    return connection

                async def __aexit__(self, *args):
                    return False

            return Context()

    count = await fail_stuck_irreversible(
        FakeEngine(),
        mutation_kinds=IRREVERSIBLE_MUTATION_KINDS,
    )

    assert count == 1
    assert set(connection.params["kinds"]) == {"create_campaign", "duplicate_campaign"}


# run_once: при failed>0 шлётся best-effort алерт в ops-топик
@pytest.mark.asyncio
async def test_run_once_alerts_when_irreversible_failed(monkeypatch) -> None:
    monkeypatch.setattr(rw, "_canonical_fail_stuck_irreversible", AsyncMock(return_value=2))
    monkeypatch.setattr(rw, "_canonical_reconcile_stuck_running", AsyncMock(return_value=0))
    monkeypatch.setattr(rw, "_canonical_cancel_stale_drafts", AsyncMock(return_value=0))

    alert_spy = AsyncMock()
    monkeypatch.setattr(rw, "_maybe_alert_irreversible", alert_spy)

    counts = await rw.run_once(object())

    assert counts["irreversible_failed"] == 2
    alert_spy.assert_awaited_once()
    assert alert_spy.await_args.args[1] == 2


# run_once: при failed==0 алерт НЕ шлётся
@pytest.mark.asyncio
async def test_run_once_no_alert_when_zero(monkeypatch) -> None:
    monkeypatch.setattr(rw, "_canonical_fail_stuck_irreversible", AsyncMock(return_value=0))
    monkeypatch.setattr(rw, "_canonical_reconcile_stuck_running", AsyncMock(return_value=0))
    monkeypatch.setattr(rw, "_canonical_cancel_stale_drafts", AsyncMock(return_value=0))
    alert_spy = AsyncMock()
    monkeypatch.setattr(rw, "_maybe_alert_irreversible", alert_spy)

    await rw.run_once(object())

    alert_spy.assert_not_awaited()


# _maybe_alert_irreversible: при ненастроенном TG не падает и не шлёт
@pytest.mark.asyncio
async def test_maybe_alert_no_tg_config_silent(monkeypatch) -> None:
    import core.telegram.service as tg_service

    monkeypatch.setattr(tg_service, "load_telegram_config", AsyncMock(return_value=None))
    # Не должно бросить исключение
    await rw._maybe_alert_irreversible(object(), 3)


# _maybe_alert_irreversible: count<=0 — мгновенный выход без обращения к БД
@pytest.mark.asyncio
async def test_maybe_alert_zero_count_noop() -> None:
    await rw._maybe_alert_irreversible(object(), 0)
