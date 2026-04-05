# -*- coding: utf-8 -*-
"""Тесты сервиса рекомендаций на включение."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain import AlertStage, AlertState, EnableRecommendationLevel, EnableTaskStatus
from core.enable_recommendations.service import (
    EnableRecommendationCandidate,
    collect_enable_recommendation_candidates,
    persist_enable_recommendation_candidates,
    promote_recommendation_to_enable_task,
)


def _rows_result(rows):
    """Создаёт мок результата scalars().all()."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


def _snapshot(
    *,
    fb_ad_id: str,
    delivery_status: str,
    offer_id,
    last_observed_at: datetime | None = None,
    offer_code: str = "OFFER-1",
    campaign_name: str = "Campaign",
    adset_name: str = "Adset",
    **overrides,
):
    """Создаёт упрощённый snapshot для recommendation service."""
    ad_id = uuid.uuid4()
    # Нормализованная цепочка fb_ad → adset → campaign
    campaign = SimpleNamespace(
        offer_id=offer_id,
        offer_code=offer_code,
        campaign_name=campaign_name,
    )
    adset = SimpleNamespace(adset_name=adset_name, campaign=campaign)
    fb_ad = SimpleNamespace(ad_name=f"Ad {fb_ad_id}", adset=adset)
    snapshot = SimpleNamespace(
        id=uuid.uuid4(),
        ad_id=ad_id,
        offer_id=offer_id,
        fb_ad_id=fb_ad_id,
        fb_ad=fb_ad,
        delivery_status=delivery_status,
        spend=Decimal("12.00"),
        clicks=10,
        cpc=Decimal("0.1200"),
        outbound_clicks=8,
        outbound_ctr=Decimal("1.10"),
        landing_page_views=5,
        cost_per_landing_page_view=Decimal("2.4000"),
        cpm=Decimal("6.2000"),
        frequency=Decimal("1.3000"),
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        alert_state=AlertState.DISABLED,
        last_observed_at=last_observed_at or datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
    )
    for key, value in overrides.items():
        setattr(snapshot, key, value)
    return snapshot


def _evaluation(stage: AlertStage | None, *, code: str = "cpc_stop"):
    """Создаёт упрощённый результат evaluator."""
    return SimpleNamespace(
        stage=stage,
        matched_rule_codes=[] if stage is None else [code],
        reason_title=None if stage is None else "Причина",
        reason_text=None if stage is None else "Подробность",
        matched_hits=[SimpleNamespace(summary="Порог сработал")],
    )


# Проверяем, что строгая OK-рекомендация создаётся только из явной безопасной оценки.
@pytest.mark.asyncio
async def test_collect_enable_recommendation_candidates_builds_ok_candidate():
    offer_id = uuid.uuid4()
    snapshot = _snapshot(
        fb_ad_id="ad-ok",
        delivery_status="OFF",
        offer_id=offer_id,
        registrations=2,
        cost_per_registration=Decimal("0.5000"),
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_rows_result([snapshot]))
    last_scan = datetime(2026, 3, 29, 12, 34, tzinfo=UTC)

    with (
        patch(
            "core.enable_recommendations.service.load_live_batch_bounds",
            new=AsyncMock(return_value=(last_scan, last_scan - timedelta(minutes=30))),
        ),
        patch(
            "core.enable_recommendations.service._load_observer_rule_settings",
            new=AsyncMock(return_value=(Decimal("80"), Decimal("100"))),
        ),
        patch(
            "core.enable_recommendations.service._load_offer_rule_map",
            new=AsyncMock(
                return_value={offer_id: (SimpleNamespace(cpa_amount=Decimal("20")), object())}
            ),
        ),
        patch(
            "core.enable_recommendations.service._evaluate_enable_recommendation",
            return_value=(EnableRecommendationLevel.OK, _evaluation(None)),
        ),
        patch(
            "core.enable_recommendations.service.build_metrics_json",
            return_value={"spend": "12.00"},
        ),
    ):
        _, candidates = await collect_enable_recommendation_candidates(session)

    assert len(candidates) == 1
    assert candidates[0].fb_ad_id == "ad-ok"
    assert candidates[0].recommendation_level == EnableRecommendationLevel.OK
    assert candidates[0].reason_title == "Строгая проверка пройдена"
    assert (
        candidates[0].reason_text
        == "Есть завершённые регистрации: 2 · CPR $0.5000. По текущим правилам блокирующих сигналов нет."
    )


# Проверяем, что OK-рекомендация по лидам получает человекочитаемую причину без требования регистрации.
@pytest.mark.asyncio
async def test_collect_enable_recommendation_candidates_builds_lead_ok_candidate():
    offer_id = uuid.uuid4()
    snapshot = _snapshot(
        fb_ad_id="ad-lead-ok",
        delivery_status="OFF",
        offer_id=offer_id,
        spend=Decimal("0.70"),
        clicks=9,
        cpc=Decimal("0.0800"),
        leads=2,
        cost_per_lead=Decimal("0.3500"),
        registrations=0,
        cost_per_registration=None,
        deposits=0,
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_rows_result([snapshot]))
    last_scan = datetime(2026, 3, 29, 12, 36, tzinfo=UTC)

    with (
        patch(
            "core.enable_recommendations.service.load_live_batch_bounds",
            new=AsyncMock(return_value=(last_scan, last_scan - timedelta(minutes=30))),
        ),
        patch(
            "core.enable_recommendations.service._load_observer_rule_settings",
            new=AsyncMock(return_value=(Decimal("80"), Decimal("100"))),
        ),
        patch(
            "core.enable_recommendations.service._load_offer_rule_map",
            new=AsyncMock(
                return_value={offer_id: (SimpleNamespace(cpa_amount=Decimal("20")), object())}
            ),
        ),
        patch(
            "core.enable_recommendations.service._evaluate_enable_recommendation",
            return_value=(EnableRecommendationLevel.OK, _evaluation(None)),
        ),
        patch(
            "core.enable_recommendations.service.build_metrics_json",
            return_value={"spend": "0.70"},
        ),
    ):
        _, candidates = await collect_enable_recommendation_candidates(session)

    assert len(candidates) == 1
    assert candidates[0].fb_ad_id == "ad-lead-ok"
    assert candidates[0].recommendation_level == EnableRecommendationLevel.OK
    assert candidates[0].reason_title == "Строгая проверка пройдена"
    assert (
        candidates[0].reason_text
        == "Есть лиды: 2 · CPL $0.3500. По текущим правилам блокирующих сигналов нет."
    )


# Проверяем, что OK-рекомендация только по кликам тоже получает понятную причину.
@pytest.mark.asyncio
async def test_collect_enable_recommendation_candidates_builds_click_ok_candidate():
    offer_id = uuid.uuid4()
    snapshot = _snapshot(
        fb_ad_id="ad-click-ok",
        delivery_status="OFF",
        offer_id=offer_id,
        spend=Decimal("0.08"),
        clicks=2,
        cpc=Decimal("0.0400"),
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_rows_result([snapshot]))
    last_scan = datetime(2026, 3, 29, 12, 38, tzinfo=UTC)

    with (
        patch(
            "core.enable_recommendations.service.load_live_batch_bounds",
            new=AsyncMock(return_value=(last_scan, last_scan - timedelta(minutes=30))),
        ),
        patch(
            "core.enable_recommendations.service._load_observer_rule_settings",
            new=AsyncMock(return_value=(Decimal("80"), Decimal("100"))),
        ),
        patch(
            "core.enable_recommendations.service._load_offer_rule_map",
            new=AsyncMock(
                return_value={offer_id: (SimpleNamespace(cpa_amount=Decimal("20")), object())}
            ),
        ),
        patch(
            "core.enable_recommendations.service._evaluate_enable_recommendation",
            return_value=(EnableRecommendationLevel.OK, _evaluation(None)),
        ),
        patch(
            "core.enable_recommendations.service.build_metrics_json",
            return_value={"spend": "0.08"},
        ),
    ):
        _, candidates = await collect_enable_recommendation_candidates(session)

    assert len(candidates) == 1
    assert candidates[0].fb_ad_id == "ad-click-ok"
    assert candidates[0].recommendation_level == EnableRecommendationLevel.OK
    assert candidates[0].reason_title == "Строгая проверка пройдена"
    assert (
        candidates[0].reason_text
        == "Есть клики: 2 · CPC $0.0400. По текущим правилам блокирующих сигналов нет."
    )


# Проверяем, что NOT_DELIVERING с EARLY_SIGNAL остаётся допустимой рекомендацией.
@pytest.mark.asyncio
async def test_collect_enable_recommendation_candidates_keeps_early_signal():
    offer_id = uuid.uuid4()
    snapshot = _snapshot(
        fb_ad_id="ad-early",
        delivery_status="NOT_DELIVERING",
        offer_id=offer_id,
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_rows_result([snapshot]))
    last_scan = datetime(2026, 3, 29, 12, 40, tzinfo=UTC)

    with (
        patch(
            "core.enable_recommendations.service.load_live_batch_bounds",
            new=AsyncMock(return_value=(last_scan, last_scan - timedelta(minutes=30))),
        ),
        patch(
            "core.enable_recommendations.service._load_observer_rule_settings",
            new=AsyncMock(return_value=(Decimal("80"), Decimal("100"))),
        ),
        patch(
            "core.enable_recommendations.service._load_offer_rule_map",
            new=AsyncMock(
                return_value={offer_id: (SimpleNamespace(cpa_amount=Decimal("20")), object())}
            ),
        ),
        patch(
            "core.enable_recommendations.service._evaluate_enable_recommendation",
            return_value=(
                EnableRecommendationLevel.EARLY_SIGNAL,
                _evaluation(AlertStage.EARLY_SIGNAL, code="early_outbound_ctr_signal"),
            ),
        ),
        patch(
            "core.enable_recommendations.service.build_metrics_json",
            return_value={"outbound_ctr": "0.80"},
        ),
    ):
        _, candidates = await collect_enable_recommendation_candidates(session)

    assert len(candidates) == 1
    assert candidates[0].delivery_status == "NOT_DELIVERING"
    assert candidates[0].recommendation_level == EnableRecommendationLevel.EARLY_SIGNAL


# Проверяем, что WARNING больше не попадает в resume-рекомендации.
@pytest.mark.asyncio
async def test_collect_enable_recommendation_candidates_skips_warning_candidate():
    offer_id = uuid.uuid4()
    snapshot = _snapshot(fb_ad_id="ad-warning", delivery_status="OFF", offer_id=offer_id)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_rows_result([snapshot]))
    last_scan = datetime(2026, 3, 29, 12, 50, tzinfo=UTC)

    with (
        patch(
            "core.enable_recommendations.service.load_live_batch_bounds",
            new=AsyncMock(return_value=(last_scan, last_scan - timedelta(minutes=30))),
        ),
        patch(
            "core.enable_recommendations.service._load_observer_rule_settings",
            new=AsyncMock(return_value=(Decimal("80"), Decimal("100"))),
        ),
        patch(
            "core.enable_recommendations.service._load_offer_rule_map",
            new=AsyncMock(
                return_value={offer_id: (SimpleNamespace(cpa_amount=Decimal("20")), object())}
            ),
        ),
        patch(
            "core.enable_recommendations.service._evaluate_enable_recommendation",
            return_value=(
                EnableRecommendationLevel.WARNING,
                _evaluation(AlertStage.WARNING, code="cpr_stop"),
            ),
        ),
        patch(
            "core.enable_recommendations.service.build_metrics_json",
            return_value={"registrations": 2},
        ),
    ):
        _, candidates = await collect_enable_recommendation_candidates(session)

    assert candidates == []


# Проверяем, что пустой OFF-snapshot не превращается в рекомендацию даже при ошибочной OK-оценке.
@pytest.mark.asyncio
async def test_collect_enable_recommendation_candidates_skips_manual_zero_spend_candidate():
    offer_id = uuid.uuid4()
    snapshot = _snapshot(
        fb_ad_id="ad-manual-zero",
        delivery_status="OFF",
        offer_id=offer_id,
        alert_state=AlertState.NORMAL,
        spend=Decimal("0"),
        clicks=0,
        cpc=None,
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_rows_result([snapshot]))
    last_scan = datetime(2026, 3, 29, 12, 55, tzinfo=UTC)

    with (
        patch(
            "core.enable_recommendations.service.load_live_batch_bounds",
            new=AsyncMock(return_value=(last_scan, last_scan - timedelta(minutes=30))),
        ),
        patch(
            "core.enable_recommendations.service._load_observer_rule_settings",
            new=AsyncMock(return_value=(Decimal("80"), Decimal("100"))),
        ),
        patch(
            "core.enable_recommendations.service._load_offer_rule_map",
            new=AsyncMock(
                return_value={offer_id: (SimpleNamespace(cpa_amount=Decimal("20")), object())}
            ),
        ),
        patch(
            "core.enable_recommendations.service._evaluate_enable_recommendation",
            return_value=(EnableRecommendationLevel.OK, _evaluation(None)),
        ),
        patch(
            "core.enable_recommendations.service.build_metrics_json",
            return_value={"spend": "0.00", "clicks": 0},
        ),
    ):
        _, candidates = await collect_enable_recommendation_candidates(session)

    assert candidates == []


# Проверяем, что сервис не создаёт recommendation, если строгая оценка вернула блокировку.
@pytest.mark.asyncio
async def test_collect_enable_recommendation_candidates_skips_non_actionable_rows():
    live_offer_id = uuid.uuid4()
    snapshots = [
        _snapshot(fb_ad_id="ad-stop", delivery_status="OFF", offer_id=live_offer_id),
        _snapshot(fb_ad_id="ad-no-offer", delivery_status="OFF", offer_id=None),
        _snapshot(fb_ad_id="ad-no-rules", delivery_status="NOT_DELIVERING", offer_id=uuid.uuid4()),
    ]

    async def execute_side_effect(stmt):
        stmt_text = str(stmt)
        assert "last_observed_at" in stmt_text
        assert "delivery_status" in stmt_text
        return _rows_result(snapshots)

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    last_scan = datetime(2026, 3, 29, 13, 0, tzinfo=UTC)

    with (
        patch(
            "core.enable_recommendations.service.load_live_batch_bounds",
            new=AsyncMock(return_value=(last_scan, last_scan - timedelta(minutes=30))),
        ),
        patch(
            "core.enable_recommendations.service._load_observer_rule_settings",
            new=AsyncMock(return_value=(Decimal("80"), Decimal("100"))),
        ),
        patch(
            "core.enable_recommendations.service._load_offer_rule_map",
            new=AsyncMock(
                return_value={
                    live_offer_id: (SimpleNamespace(cpa_amount=Decimal("20")), object()),
                    snapshots[2].offer_id: (SimpleNamespace(cpa_amount=Decimal("20")), None),
                }
            ),
        ),
        patch(
            "core.enable_recommendations.service._evaluate_enable_recommendation",
            return_value=(None, _evaluation(AlertStage.STOP)),
        ),
        patch(
            "core.enable_recommendations.service.build_metrics_json",
            return_value={},
        ),
    ):
        _, candidates = await collect_enable_recommendation_candidates(session)

    assert candidates == []


# Проверяем, что stage=None без безопасного recovery-сигнала больше не становится OK-рекомендацией.
@pytest.mark.asyncio
async def test_collect_enable_recommendation_candidates_skips_stage_none_without_safe_assessment():
    offer_id = uuid.uuid4()
    snapshot = _snapshot(fb_ad_id="ad-no-recovery", delivery_status="OFF", offer_id=offer_id)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_rows_result([snapshot]))
    last_scan = datetime(2026, 3, 29, 13, 5, tzinfo=UTC)

    with (
        patch(
            "core.enable_recommendations.service.load_live_batch_bounds",
            new=AsyncMock(return_value=(last_scan, last_scan - timedelta(minutes=30))),
        ),
        patch(
            "core.enable_recommendations.service._load_observer_rule_settings",
            new=AsyncMock(return_value=(Decimal("80"), Decimal("100"))),
        ),
        patch(
            "core.enable_recommendations.service._load_offer_rule_map",
            new=AsyncMock(
                return_value={offer_id: (SimpleNamespace(cpa_amount=Decimal("20")), object())}
            ),
        ),
        patch(
            "core.enable_recommendations.service._evaluate_enable_recommendation",
            return_value=(None, _evaluation(None)),
        ),
    ):
        _, candidates = await collect_enable_recommendation_candidates(session)

    assert candidates == []


# Проверяем, что persist не создаёт дубль для одного объявления в рамках batch.
@pytest.mark.asyncio
async def test_persist_enable_recommendation_candidates_deduplicates_by_level():
    batch_started_at = datetime(2026, 3, 29, 13, 0, tzinfo=UTC)
    candidate = EnableRecommendationCandidate(
        ad_id=uuid.uuid4(),
        snapshot_id=uuid.uuid4(),
        offer_id=uuid.uuid4(),
        fb_ad_id="ad-dedupe",
        ad_name="Deduped Ad",
        delivery_status="OFF",
        recommendation_level=EnableRecommendationLevel.WARNING,
        matched_rule_codes=["cpr_stop"],
        reason_title="Близко к порогу",
        reason_text="Повтор не нужен.",
        metrics_json={"registrations": 1},
        live_batch_started_at=batch_started_at,
    )

    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.scalar = AsyncMock(side_effect=[None, SimpleNamespace(id="existing-event")])

    created = await persist_enable_recommendation_candidates(
        session,
        [candidate, candidate],
    )

    assert len(created) == 1
    session.add.assert_called_once()
    session.flush.assert_awaited_once()


# Проверяем, что одна запись в batch меняет уровень рекомендации, а не плодит вторую.
@pytest.mark.asyncio
async def test_persist_enable_recommendation_candidates_updates_level_for_same_batch():
    batch_started_at = datetime(2026, 3, 29, 13, 0, tzinfo=UTC)
    candidate = EnableRecommendationCandidate(
        ad_id=uuid.uuid4(),
        snapshot_id=uuid.uuid4(),
        offer_id=uuid.uuid4(),
        fb_ad_id="ad-level-shift",
        ad_name="Shifted Ad",
        delivery_status="OFF",
        recommendation_level=EnableRecommendationLevel.WARNING,
        matched_rule_codes=["cpr_stop"],
        reason_title="Близко к порогу",
        reason_text="Текущая оценка изменилась.",
        metrics_json={"registrations": 2},
        live_batch_started_at=batch_started_at,
    )
    existing = SimpleNamespace(
        snapshot_id=None,
        offer_id=None,
        ad_name="Old Ad",
        delivery_status="OFF",
        recommendation_level=EnableRecommendationLevel.OK,
        matched_rule_codes=[],
        reason_title="Старая причина",
        reason_text="Старый текст",
        metrics_json={"spend": "0.00"},
    )

    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.scalar = AsyncMock(return_value=existing)

    created = await persist_enable_recommendation_candidates(session, [candidate])

    assert created == []
    assert existing.recommendation_level == EnableRecommendationLevel.WARNING
    assert existing.reason_title == "Близко к порогу"


# Проверяем, что duplicate recommendation обновляет payload существующего события.
@pytest.mark.asyncio
async def test_persist_enable_recommendation_candidates_refreshes_existing_payload():
    batch_started_at = datetime(2026, 3, 29, 13, 0, tzinfo=UTC)
    candidate = EnableRecommendationCandidate(
        ad_id=uuid.uuid4(),
        snapshot_id=uuid.uuid4(),
        offer_id=uuid.uuid4(),
        fb_ad_id="ad-refresh",
        ad_name="Refreshed Ad",
        delivery_status="OFF",
        recommendation_level=EnableRecommendationLevel.OK,
        matched_rule_codes=[],
        reason_title="Метрики в норме",
        reason_text="Обновлённые метрики.",
        metrics_json={"spend": "1.09", "clicks": 6},
        live_batch_started_at=batch_started_at,
    )
    existing = SimpleNamespace(
        snapshot_id=None,
        offer_id=None,
        delivery_status="OFF",
        matched_rule_codes=["old_rule"],
        reason_title="Старая причина",
        reason_text="Старый текст",
        metrics_json={"spend": "0.00", "clicks": 0},
    )

    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.scalar = AsyncMock(return_value=existing)

    created = await persist_enable_recommendation_candidates(session, [candidate])

    assert created == []
    assert existing.reason_text == "Обновлённые метрики."
    assert existing.metrics_json == {"spend": "1.09", "clicks": 6}


# Проверяем, что ручное создание enable-задачи блокируется, если текущее объявление больше не проходит строгую проверку.
@pytest.mark.asyncio
async def test_promote_recommendation_to_enable_task_blocks_non_recommendable_snapshot():
    event_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    offer_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    live_scan = datetime(2026, 3, 29, 13, 30, tzinfo=UTC)
    event = SimpleNamespace(
        id=event_id,
        ad_id=ad_id,
        snapshot_id=snapshot_id,
        live_batch_started_at=live_scan,
    )
    event_fb_ad = SimpleNamespace(
        fb_ad_id="ad-blocked",
        ad_name="Blocked Ad",
        adset=SimpleNamespace(campaign=SimpleNamespace(offer_id=offer_id, offer_code="OFFER-1")),
    )
    snapshot = _snapshot(
        fb_ad_id="ad-blocked",
        delivery_status="OFF",
        offer_id=offer_id,
        last_observed_at=live_scan,
    )
    snapshot.id = snapshot_id

    session = AsyncMock()
    observer_result = MagicMock()
    observer_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=observer_result)
    session.scalar = AsyncMock(side_effect=[event, event_fb_ad, snapshot])

    with (
        patch(
            "core.enable_recommendations.service.load_live_batch_bounds",
            new=AsyncMock(return_value=(live_scan, live_scan - timedelta(minutes=30))),
        ),
        patch(
            "core.enable_recommendations.service._load_offer_rule_map",
            new=AsyncMock(
                return_value={offer_id: (SimpleNamespace(cpa_amount=Decimal("20")), object())}
            ),
        ),
        patch(
            "core.enable_recommendations.service._load_observer_rule_settings",
            new=AsyncMock(return_value=(Decimal("80"), Decimal("100"))),
        ),
        patch(
            "core.enable_recommendations.service._evaluate_enable_recommendation",
            return_value=(None, _evaluation(None)),
        ),
    ):
        result = await promote_recommendation_to_enable_task(session, event_id=event_id)

    assert result.outcome == "blocked_recommendation"
    assert "больше не проходит строгую проверку" in result.detail


# Проверяем, что ручное создание enable-задачи блокируется при активном warning.
@pytest.mark.asyncio
async def test_promote_recommendation_to_enable_task_blocks_warning_snapshot():
    event_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    offer_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    live_scan = datetime(2026, 3, 29, 13, 35, tzinfo=UTC)
    event = SimpleNamespace(
        id=event_id,
        ad_id=ad_id,
        snapshot_id=snapshot_id,
        live_batch_started_at=live_scan,
    )
    event_fb_ad = SimpleNamespace(
        fb_ad_id="ad-warning-blocked",
        ad_name="Warning Ad",
        adset=SimpleNamespace(campaign=SimpleNamespace(offer_id=offer_id, offer_code="OFFER-1")),
    )
    snapshot = _snapshot(
        fb_ad_id="ad-warning-blocked",
        delivery_status="OFF",
        offer_id=offer_id,
        last_observed_at=live_scan,
    )
    snapshot.id = snapshot_id

    session = AsyncMock()
    observer_result = MagicMock()
    observer_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=observer_result)
    session.scalar = AsyncMock(side_effect=[event, event_fb_ad, snapshot])

    with (
        patch(
            "core.enable_recommendations.service.load_live_batch_bounds",
            new=AsyncMock(return_value=(live_scan, live_scan - timedelta(minutes=30))),
        ),
        patch(
            "core.enable_recommendations.service._load_offer_rule_map",
            new=AsyncMock(
                return_value={offer_id: (SimpleNamespace(cpa_amount=Decimal("20")), object())}
            ),
        ),
        patch(
            "core.enable_recommendations.service._load_observer_rule_settings",
            new=AsyncMock(return_value=(Decimal("80"), Decimal("100"))),
        ),
        patch(
            "core.enable_recommendations.service._evaluate_enable_recommendation",
            return_value=(
                EnableRecommendationLevel.WARNING,
                _evaluation(AlertStage.WARNING, code="cpr_stop"),
            ),
        ),
    ):
        result = await promote_recommendation_to_enable_task(session, event_id=event_id)

    assert result.outcome == "blocked_warning"
    assert "активен warning" in result.detail


# Проверяем, что существующая failed-enable-задача возвращается в очередь после повторной проверки.
@pytest.mark.asyncio
async def test_promote_recommendation_to_enable_task_requeues_failed_existing_task():
    event_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    offer_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    live_scan = datetime(2026, 3, 29, 13, 35, tzinfo=UTC)
    event = SimpleNamespace(
        id=event_id,
        ad_id=ad_id,
        snapshot_id=snapshot_id,
        live_batch_started_at=live_scan,
    )
    event_fb_ad = SimpleNamespace(
        fb_ad_id="ad-requeue",
        ad_name="Retry Ad",
        adset=SimpleNamespace(campaign=SimpleNamespace(offer_id=offer_id, offer_code="OFFER-1")),
    )
    snapshot = _snapshot(
        fb_ad_id="ad-requeue",
        delivery_status="OFF",
        offer_id=offer_id,
        last_observed_at=live_scan,
    )
    snapshot.id = snapshot_id
    existing_task = SimpleNamespace(
        id=uuid.uuid4(),
        status=EnableTaskStatus.FAILED,
        attempt_count=10,
        next_retry_at=live_scan + timedelta(minutes=5),
        last_error="Старый сбой",
        completed_at=live_scan,
        requested_by_telegram_user_id=None,
        requested_by_username="bot",
    )

    session = AsyncMock()
    observer_result = MagicMock()
    observer_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=observer_result)
    session.scalar = AsyncMock(side_effect=[event, event_fb_ad, snapshot, existing_task])

    with (
        patch(
            "core.enable_recommendations.service.load_live_batch_bounds",
            new=AsyncMock(return_value=(live_scan, live_scan - timedelta(minutes=30))),
        ),
        patch(
            "core.enable_recommendations.service._load_offer_rule_map",
            new=AsyncMock(
                return_value={offer_id: (SimpleNamespace(cpa_amount=Decimal("20")), object())}
            ),
        ),
        patch(
            "core.enable_recommendations.service._load_observer_rule_settings",
            new=AsyncMock(return_value=(Decimal("80"), Decimal("100"))),
        ),
        patch(
            "core.enable_recommendations.service._evaluate_enable_recommendation",
            return_value=(EnableRecommendationLevel.OK, _evaluation(None)),
        ),
    ):
        result = await promote_recommendation_to_enable_task(
            session,
            event_id=event_id,
            requested_by_username="dashboard",
        )

    assert result.outcome == "requeued"
    assert result.task_status == EnableTaskStatus.PENDING.value
    assert existing_task.status == EnableTaskStatus.PENDING
    assert existing_task.attempt_count == 0
    assert existing_task.next_retry_at is None
    assert existing_task.last_error is None
    assert existing_task.completed_at is None
    assert existing_task.requested_by_username == "dashboard"


# Проверяем, что recommendation event из прошлых суток не может заново создать enable-задачу.
@pytest.mark.asyncio
async def test_promote_recommendation_to_enable_task_rejects_stale_cabinet_day_event():
    event_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    event = SimpleNamespace(
        id=event_id,
        ad_id=ad_id,
        snapshot_id=uuid.uuid4(),
        live_batch_started_at=datetime(2026, 3, 29, 23, 40, tzinfo=UTC),
    )
    event_fb_ad = SimpleNamespace(
        fb_ad_id="ad-stale-day",
        ad_name="Stale Day Ad",
    )
    cabinet_day_start = datetime(2026, 3, 30, 0, 0, tzinfo=UTC)
    observer_row = SimpleNamespace(cabinet_day_started_at=cabinet_day_start)
    observer_result = MagicMock()
    observer_result.scalar_one_or_none.return_value = observer_row

    session = AsyncMock()
    session.scalar = AsyncMock(side_effect=[event, event_fb_ad])
    session.execute = AsyncMock(return_value=observer_result)

    result = await promote_recommendation_to_enable_task(session, event_id=event_id)

    assert result.outcome == "stale_cabinet_day"
    assert "прошлых сутках кабинета" in result.detail
