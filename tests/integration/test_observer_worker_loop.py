# -*- coding: utf-8 -*-
"""Интеграционный тест observer_worker main loop через fake gate + Redis read-model.

Покрывает: begin/finish scan_run, run_one_cycle, paused/empty/error outcomes,
durable scan execution. Не требует ни browser-agent, ни Vision.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

import apps.observer_worker.main as observer_main
from apps.observer_worker.main import (
    ScanCycleOutput,
    _maybe_alert_degraded,
    main_loop,
    run_one_cycle,
)
from core.scanner.models import (
    SCANNER_METRICS_CONTRACT_REVISION,
    ScannedAdRow,
)

pytestmark = pytest.mark.usefixtures("known_test_cabinet_timezones")


def _row(fb_ad_id: str = "230011", **overrides) -> ScannedAdRow:
    defaults = dict(
        fb_ad_id=fb_ad_id,
        campaign_id="120200000000002",
        adset_id="120200000000003",
        campaign_name="CR2 | KE | MV | promo",
        adset_name="EQ_KE",
        ad_name="Av01",
        delivery_status="ACTIVE",
        spend=Decimal("3.0"),
        reach=1000,
        impressions=2000,
        clicks=50,
        cpc=Decimal("0.05"),
        ctr=Decimal("2.5"),
        leads=10,
        registrations=5,
        deposits=2,
        outbound_clicks=30,
        landing_page_views=20,
    )
    defaults.update(overrides)
    return ScannedAdRow(**defaults)


class _FakeGate:
    """Fake ScannerGate — программируемый ScanCycleOutput."""

    def __init__(self, output: ScanCycleOutput | Exception):
        self._output = output
        self.calls = 0
        self.last_campaign_ids: list[str] | None = None
        self.last_owner_tag: str | None = None
        # Какие явно выбранные кабинеты были запрошены.
        self.account_ids: list[str] = []

    async def open_cabinet_tabs(self, ad_account_ids: list[str]) -> list[dict]:
        return [{"ad_account_id": account_id, "opened": True} for account_id in ad_account_ids]

    async def run_one_scan(
        self,
        ad_account_id: str,
        campaign_ids: list[str] | None = None,
        owner_tag: str | None = None,
    ) -> ScanCycleOutput:
        self.calls += 1
        self.last_campaign_ids = campaign_ids
        self.last_owner_tag = owner_tag
        self.account_ids.append(ad_account_id)
        if isinstance(self._output, Exception):
            raise self._output
        return self._output


@pytest_asyncio.fixture
async def clean_obs_tables(pg_engine):
    """Чистит scan_runs/observer-таблицы/task_queue/offers до и после."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            for t in (
                "task_queue",
                "alert_events",
                "ad_metrics",
                "ad_alert_state",
                "fb_ads",
                "fb_adsets",
                "fb_campaigns",
                "offer_rules",
                "offers",
                "scan_runs",
            ):
                await conn.execute(text(f"DELETE FROM {t}"))

    await _truncate()
    yield
    await _truncate()


@pytest_asyncio.fixture
async def offer_cr2(pg_engine, clean_obs_tables):
    offer_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO offers (id, code, name, is_active, ad_account_ids) "
                "VALUES (:i, 'CR2', 'CR2', TRUE, ARRAY['111'])"
            ),
            {"i": offer_id},
        )
        await conn.execute(
            text(
                "INSERT INTO offer_rules (offer_id, cpa_threshold, currency) VALUES (:o, :c, 'USD')"
            ),
            {"o": offer_id, "c": Decimal("10.00")},
        )
    return offer_id


@pytest_asyncio.fixture
async def ensure_observer_config_enabled(pg_engine):
    """Гарантирует что singleton observer_config есть и is_scanning_enabled=true.

    Задаёт НЕпустой campaign_ids: при one-cabinet скане пустой allowlist =
    opt-in блокировка (`allowlist_blocks_scan` → скан не гоняется, gate не вызывается).
    Чтобы тесты реально доходили до gate.run_one_scan, нужен непустой allowlist.
    """
    async with pg_engine.begin() as conn:
        # apply_schema создал строку с дефолтами; проверим что есть и принудительно включим
        await conn.execute(
            text(
                """
                INSERT INTO observer_config
                    (singleton_key, is_scanning_enabled, campaign_ids)
                VALUES ('default', TRUE, ARRAY['1001'])
                ON CONFLICT (singleton_key) DO UPDATE
                SET is_scanning_enabled = TRUE,
                    interval_seconds = 1,
                    campaign_ids = ARRAY['1001']
                """
            )
        )
    yield
    # после теста возвращаем дефолтные значения
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE observer_config SET interval_seconds = 90, "
                "campaign_ids = ARRAY[]::text[], owner_campaign_tag = NULL "
                "WHERE singleton_key = 'default'"
            )
        )


# Сценарий: один цикл с одной строкой → success outcome, scan_run финализирован
@pytest.mark.asyncio
async def test_run_one_cycle_happy_path(
    pg_engine, ensure_observer_config_enabled, offer_cr2, monkeypatch
) -> None:
    gate = _FakeGate(
        ScanCycleOutput(
            rows=[_row()],
            metrics_contract_revision=SCANNER_METRICS_CONTRACT_REVISION,
            total_passes=1,
        )
    )
    original_process_scan_rows = observer_main.process_scan_rows
    received_kwargs: dict[str, object] = {}

    async def capture_process_scan_rows(*args, **kwargs):
        received_kwargs.update(kwargs)
        return await original_process_scan_rows(*args, **kwargs)

    monkeypatch.setattr(
        observer_main,
        "process_scan_rows",
        capture_process_scan_rows,
    )

    summary = await run_one_cycle(pg_engine, gate=gate)

    assert gate.calls == 1
    assert "cycle_ts" not in received_kwargs
    assert summary["outcome"] == "success"
    scan_id = summary["accounts"][0]["scan_id"]
    assert scan_id is not None
    assert summary["rows_total"] == 1

    async with pg_engine.connect() as conn:
        # scan_run завершён
        sr = (
            await conn.execute(
                text(
                    "SELECT outcome, rows_total, duration_ms FROM scan_runs "
                    "WHERE id = :i ORDER BY started_at DESC LIMIT 1"
                ),
                {"i": scan_id},
            )
        ).first()
        assert sr[0] == "success"
        assert sr[1] == 1
        assert sr[2] is not None and sr[2] >= 0


# Malformed hierarchy makes the whole cabinet snapshot non-authoritative: no
# catalog, metric, FSM, alert, or money-task write may occur.
@pytest.mark.asyncio
async def test_incomplete_scan_row_fails_closed_before_all_domain_writes(
    pg_engine, ensure_observer_config_enabled, offer_cr2
) -> None:
    gate = _FakeGate(
        ScanCycleOutput(
            rows=[_row(adset_id="")],
            metrics_contract_revision=SCANNER_METRICS_CONTRACT_REVISION,
            total_passes=1,
        )
    )

    summary = await run_one_cycle(pg_engine, gate=gate)

    assert gate.calls == 1
    assert summary["outcome"] == "partial"
    assert summary["rows_total"] == 0
    assert "partial_rows:1" in summary["error"]
    scan_id = summary["accounts"][0]["scan_id"]

    async with pg_engine.connect() as conn:
        scan_run = (
            await conn.execute(
                text("SELECT outcome, error_message FROM scan_runs WHERE id = :scan_id"),
                {"scan_id": scan_id},
            )
        ).one()
        counts = (
            await conn.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM fb_campaigns) AS campaigns,
                        (SELECT count(*) FROM fb_adsets) AS adsets,
                        (SELECT count(*) FROM fb_ads) AS ads,
                        (SELECT count(*) FROM ad_metrics) AS metrics,
                        (SELECT count(*) FROM ad_alert_state) AS states,
                        (SELECT count(*) FROM alert_events) AS alerts,
                        (SELECT count(*) FROM task_queue) AS tasks
                    """
                )
            )
        ).one()

    assert scan_run.outcome == "partial"
    assert "partial_rows:1" in scan_run.error_message
    assert counts == (0, 0, 0, 0, 0, 0, 0)


# Сценарий: is_scanning_enabled=false → outcome='paused', gate не вызывается
@pytest.mark.asyncio
async def test_paused_when_scanning_disabled(pg_engine, clean_obs_tables) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO observer_config (singleton_key, is_scanning_enabled) "
                "VALUES ('default', FALSE) "
                "ON CONFLICT (singleton_key) DO UPDATE SET is_scanning_enabled = FALSE"
            )
        )

    gate = _FakeGate(
        ScanCycleOutput(
            rows=[_row()],
            metrics_contract_revision=SCANNER_METRICS_CONTRACT_REVISION,
        )
    )
    summary = await run_one_cycle(pg_engine, gate=gate)
    assert summary["outcome"] == "paused"
    assert summary["accounts"] == []
    assert gate.calls == 0


# Неподтверждённый empty не является нулём: arbitrary reason → explicit partial.
@pytest.mark.asyncio
async def test_unclassified_empty_scan_is_partial(
    pg_engine, ensure_observer_config_enabled, offer_cr2
) -> None:
    gate = _FakeGate(
        ScanCycleOutput(
            rows=[],
            metrics_contract_revision=SCANNER_METRICS_CONTRACT_REVISION,
            empty_reason="cabinet was reset",
        )
    )
    summary = await run_one_cycle(pg_engine, gate=gate)
    assert summary["outcome"] == "partial"
    scan_id = summary["accounts"][0]["scan_id"]
    async with pg_engine.connect() as conn:
        sr = (
            await conn.execute(
                text(
                    "SELECT outcome, error_message FROM scan_runs "
                    "WHERE id = :i ORDER BY started_at DESC LIMIT 1"
                ),
                {"i": scan_id},
            )
        ).first()
    assert sr[0] == "partial"
    assert sr[1] == "cabinet was reset"


# Сценарий: gate упал с исключением → outcome='error', error_message заполнен
@pytest.mark.asyncio
async def test_gate_raises(pg_engine, ensure_observer_config_enabled, offer_cr2) -> None:
    gate = _FakeGate(ConnectionError("browser-agent unreachable"))
    summary = await run_one_cycle(pg_engine, gate=gate)
    assert summary["outcome"] == "error"
    assert "ConnectionError" in summary["error"]


@pytest.mark.asyncio
async def test_unknown_cabinet_timezone_creates_no_money_or_fsm_state(
    pg_engine,
    ensure_observer_config_enabled,
    offer_cr2,
    monkeypatch,
) -> None:
    """A numeric/implicit UTC fallback can never reach the observer pipeline."""
    import apps.observer_worker.main as obs_main

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE offers SET ad_account_ids = ARRAY['999'] WHERE code = 'CR2'")
        )
        await conn.execute(text("DELETE FROM meta_account_snapshot WHERE account_id = '999'"))
    monkeypatch.setattr(obs_main, "notify_recurring_incident", AsyncMock(return_value=True))
    monkeypatch.setattr(obs_main, "resolve_recurring_incident", AsyncMock(return_value=True))

    gate = _FakeGate(
        ScanCycleOutput(
            rows=[_row(spend=Decimal("50"), deposits=0, leads=0, registrations=0)],
            metrics_contract_revision=SCANNER_METRICS_CONTRACT_REVISION,
        )
    )
    summary = await run_one_cycle(pg_engine, gate=gate)

    assert summary["outcome"] == "error"
    assert summary["error"] == "cabinet_timezone_unknown"
    async with pg_engine.connect() as conn:
        counts = (
            await conn.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM ad_metrics) AS metrics,
                        (SELECT count(*) FROM ad_alert_state) AS states,
                        (SELECT count(*) FROM task_queue) AS tasks
                    """
                )
            )
        ).one()
    assert counts == (0, 0, 0)


# Сценарий: main_loop с лимитом итераций (через should_continue) — graceful exit
@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_main_loop_runs_n_cycles_and_exits(
    pg_engine, ensure_observer_config_enabled, offer_cr2, monkeypatch
) -> None:
    # Sleep между циклами мокаем no-op: clamp_interval поднимает любой base-интервал
    # до MIN_INTERVAL_SECONDS=10 (anti-detect), иначе тест ждёт реальные ~10с/цикл и
    # упирается в timeout. Проверяем логику циклов, не длительность sleep.
    import apps.observer_worker.main as obs_main

    async def _no_sleep(*a, **k):
        return None

    monkeypatch.setattr(obs_main, "_wait_for_durable_scan", _no_sleep)

    iterations = {"n": 0}

    def _should_continue() -> bool:
        iterations["n"] += 1
        return iterations["n"] <= 2  # ровно 2 итерации

    gate = _FakeGate(
        ScanCycleOutput(
            rows=[_row(fb_ad_id="230001")],
            metrics_contract_revision=SCANNER_METRICS_CONTRACT_REVISION,
        )
    )

    async def _gate_factory():
        return gate

    await main_loop(
        gate_factory=_gate_factory,
        should_continue=_should_continue,
    )

    # Должен был сделать минимум один scan (второй не успеет дойти до sleep'а)
    assert gate.calls >= 1


# Layer 3: every failed tick reaches the durable facade with one stable event key.
@pytest.mark.asyncio
async def test_degraded_alert_uses_stable_durable_event_key(pg_engine) -> None:
    from unittest.mock import AsyncMock, patch

    with patch(
        "apps.observer_worker.main.notify_recurring_incident",
        AsyncMock(side_effect=(True, False)),
    ) as spy:
        ok1 = await _maybe_alert_degraded(pg_engine, consecutive_failures=3, last_error="page gone")
        assert ok1 is True
        ok2 = await _maybe_alert_degraded(pg_engine, consecutive_failures=4, last_error="page gone")
        assert ok2 is False
        assert spy.await_count == 2
        assert {call.kwargs["incident_key"] for call in spy.await_args_list} == {
            "observer:degraded"
        }


# Layer 3: main_loop sends every threshold breach to the durable facade.
@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_main_loop_degraded_alert_after_threshold(
    pg_engine,
    ensure_observer_config_enabled,
    offer_cr2,
    monkeypatch,
) -> None:
    # Sleep между циклами мокаем no-op: clamp_interval поднимает base до
    # MIN_INTERVAL_SECONDS=10 (interval=0 не помогает — clamp всё равно 10с), иначе timeout.
    import apps.observer_worker.main as obs_main

    async def _no_sleep(*a, **k):
        return None

    monkeypatch.setattr(obs_main, "_wait_for_durable_scan", _no_sleep)
    # _get_database_url воркеров перенаправлен на тестовую БД autouse-фикстурой
    # _redirect_worker_db_to_test (conftest) — main_loop пишет в fb_stop_bot_test, не в прод.
    # gate всегда падает → outcome=error каждый цикл, self-heal не помогает
    gate = _FakeGate(RuntimeError("Основная страница браузера недоступна"))

    iters = {"n": 0}

    def _should_continue() -> bool:
        iters["n"] += 1
        return iters["n"] <= 4  # 4 цикла: threshold достигнут на 3-м и 4-м

    async def _gate_factory():
        return gate

    from unittest.mock import AsyncMock, patch

    with patch(
        "apps.observer_worker.main.notify_recurring_incident",
        AsyncMock(return_value=True),
    ) as spy:
        await main_loop(
            gate_factory=_gate_factory,
            should_continue=_should_continue,
        )

    # threshold=3 → facade вызван на 3-м и 4-м сбоях; PostgreSQL схлопнет event.
    degraded_calls = [
        c for c in spy.await_args_list if c.kwargs.get("event_type") == "observer_degraded"
    ]
    assert len(degraded_calls) == 2
    assert "Observer" in degraded_calls[0].kwargs["title"]
    assert degraded_calls[0].kwargs["severity"] == "critical"
    assert {call.kwargs["incident_key"] for call in degraded_calls} == {"observer:degraded"}


# ====================== Явная multi-cabinet identity ======================


class _MultiAccountGate:
    """Fake ScannerGate: по ScanCycleOutput/Exception на каждый кабинет (по порядку вызовов)."""

    def __init__(self, outputs: dict[str, ScanCycleOutput | Exception]):
        self._outputs = outputs
        self.account_ids: list[str] = []

    async def open_cabinet_tabs(self, ad_account_ids: list[str]) -> list[dict]:
        return [{"ad_account_id": account_id, "opened": True} for account_id in ad_account_ids]

    async def run_one_scan(
        self,
        ad_account_id: str,
        campaign_ids: list[str] | None = None,
        owner_tag: str | None = None,
    ) -> ScanCycleOutput:
        self.account_ids.append(ad_account_id)
        out = self._outputs[ad_account_id]
        if isinstance(out, Exception):
            raise out
        return out


# Сценарий: два кабинета из union офферов сканируются последовательно, каждый со своим
# scan_run (ad_account_id записан), счётчики суммируются в общем summary.
@pytest.mark.asyncio
async def test_multi_cabinet_sequential_scan(
    pg_engine, ensure_observer_config_enabled, offer_cr2, monkeypatch
) -> None:
    # Пауза между кабинетами не нужна в тесте — ускоряем.
    import apps.observer_worker.main as obs_main

    monkeypatch.setattr(obs_main, "ACCOUNT_SCAN_PAUSE_SECONDS", 0.0)

    # Привязываем кабинеты к офферам: CR2 → 111; второй оффер → 222 + 111 (дедуп union).
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE offers SET ad_account_ids = ARRAY['111'] WHERE code = 'CR2'")
        )
        await conn.execute(
            text(
                "INSERT INTO offers (id, code, name, is_active, ad_account_ids) "
                "VALUES (:i, 'CR9', 'CR9', TRUE, ARRAY['222', '111'])"
            ),
            {"i": uuid.uuid4()},
        )
        # Money-гард R4: мульти-каб (>1 кабинета) без owner_campaign_tag скан пропускает —
        # задаём тег (совпадает с 'MV' в campaign_name строк _row()).
        await conn.execute(
            text(
                "UPDATE observer_config SET owner_campaign_tag = 'MV' WHERE singleton_key = 'default'"
            )
        )

    gate = _MultiAccountGate(
        {
            "111": ScanCycleOutput(
                rows=[_row()],
                metrics_contract_revision=SCANNER_METRICS_CONTRACT_REVISION,
                total_passes=1,
            ),
            "222": ScanCycleOutput(
                rows=[],
                metrics_contract_revision=SCANNER_METRICS_CONTRACT_REVISION,
                empty_reason="no_active_ads",
            ),
        }
    )

    summary = await run_one_cycle(pg_engine, gate=gate)

    # Кабинеты обойдены последовательно в отсортированном порядке, без дублей.
    assert gate.account_ids == ["111", "222"]
    # Хотя бы один кабинет success → весь цикл success; счётчики просуммированы.
    assert summary["outcome"] == "success"
    assert summary["rows_total"] == 1
    assert [a["ad_account_id"] for a in summary["accounts"]] == ["111", "222"]

    async with pg_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT ad_account_id, outcome FROM scan_runs "
                    "WHERE ad_account_id IS NOT NULL ORDER BY id DESC LIMIT 2"
                )
            )
        ).fetchall()
    # Оба scan_run записаны со своим кабинетом.
    assert {r[0] for r in rows} == {"111", "222"}


# Ошибка первого кабинета не прерывает второй, но aggregate остаётся partial:
# успешный сосед не должен давать false-green для неполного snapshot.
@pytest.mark.asyncio
async def test_multi_cabinet_error_is_partial_and_does_not_break_others(
    pg_engine, ensure_observer_config_enabled, offer_cr2, monkeypatch
) -> None:
    import apps.observer_worker.main as obs_main

    monkeypatch.setattr(obs_main, "ACCOUNT_SCAN_PAUSE_SECONDS", 0.0)

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE offers SET ad_account_ids = ARRAY['111', '222'] WHERE code = 'CR2'")
        )
        # Money-гард R4: мульти-каб без owner_campaign_tag скан пропускает — задаём тег.
        await conn.execute(
            text(
                "UPDATE observer_config SET owner_campaign_tag = 'MV' WHERE singleton_key = 'default'"
            )
        )

    gate = _MultiAccountGate(
        {
            "111": RuntimeError("test: кабинет 111 упал"),
            "222": ScanCycleOutput(
                rows=[_row()],
                metrics_contract_revision=SCANNER_METRICS_CONTRACT_REVISION,
                total_passes=1,
            ),
        }
    )

    summary = await run_one_cycle(pg_engine, gate=gate)

    # Оба кабинета были запрошены, несмотря на ошибку первого.
    assert gate.account_ids == ["111", "222"]
    assert summary["outcome"] == "partial"
    outcomes = {a["ad_account_id"]: a["outcome"] for a in summary["accounts"]}
    assert outcomes == {"111": "error", "222": "success"}


# Сценарий: офферы без кабинетов → fail-closed без обращения к текущей вкладке.
@pytest.mark.asyncio
async def test_scan_without_explicit_cabinet_is_blocked(
    pg_engine, ensure_observer_config_enabled, offer_cr2
) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE offers SET ad_account_ids = ARRAY[]::text[] WHERE code = 'CR2'")
        )
    gate = _FakeGate(
        ScanCycleOutput(
            rows=[_row()],
            metrics_contract_revision=SCANNER_METRICS_CONTRACT_REVISION,
            total_passes=1,
        )
    )

    summary = await run_one_cycle(pg_engine, gate=gate)

    assert gate.calls == 0
    assert gate.account_ids == []
    assert summary == {
        "outcome": "skipped",
        "accounts": [],
        "reason": "no_configured_cabinets",
        "orphan_offers": ["CR2"],
    }
