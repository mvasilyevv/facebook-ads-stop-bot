# -*- coding: utf-8 -*-
"""Тесты выравнивания RuleContext recommendation worker с observer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain import (
    AlertState,
    EnableRecommendationLevel,
)
from core.enable_recommendations.service import (
    _evaluate_enable_recommendation,
    collect_enable_recommendation_candidates,
)


def _rule_config_default():
    """Возвращает минимальный rule_config с дефолтными порогами."""
    return SimpleNamespace(
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
        spend_no_dep_to_percent=Decimal("70"),
        spend_with_dep_enabled=True,
        spend_with_dep_from_percent=Decimal("70"),
        spend_with_dep_to_percent=Decimal("90"),
        warning_percent_of_stop=Decimal("80"),
        stop_percent_of_base=Decimal("80"),
        cpc_warning_percent_of_stop=Decimal("80"),
        cpc_stop_percent_of_base=Decimal("80"),
        cpl_warning_percent_of_stop=Decimal("80"),
        cpl_stop_percent_of_base=Decimal("80"),
        cpr_warning_percent_of_stop=Decimal("80"),
        cpr_stop_percent_of_base=Decimal("80"),
        frequency_anomaly_enabled=True,
        frequency_warning_threshold=Decimal("2.5"),
        frequency_growth_warning_pct=Decimal("30.0"),
        frequency_stop_threshold=Decimal("3.5"),
        frequency_elevated_threshold=Decimal("2"),
        frequency_critical_threshold=Decimal("3"),
        use_adaptive_cpa=False,
        adaptive_cpa_window_days=7,
        adaptive_cpa_min_samples=5,
    )


def _scanned_row(**overrides):
    """Минимальный ScannedAdRow для теста ctx."""
    from core.scanner.models import ScannedAdRow

    base = {
        "fb_ad_id": "ad-ctx",
        "campaign_name": "Campaign",
        "adset_name": "Adset",
        "ad_name": "Ad",
        "delivery_status": "OFF",
        "spend": Decimal("3.00"),
        "reach": 100,
        "impressions": 200,
        "clicks": 5,
        "cpc": Decimal("0.6000"),
        "ctr": Decimal("2.50"),
        "frequency": Decimal("1.4"),
        "leads": 1,
        "cost_per_lead": Decimal("3.0000"),
        "registrations": 1,
        "cost_per_registration": Decimal("3.0000"),
        "deposits": 0,
    }
    base.update(overrides)
    return ScannedAdRow(**base)


# RuleContext recommendation worker содержит те же поля, что observer передаёт.
@pytest.mark.asyncio
async def test_ctx_includes_all_observer_aligned_fields(monkeypatch):
    """Проверяем, что _evaluate_enable_recommendation строит ctx с нужными полями."""
    captured_ctx = {}

    def _capture_ctx(*args, **kwargs):
        ctx = kwargs.get("ctx") if "ctx" in kwargs else (args[1] if len(args) > 1 else None)
        captured_ctx["ctx"] = ctx
        return SimpleNamespace(
            stage=None,
            warning_hits=(),
            stop_hits=(),
            matched_rule_codes=[],
            reason_title=None,
            reason_text=None,
            matched_hits=[],
        )

    def _capture_recommendation_level(row, ctx, *, stop_evaluation):
        captured_ctx["level_ctx"] = ctx
        return EnableRecommendationLevel.OK

    monkeypatch.setattr(
        "core.enable_recommendations.service.evaluate_stop_rules",
        _capture_ctx,
    )
    monkeypatch.setattr(
        "core.enable_recommendations.service.determine_enable_recommendation_level",
        _capture_recommendation_level,
    )

    row = _scanned_row(impressions=500, reach=300, frequency=Decimal("2.1"))
    _evaluate_enable_recommendation(
        row=row,
        offer_cpa=Decimal("20"),
        rule_config=_rule_config_default(),
        adaptive_cpa=Decimal("18.5000"),
        use_adaptive_cpa=True,
    )

    ctx = captured_ctx["ctx"]
    assert ctx is not None
    # adaptive_cpa учтён: cpa_amount должен равняться adaptive (use_adaptive_cpa=True)
    assert ctx.use_adaptive_cpa is True
    assert ctx.adaptive_cpa == Decimal("18.5000")
    assert ctx.cpa_amount == Decimal("18.5000")
    # impressions/reach из row
    assert ctx.impressions == 500
    assert ctx.reach == 300
    # frequency_current из row
    assert ctx.frequency_current == Decimal("2.1")


# collect загружает offer_rule_map и передаёт в evaluate.
@pytest.mark.asyncio
async def test_collect_uses_loaded_offer_rule_map():
    offer_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    last_observed = datetime(2026, 4, 25, 10, 0, tzinfo=UTC)
    snapshot = SimpleNamespace(
        id=uuid.uuid4(),
        ad_id=ad_id,
        offer_id=offer_id,
        fb_ad_id="ad-collect",
        delivery_status="OFF",
        spend=Decimal("1.20"),
        clicks=3,
        cpc=Decimal("0.1200"),
        outbound_clicks=2,
        outbound_ctr=Decimal("1.10"),
        landing_page_views=1,
        cost_per_landing_page_view=Decimal("2.4000"),
        cpm=Decimal("6.2000"),
        frequency=Decimal("1.3000"),
        leads=2,
        cost_per_lead=Decimal("0.6000"),
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        alert_state=AlertState.DISABLED,
        last_observed_at=last_observed,
        fb_ad=SimpleNamespace(
            ad_name="Test Ad",
            adset=SimpleNamespace(
                adset_name="Adset",
                campaign=SimpleNamespace(
                    offer_id=offer_id,
                    offer_code="OFFER_X",
                    campaign_name="Camp",
                ),
            ),
        ),
    )

    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = [snapshot]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=rows_result)
    last_scan = datetime(2026, 4, 25, 10, 0, tzinfo=UTC)

    captured = {}

    def _capture_evaluate(*, row, offer_cpa, rule_config, **kwargs):
        captured["kwargs"] = kwargs
        captured["row"] = row
        return (
            EnableRecommendationLevel.OK,
            SimpleNamespace(
                matched_rule_codes=[],
                reason_title="OK",
                reason_text="OK",
                matched_hits=[],
            ),
        )

    with (
        patch(
            "core.enable_recommendations.service.load_live_batch_bounds",
            new=AsyncMock(return_value=(last_scan, last_scan - timedelta(minutes=30))),
        ),
        patch(
            "core.enable_recommendations.service._load_offer_rule_map",
            new=AsyncMock(
                return_value={
                    offer_id: (
                        SimpleNamespace(cpa_amount=Decimal("20")),
                        SimpleNamespace(use_adaptive_cpa=False),
                    )
                }
            ),
        ),
        patch(
            "core.enable_recommendations.service._evaluate_enable_recommendation",
            side_effect=_capture_evaluate,
        ),
        patch(
            "core.enable_recommendations.service.build_metrics_json",
            return_value={},
        ),
    ):
        _, candidates = await collect_enable_recommendation_candidates(session)

    assert len(candidates) == 1
    kwargs = captured["kwargs"]
    # use_adaptive_cpa берётся из rule_config
    assert kwargs["use_adaptive_cpa"] is False
    # adaptive_cpa для OFF-снэпшотов недоступен → None (документировано)
    assert kwargs["adaptive_cpa"] is None
