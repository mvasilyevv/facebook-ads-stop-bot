# -*- coding: utf-8 -*-
"""Тесты dashboard-эндпоинтов и helper'ов агрегации."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from zoneinfo import ZoneInfo

from core.domain import AlertState


# Вспомогательная фабрика мок-результата GROUP BY
def _make_state_rows(normal=0, warning=0, stop=0, disabled=0):
    """Создаёт строки как их возвращает GROUP BY alert_state."""
    rows = []
    if normal:
        rows.append((AlertState.NORMAL, normal, Decimal("10.00")))
    if warning:
        rows.append((AlertState.WARNING_SENT, warning, Decimal("25.50")))
    if stop:
        rows.append((AlertState.STOP_SENT, stop, Decimal("50.00")))
    if disabled:
        rows.append((AlertState.DISABLED, disabled, Decimal("5.00")))
    return rows


def _make_snapshot(
    *,
    campaign_name: str,
    spend: str,
    clicks: int,
    leads: int,
    registrations: int,
    deposits: int,
    last_observed_at: datetime,
):
    """Создаёт упрощённый snapshot для helper-тестов performance."""
    return SimpleNamespace(
        campaign_name=campaign_name,
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


@pytest.fixture
def mock_db():
    """Мок async DB-сессии."""
    db = AsyncMock()
    return db


# Проверяем что dashboard возвращает правильные счётчики при наличии данных
@pytest.mark.asyncio
async def test_dashboard_stats_counts(mock_db):
    state_rows = _make_state_rows(normal=10, warning=3, stop=2, disabled=1)

    # Мокаем результат GROUP BY запроса
    group_result = MagicMock()
    group_result.all.return_value = state_rows
    mock_db.execute = AsyncMock(return_value=group_result)

    # scalar вызовы: last_scan, cabinet_day_start, active_offers, pending_tasks, disabled_today
    mock_db.scalar = AsyncMock(side_effect=[None, None, 5, 2, 1])

    from apps.api.main import get_dashboard_stats

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
    mock_db.execute = AsyncMock(return_value=group_result)
    mock_db.scalar = AsyncMock(side_effect=[None, None, 0, 0, 0])

    from apps.api.main import get_dashboard_stats

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
    mock_db.execute = AsyncMock(return_value=group_result)
    mock_db.scalar = AsyncMock(side_effect=[None, None, 1, 0, 0])

    from apps.api.main import get_dashboard_stats

    await get_dashboard_stats(db=mock_db)

    # Один execute (GROUP BY) вместо нескольких scalar для каждого состояния
    assert mock_db.execute.call_count == 1
    # scalar: last_scan + cabinet_day_start + active_offers + pending_tasks + disabled_today = 5
    assert mock_db.scalar.call_count == 5


# Проверяем что helper корректно собирает summary, funnel и сортировку кампаний
def test_build_dashboard_performance_payload_aggregates_metrics():
    from apps.api.main import _build_dashboard_performance_payload

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

    payload = _build_dashboard_performance_payload(snapshots, period="today", now=now)

    assert payload.summary.spend == Decimal("150.00")
    assert payload.summary.clicks == 300
    assert payload.summary.leads == 70
    assert payload.summary.registrations == 30
    assert payload.summary.deposits == 5
    assert payload.summary.cpc == Decimal("0.5000")
    assert payload.summary.cpl == Decimal("2.1429")
    assert payload.summary.cpr == Decimal("5.0000")
    assert payload.summary.spend_per_dep == Decimal("30.0000")
    assert payload.summary.click_to_lead_rate == pytest.approx(23.3)
    assert payload.summary.lead_to_reg_rate == pytest.approx(42.9)
    assert payload.summary.reg_to_dep_rate == pytest.approx(16.7)
    assert [step.key for step in payload.funnel] == ["clicks", "leads", "registrations", "deposits"]
    assert payload.funnel[1].conversion_rate == pytest.approx(23.3)
    assert [row.campaign for row in payload.campaigns] == ["Campaign A", "Campaign B"]
    assert payload.campaigns[0].spend == Decimal("100.00")
    assert payload.campaigns[1].reg_to_dep_rate == pytest.approx(10.0)


# Проверяем что периоды today, 7d и 30d фильтруют снэпшоты по-разному
def test_build_dashboard_performance_payload_respects_periods():
    from apps.api.main import _build_dashboard_performance_payload

    now = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
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

    today_payload = _build_dashboard_performance_payload(snapshots, period="today", now=now)
    week_payload = _build_dashboard_performance_payload(snapshots, period="7d", now=now)
    month_payload = _build_dashboard_performance_payload(snapshots, period="30d", now=now)

    assert today_payload.summary.spend == Decimal("20.00")
    assert today_payload.summary.deposits == 1
    assert week_payload.summary.spend == Decimal("50.00")
    assert week_payload.summary.deposits == 3
    assert month_payload.summary.spend == Decimal("90.00")
    assert month_payload.summary.deposits == 6


# Проверяем что архив суток и текущий срез суммируются в историческом period
def test_build_dashboard_performance_payload_merges_archives_with_current_snapshots():
    from apps.api.main import _build_dashboard_performance_payload

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
        )
    ]
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
    )

    assert payload.summary.spend == Decimal("50.00")
    assert payload.summary.deposits == 3
    assert [row.campaign for row in payload.campaigns] == ["Archive campaign", "Current campaign"]


# Проверяем что period=today режется по локальной дате dashboard, а не по UTC
def test_build_dashboard_performance_payload_uses_local_day_cutoff():
    from apps.api.main import _build_dashboard_performance_payload

    tz = ZoneInfo("Europe/Kaliningrad")
    now = datetime(2026, 3, 29, 0, 30, tzinfo=tz)
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

    payload = _build_dashboard_performance_payload(snapshots, period="today", now=now)

    assert payload.summary.spend == Decimal("7.00")
    assert [row.campaign for row in payload.campaigns] == ["Сегодня локально"]


# Проверяем что zero-safe расчёты не подставляют ложные нули в cost и conversion
def test_build_dashboard_performance_payload_keeps_nulls_for_zero_denominators():
    from apps.api.main import _build_dashboard_performance_payload

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

    payload = _build_dashboard_performance_payload(snapshots, period="today", now=now)

    assert payload.summary.cpc is None
    assert payload.summary.cpl is None
    assert payload.summary.cpr is None
    assert payload.summary.spend_per_dep is None
    assert payload.summary.click_to_lead_rate is None
    assert payload.summary.lead_to_reg_rate is None
    assert payload.summary.reg_to_dep_rate is None
    assert payload.campaigns[0].spend_per_dep is None
    assert payload.campaigns[0].reg_to_dep_rate is None


# Проверяем что today в endpoint опирается на актуальную скан-сессию, а не на полночь
@pytest.mark.asyncio
async def test_dashboard_performance_today_uses_current_scan_cutoff(mock_db):
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=execute_result)
    last_scan = datetime(2026, 3, 28, 10, 0, tzinfo=UTC)
    mock_db.scalar = AsyncMock(return_value=last_scan)

    from apps.api.main import get_dashboard_performance

    await get_dashboard_performance(period="today", db=mock_db)

    stmt = mock_db.execute.call_args.args[0]
    where_clause = list(stmt._where_criteria)[0]
    assert where_clause.right.value == last_scan - timedelta(minutes=30)


# Проверяем что chart-data для today без zero-scan использует fallback начала локального дня
@pytest.mark.asyncio
async def test_chart_data_today_uses_local_day_fallback(mock_db):
    empty_result = MagicMock()
    empty_result.all.return_value = []
    empty_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(side_effect=[empty_result, empty_result, empty_result, empty_result, empty_result])
    mock_db.scalar = AsyncMock(side_effect=[None, None, None])

    from apps.api.main import get_chart_data
    from unittest.mock import patch

    now = datetime(2026, 3, 28, 13, 45, tzinfo=ZoneInfo("Europe/Kaliningrad"))

    with patch("apps.api.main._dashboard_now", return_value=now):
        await get_chart_data(period="today", db=mock_db)

    stmt = mock_db.execute.call_args_list[0].args[0]
    where_clause = list(stmt._where_criteria)[0]
    assert where_clause.right.value == now.replace(hour=0, minute=0, second=0, microsecond=0)


# Проверяем что кастомная граница суток кабинета отрезает данные до zero-scan
def test_build_dashboard_performance_payload_respects_explicit_cabinet_cutoff():
    from apps.api.main import _build_dashboard_performance_payload

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

    payload = _build_dashboard_performance_payload(
        snapshots,
        period="today",
        now=now,
        cutoff=cutoff,
    )

    assert payload.summary.spend == Decimal("4.00")
    assert [row.campaign for row in payload.campaigns] == ["После reset"]


# Проверяем что endpoint performance использует helper и возвращает zero-safe payload
@pytest.mark.asyncio
async def test_dashboard_performance_endpoint_returns_payload(mock_db):
    observed_at = datetime.now(UTC) - timedelta(minutes=10)
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [
        _make_snapshot(
            campaign_name="Campaign A",
            spend="42.00",
            clicks=84,
            leads=14,
            registrations=7,
            deposits=2,
            last_observed_at=observed_at,
        )
    ]
    mock_db.execute = AsyncMock(return_value=execute_result)
    mock_db.scalar = AsyncMock(return_value=observed_at)

    from apps.api.main import get_dashboard_performance

    payload = await get_dashboard_performance(period="today", db=mock_db)

    assert payload.period == "today"
    assert payload.summary.spend == Decimal("42.00")
    assert payload.summary.cpc == Decimal("0.5000")
    assert payload.campaigns[0].campaign == "Campaign A"
    assert mock_db.execute.call_count == 1
