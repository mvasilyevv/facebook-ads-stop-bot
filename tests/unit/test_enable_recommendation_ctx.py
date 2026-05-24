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
    _baseline_for_offer,
    _confidence_for_offer,
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
        time_weights_enabled=False,
        hour_weights=None,
        day_weights=None,
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
    """Проверяем, что _evaluate_enable_recommendation строит ctx с теми же полями."""
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

    observed_at = datetime(2026, 4, 25, 10, 0, tzinfo=UTC)
    row = _scanned_row(impressions=500, reach=300, frequency=Decimal("2.1"))
    _evaluate_enable_recommendation(
        row=row,
        offer_cpa=Decimal("20"),
        rule_config=_rule_config_default(),
        offer_median_cpl=Decimal("2.5000"),
        offer_median_cpr=Decimal("4.2000"),
        adaptive_cpa=Decimal("18.5000"),
        use_adaptive_cpa=True,
        observed_at=observed_at,
        rule_confidence_map={"cpc_stop": Decimal("0.7"), "cpr_stop": Decimal("0.6")},
    )

    ctx = captured_ctx["ctx"]
    assert ctx is not None
    # offer_median_* передан
    assert ctx.offer_median_cpl == Decimal("2.5000")
    assert ctx.offer_median_cpr == Decimal("4.2000")
    # adaptive_cpa учтён: cpa_amount должен равняться adaptive (use_adaptive_cpa=True)
    assert ctx.use_adaptive_cpa is True
    assert ctx.adaptive_cpa == Decimal("18.5000")
    assert ctx.cpa_amount == Decimal("18.5000")
    # impressions/reach из row
    assert ctx.impressions == 500
    assert ctx.reach == 300
    # frequency_current из row
    assert ctx.frequency_current == Decimal("2.1")
    # rule_confidence_map проброшен
    assert ctx.rule_confidence == {"cpc_stop": Decimal("0.7"), "cpr_stop": Decimal("0.6")}


# Time weight использует observed_at снэпшота, а не datetime.now().
@pytest.mark.asyncio
async def test_time_weight_uses_observed_at_not_now(monkeypatch):
    """С enabled time_weights observed_at в прошлом даёт детерминированный вес."""
    captured_ctx = {}

    def _capture_ctx(row, ctx):
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

    def _stub_level(row, ctx, *, stop_evaluation):
        return EnableRecommendationLevel.OK

    monkeypatch.setattr(
        "core.enable_recommendations.service.evaluate_stop_rules",
        _capture_ctx,
    )
    monkeypatch.setattr(
        "core.enable_recommendations.service.determine_enable_recommendation_level",
        _stub_level,
    )

    # Включаем time_weights: hour=3 ночь, day=0 (Пн) — задаём явные веса
    rule_config = _rule_config_default()
    rule_config.time_weights_enabled = True
    hour_weights = [Decimal("0.5")] * 24
    hour_weights[3] = Decimal("0.7")
    rule_config.hour_weights = hour_weights
    day_weights = [Decimal("1.0")] * 7
    rule_config.day_weights = day_weights

    # observed_at — конкретный момент. Берём UTC время, при котором локальный час MSK
    # совпадёт с известным. Europe/Moscow = UTC+3, hour=3 локальный → UTC=0.
    observed_at = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)  # Пн 03:00 MSK
    row = _scanned_row()
    _evaluate_enable_recommendation(
        row=row,
        offer_cpa=Decimal("20"),
        rule_config=rule_config,
        observed_at=observed_at,
    )

    ctx = captured_ctx["ctx"]
    assert ctx.time_weights_enabled is True
    # hour_of_day должен быть 3 (из observed_at локально), а не текущий час
    assert ctx.hour_of_day == 3
    assert ctx.day_of_week == 0  # 2024-01-01 — понедельник
    # Вес: hour_w * day_w = 0.7 * 1.0 = 0.7
    assert ctx.time_weight == Decimal("0.7000")


# Helper _baseline_for_offer возвращает значения по casefold-ключу.
def test_baseline_for_offer_casefold_lookup():
    baselines = {
        "drc_cr2": (Decimal("2.0000"), Decimal("5.0000")),
    }
    # Прямой регистр
    cpl, cpr = _baseline_for_offer(baselines, "DRC_CR2")
    assert cpl == Decimal("2.0000")
    assert cpr == Decimal("5.0000")
    # Несуществующий
    cpl, cpr = _baseline_for_offer(baselines, "OTHER")
    assert cpl is None and cpr is None
    # None
    cpl, cpr = _baseline_for_offer(baselines, None)
    assert cpl is None and cpr is None


# Helper _confidence_for_offer находит словарь по casefold.
def test_confidence_for_offer_casefold_lookup():
    confidence = {
        "DRC_CR2": {"cpl_stop": Decimal("0.8")},
    }
    found = _confidence_for_offer(confidence, "drc_cr2")
    assert found == {"cpl_stop": Decimal("0.8")}
    assert _confidence_for_offer(confidence, "OTHER") is None
    assert _confidence_for_offer(confidence, None) is None


# collect загружает baselines/confidence один раз и пробрасывает в evaluate.
@pytest.mark.asyncio
async def test_collect_uses_loaded_baselines_and_confidence():
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
            "core.enable_recommendations.service._load_offer_baselines",
            new=AsyncMock(return_value={"offer_x": (Decimal("1.5000"), Decimal("3.0000"))}),
        ),
        patch(
            "core.enable_recommendations.service._load_rule_confidence_map",
            new=AsyncMock(return_value={"OFFER_X": {"cpl_stop": Decimal("0.9")}}),
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
    # Baseline передан с casefold-ключом
    assert kwargs["offer_median_cpl"] == Decimal("1.5000")
    assert kwargs["offer_median_cpr"] == Decimal("3.0000")
    # Confidence передан
    assert kwargs["rule_confidence_map"] == {"cpl_stop": Decimal("0.9")}
    # observed_at — это last_observed_at из snapshot, а не now()
    assert kwargs["observed_at"] == last_observed
    # use_adaptive_cpa берётся из rule_config
    assert kwargs["use_adaptive_cpa"] is False
    # adaptive_cpa для OFF-снэпшотов недоступен → None (документировано)
    assert kwargs["adaptive_cpa"] is None
