# -*- coding: utf-8 -*-
"""Unit-тесты кейса куратора: сигнал «мало показов + хороший CTR» + grace-механика."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.enable_reco.alert import EnableRecoRenderInput, render_enable_reco_alert
from core.enable_reco.analyzer import (
    MetricSnapshot,
    OfferThresholds,
    RecommendationDecision,
    should_recommend,
)
from core.observer.enable_grace import EnableGrace, _parse_grace, grace_is_active

_NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def _snap(
    *,
    impressions: int | None,
    ctr: str | None,
    spend: str = "0.44",
    minutes_ago: int = 5,
) -> MetricSnapshot:
    return MetricSnapshot(
        cycle_ts=_NOW - timedelta(minutes=minutes_ago),
        spend=Decimal(spend),
        impressions=impressions,
        ctr=Decimal(ctr) if ctr is not None else None,
    )


def _decide(metrics: list[MetricSnapshot]):
    return should_recommend(
        alert_state="disabled",
        snoozed_until=None,
        now=_NOW,
        metrics=metrics,
        offer=OfferThresholds(cpa_threshold=Decimal("3.00")),
    )


# Кейс куратора (сценарий с фото): 108 показов, CTR 3.7% → включить и держать до CPL
def test_curator_low_impressions_good_ctr_holds() -> None:
    decision = _decide([_snap(impressions=108, ctr="3.7")])
    assert decision.recommend is True
    assert decision.hold_until_cpl is True
    assert decision.level == "warning"
    assert decision.snapshot["hold_until_cpl"] is True
    # Спенд-кап = 1×CPA оффера — до него держим стоп-правила
    assert decision.snapshot["grace_spend_cap"] == "3.00"
    assert "показов мало" in decision.reasons[0]


# Ревью M-1: у оффера нет cpa_threshold → денежный кап grace берётся из фолбэка,
# безлимитного «держать час на любые деньги» не существует
def test_curator_fallback_spend_cap_without_cpa() -> None:
    decision = should_recommend(
        alert_state="disabled",
        snoozed_until=None,
        now=_NOW,
        metrics=[_snap(impressions=108, ctr="3.7")],
        offer=OfferThresholds(cpa_threshold=None),
    )
    assert decision.hold_until_cpl is True
    assert decision.snapshot["grace_spend_cap"] == "10.00"


# Мало показов, но CTR плохой — кейс куратора НЕ срабатывает
def test_curator_low_ctr_not_recommended() -> None:
    decision = _decide([_snap(impressions=108, ctr="0.5")])
    assert decision.hold_until_cpl is False


# Показов много — данных достаточно, кейс куратора не применяется (CTR любой)
def test_curator_enough_impressions_skipped() -> None:
    decision = _decide([_snap(impressions=5000, ctr="4.0")])
    assert decision.hold_until_cpl is False


# Recovery-сигналы не смешиваются с curator: hold-рекомендация всегда level=warning
def test_curator_not_mixed_with_recovery_level() -> None:
    # spend мал (recovery-правило 1 тоже сработало бы) — но curator-ветка раньше
    decision = _decide([_snap(impressions=50, ctr="10.0", spend="0.05")])
    assert decision.hold_until_cpl is True
    assert decision.level == "warning"


# grace_is_active: истёкшее время → False, даже если спенд мал
def test_grace_expired_by_time() -> None:
    g = EnableGrace(until=_NOW - timedelta(seconds=1), spend_cap=Decimal("3"))
    assert grace_is_active(g, now=_NOW, spend=Decimal("0.1")) is False


# grace_is_active: спенд-кап достигнут → False, дальше судит цена лида
def test_grace_expired_by_spend_cap() -> None:
    g = EnableGrace(until=_NOW + timedelta(hours=1), spend_cap=Decimal("3"))
    assert grace_is_active(g, now=_NOW, spend=Decimal("3.00")) is False


# grace_is_active: время не вышло и спенд ниже капа → True
def test_grace_active() -> None:
    g = EnableGrace(until=_NOW + timedelta(hours=1), spend_cap=Decimal("3"))
    assert grace_is_active(g, now=_NOW, spend=Decimal("1.50")) is True


# grace_is_active: без спенд-капа действует только время; spend=None не роняет
def test_grace_no_cap_and_no_spend() -> None:
    g = EnableGrace(until=_NOW + timedelta(hours=1), spend_cap=None)
    assert grace_is_active(g, now=_NOW, spend=None) is True


# Битый JSON-маркер в Redis → None (правила действуют как обычно, не падаем)
def test_parse_grace_garbage() -> None:
    assert _parse_grace("не json") is None
    assert _parse_grace('{"нет": "until"}') is None


# Рендер hold-рекомендации: заголовок про цену лида, кнопка ereco на месте
def test_render_hold_recommendation() -> None:
    decision = RecommendationDecision(
        recommend=True,
        level="warning",
        hold_until_cpl=True,
        reasons=("показов мало (108 < 500) при хорошем CTR (3.7% ≥ 3.0%)",),
        snapshot={"hold_until_cpl": True, "grace_spend_cap": "3.00"},
    )
    text, markup = render_enable_reco_alert(
        EnableRecoRenderInput(
            fb_ad_id="2300112233",
            ad_name="CR2_CR002",
            campaign_name="CR2 | DRC | MV",
            adset_name="EQ",
            offer_code="GH_CR2",
            decision=decision,
        )
    )
    assert "держать до цены лида" in text
    assert "1×CPA" in text
    buttons = [b for row in markup["inline_keyboard"] for b in row]
    assert any(b.get("callback_data") == "ereco:2300112233" for b in buttons)
