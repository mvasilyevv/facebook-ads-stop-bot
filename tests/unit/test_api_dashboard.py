# -*- coding: utf-8 -*-
"""Тесты dashboard-эндпоинтов и helper'ов агрегации."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from core.domain import AlertStage, AlertState, DisableTaskStatus


# Вспомогательная фабрика мок-результата GROUP BY
def _make_state_rows(normal=0, warning=0, stop=0, disabled=0):
    """Создаёт строки как их возвращает GROUP BY alert_state."""
    rows = []
    if normal:
        rows.append((AlertState.NORMAL, "ACTIVE", normal, Decimal("10.00")))
    if warning:
        rows.append((AlertState.WARNING_SENT, "ACTIVE", warning, Decimal("25.50")))
    if stop:
        rows.append((AlertState.STOP_SENT, "ACTIVE", stop, Decimal("50.00")))
    if disabled:
        rows.append((AlertState.DISABLED, "OFF", disabled, Decimal("5.00")))
    return rows


def _make_snapshot(
    *,
    spend: str,
    clicks: int,
    leads: int,
    registrations: int,
    deposits: int,
    last_observed_at: datetime,
    ad_id: object | None = None,
    campaign_name: str = "",
):
    """Создаёт упрощённый snapshot для helper-тестов performance.

    campaign_name принимается для удобства, но хранится как _campaign_name —
    в реальной системе берётся через ad_context_map.
    """
    return SimpleNamespace(
        ad_id=ad_id or uuid.uuid4(),
        fb_ad_id=f"fb-{uuid.uuid4().hex[:8]}",
        _campaign_name=campaign_name,
        spend=Decimal(spend),
        clicks=clicks,
        leads=leads,
        registrations=registrations,
        deposits=deposits,
        last_observed_at=last_observed_at,
    )


def _make_archive(
    *,
    started_at: datetime,
    ended_at: datetime,
    spend: str,
    clicks: int,
    leads: int,
    registrations: int,
    deposits: int,
    campaigns_json: list[dict] | None = None,
):
    """Создаёт упрощённый архив завершившихся суток кабинета."""
    return SimpleNamespace(
        started_at=started_at,
        ended_at=ended_at,
        reset_detected_at=ended_at,
        summary_json={
            "spend": spend,
            "clicks": clicks,
            "leads": leads,
            "registrations": registrations,
            "deposits": deposits,
        },
        campaigns_json=campaigns_json or [],
    )


def _make_risk_snapshot(
    *,
    alert_state: AlertState,
    warning_rule_codes: list[str] | None = None,
    stop_rule_codes: list[str] | None = None,
):
    """Создаёт упрощённый snapshot для тестов причин активных рисков."""
    return SimpleNamespace(
        alert_state=alert_state,
        warning_rule_codes=warning_rule_codes or [],
        stop_rule_codes=stop_rule_codes or [],
    )


def _make_stop_overrun_snapshot(
    *,
    offer_id,
    campaign_name: str,
    ad_name: str,
    ad_id: object | None = None,
    spend: str = "0.00",
    clicks: int = 0,
    cpc: str | None = None,
    leads: int = 0,
    cost_per_lead: str | None = None,
    registrations: int = 0,
    cost_per_registration: str | None = None,
    deposits: int = 0,
):
    """Создаёт snapshot для теста перекрута над базовыми стопами.

    offer_id, campaign_name и ad_name передаются через ad_context_map, не как поля snapshot.
    ad_id используется как ключ в ad_context_map.
    """
    _ad_id = ad_id or uuid.uuid4()
    return SimpleNamespace(
        ad_id=_ad_id,
        _offer_id=offer_id,
        _campaign_name=campaign_name,
        _ad_name=ad_name,
        spend=Decimal(spend),
        clicks=clicks,
        cpc=Decimal(cpc) if cpc is not None else None,
        leads=leads,
        cost_per_lead=Decimal(cost_per_lead) if cost_per_lead is not None else None,
        registrations=registrations,
        cost_per_registration=(
            Decimal(cost_per_registration) if cost_per_registration is not None else None
        ),
        deposits=deposits,
    )


def _make_scalars_result(rows):
    """Создаёт мок SQLAlchemy-результата для scalars().all()."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


def _make_scalar_result(value):
    """Создаёт мок SQLAlchemy-результата для scalar_one_or_none() и scalar_one()."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar_one.return_value = value
    return result


# Проверяем что схемы API всегда отдают код оффера в верхнем регистре
def test_offer_code_schemas_normalize_uppercase():
    from apps.api.schemas import AdSnapshotSchema, OfferSchema

    offer = OfferSchema(code=" drc_cr2 ", cpa_amount=Decimal("10"), is_active=True)
    snapshot = AdSnapshotSchema(
        id="1",
        fb_ad_id="ad-1",
        campaign_name="Campaign",
        adset_name="Adset",
        ad_name="Ad",
        delivery_status="ACTIVE",
        offer_code=" drc_cr2 ",
        spend=Decimal("12.50"),
        clicks=3,
        leads=0,
        registrations=0,
        deposits=0,
        alert_state="NORMAL",
    )

    assert offer.code == "DRC_CR2"
    assert snapshot.offer_code == "DRC_CR2"


# Проверяем что lookup-ключ для оффера не зависит от регистра и пробелов
def test_offer_code_lookup_key_is_case_insensitive():
    from apps.api.schemas import _offer_code_lookup_key

    assert _offer_code_lookup_key(" drc_cr2 ") == "drc_cr2"
    assert _offer_code_lookup_key("DRC_CR2") == "drc_cr2"


# Проверяем что причины активных рисков считаются по живым риск-статусам, а не по архивным snapshot
def test_build_current_risk_reason_rows_uses_active_snapshot_states():
    from apps.api.routers.dashboard import _build_current_risk_reason_rows

    snapshots = [
        _make_risk_snapshot(
            alert_state=AlertState.WARNING_SENT,
            warning_rule_codes=["cpc_stop"],
        ),
        _make_risk_snapshot(
            alert_state=AlertState.CLAIMED,
            warning_rule_codes=["cpr_stop"],
            stop_rule_codes=["cpl_stop", "cpl_stop"],
        ),
        _make_risk_snapshot(
            alert_state=AlertState.DISABLED,
            stop_rule_codes=["spend_no_dep_range"],
        ),
    ]

    rows = _build_current_risk_reason_rows(snapshots)
    reason_counts = {row["rule"]: row["count"] for row in rows}

    assert reason_counts == {
        "Дорогой клик": 1,
        "Дорогой лид": 1,
    }


# Проверяем что current incident уходит в ручной разбор после лимита тихих автоповторов.
def test_build_active_incident_schema_marks_manual_attention_after_retry_limit():
    from apps.api.routers.dashboard import _build_active_incident_schema

    now = datetime.now(UTC)
    snapshot = SimpleNamespace(
        ad_id=uuid.uuid4(),
        fb_ad_id="ad-1",
        alert_state=AlertState.CLAIMED,
        current_stage=AlertStage.STOP,
        delivery_status="UNKNOWN",
        open_state_token="incident-1",
        telegram_group_key="incident-1",
        stop_rule_codes=["cpc_stop"],
        warning_rule_codes=[],
        last_observed_at=now,
        updated_at=now,
        created_at=now - timedelta(hours=1),
    )
    event = SimpleNamespace(
        telegram_group_key="incident-1",
        created_at=now - timedelta(minutes=10),
        reason_title="Дорогой клик",
        reason_text="Цена клика превысила порог.",
        metrics_json={"spend": "50.00"},
        stage=AlertStage.STOP,
    )
    tasks = [
        SimpleNamespace(
            id=f"task-{index}",
            open_state_token="incident-1",
            status=DisableTaskStatus.SUCCEEDED if index < 3 else DisableTaskStatus.FAILED,
            requested_by_username="bot_auto_stop",
            attempt_count=index + 1,
            created_at=now - timedelta(minutes=30 - index),
            updated_at=now - timedelta(minutes=20 - index),
            completed_at=now - timedelta(minutes=20 - index),
            next_retry_at=None,
            last_error="Meta не подтверждает OFF" if index == 3 else None,
        )
        for index in range(4)
    ]

    ad_ctx = {
        "fb_ad_id": "ad-1",
        "ad_name": "Объявление",
        "campaign_name": "Campaign",
        "adset_name": "Adset",
    }
    incident = _build_active_incident_schema(
        snapshot,
        alert_events=[event],
        disable_tasks=tasks,
        ad_ctx=ad_ctx,
    )

    assert incident.incident_key == "incident-1"
    assert incident.incident_retry_count == 3
    assert incident.needs_manual_attention is True
    assert incident.latest_disable_task_status == DisableTaskStatus.FAILED.value
    assert incident.reason_title == "Дорогой клик"


# Проверяем что отклонение по кампании возвращает и перерасход, и экономию относительно базы.
def test_build_campaign_stop_overrun_rows_returns_aggregated_base_stop_excess_by_campaign():
    from apps.api.routers.dashboard import _build_campaign_stop_overrun_rows

    offer_id = uuid.uuid4()
    snapshots = [
        _make_stop_overrun_snapshot(
            offer_id=offer_id,
            campaign_name="Campaign A",
            ad_name="Ad 1",
            spend="0.15",
            clicks=1,
            cpc="0.15",
        ),
        _make_stop_overrun_snapshot(
            offer_id=offer_id,
            campaign_name="Campaign A",
            ad_name="Ad 2",
            spend="0.55",
            clicks=12,
            leads=1,
            cost_per_lead="0.55",
        ),
        _make_stop_overrun_snapshot(
            offer_id=offer_id,
            campaign_name="Campaign B",
            ad_name="Ad 3",
            spend="0.09",
            clicks=1,
            cpc="0.09",
        ),
    ]
    # Строим ad_context_map из _-полей snapshot
    ad_context_map = {
        s.ad_id: {
            "offer_id": s._offer_id,
            "campaign_name": s._campaign_name,
            "ad_name": s._ad_name,
        }
        for s in snapshots
    }
    offer_rule_map = {
        offer_id: (
            SimpleNamespace(id=offer_id, cpa_amount=Decimal("5.00")),
            SimpleNamespace(
                cpc_percent_enabled=True,
                cpc_percent_stop=Decimal("2"),
                cpl_percent_enabled=True,
                cpl_percent_stop=Decimal("10"),
                cpr_percent_enabled=True,
                cpr_percent_stop=Decimal("20"),
                regs_no_dep_enabled=True,
                regs_no_dep_stop_count=5,
                spend_no_dep_enabled=True,
                spend_no_dep_from_percent=Decimal("50"),
                spend_with_dep_enabled=True,
                spend_with_dep_from_percent=Decimal("70"),
            ),
        )
    }

    rows = _build_campaign_stop_overrun_rows(
        snapshots,
        offer_rule_map,
        ad_context_map=ad_context_map,
    )

    assert len(rows) == 2
    assert rows[0]["campaign_full"] == "Campaign A"
    assert rows[0]["budget_status"] == "OVER"
    assert rows[0]["total_ads"] == 2
    assert rows[0]["affected_ads"] == 2
    assert rows[0]["over_budget_ads"] == 2
    assert rows[0]["under_budget_ads"] == 0
    assert rows[0]["on_target_ads"] == 0
    assert rows[0]["actual_spend"] == pytest.approx(0.70)
    assert rows[0]["ideal_spend"] == pytest.approx(0.60)
    assert rows[0]["budget_delta_amount"] == pytest.approx(0.10)
    assert rows[0]["budget_delta_percent"] == pytest.approx(16.7)
    assert rows[0]["dominant_metric"] == "CPC"
    assert rows[0]["top_ad_name"] == "Ad 1"
    assert rows[0]["max_ad_overrun_amount"] == pytest.approx(0.05)
    assert rows[0]["max_ad_overrun_percent"] == pytest.approx(50.0)
    assert rows[1]["campaign_full"] == "Campaign B"
    assert rows[1]["budget_status"] == "UNDER"
    assert rows[1]["total_ads"] == 1
    assert rows[1]["over_budget_ads"] == 0
    assert rows[1]["under_budget_ads"] == 1
    assert rows[1]["on_target_ads"] == 0
    assert rows[1]["actual_spend"] == pytest.approx(0.09)
    assert rows[1]["ideal_spend"] == pytest.approx(0.10)
    assert rows[1]["budget_delta_amount"] == pytest.approx(-0.01)
    assert rows[1]["budget_delta_percent"] == pytest.approx(-10.0)


# Проверяем что при нулевой суммарной дельте кампания остаётся в выдаче как точное попадание в базу.
def test_build_campaign_stop_overrun_rows_keeps_campaign_when_total_matches_base():
    from apps.api.routers.dashboard import _build_campaign_stop_overrun_rows

    offer_id = uuid.uuid4()
    snapshots = [
        _make_stop_overrun_snapshot(
            offer_id=offer_id,
            campaign_name="Campaign A",
            ad_name="Ad 1",
            spend="0.15",
            clicks=1,
            cpc="0.15",
        ),
        _make_stop_overrun_snapshot(
            offer_id=offer_id,
            campaign_name="Campaign A",
            ad_name="Ad 2",
            spend="0.45",
            clicks=9,
            leads=1,
            cost_per_lead="0.45",
        ),
    ]
    ad_context_map = {
        s.ad_id: {
            "offer_id": s._offer_id,
            "campaign_name": s._campaign_name,
            "ad_name": s._ad_name,
        }
        for s in snapshots
    }
    offer_rule_map = {
        offer_id: (
            SimpleNamespace(id=offer_id, cpa_amount=Decimal("5.00")),
            SimpleNamespace(
                cpc_percent_enabled=True,
                cpc_percent_stop=Decimal("2"),
                cpl_percent_enabled=True,
                cpl_percent_stop=Decimal("10"),
                cpr_percent_enabled=True,
                cpr_percent_stop=Decimal("20"),
                regs_no_dep_enabled=True,
                regs_no_dep_stop_count=5,
                spend_no_dep_enabled=True,
                spend_no_dep_from_percent=Decimal("50"),
                spend_with_dep_enabled=True,
                spend_with_dep_from_percent=Decimal("70"),
            ),
        )
    }

    rows = _build_campaign_stop_overrun_rows(
        snapshots,
        offer_rule_map,
        ad_context_map=ad_context_map,
    )

    assert len(rows) == 1
    assert rows[0]["campaign_full"] == "Campaign A"
    assert rows[0]["budget_status"] == "ON_TARGET"
    assert rows[0]["total_ads"] == 2
    assert rows[0]["over_budget_ads"] == 1
    assert rows[0]["under_budget_ads"] == 1
    assert rows[0]["on_target_ads"] == 0
    assert rows[0]["actual_spend"] == pytest.approx(0.60)
    assert rows[0]["ideal_spend"] == pytest.approx(0.60)
    assert rows[0]["budget_delta_amount"] == pytest.approx(0.0)
    assert rows[0]["budget_delta_percent"] == pytest.approx(0.0)
    assert rows[0]["dominant_metric"] == "CPC"
    assert rows[0]["top_ad_name"] == "Ad 1"


# Проверяем что endpoint current incidents сортирует кейсы по последней активности.
@pytest.mark.asyncio
async def test_list_active_incidents_sorts_by_last_activity(mock_db):
    from apps.api.routers.dashboard import list_active_incidents

    now = datetime.now(UTC)
    old_ad_id = uuid.uuid4()
    new_ad_id = uuid.uuid4()
    older_snapshot = SimpleNamespace(
        ad_id=old_ad_id,
        fb_ad_id="ad-old",
        alert_state=AlertState.WARNING_SENT,
        current_stage=AlertStage.WARNING,
        delivery_status="ACTIVE",
        open_state_token="incident-old",
        telegram_group_key="incident-old",
        warning_rule_codes=["cpl_stop"],
        stop_rule_codes=[],
        last_observed_at=now - timedelta(minutes=5),
        updated_at=now - timedelta(minutes=5),
        created_at=now - timedelta(hours=2),
    )
    newer_snapshot = SimpleNamespace(
        ad_id=new_ad_id,
        fb_ad_id="ad-new",
        alert_state=AlertState.CLAIMED,
        current_stage=AlertStage.STOP,
        delivery_status="UNKNOWN",
        open_state_token="incident-new",
        telegram_group_key="incident-new",
        warning_rule_codes=[],
        stop_rule_codes=["cpc_stop"],
        last_observed_at=now,
        updated_at=now,
        created_at=now - timedelta(hours=1),
    )
    events = [
        SimpleNamespace(
            ad_id=old_ad_id,
            telegram_group_key="incident-old",
            created_at=now - timedelta(minutes=4),
            reason_title="Предупреждение",
            reason_text="Старый кейс",
            metrics_json={},
            stage=AlertStage.WARNING,
        ),
        SimpleNamespace(
            ad_id=new_ad_id,
            telegram_group_key="incident-new",
            created_at=now - timedelta(minutes=1),
            reason_title="Стоп",
            reason_text="Новый кейс",
            metrics_json={},
            stage=AlertStage.STOP,
        ),
    ]
    tasks = [
        SimpleNamespace(
            id="task-new",
            ad_id=new_ad_id,
            open_state_token="incident-new",
            status=DisableTaskStatus.RETRYING,
            requested_by_username="bot_auto_stop",
            attempt_count=2,
            created_at=now - timedelta(minutes=3),
            updated_at=now - timedelta(minutes=1),
            completed_at=None,
            next_retry_at=now + timedelta(minutes=1),
            last_error="Таймаут браузера",
        )
    ]
    # ad_context_map мок — возвращает контекст по ad_id
    ad_ctx_map = {
        old_ad_id: {
            "fb_ad_id": "ad-old",
            "ad_name": "Старый инцидент",
            "campaign_name": "Campaign",
            "adset_name": "Adset",
        },
        new_ad_id: {
            "fb_ad_id": "ad-new",
            "ad_name": "Новый инцидент",
            "campaign_name": "Campaign",
            "adset_name": "Adset",
        },
    }

    mock_db.scalar = AsyncMock(return_value=now)
    mock_db.execute = AsyncMock(
        side_effect=[
            _make_scalars_result([older_snapshot, newer_snapshot]),
            _make_scalars_result(events),
            _make_scalars_result(tasks),
        ]
    )

    with patch(
        "apps.api.routers.dashboard._load_ad_context_map",
        new=AsyncMock(return_value=ad_ctx_map),
    ):
        incidents = await list_active_incidents(limit=10, db=mock_db)

    assert [incident.fb_ad_id for incident in incidents] == ["ad-new", "ad-old"]
    assert incidents[0].latest_disable_task_status == DisableTaskStatus.RETRYING.value
    assert incidents[0].current_state == AlertState.CLAIMED.value


@pytest.fixture
def mock_db():
    """Мок async DB-сессии."""
    db = AsyncMock()
    return db


# Проверяем что dashboard возвращает правильные счётчики при наличии данных
@pytest.mark.asyncio
async def test_dashboard_stats_counts(mock_db):
    state_rows = _make_state_rows(normal=10, warning=3, stop=2, disabled=1)

    # Мокаем результат GROUP BY запроса + observer_settings (execute)
    group_result = MagicMock()
    group_result.all.return_value = state_rows
    observer_result = MagicMock()
    observer_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(side_effect=[group_result, observer_result])

    # scalar вызовы: last_scan, active_offers, pending_tasks, pending_enable_tasks, disabled_today
    mock_db.scalar = AsyncMock(side_effect=[None, 5, 2, 0, 1])

    from apps.api.routers.dashboard import get_dashboard_stats

    with patch(
        "apps.api.routers.dashboard._load_current_enable_recommendations",
        new=AsyncMock(return_value=(None, [])),
    ):
        result = await get_dashboard_stats(db=mock_db)

    assert result.total_ads_monitored == 16  # 10+3+2+1
    assert result.ads_in_warning == 3
    assert result.ads_in_stop == 2
    assert result.ads_disabled == 1
    assert result.total_spend == Decimal("90.50")  # 10+25.5+50+5
    assert result.active_offers == 5
    assert result.pending_disable_tasks == 2
    assert result.ads_disabled_today == 1
    assert result.last_scan_at is None


# Проверяем что dashboard возвращает нули при пустой БД
@pytest.mark.asyncio
async def test_dashboard_stats_empty_db(mock_db):
    group_result = MagicMock()
    group_result.all.return_value = []
    observer_result = MagicMock()
    observer_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(side_effect=[group_result, observer_result])
    mock_db.scalar = AsyncMock(side_effect=[None, 0, 0, 0, 0])

    from apps.api.routers.dashboard import get_dashboard_stats

    with patch(
        "apps.api.routers.dashboard._load_current_enable_recommendations",
        new=AsyncMock(return_value=(None, [])),
    ):
        result = await get_dashboard_stats(db=mock_db)

    assert result.total_ads_monitored == 0
    assert result.ads_in_warning == 0
    assert result.ads_in_stop == 0
    assert result.ads_disabled == 0
    assert result.total_spend == Decimal("0")
    assert result.active_offers == 0
    assert result.pending_disable_tasks == 0
    assert result.ads_disabled_today == 0


# Проверяем что execute вызывается один раз (GROUP BY), а не 5 раз
@pytest.mark.asyncio
async def test_dashboard_uses_single_group_by_query(mock_db):
    group_result = MagicMock()
    group_result.all.return_value = _make_state_rows(normal=5)
    observer_result = MagicMock()
    observer_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(side_effect=[group_result, observer_result])
    mock_db.scalar = AsyncMock(side_effect=[None, 1, 0, 0, 0])

    from apps.api.routers.dashboard import get_dashboard_stats

    with patch(
        "apps.api.routers.dashboard._load_current_enable_recommendations",
        new=AsyncMock(return_value=(None, [])),
    ):
        await get_dashboard_stats(db=mock_db)

    # execute: GROUP BY + observer_settings = 2
    assert mock_db.execute.call_count == 2
    # scalar: last_scan + active_offers + pending_tasks + pending_enable_tasks + disabled_today = 5
    assert mock_db.scalar.call_count == 5


# Проверяем что dashboard считает warning и stop отдельно.
@pytest.mark.asyncio
async def test_dashboard_stats_counts_warning_and_stop_separately(mock_db):
    state_rows = _make_state_rows(normal=7, warning=2, stop=1)

    group_result = MagicMock()
    group_result.all.return_value = state_rows
    observer_result = MagicMock()
    observer_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(side_effect=[group_result, observer_result])
    mock_db.scalar = AsyncMock(side_effect=[None, 3, 0, 0, 0])

    from apps.api.routers.dashboard import get_dashboard_stats

    with patch(
        "apps.api.routers.dashboard._load_current_enable_recommendations",
        new=AsyncMock(return_value=(None, [])),
    ):
        result = await get_dashboard_stats(db=mock_db)

    assert result.total_ads_monitored == 10
    assert result.ads_in_warning == 2
    assert result.ads_in_stop == 1


# Проверяем что объявления в состоянии DISABLED корректно считаются.
@pytest.mark.asyncio
async def test_dashboard_stats_counts_disabled_state(mock_db):
    group_result = MagicMock()
    group_result.all.return_value = [
        (AlertState.NORMAL, "ACTIVE", 2, Decimal("1.00")),
        (AlertState.WARNING_SENT, "ACTIVE", 1, Decimal("0.50")),
        (AlertState.DISABLED, "OFF", 3, Decimal("2.00")),
    ]
    observer_result = MagicMock()
    observer_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(side_effect=[group_result, observer_result])
    mock_db.scalar = AsyncMock(side_effect=[None, 1, 0, 0, 0])

    from apps.api.routers.dashboard import get_dashboard_stats

    with patch(
        "apps.api.routers.dashboard._load_current_enable_recommendations",
        new=AsyncMock(return_value=(None, [])),
    ):
        result = await get_dashboard_stats(db=mock_db)

    assert result.total_ads_monitored == 6
    assert result.ads_disabled == 3
    assert result.ads_in_warning == 1


# Проверяем что dashboard отдаёт runtime-статус observer вместе с основной статистикой
@pytest.mark.asyncio
async def test_dashboard_stats_includes_observer_runtime_fields(mock_db):
    now = datetime(2026, 3, 29, 15, 45, tzinfo=UTC)
    state_rows = _make_state_rows(normal=2)
    group_result = MagicMock()
    group_result.all.return_value = state_rows
    observer_row = SimpleNamespace(
        cabinet_day_started_at=now - timedelta(hours=3),
        worker_status="ERROR",
        worker_message="Vision запустил профиль без CDP-порта.",
        worker_heartbeat_at=now,
        worker_last_error="Vision запустил профиль без CDP-порта.",
        worker_last_error_at=now - timedelta(seconds=10),
    )
    mock_db.execute = AsyncMock(
        side_effect=[
            group_result,
            _make_scalar_result(observer_row),  # _get_observer_settings (прямой вызов)
        ]
    )
    mock_db.scalar = AsyncMock(
        side_effect=[
            now - timedelta(minutes=5),  # func.max(last_observed_at)
            4,
            1,
            0,
            0,
        ]
    )

    from apps.api.routers.dashboard import get_dashboard_stats

    with patch(
        "apps.api.routers.dashboard._load_current_enable_recommendations",
        new=AsyncMock(return_value=(None, [])),
    ):
        result = await get_dashboard_stats(db=mock_db)

    assert result.observer_status == "ERROR"
    assert result.observer_status_message == "Vision запустил профиль без CDP-порта."
    assert result.observer_heartbeat_at == now.isoformat()
    assert result.observer_last_error == "Vision запустил профиль без CDP-порта."
    assert result.observer_last_error_at == (now - timedelta(seconds=10)).isoformat()


# Проверяем что dashboard по умолчанию отдаёт только актуальные задачи отключения без SUCCEEDED
@pytest.mark.asyncio
async def test_list_disable_tasks_filters_to_operational_statuses_by_default(mock_db):
    ad_id_1 = uuid.uuid4()
    ad_id_2 = uuid.uuid4()
    tasks = [
        SimpleNamespace(
            id="task-1",
            ad_id=ad_id_1,
            status=DisableTaskStatus.PENDING,
            attempt_count=0,
            last_error=None,
            next_retry_at=None,
            requested_by_username="bot",
            open_state_token="tok-1",
            created_at=datetime(2026, 3, 29, 15, 0, tzinfo=UTC),
            updated_at=datetime(2026, 3, 29, 15, 0, tzinfo=UTC),
            completed_at=None,
        ),
        SimpleNamespace(
            id="task-2",
            ad_id=ad_id_2,
            status=DisableTaskStatus.SUCCEEDED,
            attempt_count=1,
            last_error=None,
            next_retry_at=None,
            requested_by_username="bot",
            open_state_token="tok-2",
            created_at=datetime(2026, 3, 29, 15, 1, tzinfo=UTC),
            updated_at=datetime(2026, 3, 29, 15, 2, tzinfo=UTC),
            completed_at=datetime(2026, 3, 29, 15, 2, tzinfo=UTC),
        ),
    ]
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [tasks[0]]
    mock_db.execute = AsyncMock(return_value=result_mock)

    ad_ctx_map = {
        ad_id_1: {"fb_ad_id": "ad-1", "ad_name": "Ad 1"},
        ad_id_2: {"fb_ad_id": "ad-2", "ad_name": "Ad 2"},
    }

    from apps.api.routers.dashboard import list_disable_tasks

    with (
        patch(
            "apps.api.routers.dashboard.reconcile_disable_tasks",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "apps.api.routers.dashboard._load_ad_context_map",
            new=AsyncMock(return_value=ad_ctx_map),
        ),
    ):
        result = await list_disable_tasks(status=None, limit=50, offset=0, db=mock_db)

    assert [item.status for item in result] == ["PENDING"]


# Проверяем что dashboard может вернуть терминальные задачи по явному фильтру статуса
@pytest.mark.asyncio
async def test_list_disable_tasks_supports_explicit_succeeded_filter(mock_db):
    ad_id = uuid.uuid4()
    task = SimpleNamespace(
        id="task-2",
        ad_id=ad_id,
        status=DisableTaskStatus.SUCCEEDED,
        attempt_count=1,
        last_error=None,
        next_retry_at=None,
        requested_by_username="bot",
        open_state_token="tok-2",
        created_at=datetime(2026, 3, 29, 15, 1, tzinfo=UTC),
        updated_at=datetime(2026, 3, 29, 15, 2, tzinfo=UTC),
        completed_at=datetime(2026, 3, 29, 15, 2, tzinfo=UTC),
    )
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [task]
    mock_db.execute = AsyncMock(return_value=result_mock)

    ad_ctx_map = {ad_id: {"fb_ad_id": "ad-2", "ad_name": "Ad 2"}}

    from apps.api.routers.dashboard import list_disable_tasks

    with (
        patch(
            "apps.api.routers.dashboard.reconcile_disable_tasks",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "apps.api.routers.dashboard._load_ad_context_map",
            new=AsyncMock(return_value=ad_ctx_map),
        ),
    ):
        result = await list_disable_tasks(status="SUCCEEDED", limit=50, offset=0, db=mock_db)

    assert [item.status for item in result] == ["SUCCEEDED"]


# Проверяем что UI-перезапуск disable worker останавливает старый процесс и поднимает новый.
@pytest.mark.asyncio
async def test_restart_disable_worker_restarts_process():
    from apps.api.routers.settings import restart_disable_worker

    with (
        patch(
            "apps.api.routers.settings._stop_disable_process", new=AsyncMock(return_value=11111)
        ) as stop_mock,
        patch(
            "apps.api.routers.settings._start_disable_process", new=AsyncMock(return_value=22222)
        ) as start_mock,
    ):
        result = await restart_disable_worker()

    assert result == {"restarted": True, "old_pid": 11111, "new_pid": 22222}
    stop_mock.assert_awaited_once_with()
    start_mock.assert_awaited_once_with(reason="Перезапуск воркера отключения через интерфейс")


# Проверяем что зависшую RUNNING-задачу можно вручную вернуть в очередь перед рестартом воркера.
@pytest.mark.asyncio
async def test_retry_disable_task_allows_stale_running_task(mock_db):
    from apps.api.routers.dashboard import retry_disable_task

    stale_time = datetime.now(UTC) - timedelta(minutes=10)
    task = SimpleNamespace(
        id="task-stale",
        status=DisableTaskStatus.RUNNING,
        created_at=stale_time,
        updated_at=stale_time,
        next_retry_at=stale_time,
        last_error="Зависло в браузере",
        completed_at=None,
    )
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = task
    mock_db.execute = AsyncMock(return_value=result_mock)
    mock_db.commit = AsyncMock()

    result = await retry_disable_task(task.id, db=mock_db)

    assert result == {"ok": True}
    assert task.status == DisableTaskStatus.PENDING
    assert task.next_retry_at is None
    assert task.last_error is None
    assert task.completed_at is None
    mock_db.commit.assert_awaited_once_with()


# Проверяем что задачу отключения можно вручную удалить из активной очереди.
@pytest.mark.asyncio
async def test_cancel_disable_task_marks_task_cancelled_and_restores_snapshot(mock_db):
    from apps.api.routers.dashboard import cancel_disable_task

    task_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    task = SimpleNamespace(
        id=task_id,
        ad_id=ad_id,
        open_state_token="incident-1",
        status=DisableTaskStatus.RUNNING,
        completed_at=None,
        next_retry_at=datetime.now(UTC),
        last_error=None,
    )
    snapshot = SimpleNamespace(
        ad_id=ad_id,
        open_state_token="incident-1",
        delivery_status="ACTIVE",
        alert_state=AlertState.CLAIMED,
        current_stage=AlertStage.STOP,
    )
    task_result = MagicMock()
    task_result.scalar_one_or_none.return_value = task
    snapshot_result = MagicMock()
    snapshot_result.scalar_one_or_none.return_value = snapshot
    mock_db.execute = AsyncMock(side_effect=[task_result, snapshot_result])
    mock_db.commit = AsyncMock()

    result = await cancel_disable_task(str(task_id), db=mock_db)

    assert result == {"ok": True}
    assert task.status == DisableTaskStatus.CANCELLED
    assert task.completed_at is not None
    assert task.next_retry_at is None
    assert task.last_error == "Задача удалена из очереди вручную через dashboard"
    assert snapshot.alert_state == AlertState.STOP_SENT
    mock_db.commit.assert_awaited_once_with()


# Проверяем что завершённую задачу нельзя повторно удалить из очереди.
@pytest.mark.asyncio
async def test_cancel_disable_task_rejects_completed_task(mock_db):
    from fastapi import HTTPException

    from apps.api.routers.dashboard import cancel_disable_task

    task = SimpleNamespace(id=uuid.uuid4(), status=DisableTaskStatus.SUCCEEDED)
    task_result = MagicMock()
    task_result.scalar_one_or_none.return_value = task
    mock_db.execute = AsyncMock(return_value=task_result)

    with pytest.raises(HTTPException) as exc_info:
        await cancel_disable_task(str(task.id), db=mock_db)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Задача уже завершена"


# Проверяем что helper корректно собирает summary, funnel и сортировку кампаний
def test_build_dashboard_performance_payload_aggregates_metrics():
    from apps.api.routers.dashboard import _build_dashboard_performance_payload

    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    snapshots = [
        _make_snapshot(
            campaign_name="Campaign A",
            spend="100.00",
            clicks=200,
            leads=50,
            registrations=20,
            deposits=4,
            last_observed_at=now - timedelta(hours=1),
        ),
        _make_snapshot(
            campaign_name="Campaign B",
            spend="50.00",
            clicks=100,
            leads=20,
            registrations=10,
            deposits=1,
            last_observed_at=now - timedelta(hours=3),
        ),
    ]
    ad_ctx = {
        s.ad_id: {"campaign_name": cn}
        for s, cn in zip(snapshots, ["Campaign A", "Campaign B"], strict=True)
    }

    payload = _build_dashboard_performance_payload(
        snapshots,
        period="today",
        now=now,
        ad_context_map=ad_ctx,
    )

    assert payload.summary.spend == Decimal("150.00")
    assert payload.summary.clicks == 300
    assert payload.summary.leads == 70
    assert payload.summary.registrations == 30
    assert payload.summary.deposits == 5
    assert payload.summary.cpc == Decimal("0.5000")
    assert payload.summary.cpl == Decimal("2.1429")
    assert payload.summary.cpr == Decimal("5.0000")
    assert payload.summary.cost_per_deposit == Decimal("30.0000")
    assert payload.summary.click_to_lead_rate == pytest.approx(23.3)
    assert payload.summary.lead_to_reg_rate == pytest.approx(42.9)
    assert payload.summary.reg_to_dep_rate == pytest.approx(16.7)
    assert [step.key for step in payload.funnel] == ["clicks", "leads", "registrations", "deposits"]
    assert payload.funnel[1].conversion_rate == pytest.approx(23.3)
    assert [row.campaign for row in payload.campaigns] == ["Campaign A", "Campaign B"]
    assert payload.campaigns[0].spend == Decimal("100.00")
    assert payload.campaigns[1].reg_to_dep_rate == pytest.approx(10.0)


# Проверяем, что 30-минутный график показывает накопительный (нарастающий) итог.
def test_build_performance_timeline_from_metric_history_cumulative_spend():
    # Проверяем что timeline показывает нарастающий итог — после появления значения
    # оно сохраняется в последующих бакетах до следующего обновления.
    from apps.api.routers.dashboard import _build_performance_timeline_from_metric_history_rows

    ad_1 = uuid.uuid4()
    ad_2 = uuid.uuid4()
    cutoff = datetime(2026, 3, 28, 7, 0, tzinfo=UTC)
    rows = [
        SimpleNamespace(
            ad_id=ad_1,
            fb_ad_id="fb-1",
            cycle_ts=cutoff + timedelta(minutes=5),
            spend=Decimal("0.10"),
            registrations=0,
            deposits=0,
        ),
        SimpleNamespace(
            ad_id=ad_2,
            fb_ad_id="fb-2",
            cycle_ts=cutoff + timedelta(minutes=10),
            spend=Decimal("0.20"),
            registrations=0,
            deposits=0,
        ),
        SimpleNamespace(
            ad_id=ad_1,
            fb_ad_id="fb-1",
            cycle_ts=cutoff + timedelta(hours=1, minutes=15),
            spend=Decimal("0.50"),
            registrations=1,
            deposits=0,
        ),
    ]

    timeline = _build_performance_timeline_from_metric_history_rows(
        rows,
        period="today",
        now=cutoff + timedelta(hours=2, minutes=30),
        cutoff=cutoff,
    )

    # Метки в TZ дашборда (UTC+2 для Europe/Kaliningrad): 07:00 UTC → 09:00 локально.
    assert [point.label for point in timeline] == [
        "09:00",
        "09:30",
        "10:00",
        "10:30",
        "11:00",
        "11:30",
    ]
    # 09:00 — ad_1(0.10) + ad_2(0.20) = 0.30
    # 09:30 — нет новых данных → переносим 0.30
    # 10:00 — ad_1 вырос до 0.50, ad_2=0.20 → 0.70
    # 10:30..11:30 — нет данных → переносим 0.70
    assert [point.spend for point in timeline] == [
        Decimal("0.30"),
        Decimal("0.30"),
        Decimal("0.70"),
        Decimal("0.70"),
        Decimal("0.70"),
        Decimal("0.70"),
    ]
    # registrations: 0 до 10:00, потом 1 (нарастающий)
    assert [point.registrations for point in timeline] == [0, 0, 1, 1, 1, 1]


# Проверяем что периоды today, 7d и 30d фильтруют снэпшоты по-разному
def test_build_dashboard_performance_payload_respects_periods():
    from apps.api.routers.dashboard import _build_dashboard_performance_payload

    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    names = ["Today campaign", "Within week", "Within month"]
    snapshots = [
        _make_snapshot(
            campaign_name="Today campaign",
            spend="20.00",
            clicks=40,
            leads=8,
            registrations=4,
            deposits=1,
            last_observed_at=now - timedelta(hours=2),
        ),
        _make_snapshot(
            campaign_name="Within week",
            spend="30.00",
            clicks=60,
            leads=10,
            registrations=5,
            deposits=2,
            last_observed_at=datetime(2026, 3, 22, 18, 30, tzinfo=UTC),
        ),
        _make_snapshot(
            campaign_name="Within month",
            spend="40.00",
            clicks=80,
            leads=16,
            registrations=8,
            deposits=3,
            last_observed_at=datetime(2026, 3, 10, 11, 0, tzinfo=UTC),
        ),
    ]
    ad_ctx = {s.ad_id: {"campaign_name": n} for s, n in zip(snapshots, names, strict=True)}

    today_payload = _build_dashboard_performance_payload(
        snapshots,
        period="today",
        now=now,
        ad_context_map=ad_ctx,
    )
    week_payload = _build_dashboard_performance_payload(
        snapshots,
        period="7d",
        now=now,
        ad_context_map=ad_ctx,
    )
    month_payload = _build_dashboard_performance_payload(
        snapshots,
        period="30d",
        now=now,
        ad_context_map=ad_ctx,
    )

    assert today_payload.summary.spend == Decimal("20.00")
    assert today_payload.summary.deposits == 1
    assert week_payload.summary.spend == Decimal("50.00")
    assert week_payload.summary.deposits == 3
    assert month_payload.summary.spend == Decimal("90.00")
    assert month_payload.summary.deposits == 6


# Проверяем что архив суток и текущий срез суммируются в историческом period
def test_build_dashboard_performance_payload_merges_archives_with_current_snapshots():
    from apps.api.routers.dashboard import _build_dashboard_performance_payload

    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    snapshots = [
        _make_snapshot(
            campaign_name="Current campaign",
            spend="20.00",
            clicks=40,
            leads=8,
            registrations=4,
            deposits=1,
            last_observed_at=now - timedelta(minutes=20),
        ),
    ]
    ad_ctx = {snapshots[0].ad_id: {"campaign_name": "Current campaign"}}
    archives = [
        _make_archive(
            started_at=datetime(2026, 3, 27, 8, 0, tzinfo=UTC),
            ended_at=datetime(2026, 3, 28, 8, 0, tzinfo=UTC),
            spend="30.00",
            clicks=60,
            leads=10,
            registrations=5,
            deposits=2,
            campaigns_json=[
                {
                    "campaign": "Archive campaign",
                    "spend": "30.00",
                    "clicks": 60,
                    "leads": 10,
                    "registrations": 5,
                    "deposits": 2,
                }
            ],
        )
    ]

    payload = _build_dashboard_performance_payload(
        snapshots,
        period="7d",
        now=now,
        cutoff=now - timedelta(days=7),
        archives=archives,
        ad_context_map=ad_ctx,
    )

    assert payload.summary.spend == Decimal("50.00")
    assert payload.summary.deposits == 3
    assert [row.campaign for row in payload.campaigns] == ["Archive campaign", "Current campaign"]


# Проверяем что period=today режется по локальной дате dashboard, а не по UTC
def test_build_dashboard_performance_payload_uses_local_day_cutoff():
    from apps.api.routers.dashboard import _build_dashboard_performance_payload

    tz = ZoneInfo("Europe/Kaliningrad")
    now = datetime(2026, 3, 29, 0, 30, tzinfo=tz)
    names = ["Вчера поздно", "Сегодня локально"]
    snapshots = [
        _make_snapshot(
            campaign_name="Вчера поздно",
            spend="11.00",
            clicks=22,
            leads=5,
            registrations=2,
            deposits=1,
            last_observed_at=datetime(2026, 3, 28, 21, 30, tzinfo=UTC),
        ),
        _make_snapshot(
            campaign_name="Сегодня локально",
            spend="7.00",
            clicks=14,
            leads=4,
            registrations=1,
            deposits=0,
            last_observed_at=datetime(2026, 3, 28, 22, 15, tzinfo=UTC),
        ),
    ]
    ad_ctx = {s.ad_id: {"campaign_name": n} for s, n in zip(snapshots, names, strict=True)}

    payload = _build_dashboard_performance_payload(
        snapshots,
        period="today",
        now=now,
        ad_context_map=ad_ctx,
    )

    assert payload.summary.spend == Decimal("7.00")
    assert [row.campaign for row in payload.campaigns] == ["Сегодня локально"]


# Проверяем что zero-safe расчёты не подставляют ложные нули в cost и conversion
def test_build_dashboard_performance_payload_keeps_nulls_for_zero_denominators():
    from apps.api.routers.dashboard import _build_dashboard_performance_payload

    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    snapshots = [
        _make_snapshot(
            campaign_name="Zero campaign",
            spend="15.00",
            clicks=0,
            leads=0,
            registrations=0,
            deposits=0,
            last_observed_at=now - timedelta(minutes=30),
        )
    ]

    ad_ctx = {snapshots[0].ad_id: {"campaign_name": "Zero campaign"}}
    payload = _build_dashboard_performance_payload(
        snapshots,
        period="today",
        now=now,
        ad_context_map=ad_ctx,
    )

    assert payload.summary.cpc is None
    assert payload.summary.cpl is None
    assert payload.summary.cpr is None
    assert payload.summary.cost_per_deposit is None
    assert payload.summary.click_to_lead_rate is None
    assert payload.summary.lead_to_reg_rate is None
    assert payload.summary.reg_to_dep_rate is None
    assert payload.campaigns[0].cost_per_deposit is None
    assert payload.campaigns[0].reg_to_dep_rate is None


# Проверяем что performance-срез предпочитает начало текущих суток кабинета.
@pytest.mark.asyncio
async def test_resolve_dashboard_performance_cutoff_prefers_cabinet_day(mock_db):
    from apps.api.routers.dashboard import _resolve_dashboard_performance_cutoff

    cabinet_day_start = datetime(2026, 3, 28, 8, 0, tzinfo=UTC)

    with patch(
        "apps.api.routers.dashboard._get_cabinet_day_start",
        new=AsyncMock(return_value=cabinet_day_start),
    ):
        result = await _resolve_dashboard_performance_cutoff(mock_db)

    assert result == cabinet_day_start


# Проверяем что без зафиксированных суток кабинета performance остаётся на старом live-fallback.
@pytest.mark.asyncio
async def test_resolve_dashboard_performance_cutoff_falls_back_to_current_scan(mock_db):
    from apps.api.routers.dashboard import _resolve_dashboard_performance_cutoff

    last_scan = datetime(2026, 3, 28, 10, 0, tzinfo=UTC)
    mock_db.scalar = AsyncMock(return_value=last_scan)

    with patch(
        "apps.api.routers.dashboard._get_cabinet_day_start",
        new=AsyncMock(return_value=None),
    ):
        result = await _resolve_dashboard_performance_cutoff(mock_db)

    assert result == last_scan - timedelta(minutes=30)


# Проверяем что today в endpoint использует границу performance-среза, а не только live-сессию.
@pytest.mark.asyncio
async def test_dashboard_performance_today_uses_performance_cutoff(mock_db):
    snapshots_result = MagicMock()
    snapshots_result.scalars.return_value.all.return_value = []
    offers_result = MagicMock()
    offers_result.scalars.return_value.all.return_value = []
    cutoff = datetime(2026, 3, 28, 8, 0, tzinfo=UTC)
    history_result = MagicMock()
    history_result.all.return_value = []
    mock_db.execute = AsyncMock(side_effect=[snapshots_result, offers_result, history_result])

    from apps.api.routers.dashboard import get_dashboard_performance

    with (
        patch(
            "apps.api.routers.dashboard._resolve_dashboard_performance_cutoff",
            new=AsyncMock(return_value=cutoff),
        ),
        patch(
            "apps.api.routers.dashboard._load_fake_deposits_map",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "apps.api.routers.dashboard._load_ad_context_map",
            new_callable=AsyncMock,
            return_value={},
        ),
    ):
        await get_dashboard_performance(period="today", db=mock_db)

    # Первый вызов execute — запрос снэпшотов с фильтром по last_observed_at
    stmt = mock_db.execute.call_args_list[0].args[0]
    where_clause = list(stmt._where_criteria)[0]
    assert where_clause.right.value == cutoff


# Проверяем что chart-data для today без zero-scan использует fallback начала локального дня
@pytest.mark.asyncio
async def test_chart_data_today_uses_local_day_fallback(mock_db):
    empty_result = MagicMock()
    empty_result.all.return_value = []
    empty_result.scalars.return_value.all.return_value = []
    # observer_settings возвращает None (через execute → scalar_one_or_none)
    observer_result = MagicMock()
    observer_result.scalar_one_or_none.return_value = None
    observer_result = _make_scalar_result(None)  # _get_cabinet_day_start → ObserverSettings
    mock_db.execute = AsyncMock(
        side_effect=[
            observer_result,  # _get_cabinet_day_start для _resolve_dashboard_event_cutoff
            empty_result,  # alerts query
            empty_result,  # snapshots query
            empty_result,
            empty_result,
            empty_result,
        ]
    )
    # scalar: last_scan=None (snapshot_cutoff), last_archive_end=None
    mock_db.scalar = AsyncMock(side_effect=[None, None])

    from unittest.mock import patch

    from apps.api.routers.dashboard import get_chart_data

    now = datetime(2026, 3, 28, 13, 45, tzinfo=ZoneInfo("Europe/Kaliningrad"))

    with (
        patch("apps.api.routers.dashboard._dashboard_now", return_value=now),
        patch(
            "apps.api.routers.dashboard._resolve_dashboard_snapshot_cutoff",
            new=AsyncMock(return_value=now.replace(hour=0, minute=0, second=0, microsecond=0)),
        ),
        patch(
            "apps.api.routers.dashboard._resolve_dashboard_performance_cutoff",
            new=AsyncMock(return_value=now.replace(hour=0, minute=0, second=0, microsecond=0)),
        ),
        patch(
            "apps.api.routers.dashboard._load_fake_deposits_map",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "apps.api.routers.dashboard._load_ad_context_map",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "apps.api.routers.dashboard._load_offer_rules_for_snapshots",
            new_callable=AsyncMock,
            return_value={},
        ),
    ):
        await get_chart_data(period="today", db=mock_db)

    # call_args_list[0] = observer_settings, [1] = alerts query
    stmt = mock_db.execute.call_args_list[1].args[0]
    where_clause = list(stmt._where_criteria)[0]
    assert where_clause.right.value == now.replace(hour=0, minute=0, second=0, microsecond=0)


# Проверяем что кастомная граница суток кабинета отрезает данные до zero-scan
def test_build_dashboard_performance_payload_respects_explicit_cabinet_cutoff():
    from apps.api.routers.dashboard import _build_dashboard_performance_payload

    tz = ZoneInfo("Europe/Kaliningrad")
    cutoff = datetime(2026, 3, 28, 8, 0, tzinfo=tz)
    now = datetime(2026, 3, 28, 10, 0, tzinfo=tz)
    snapshots = [
        _make_snapshot(
            campaign_name="До reset",
            spend="9.00",
            clicks=18,
            leads=3,
            registrations=1,
            deposits=0,
            last_observed_at=datetime(2026, 3, 28, 5, 30, tzinfo=UTC),
        ),
        _make_snapshot(
            campaign_name="После reset",
            spend="4.00",
            clicks=8,
            leads=2,
            registrations=1,
            deposits=0,
            last_observed_at=datetime(2026, 3, 28, 6, 15, tzinfo=UTC),
        ),
    ]

    ad_ctx = {
        snapshots[0].ad_id: {"campaign_name": "До reset"},
        snapshots[1].ad_id: {"campaign_name": "После reset"},
    }
    payload = _build_dashboard_performance_payload(
        snapshots,
        period="today",
        now=now,
        cutoff=cutoff,
        ad_context_map=ad_ctx,
    )

    assert payload.summary.spend == Decimal("4.00")
    assert [row.campaign for row in payload.campaigns] == ["После reset"]


# Проверяем что endpoint performance использует helper и возвращает zero-safe payload
@pytest.mark.asyncio
async def test_dashboard_performance_endpoint_returns_payload(mock_db):
    observed_at = datetime.now(UTC) - timedelta(minutes=10)
    snap = _make_snapshot(
        campaign_name="Campaign A",
        spend="42.00",
        clicks=84,
        leads=14,
        registrations=7,
        deposits=2,
        last_observed_at=observed_at,
    )
    snapshots_result = MagicMock()
    snapshots_result.scalars.return_value.all.return_value = [snap]
    # Второй execute — запрос офферов (для ROAS), возвращаем пустой список
    offers_result = MagicMock()
    offers_result.scalars.return_value.all.return_value = []
    history_result = MagicMock()
    history_result.all.return_value = []
    mock_db.execute = AsyncMock(side_effect=[snapshots_result, offers_result, history_result])
    mock_db.scalar = AsyncMock(return_value=observed_at)

    from unittest.mock import patch

    from apps.api.routers.dashboard import get_dashboard_performance

    ad_ctx = {snap.ad_id: {"campaign_name": "Campaign A"}}
    with (
        patch(
            "apps.api.routers.dashboard._resolve_dashboard_performance_cutoff",
            new=AsyncMock(return_value=observed_at - timedelta(minutes=30)),
        ),
        patch(
            "apps.api.routers.dashboard._load_ad_context_map",
            new_callable=AsyncMock,
            return_value=ad_ctx,
        ),
        patch(
            "apps.api.routers.dashboard._load_fake_deposits_map",
            new_callable=AsyncMock,
            return_value={},
        ),
    ):
        payload = await get_dashboard_performance(period="today", db=mock_db)

    assert payload.period == "today"
    assert payload.summary.spend == Decimal("42.00")
    assert payload.summary.cpc == Decimal("0.5000")
    assert payload.campaigns[0].campaign == "Campaign A"
    assert mock_db.execute.call_count == 3


# =====================================================================
# POST /dashboard/disable-tasks — ручное создание задачи отключения
# =====================================================================


# Проверяем что ручное создание задачи заполняет все обязательные поля DisableTask
@pytest.mark.asyncio
async def test_create_disable_task_sets_required_fields(mock_db):
    from unittest.mock import patch

    from apps.api.routers.dashboard import create_disable_task

    snapshot_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    offer_id = uuid.uuid4()
    snapshot = SimpleNamespace(
        id=snapshot_id,
        ad_id=ad_id,
        fb_ad_id="123456",
        open_state_token="tok-abc",
    )

    # scalar: первый вызов — snapshot, второй — existing_task (None)
    mock_db.scalar = AsyncMock(side_effect=[snapshot, None])
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    now = datetime.now(UTC)

    async def _fake_refresh(obj: object) -> None:
        obj.id = uuid.uuid4()
        obj.created_at = now
        obj.updated_at = now
        obj.attempt_count = 0
        obj.last_error = None
        obj.next_retry_at = None
        obj.completed_at = None

    mock_db.refresh = AsyncMock(side_effect=_fake_refresh)

    ad_ctx = {ad_id: {"fb_ad_id": "123456", "ad_name": "Test Ad", "offer_id": offer_id}}
    body = SimpleNamespace(fb_ad_id="123456")
    with patch(
        "apps.api.routers.dashboard._load_ad_context_map",
        new_callable=AsyncMock,
        return_value=ad_ctx,
    ):
        await create_disable_task(body=body, db=mock_db)

    # Проверяем что db.add вызван с DisableTask, у которого все поля заполнены
    added_task = mock_db.add.call_args[0][0]
    assert added_task.ad_id == ad_id
    assert added_task.snapshot_id == snapshot_id
    assert added_task.offer_id == offer_id
    assert added_task.open_state_token == "tok-abc"
    assert added_task.idempotency_key == "dashboard_123456_tok-abc"
    assert added_task.status == DisableTaskStatus.PENDING
    assert added_task.requested_by_username == "dashboard"


# Проверяем что при отсутствии open_state_token генерируется новый UUID
@pytest.mark.asyncio
async def test_create_disable_task_generates_token_when_missing(mock_db):
    from unittest.mock import patch

    from apps.api.routers.dashboard import create_disable_task

    ad_id = uuid.uuid4()
    snapshot = SimpleNamespace(
        id=uuid.uuid4(),
        ad_id=ad_id,
        fb_ad_id="789",
        open_state_token=None,
    )
    mock_db.scalar = AsyncMock(side_effect=[snapshot, None])
    mock_db.commit = AsyncMock()
    mock_db.flush = AsyncMock()
    mock_db.add = MagicMock()

    now = datetime.now(UTC)

    async def _fake_refresh(obj: object) -> None:
        obj.id = uuid.uuid4()
        obj.created_at = now
        obj.updated_at = now
        obj.attempt_count = 0
        obj.last_error = None
        obj.next_retry_at = None
        obj.completed_at = None

    mock_db.refresh = AsyncMock(side_effect=_fake_refresh)

    ad_ctx = {ad_id: {"fb_ad_id": "789", "ad_name": "Ad No Token", "offer_id": uuid.uuid4()}}
    body = SimpleNamespace(fb_ad_id="789")
    with patch(
        "apps.api.routers.dashboard._load_ad_context_map",
        new_callable=AsyncMock,
        return_value=ad_ctx,
    ):
        await create_disable_task(body=body, db=mock_db)

    # Snapshot должен получить сгенерированный токен
    assert snapshot.open_state_token is not None
    assert len(snapshot.open_state_token) == 36  # UUID формат
    mock_db.flush.assert_awaited_once()

    added_task = mock_db.add.call_args[0][0]
    assert added_task.idempotency_key.startswith("dashboard_789_")


# Проверяем что для несуществующего snapshot возвращается 404
@pytest.mark.asyncio
async def test_create_disable_task_returns_404_for_missing_snapshot(mock_db):
    from fastapi import HTTPException

    from apps.api.routers.dashboard import create_disable_task

    mock_db.scalar = AsyncMock(return_value=None)

    body = SimpleNamespace(fb_ad_id="nonexistent")
    with pytest.raises(HTTPException) as exc_info:
        await create_disable_task(body=body, db=mock_db)

    assert exc_info.value.status_code == 404


# Проверяем, что ручное отключение автовключения использует текущие сутки кабинета из ObserverSettings.
@pytest.mark.asyncio
async def test_disable_auto_enable_uses_observer_cabinet_day(mock_db):
    from apps.api.routers.dashboard import disable_auto_enable

    cabinet_day_started_at = datetime(2026, 4, 24, 0, 0, tzinfo=UTC)
    existing_result = MagicMock()
    existing_result.scalar_one_or_none.return_value = None
    settings_result = MagicMock()
    settings_result.scalar_one_or_none.return_value = SimpleNamespace(
        cabinet_day_started_at=cabinet_day_started_at
    )
    mock_db.execute = AsyncMock(side_effect=[existing_result, settings_result])
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    response = await disable_auto_enable("ad-42", db=mock_db)

    assert response == {"ok": True}
    added_row = mock_db.add.call_args.args[0]
    assert added_row.fb_ad_id == "ad-42"
    assert added_row.cabinet_day_started_at == cabinet_day_started_at
    mock_db.commit.assert_awaited_once()


# Проверяем, что отсутствие ObserverSettings не ломает ручное отключение автовключения.
@pytest.mark.asyncio
async def test_disable_auto_enable_handles_missing_observer_settings(mock_db):
    from apps.api.routers.dashboard import (
        _UNKNOWN_CABINET_DAY_STARTED_AT,
        disable_auto_enable,
    )

    existing_result = MagicMock()
    existing_result.scalar_one_or_none.return_value = None
    settings_result = MagicMock()
    settings_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(side_effect=[existing_result, settings_result])
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    response = await disable_auto_enable("ad-no-settings", db=mock_db)

    assert response == {"ok": True}
    added_row = mock_db.add.call_args.args[0]
    assert added_row.fb_ad_id == "ad-no-settings"
    assert added_row.cabinet_day_started_at == _UNKNOWN_CABINET_DAY_STARTED_AT
    mock_db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Тесты логики выбора источника таймлайна в get_dashboard_performance
# ---------------------------------------------------------------------------


def _make_timeline_point(spend="0.00", registrations=0, deposits=0):
    """Создаёт точку таймлайна с заданными значениями."""
    from decimal import Decimal

    from apps.api.schemas import DashboardPerformanceTimelinePointSchema

    return DashboardPerformanceTimelinePointSchema(
        timestamp="2026-01-01T00:00:00+00:00",
        label="00:00",
        spend=Decimal(spend),
        registrations=registrations,
        deposits=deposits,
    )


# metric_timeline — пустой массив, должен быть плоский таймлайн (не snapshot-buckets)
@pytest.mark.asyncio
async def test_get_dashboard_performance_uses_flat_timeline_when_metric_timeline_empty(mock_db):
    from unittest.mock import AsyncMock, MagicMock, patch

    from apps.api.routers.dashboard import get_dashboard_performance

    flat_points = [
        _make_timeline_point(spend="0.00"),
        _make_timeline_point(spend="0.00"),
    ]
    fake_payload = MagicMock()
    fake_payload.timeline = [_make_timeline_point(spend="99.00")]

    mock_db.scalar = AsyncMock(return_value=None)
    mock_db.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )

    with (
        patch(
            "apps.api.routers.dashboard._dashboard_now",
            return_value=datetime(2026, 1, 1, 12, tzinfo=UTC),
        ),
        patch(
            "apps.api.routers.dashboard._resolve_dashboard_performance_cutoff",
            new=AsyncMock(return_value=datetime(2026, 1, 1, 0, tzinfo=UTC)),
        ),
        patch(
            "apps.api.routers.dashboard._load_dashboard_archives", new=AsyncMock(return_value=[])
        ),
        patch("apps.api.routers.dashboard._load_fake_deposits_map", new=AsyncMock(return_value={})),
        patch("apps.api.routers.dashboard._load_ad_context_map", new=AsyncMock(return_value={})),
        patch(
            "apps.api.routers.dashboard._build_dashboard_performance_payload",
            return_value=fake_payload,
        ),
        patch(
            "apps.api.routers.dashboard._build_flat_performance_timeline",
            return_value=flat_points,
        ),
        patch(
            "apps.api.routers.dashboard._load_performance_timeline_from_metric_history",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await get_dashboard_performance(period="today", db=mock_db)

    assert result.timeline == flat_points


# metric_timeline — все нули, должен быть плоский таймлайн
@pytest.mark.asyncio
async def test_get_dashboard_performance_uses_flat_timeline_when_metric_timeline_all_zeros(mock_db):
    from unittest.mock import AsyncMock, MagicMock, patch

    from apps.api.routers.dashboard import get_dashboard_performance

    flat_points = [_make_timeline_point(spend="0.00", registrations=0, deposits=0)]
    fake_payload = MagicMock()
    fake_payload.timeline = [_make_timeline_point(spend="0.43")]

    zero_points = [
        _make_timeline_point(spend="0.00", registrations=0, deposits=0) for _ in range(3)
    ]

    mock_db.scalar = AsyncMock(return_value=None)
    mock_db.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )

    with (
        patch(
            "apps.api.routers.dashboard._dashboard_now",
            return_value=datetime(2026, 1, 1, 12, tzinfo=UTC),
        ),
        patch(
            "apps.api.routers.dashboard._resolve_dashboard_performance_cutoff",
            new=AsyncMock(return_value=datetime(2026, 1, 1, 0, tzinfo=UTC)),
        ),
        patch(
            "apps.api.routers.dashboard._load_dashboard_archives", new=AsyncMock(return_value=[])
        ),
        patch("apps.api.routers.dashboard._load_fake_deposits_map", new=AsyncMock(return_value={})),
        patch("apps.api.routers.dashboard._load_ad_context_map", new=AsyncMock(return_value={})),
        patch(
            "apps.api.routers.dashboard._build_dashboard_performance_payload",
            return_value=fake_payload,
        ),
        patch(
            "apps.api.routers.dashboard._build_flat_performance_timeline",
            return_value=flat_points,
        ),
        patch(
            "apps.api.routers.dashboard._load_performance_timeline_from_metric_history",
            new=AsyncMock(return_value=zero_points),
        ),
    ):
        result = await get_dashboard_performance(period="today", db=mock_db)

    assert result.timeline == flat_points


# metric_timeline — есть реальные данные, должен использоваться metric_timeline
@pytest.mark.asyncio
async def test_get_dashboard_performance_uses_metric_timeline_when_has_real_data(mock_db):
    from unittest.mock import AsyncMock, MagicMock, patch

    from apps.api.routers.dashboard import get_dashboard_performance

    snapshot_point = _make_timeline_point(spend="0.43")
    fake_payload = MagicMock()
    fake_payload.timeline = [snapshot_point]

    real_points = [
        _make_timeline_point(spend="0.00"),
        _make_timeline_point(spend="1.50", registrations=2),
        _make_timeline_point(spend="0.00"),
    ]

    mock_db.scalar = AsyncMock(return_value=None)
    mock_db.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )

    with (
        patch(
            "apps.api.routers.dashboard._dashboard_now",
            return_value=datetime(2026, 1, 1, 12, tzinfo=UTC),
        ),
        patch(
            "apps.api.routers.dashboard._resolve_dashboard_performance_cutoff",
            new=AsyncMock(return_value=datetime(2026, 1, 1, 0, tzinfo=UTC)),
        ),
        patch(
            "apps.api.routers.dashboard._load_dashboard_archives", new=AsyncMock(return_value=[])
        ),
        patch("apps.api.routers.dashboard._load_fake_deposits_map", new=AsyncMock(return_value={})),
        patch("apps.api.routers.dashboard._load_ad_context_map", new=AsyncMock(return_value={})),
        patch(
            "apps.api.routers.dashboard._build_dashboard_performance_payload",
            return_value=fake_payload,
        ),
        # metric_timeline содержит точку с реальными данными
        patch(
            "apps.api.routers.dashboard._load_performance_timeline_from_metric_history",
            new=AsyncMock(return_value=real_points),
        ),
    ):
        result = await get_dashboard_performance(period="today", db=mock_db)

    # Таймлайн должен быть из metric_history
    assert result.timeline == real_points
