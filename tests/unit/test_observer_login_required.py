# -*- coding: utf-8 -*-
"""MID X-16: разлогин/чекпоинт Vision-профиля в observer.

browser-agent детектит redirect на login.php/checkpoint, HTML вместо JSON или Graph 190
с login-subcode и отдаёт empty_reason='login_required'. Money-критично (инцидент 01.07 —
канал умер молча): такой цикл — НЕ «пустой кабинет», а слепота канала. Проверяем:
- login_required-скан → outcome='error' (не 'empty'): resolve_scan_mode даёт CALM, не IDLE,
  и degraded-счётчик растёт (авто-стоп не «спит» в IDLE при живом инциденте);
- поднимается durable incident «Vision-профиль требует повторного входа»;
- обычный пустой скан (no_active_ads) НЕ триггерит ни error, ни алерт.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

import apps.observer_worker.main as ow
from apps.observer_worker.main import ScanCycleOutput
from core.observer.adaptive_interval import resolve_scan_mode
from core.scanner.models import (
    SCANNER_METRICS_CONTRACT_REVISION,
    ScannedAdRow,
)

# ====================== _maybe_alert_login_required ======================


# Outbox accepted the incident; facts identify the account and action.
@pytest.mark.asyncio
async def test_login_required_alert_delivers_via_recipients(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "notify_recurring_incident", spy)

    ok = await ow._maybe_alert_login_required(object(), ad_account_id="act_777")

    assert ok is True
    spy.assert_awaited_once()
    facts = spy.await_args.kwargs
    # Заголовок называет проблему, строки — что сделать оператору.
    assert "повторного входа" in facts["title"].lower()
    assert any("войди" in line.lower() for line in facts["lines"])
    assert any("вручную" in line.lower() for line in facts["lines"])
    assert "777" in facts["summary"]  # канонический кабинет в типизированных facts
    assert facts["incident_key"] == "observer:login_required:777"
    assert facts["audience"] == "all"
    assert facts["resource_type"] == "ad_account"
    assert facts["resource_id"] == "777"


@pytest.mark.asyncio
async def test_login_required_alert_rejects_missing_account(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "notify_recurring_incident", spy)
    with pytest.raises(ValueError, match="explicit numeric account id"):
        await ow._maybe_alert_login_required(
            object(),
            ad_account_id=None,  # type: ignore[arg-type]
        )

    spy.assert_not_awaited()


# Outbox rejection is visible in logs.
@pytest.mark.asyncio
async def test_login_required_alert_outbox_rejection_warns(monkeypatch, caplog):
    spy = AsyncMock(return_value=False)
    monkeypatch.setattr(ow, "notify_recurring_incident", spy)
    with caplog.at_level("WARNING"):
        ok = await ow._maybe_alert_login_required(object(), ad_account_id="act_1")

    assert ok is False
    spy.assert_awaited_once()
    assert any("outbox" in r.getMessage().lower() for r in caplog.records)


# ====================== _run_account_scan: login_required → error + alert ======================


@pytest.fixture
def _stub_scan_db(monkeypatch):
    """Заглушки DB-хелперов _run_account_scan, чтобы тестировать ветку без БД."""
    monkeypatch.setattr(ow, "_begin_scan_run", AsyncMock(return_value=101))
    monkeypatch.setattr(ow, "_finish_scan_run", AsyncMock())
    # process_scan_rows не должен вызываться при пустом скане — но подстрахуемся.
    monkeypatch.setattr(ow, "process_scan_rows", AsyncMock())
    resolve = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "resolve_recurring_incident", resolve)
    return resolve


class _FakeGate:
    """Fake ScannerGate с заранее заданным ScanCycleOutput."""

    def __init__(self, output: ScanCycleOutput):
        self._output = output

    async def run_one_scan(self, **kwargs) -> ScanCycleOutput:
        return self._output

    async def open_cabinet_tabs(self, ad_account_ids):
        return []


# login_required-скан → outcome='error' (НЕ 'empty') + deduped алерт вызван
@pytest.mark.asyncio
async def test_run_account_scan_login_required_marks_error_and_alerts(_stub_scan_db, monkeypatch):
    alert_spy = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "_maybe_alert_login_required", alert_spy)
    gate = _FakeGate(
        ScanCycleOutput(
            rows=[],
            metrics_contract_revision=SCANNER_METRICS_CONTRACT_REVISION,
            empty_reason="login_required",
        )
    )

    summary = await ow._run_account_scan(
        object(),
        gate=gate,
        config={"campaign_ids": ["c1"], "owner_campaign_tag": "MV"},
        ad_account_id="act_5",
    )

    assert summary["outcome"] == "error"  # не 'empty' → degraded-детектор его увидит
    assert summary["error"] == "login_required"
    alert_spy.assert_awaited_once()
    assert alert_spy.await_args.kwargs["ad_account_id"] == "act_5"
    _stub_scan_db.assert_not_awaited()


# Обычный пустой скан (no_active_ads) → outcome='empty', алерт НЕ вызван (регресс-защита)
@pytest.mark.asyncio
async def test_run_account_scan_normal_empty_no_alert(_stub_scan_db, monkeypatch):
    alert_spy = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "_maybe_alert_login_required", alert_spy)
    gate = _FakeGate(
        ScanCycleOutput(
            rows=[],
            metrics_contract_revision=SCANNER_METRICS_CONTRACT_REVISION,
            empty_reason="no_active_ads",
        )
    )

    summary = await ow._run_account_scan(
        object(),
        gate=gate,
        config={"campaign_ids": ["c1"], "owner_campaign_tag": "MV"},
        ad_account_id="act_5",
    )

    assert summary["outcome"] == "empty"
    alert_spy.assert_not_awaited()
    _stub_scan_db.assert_awaited_once()
    assert _stub_scan_db.await_args.kwargs["incident_key"] == "observer:login_required:5"


@pytest.mark.asyncio
async def test_incomplete_identity_is_partial_and_never_reaches_money_writers(
    _stub_scan_db,
) -> None:
    row = ScannedAdRow(
        fb_ad_id="120200000000001",
        campaign_id="120200000000002",
        adset_id="",
        campaign_name="MV | CR2 | KE",
        adset_name="KE broad",
        ad_name="Creative 1",
        delivery_status="ACTIVE",
        spend=Decimal("1"),
    )
    gate = _FakeGate(
        ScanCycleOutput(
            rows=[row],
            metrics_contract_revision=SCANNER_METRICS_CONTRACT_REVISION,
            # Simulate producer/version skew: Python must independently catch
            # the missing adset ID even if browser-agent sent no marker.
            partial_row_ids=[],
        )
    )

    summary = await ow._run_account_scan(
        object(),
        gate=gate,
        config={"campaign_ids": ["c1"], "owner_campaign_tag": "MV"},
        ad_account_id="act_5",
    )

    assert summary["outcome"] == "partial"
    assert summary["error"] == "partial_rows:1"
    ow.process_scan_rows.assert_not_awaited()
    assert gate._output.partial_row_ids == ["120200000000001"]
    # Rows prove authentication even though incomplete metrics must not drive FSM.
    _stub_scan_db.assert_awaited_once()


@pytest.mark.asyncio
async def test_incomplete_metric_marker_is_partial_and_never_reaches_money_writers(
    _stub_scan_db,
) -> None:
    row = ScannedAdRow(
        fb_ad_id="120200000000101",
        campaign_id="120200000000102",
        adset_id="120200000000103",
        campaign_name="MV | CR2 | KE",
        adset_name="KE broad",
        ad_name="Creative 1",
        delivery_status="ACTIVE",
        # The current non-nullable protobuf transports a placeholder, while
        # browser-agent preserves the missing raw spend as a partial marker.
        spend=Decimal("0"),
    )
    gate = _FakeGate(
        ScanCycleOutput(
            rows=[row],
            metrics_contract_revision=SCANNER_METRICS_CONTRACT_REVISION,
            partial_row_ids=["120200000000101"],
        )
    )

    summary = await ow._run_account_scan(
        object(),
        gate=gate,
        config={"campaign_ids": ["c1"], "owner_campaign_tag": "MV"},
        ad_account_id="act_5",
    )

    assert summary["outcome"] == "partial"
    assert summary["error"] == "partial_rows:1"
    ow.process_scan_rows.assert_not_awaited()
    _stub_scan_db.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("contract_revision", [0, 2])
async def test_unknown_metric_contract_revision_is_partial_before_money_writers(
    _stub_scan_db,
    contract_revision: int,
) -> None:
    row = ScannedAdRow(
        fb_ad_id="120200000000201",
        campaign_id="120200000000202",
        adset_id="120200000000203",
        campaign_name="MV | CR2 | KE",
        adset_name="KE broad",
        ad_name="Creative 1",
        delivery_status="ACTIVE",
        spend=Decimal("1"),
        reach=10,
        impressions=12,
        clicks=2,
    )
    gate = _FakeGate(
        ScanCycleOutput(
            rows=[row],
            metrics_contract_revision=contract_revision,
        )
    )

    summary = await ow._run_account_scan(
        object(),
        gate=gate,
        config={"campaign_ids": ["c1"], "owner_campaign_tag": "MV"},
        ad_account_id="act_5",
    )

    assert summary["outcome"] == "partial"
    assert summary["error"] == f"metrics_contract_revision:{contract_revision}"
    ow.process_scan_rows.assert_not_awaited()
    # An incompatible metrics contract is not evidence that authentication is
    # healthy, so it cannot resolve the durable login incident.
    _stub_scan_db.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_contract_cannot_confirm_empty_cabinet(_stub_scan_db) -> None:
    gate = _FakeGate(
        ScanCycleOutput(
            rows=[],
            metrics_contract_revision=0,
            empty_reason="no_active_ads",
        )
    )

    summary = await ow._run_account_scan(
        object(),
        gate=gate,
        config={"campaign_ids": ["c1"], "owner_campaign_tag": "MV"},
        ad_account_id="act_5",
    )

    assert summary["outcome"] == "partial"
    assert summary["error"] == "metrics_contract_revision:0"
    ow.process_scan_rows.assert_not_awaited()
    _stub_scan_db.assert_not_awaited()


@pytest.mark.asyncio
async def test_unclassified_empty_is_partial_not_confirmed_zero(_stub_scan_db) -> None:
    gate = _FakeGate(
        ScanCycleOutput(
            rows=[],
            metrics_contract_revision=SCANNER_METRICS_CONTRACT_REVISION,
            empty_reason="no final result",
        )
    )

    summary = await ow._run_account_scan(
        object(),
        gate=gate,
        config={"campaign_ids": ["c1"], "owner_campaign_tag": "MV"},
        ad_account_id="act_5",
    )

    assert summary["outcome"] == "partial"
    ow.process_scan_rows.assert_not_awaited()
    _stub_scan_db.assert_not_awaited()


# ====================== инвариант адаптива: login_required = error → CALM, не IDLE ======================


# Money-инвариант: login_required-цикл (outcome='error') держит CALM-темп, НЕ уходит в IDLE
def test_login_required_summary_resolves_to_calm_not_idle() -> None:
    # Ключевое поле — outcome='error' (его выставляет _run_account_scan при login_required).
    summary = {"outcome": "error", "rows_with_offer": 0, "alerts_stop": 0, "alerts_warning": 0}
    mode = resolve_scan_mode(summary)
    assert mode == "CALM"
    assert mode != "IDLE"  # иначе горящее объявление ждёт дольше при слепом канале
