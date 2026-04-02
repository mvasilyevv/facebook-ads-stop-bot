# -*- coding: utf-8 -*-
"""Тесты API для рекомендаций на включение и enable-task flow."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.db.base import Base
from core.domain import AlertState, DisableTaskStatus, EnableRecommendationLevel, EnableTaskStatus
from core.models import EnableRecommendationEvent, EnableTask, ObserverSettings


def _rows_result(rows):
    """Создаёт мок результата scalars().all()."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


def _scalar_result(obj):
    """Создаёт мок результата scalar_one_or_none()."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = obj
    return result


@pytest.fixture
def mock_db():
    """Мок async DB-сессии."""
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


# Проверяем, что список рекомендаций отдаёт только последнюю запись на объявление и state TASK_CREATED.
@pytest.mark.asyncio
async def test_list_enable_recommendations_deduplicates_by_ad_and_marks_task_created(mock_db):
    from apps.api.main import list_enable_recommendations

    last_scan = datetime(2026, 3, 29, 14, 5, tzinfo=UTC)
    shared_batch = datetime(2026, 3, 29, 14, 0, tzinfo=UTC)
    latest_event = SimpleNamespace(
        id=uuid.uuid4(),
        fb_ad_id="ad-1",
        ad_name="Ad 1 new",
        delivery_status="OFF",
        recommendation_level=EnableRecommendationLevel.OK,
        matched_rule_codes=[],
        reason_title="Метрики в норме",
        reason_text="Объявление снова проходит по текущим правилам.",
        metrics_json={},
        live_batch_started_at=shared_batch,
        created_at=datetime(2026, 3, 29, 14, 2, tzinfo=UTC),
        updated_at=datetime(2026, 3, 29, 14, 6, tzinfo=UTC),
    )
    second_ad_event = SimpleNamespace(
        id=uuid.uuid4(),
        fb_ad_id="ad-2",
        ad_name="Ad 2",
        delivery_status="NOT_DELIVERING",
        recommendation_level=EnableRecommendationLevel.EARLY_SIGNAL,
        matched_rule_codes=["early_outbound_ctr_signal"],
        reason_title="Early",
        reason_text="Есть ранний сигнал",
        metrics_json={},
        live_batch_started_at=shared_batch,
        created_at=datetime(2026, 3, 29, 14, 3, tzinfo=UTC),
        updated_at=datetime(2026, 3, 29, 14, 3, tzinfo=UTC),
    )
    task = SimpleNamespace(
        id=uuid.uuid4(),
        recommendation_event_id=latest_event.id,
        status=EnableTaskStatus.PENDING,
        created_at=datetime(2026, 3, 29, 14, 4, tzinfo=UTC),
    )
    snapshot_ad_1 = SimpleNamespace(
        fb_ad_id="ad-1",
        ad_name="Ad 1 live",
        campaign_name="Campaign A",
        adset_name="Adset A",
        delivery_status="OFF",
        alert_state=AlertState.DISABLED,
        spend=Decimal("1.09"),
        clicks=6,
        cpc=Decimal("0.1800"),
        outbound_clicks=0,
        outbound_ctr=None,
        landing_page_views=0,
        cost_per_landing_page_view=None,
        cpm=None,
        frequency=None,
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        last_observed_at=last_scan,
    )
    snapshot_ad_2 = SimpleNamespace(
        fb_ad_id="ad-2",
        ad_name="Ad 2 live",
        campaign_name="Campaign B",
        adset_name="Adset B",
        delivery_status="NOT_DELIVERING",
        alert_state=AlertState.DISABLED,
        spend=Decimal("0.50"),
        clicks=2,
        cpc=Decimal("0.2500"),
        outbound_clicks=0,
        outbound_ctr=None,
        landing_page_views=0,
        cost_per_landing_page_view=None,
        cpm=None,
        frequency=None,
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        last_observed_at=datetime(2026, 3, 29, 14, 5, tzinfo=UTC),
    )

    live_rows = [
        SimpleNamespace(
            event=second_ad_event,
            snapshot=snapshot_ad_2,
            candidate=SimpleNamespace(
                recommendation_level=EnableRecommendationLevel.EARLY_SIGNAL,
                matched_rule_codes=["early_outbound_ctr_signal"],
                reason_title="Early",
                reason_text="Есть ранний сигнал",
                metrics_json={"outbound_ctr": "0.80"},
            ),
        ),
        SimpleNamespace(
            event=latest_event,
            snapshot=snapshot_ad_1,
            candidate=SimpleNamespace(
                recommendation_level=EnableRecommendationLevel.OK,
                matched_rule_codes=[],
                reason_title=None,
                reason_text=None,
                metrics_json={"spend": "1.09", "clicks": 6},
            ),
        ),
    ]
    mock_db.execute = AsyncMock(return_value=_rows_result([task]))

    with patch(
        "apps.api.routers.dashboard._load_current_enable_recommendations",
        new=AsyncMock(return_value=(shared_batch, live_rows)),
    ):
        result = await list_enable_recommendations(limit=20, db=mock_db)

    assert [row.fb_ad_id for row in result] == ["ad-2", "ad-1"]
    assert result[1].recommendation_level == "OK"
    assert result[1].state == "TASK_CREATED"
    assert result[1].campaign_name == "Campaign A"
    assert result[1].adset_name == "Adset A"
    assert result[1].ad_name == "Ad 1 live"
    assert result[1].reason_title == "Нет блокирующих сигналов"
    assert result[1].reason_text == "По текущим правилам блокирующих сигналов нет."
    assert result[1].metrics_json["spend"] == "1.09"
    assert result[1].metrics_json["clicks"] == 6
    assert result[1].updated_at == "2026-03-29T14:06:00+00:00"


# Проверяем, что явный recovery-кейс не теряет свой текст при сериализации.
@pytest.mark.asyncio
async def test_serialize_enable_recommendation_preserves_explicit_recovery_copy():
    from apps.api.main import _serialize_enable_recommendation_event

    event = SimpleNamespace(
        id=uuid.uuid4(),
        fb_ad_id="ad-recovery",
        ad_name="Recovery Ad",
        delivery_status="OFF",
        recommendation_level=EnableRecommendationLevel.OK,
        matched_rule_codes=[],
        reason_title="Можно вернуть в работу",
        reason_text="Проверка пройдена вручную и блокирующих сигналов нет.",
        metrics_json={"spend": "0.00"},
        live_batch_started_at=datetime(2026, 3, 29, 15, 0, tzinfo=UTC),
        created_at=datetime(2026, 3, 29, 15, 1, tzinfo=UTC),
        updated_at=datetime(2026, 3, 29, 15, 2, tzinfo=UTC),
    )

    serialized = _serialize_enable_recommendation_event(
        event,
        current_batch_marker=event.live_batch_started_at,
        current_snapshot=None,
    )

    assert serialized.reason_title == "Можно вернуть в работу"
    assert serialized.reason_text == "Проверка пройдена вручную и блокирующих сигналов нет."


# Проверяем, что fallback-метрики snapshot включают дополнительные колонки из текущего пресета.
def test_build_snapshot_metrics_json_includes_additional_ads_manager_columns():
    from apps.api.main import _build_snapshot_metrics_json

    snapshot = SimpleNamespace(
        spend=Decimal("10.50"),
        budget="$20.00 Daily",
        reach=123,
        impressions=456,
        clicks=42,
        cpc=Decimal("0.2500"),
        ctr=Decimal("9.2100"),
        outbound_clicks=14,
        outbound_ctr=Decimal("0.9500"),
        landing_page_views=4,
        cost_per_result=Decimal("10.5000"),
        cost_per_landing_page_view=Decimal("2.6250"),
        cpm=Decimal("12.5000"),
        frequency=Decimal("1.2500"),
        leads=5,
        cost_per_lead=Decimal("2.1000"),
        registrations=2,
        cost_per_registration=Decimal("5.2500"),
        deposits=1,
    )

    metrics = _build_snapshot_metrics_json(snapshot)

    assert metrics["budget"] == "$20.00 Daily"
    assert metrics["reach"] == 123
    assert metrics["impressions"] == 456
    assert metrics["ctr"] == "9.2100"
    assert metrics["cost_per_result"] == "10.5000"


# Проверяем, что current-batch recommendation скрывается, если текущее объявление уже снова включено.
@pytest.mark.asyncio
async def test_load_current_enable_recommendations_filters_out_ads_that_are_no_longer_disabled(
    mock_db,
):
    from apps.api.main import _load_current_enable_recommendations

    last_scan = datetime(2026, 3, 29, 14, 35, tzinfo=UTC)
    shared_batch = datetime(2026, 3, 29, 14, 30, tzinfo=UTC)
    stale_event = SimpleNamespace(
        id=uuid.uuid4(),
        fb_ad_id="ad-enabled",
        ad_name="Enabled Ad",
        delivery_status="OFF",
        recommendation_level=EnableRecommendationLevel.OK,
        matched_rule_codes=[],
        reason_title=None,
        reason_text=None,
        metrics_json={},
        live_batch_started_at=shared_batch,
        created_at=datetime(2026, 3, 29, 14, 31, tzinfo=UTC),
        updated_at=datetime(2026, 3, 29, 14, 31, tzinfo=UTC),
    )
    current_snapshot = SimpleNamespace(
        fb_ad_id="ad-enabled",
        ad_name="Enabled Ad",
        campaign_name="Campaign C",
        adset_name="Adset C",
        delivery_status="ACTIVE",
        alert_state=AlertState.DISABLED,
        spend=Decimal("0"),
        clicks=0,
        cpc=None,
        outbound_clicks=0,
        outbound_ctr=None,
        landing_page_views=0,
        cost_per_landing_page_view=None,
        cpm=None,
        frequency=None,
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        last_observed_at=last_scan,
    )

    mock_db.execute = AsyncMock(
        side_effect=[
            _rows_result([stale_event]),
            _rows_result([current_snapshot]),
        ]
    )

    with (
        patch(
            "apps.api.routers.dashboard.load_live_batch_bounds",
            new=AsyncMock(return_value=(last_scan, last_scan - timedelta(minutes=30))),
        ),
        patch(
            "apps.api.routers.dashboard.collect_enable_recommendation_candidates_for_snapshots",
            new=AsyncMock(return_value=[]),
        ),
    ):
        marker, result = await _load_current_enable_recommendations(mock_db)

    assert marker == shared_batch
    assert result == []


# Проверяем, что stale event скрывается, если live-переоценка уже не подтверждает рекомендацию.
@pytest.mark.asyncio
async def test_load_current_enable_recommendations_filters_out_stale_live_event(mock_db):
    from apps.api.main import _load_current_enable_recommendations

    last_scan = datetime(2026, 3, 29, 14, 45, tzinfo=UTC)
    shared_batch = datetime(2026, 3, 29, 14, 30, tzinfo=UTC)
    event = SimpleNamespace(
        id=uuid.uuid4(),
        fb_ad_id="ad-stale",
        ad_name="Stale Ad",
        delivery_status="OFF",
        recommendation_level=EnableRecommendationLevel.OK,
        matched_rule_codes=[],
        reason_title="Метрики в норме",
        reason_text="Объявление снова проходит по текущим правилам.",
        metrics_json={},
        live_batch_started_at=shared_batch,
        created_at=datetime(2026, 3, 29, 14, 31, tzinfo=UTC),
        updated_at=datetime(2026, 3, 29, 14, 31, tzinfo=UTC),
    )
    snapshot = SimpleNamespace(
        fb_ad_id="ad-stale",
        ad_name="Stale Ad",
        campaign_name="Campaign S",
        adset_name="Adset S",
        delivery_status="OFF",
        alert_state=AlertState.DISABLED,
        spend=Decimal("30.69"),
        clicks=264,
        cpc=Decimal("0.1200"),
        outbound_clicks=0,
        outbound_ctr=None,
        landing_page_views=0,
        cost_per_landing_page_view=None,
        cpm=None,
        frequency=None,
        leads=5,
        cost_per_lead=Decimal("6.1400"),
        registrations=3,
        cost_per_registration=None,
        deposits=0,
        last_observed_at=last_scan,
    )

    mock_db.execute = AsyncMock(
        side_effect=[
            _rows_result([event]),
            _rows_result([snapshot]),
        ]
    )

    with (
        patch(
            "apps.api.routers.dashboard.load_live_batch_bounds",
            new=AsyncMock(return_value=(last_scan, last_scan - timedelta(minutes=30))),
        ),
        patch(
            "apps.api.routers.dashboard.collect_enable_recommendation_candidates_for_snapshots",
            new=AsyncMock(return_value=[]),
        ),
    ):
        _, result = await _load_current_enable_recommendations(mock_db)

    assert result == []


# Проверяем, что API может создать enable-задачу из recommendation event и вернуть сериализованный task.
@pytest.mark.asyncio
async def test_create_enable_task_from_recommendation_returns_task_payload(mock_db):
    from apps.api.main import create_enable_task_from_recommendation

    task_id = uuid.uuid4()
    api_task = SimpleNamespace(
        id=task_id,
        recommendation_event_id=uuid.uuid4(),
        fb_ad_id="ad-10",
        ad_name="Enable me",
        status=EnableTaskStatus.PENDING,
        attempt_count=0,
        last_error=None,
        next_retry_at=None,
        requested_by_username="dashboard",
        created_at=datetime(2026, 3, 29, 15, 0, tzinfo=UTC),
        updated_at=datetime(2026, 3, 29, 15, 1, tzinfo=UTC),
        completed_at=None,
    )
    service_result = SimpleNamespace(
        outcome="created",
        created_new=True,
        detail="✅ Создана задача на включение.",
        task_id=str(task_id),
    )

    mock_db.scalar = AsyncMock(return_value=api_task)

    with patch(
        "apps.api.routers.dashboard.promote_recommendation_to_enable_task",
        new=AsyncMock(return_value=service_result),
    ):
        result = await create_enable_task_from_recommendation("event-10", db=mock_db)

    assert result["ok"] is True
    assert result["created_new"] is True
    assert result["task"]["status"] == "PENDING"
    assert result["task"]["updated_at"] == "2026-03-29T15:01:00+00:00"
    mock_db.commit.assert_awaited_once()


# Проверяем, что API принимает переочередение существующей failed-задачи как успешный результат.
@pytest.mark.asyncio
async def test_create_enable_task_from_recommendation_accepts_requeued_outcome(mock_db):
    from apps.api.main import create_enable_task_from_recommendation

    task_id = uuid.uuid4()
    api_task = SimpleNamespace(
        id=task_id,
        recommendation_event_id=uuid.uuid4(),
        fb_ad_id="ad-11",
        ad_name="Retry me",
        status=EnableTaskStatus.PENDING,
        attempt_count=0,
        last_error=None,
        next_retry_at=None,
        requested_by_username="dashboard",
        created_at=datetime(2026, 3, 29, 15, 0, tzinfo=UTC),
        updated_at=datetime(2026, 3, 29, 15, 2, tzinfo=UTC),
        completed_at=None,
    )
    service_result = SimpleNamespace(
        outcome="requeued",
        created_new=False,
        detail="✅ Существующая задача на включение возвращена в очередь.",
        task_id=str(task_id),
    )

    mock_db.scalar = AsyncMock(return_value=api_task)

    with patch(
        "apps.api.routers.dashboard.promote_recommendation_to_enable_task",
        new=AsyncMock(return_value=service_result),
    ):
        result = await create_enable_task_from_recommendation("event-11", db=mock_db)

    assert result["ok"] is True
    assert result["created_new"] is False
    assert result["task"]["status"] == "PENDING"
    mock_db.commit.assert_awaited_once()


# Проверяем, что список enable-задач по умолчанию отдаёт статусы для мониторинга.
@pytest.mark.asyncio
async def test_list_enable_tasks_returns_monitoring_statuses_by_default(mock_db):
    from apps.api.main import list_enable_tasks

    task = SimpleNamespace(
        id=uuid.uuid4(),
        recommendation_event_id=uuid.uuid4(),
        fb_ad_id="ad-20",
        ad_name="Enable queue",
        status=EnableTaskStatus.RETRYING,
        attempt_count=2,
        last_error="Таймаут",
        next_retry_at=datetime(2026, 3, 29, 15, 5, tzinfo=UTC),
        requested_by_username="dashboard",
        created_at=datetime(2026, 3, 29, 15, 0, tzinfo=UTC),
        updated_at=datetime(2026, 3, 29, 15, 1, tzinfo=UTC),
        completed_at=None,
    )
    mock_db.scalar = AsyncMock(return_value=None)
    mock_db.execute = AsyncMock(return_value=_rows_result([task]))

    result = await list_enable_tasks(status=None, limit=20, offset=0, db=mock_db)

    assert len(result) == 1
    assert result[0].status == "RETRYING"


# Проверяем, что старый FAILED не торчит в мониторинге, если по тому же объявлению уже есть более поздний SUCCEEDED.
@pytest.mark.asyncio
async def test_list_enable_tasks_hides_superseded_failed_task():
    from apps.api.main import list_enable_tasks

    pytest.importorskip("aiosqlite")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        failed_time = datetime(2026, 3, 29, 15, 0, tzinfo=UTC)
        success_time = datetime(2026, 3, 29, 15, 10, tzinfo=UTC)

        async with session_factory() as db:
            db.add_all(
                [
                    EnableTask(
                        fb_ad_id="ad-20",
                        ad_name="Enable queue",
                        idempotency_key="enable-task-old-failed",
                        status=EnableTaskStatus.FAILED,
                        attempt_count=10,
                        max_attempts=10,
                        last_error="Старый сбой",
                        created_at=failed_time,
                        updated_at=failed_time,
                        completed_at=failed_time,
                    ),
                    EnableTask(
                        fb_ad_id="ad-20",
                        ad_name="Enable queue",
                        idempotency_key="enable-task-new-success",
                        status=EnableTaskStatus.SUCCEEDED,
                        attempt_count=1,
                        max_attempts=10,
                        created_at=success_time,
                        updated_at=success_time,
                        completed_at=success_time,
                    ),
                    EnableTask(
                        fb_ad_id="ad-21",
                        ad_name="Still failing",
                        idempotency_key="enable-task-still-failed",
                        status=EnableTaskStatus.FAILED,
                        attempt_count=3,
                        max_attempts=10,
                        last_error="Актуальный сбой",
                        created_at=failed_time + timedelta(minutes=5),
                        updated_at=failed_time + timedelta(minutes=5),
                        completed_at=failed_time + timedelta(minutes=5),
                    ),
                ]
            )
            await db.commit()

            result = await list_enable_tasks(status=None, limit=20, offset=0, db=db)

        assert [(item.fb_ad_id, item.status) for item in result] == [
            ("ad-20", "SUCCEEDED"),
            ("ad-21", "FAILED"),
        ]
        assert all(item.last_error != "Старый сбой" for item in result)
    finally:
        await engine.dispose()


# Проверяем, что список enable-задач по умолчанию не тащит записи из прошлых суток кабинета.
@pytest.mark.asyncio
async def test_list_enable_tasks_filters_out_previous_cabinet_day():
    from apps.api.main import list_enable_tasks

    pytest.importorskip("aiosqlite")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        cabinet_day_start = datetime(2026, 3, 30, 0, 0, tzinfo=UTC)
        previous_day_time = cabinet_day_start - timedelta(minutes=10)
        current_day_time = cabinet_day_start + timedelta(minutes=5)

        async with session_factory() as db:
            db.add(
                ObserverSettings(
                    singleton_key="default",
                    cabinet_day_started_at=cabinet_day_start,
                )
            )
            stale_event = EnableRecommendationEvent(
                fb_ad_id="ad-30",
                ad_name="Yesterday recommendation",
                delivery_status="OFF",
                recommendation_level=EnableRecommendationLevel.OK,
                matched_rule_codes=[],
                reason_title="Старый сигнал",
                reason_text="Вчерашний батч.",
                metrics_json={},
                live_batch_started_at=previous_day_time,
                idempotency_key="enable-reco-yesterday",
            )
            fresh_event = EnableRecommendationEvent(
                fb_ad_id="ad-31",
                ad_name="Today recommendation",
                delivery_status="OFF",
                recommendation_level=EnableRecommendationLevel.OK,
                matched_rule_codes=[],
                reason_title="Свежий сигнал",
                reason_text="Текущий батч.",
                metrics_json={},
                live_batch_started_at=current_day_time,
                idempotency_key="enable-reco-today",
            )
            db.add_all([stale_event, fresh_event])
            await db.flush()
            db.add_all(
                [
                    EnableTask(
                        recommendation_event_id=stale_event.id,
                        fb_ad_id="ad-30",
                        ad_name="Yesterday task",
                        idempotency_key="enable-task-yesterday",
                        status=EnableTaskStatus.SUCCEEDED,
                        attempt_count=1,
                        max_attempts=10,
                        created_at=current_day_time,
                        updated_at=current_day_time,
                        completed_at=current_day_time,
                    ),
                    EnableTask(
                        recommendation_event_id=fresh_event.id,
                        fb_ad_id="ad-31",
                        ad_name="Today task",
                        idempotency_key="enable-task-today",
                        status=EnableTaskStatus.RETRYING,
                        attempt_count=2,
                        max_attempts=10,
                        last_error="Ждём следующий запуск",
                        next_retry_at=current_day_time + timedelta(minutes=1),
                        created_at=current_day_time,
                        updated_at=current_day_time,
                    ),
                ]
            )
            await db.commit()

            result = await list_enable_tasks(status=None, limit=20, offset=0, db=db)

        assert [(item.fb_ad_id, item.status) for item in result] == [
            ("ad-31", "RETRYING"),
        ]
    finally:
        await engine.dispose()


# Проверяем, что успешная enable-задача не отдаёт старую ошибку и retry-поля в API.
def test_serialize_enable_task_clears_stale_error_for_succeeded_task():
    from apps.api.main import _serialize_enable_task

    task = SimpleNamespace(
        id=uuid.uuid4(),
        recommendation_event_id=uuid.uuid4(),
        fb_ad_id="ad-22",
        ad_name="Recovered ad",
        status=EnableTaskStatus.SUCCEEDED,
        attempt_count=2,
        last_error="Старый сбой",
        next_retry_at=datetime(2026, 3, 29, 15, 20, tzinfo=UTC),
        requested_by_username="dashboard",
        created_at=datetime(2026, 3, 29, 15, 0, tzinfo=UTC),
        updated_at=datetime(2026, 3, 29, 15, 10, tzinfo=UTC),
        completed_at=datetime(2026, 3, 29, 15, 10, tzinfo=UTC),
    )

    serialized = _serialize_enable_task(task)

    assert serialized.status == "SUCCEEDED"
    assert serialized.last_error is None
    assert serialized.next_retry_at is None


# Проверяем, что dashboard stats включает счётчики рекомендаций и очередь enable-задач.
@pytest.mark.asyncio
async def test_dashboard_stats_includes_enable_recommendation_counters(mock_db):
    from apps.api.main import get_dashboard_stats

    group_result = MagicMock()
    group_result.all.return_value = [
        (AlertState.NORMAL, 5, Decimal("10.00")),
        (AlertState.WARNING_SENT, 1, Decimal("5.00")),
    ]
    mock_db.execute = AsyncMock(return_value=group_result)
    mock_db.scalar = AsyncMock(side_effect=[None, None, 3, 2, 4, 1])

    recommendation_events = [
        SimpleNamespace(
            candidate=SimpleNamespace(recommendation_level=EnableRecommendationLevel.OK)
        ),
        SimpleNamespace(
            candidate=SimpleNamespace(recommendation_level=EnableRecommendationLevel.EARLY_SIGNAL)
        ),
        SimpleNamespace(
            candidate=SimpleNamespace(recommendation_level=EnableRecommendationLevel.WARNING)
        ),
        SimpleNamespace(
            candidate=SimpleNamespace(recommendation_level=EnableRecommendationLevel.WARNING)
        ),
    ]

    with patch(
        "apps.api.routers.dashboard._load_current_enable_recommendations",
        new=AsyncMock(return_value=(None, recommendation_events)),
    ):
        result = await get_dashboard_stats(db=mock_db)

    assert result.pending_enable_tasks == 4
    assert result.enable_recommendations_ok == 1
    assert result.enable_recommendations_early_signal == 1
    assert result.enable_recommendations_warning == 2


# Проверяем, что dashboard stats считает enable-очередь только в рамках текущих суток кабинета.
@pytest.mark.asyncio
async def test_dashboard_stats_filters_pending_enable_tasks_by_cabinet_day():
    from apps.api.main import get_dashboard_stats

    pytest.importorskip("aiosqlite")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        cabinet_day_start = datetime(2026, 3, 30, 0, 0, tzinfo=UTC)
        previous_day_time = cabinet_day_start - timedelta(minutes=10)
        current_day_time = cabinet_day_start + timedelta(minutes=5)

        async with session_factory() as db:
            db.add(
                ObserverSettings(
                    singleton_key="default",
                    cabinet_day_started_at=cabinet_day_start,
                )
            )
            stale_event = EnableRecommendationEvent(
                fb_ad_id="ad-old",
                ad_name="Yesterday recommendation",
                delivery_status="OFF",
                recommendation_level=EnableRecommendationLevel.OK,
                matched_rule_codes=[],
                reason_title="Старый сигнал",
                reason_text="Вчерашний батч.",
                metrics_json={},
                live_batch_started_at=previous_day_time,
                idempotency_key="enable-reco-old",
            )
            fresh_event = EnableRecommendationEvent(
                fb_ad_id="ad-new",
                ad_name="Today recommendation",
                delivery_status="OFF",
                recommendation_level=EnableRecommendationLevel.OK,
                matched_rule_codes=[],
                reason_title="Свежий сигнал",
                reason_text="Текущий батч.",
                metrics_json={},
                live_batch_started_at=current_day_time,
                idempotency_key="enable-reco-new",
            )
            db.add_all([stale_event, fresh_event])
            await db.flush()
            db.add_all(
                [
                    EnableTask(
                        recommendation_event_id=stale_event.id,
                        fb_ad_id="ad-old",
                        ad_name="Yesterday pending",
                        idempotency_key="enable-task-old-pending",
                        status=EnableTaskStatus.PENDING,
                        created_at=current_day_time,
                        updated_at=current_day_time,
                    ),
                    EnableTask(
                        recommendation_event_id=fresh_event.id,
                        fb_ad_id="ad-new",
                        ad_name="Today running",
                        idempotency_key="enable-task-new-running",
                        status=EnableTaskStatus.RUNNING,
                        created_at=current_day_time,
                        updated_at=current_day_time,
                    ),
                ]
            )
            await db.commit()

            result = await get_dashboard_stats(db=db)

        assert result.pending_enable_tasks == 1
    finally:
        await engine.dispose()


# Проверяем, что timeline объявления включает recommendation events и enable tasks.
@pytest.mark.asyncio
async def test_get_ad_timeline_includes_enable_recommendations_and_enable_tasks(mock_db):
    from apps.api.main import get_ad_timeline

    snapshot = SimpleNamespace(
        fb_ad_id="ad-42",
        ad_name="Timeline Ad",
        campaign_name="Campaign",
        adset_name="Adset",
        open_state_token=None,
        telegram_group_key=None,
        alert_state=AlertState.WARNING_SENT,
        current_stage=None,
        early_signal_rule_codes=[],
        warning_rule_codes=[],
        stop_rule_codes=[],
        delivery_status="NOT_DELIVERING",
        spend=Decimal("15.00"),
        clicks=10,
        cpc=Decimal("1.5000"),
        outbound_clicks=5,
        outbound_ctr=Decimal("1.20"),
        landing_page_views=3,
        cost_per_landing_page_view=Decimal("5.0000"),
        cpm=Decimal("7.1000"),
        frequency=Decimal("1.4000"),
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        created_at=datetime(2026, 3, 29, 15, 10, tzinfo=UTC),
        updated_at=datetime(2026, 3, 29, 15, 10, tzinfo=UTC),
        last_observed_at=datetime(2026, 3, 29, 15, 10, tzinfo=UTC),
    )
    disable_task = SimpleNamespace(
        id=uuid.uuid4(),
        status=DisableTaskStatus.PENDING,
        attempt_count=0,
        open_state_token="ad-42",
        requested_by_username="bot",
        created_at=datetime(2026, 3, 29, 15, 0, tzinfo=UTC),
        updated_at=datetime(2026, 3, 29, 15, 0, tzinfo=UTC),
        completed_at=None,
        next_retry_at=None,
        last_error=None,
    )
    recommendation_event = SimpleNamespace(
        recommendation_level=EnableRecommendationLevel.EARLY_SIGNAL,
        delivery_status="NOT_DELIVERING",
        matched_rule_codes=["early_outbound_ctr_signal"],
        reason_title="Early",
        reason_text="Есть ранний сигнал",
        metrics_json={"outbound_ctr": "0.80"},
        created_at=datetime(2026, 3, 29, 15, 5, tzinfo=UTC),
    )
    ok_recommendation_event = SimpleNamespace(
        recommendation_level=EnableRecommendationLevel.OK,
        delivery_status="OFF",
        matched_rule_codes=[],
        reason_title="Метрики в норме",
        reason_text="Объявление снова проходит по текущим правилам.",
        metrics_json={"spend": "1.09"},
        created_at=datetime(2026, 3, 29, 15, 4, tzinfo=UTC),
    )
    enable_task = SimpleNamespace(
        status=EnableTaskStatus.PENDING,
        attempt_count=0,
        requested_by_username="dashboard",
        recommendation_event_id=uuid.uuid4(),
        created_at=datetime(2026, 3, 29, 15, 6, tzinfo=UTC),
        completed_at=None,
        last_error=None,
    )

    mock_db.execute = AsyncMock(
        side_effect=[
            _scalar_result(snapshot),
            _rows_result([]),
            _rows_result([disable_task]),
            _rows_result([recommendation_event, ok_recommendation_event]),
            _rows_result([enable_task]),
        ]
    )

    with patch(
        "apps.api.routers.dashboard._build_snapshot_diagnostics_map",
        new=AsyncMock(return_value={}),
    ):
        result = await get_ad_timeline("ad-42", db=mock_db)

    timeline_types = [item["type"] for item in result["timeline"]]
    assert "enable_recommendation" in timeline_types
    assert "enable_task" in timeline_types
    ok_timeline_item = next(
        item
        for item in result["timeline"]
        if item["type"] == "enable_recommendation" and item["recommendation_level"] == "OK"
    )
    assert ok_timeline_item["reason_title"] == "Нет блокирующих сигналов"
    assert ok_timeline_item["reason_text"] == "По текущим правилам блокирующих сигналов нет."
    assert result["current_metrics"]["delivery_status"] == "NOT_DELIVERING"
