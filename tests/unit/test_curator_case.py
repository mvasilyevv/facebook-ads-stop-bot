# -*- coding: utf-8 -*-
"""Unit-тесты кейса куратора: сигнал «мало показов + хороший CTR» + grace-механика."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from core.enable_reco.alert import EnableRecoRenderInput, render_enable_reco_alert
from core.enable_reco.analyzer import (
    MetricSnapshot,
    OfferThresholds,
    RecommendationDecision,
    should_recommend,
)
from core.observer.enable_grace import (
    GRACE_SCHEMA_VERSION,
    EnableGrace,
    _parse_grace,
    grace_is_active,
    set_enable_grace,
)

_NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
_CABINET_DAY_START = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)


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
    decision = _decide([_snap(impressions=108, ctr="3.7", spend="2.99")])
    assert decision.recommend is True
    assert decision.hold_until_cpl is True
    assert decision.level == "warning"
    assert decision.snapshot["hold_until_cpl"] is True
    # Спенд-кап = 1×CPA оффера — до него держим стоп-правила
    assert decision.snapshot["grace_spend_cap"] == "3.00"
    assert "показов мало" in decision.reasons[0]


# Curator hold разрешён только пока текущий кумулятивный spend строго ниже CPA.
@pytest.mark.parametrize("spend", ["3.00", "3.01", "50.00"])
def test_curator_spend_at_or_above_cpa_does_not_hold(spend: str) -> None:
    decision = _decide([_snap(impressions=108, ctr="3.7", spend=spend)])

    assert decision.hold_until_cpl is False
    assert decision.snapshot.get("hold_until_cpl") is not True


# Без CPA нет надёжного абсолютного денежного cap → curator hold не предлагаем.
def test_curator_without_cpa_does_not_hold() -> None:
    decision = should_recommend(
        alert_state="disabled",
        snoozed_until=None,
        now=_NOW,
        metrics=[_snap(impressions=108, ctr="3.7")],
        offer=OfferThresholds(cpa_threshold=None),
    )
    assert decision.hold_until_cpl is False
    assert decision.snapshot.get("grace_spend_cap") is None


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


# Worker может запретить curator-ветку для небезопасного кандидата,
# не ломая обычные recovery-правила.
def test_curator_branch_can_be_disabled_for_unsafe_candidate() -> None:
    decision = should_recommend(
        alert_state="disabled",
        snoozed_until=None,
        now=_NOW,
        metrics=[_snap(impressions=108, ctr="3.7", spend="50.00")],
        offer=OfferThresholds(cpa_threshold=Decimal("3.00")),
        allow_curator=False,
    )
    assert decision.recommend is False
    assert decision.hold_until_cpl is False


# grace_is_active: истёкшее время → False, даже если спенд мал
def test_grace_expired_by_time() -> None:
    g = EnableGrace(
        until=_NOW - timedelta(seconds=1),
        spend_cap=Decimal("3"),
        baseline_spend=Decimal("0.1"),
        cabinet_day_start=_CABINET_DAY_START,
    )
    assert (
        grace_is_active(
            g,
            now=_NOW,
            spend=Decimal("0.1"),
            absolute_spend_cap=Decimal("3"),
            current_cabinet_day_start=_CABINET_DAY_START,
        )
        is False
    )


# grace_is_active: спенд-кап достигнут → False, дальше судит цена лида
def test_grace_expired_by_spend_cap() -> None:
    g = EnableGrace(
        until=_NOW + timedelta(hours=1),
        spend_cap=Decimal("3"),
        baseline_spend=Decimal("0.1"),
        cabinet_day_start=_CABINET_DAY_START,
    )
    assert (
        grace_is_active(
            g,
            now=_NOW,
            spend=Decimal("3.00"),
            absolute_spend_cap=Decimal("3"),
            current_cabinet_day_start=_CABINET_DAY_START,
        )
        is False
    )


# Старый marker мог содержать baseline + CPA; актуальный CPA обязан ограничить его.
def test_legacy_incremental_marker_is_clamped_to_absolute_cpa() -> None:
    g = EnableGrace(
        until=_NOW + timedelta(hours=1),
        spend_cap=Decimal("18.00"),
        baseline_spend=Decimal("8.00"),
        cabinet_day_start=_CABINET_DAY_START,
    )

    assert (
        grace_is_active(
            g,
            now=_NOW,
            spend=Decimal("9.99"),
            absolute_spend_cap=Decimal("10.00"),
            current_cabinet_day_start=_CABINET_DAY_START,
        )
        is True
    )
    assert (
        grace_is_active(
            g,
            now=_NOW,
            spend=Decimal("10.00"),
            absolute_spend_cap=Decimal("10.00"),
            current_cabinet_day_start=_CABINET_DAY_START,
        )
        is False
    )


# Marker одобрен только для одного cabinet-day и не может ожить после reset.
def test_grace_day_mismatch_is_inactive() -> None:
    g = EnableGrace(
        until=_NOW + timedelta(days=2),
        spend_cap=Decimal("3"),
        baseline_spend=Decimal("0.1"),
        cabinet_day_start=_CABINET_DAY_START,
    )

    assert (
        grace_is_active(
            g,
            now=_NOW + timedelta(days=1),
            spend=Decimal("0.2"),
            absolute_spend_cap=Decimal("3"),
            current_cabinet_day_start=_CABINET_DAY_START + timedelta(days=1),
        )
        is False
    )


def test_legacy_schema_marker_is_inactive() -> None:
    g = EnableGrace(
        until=_NOW + timedelta(hours=1),
        spend_cap=Decimal("3"),
        baseline_spend=Decimal("0.1"),
        cabinet_day_start=_CABINET_DAY_START,
        schema_version=1,
    )

    assert (
        grace_is_active(
            g,
            now=_NOW,
            spend=Decimal("1.5"),
            absolute_spend_cap=Decimal("3"),
            current_cabinet_day_start=_CABINET_DAY_START,
        )
        is False
    )


@pytest.mark.parametrize(
    "current_cpa",
    [None, Decimal("NaN"), Decimal("Infinity"), Decimal("0"), Decimal("-1")],
)
def test_grace_is_inactive_without_valid_current_cpa(current_cpa: Decimal | None) -> None:
    g = EnableGrace(
        until=_NOW + timedelta(hours=1),
        spend_cap=Decimal("3"),
        baseline_spend=Decimal("0.1"),
        cabinet_day_start=_CABINET_DAY_START,
    )

    assert (
        grace_is_active(
            g,
            now=_NOW,
            spend=Decimal("1.5"),
            absolute_spend_cap=current_cpa,
            current_cabinet_day_start=_CABINET_DAY_START,
        )
        is False
    )


# grace_is_active: время не вышло и спенд ниже капа → True
def test_grace_active() -> None:
    g = EnableGrace(
        until=_NOW + timedelta(hours=1),
        spend_cap=Decimal("3"),
        baseline_spend=Decimal("0.1"),
        cabinet_day_start=_CABINET_DAY_START,
    )
    assert (
        grace_is_active(
            g,
            now=_NOW,
            spend=Decimal("1.50"),
            absolute_spend_cap=Decimal("3"),
            current_cabinet_day_start=_CABINET_DAY_START,
        )
        is True
    )


# Нет денежного cap и spend → fail-safe: grace не подавляет стоп-правила.
def test_grace_no_cap_and_no_spend_is_inactive() -> None:
    g = EnableGrace(
        until=_NOW + timedelta(hours=1),
        spend_cap=None,
        baseline_spend=Decimal("0.1"),
        cabinet_day_start=_CABINET_DAY_START,
    )
    assert (
        grace_is_active(
            g,
            now=_NOW,
            spend=None,
            absolute_spend_cap=Decimal("3"),
            current_cabinet_day_start=_CABINET_DAY_START,
        )
        is False
    )


# Битый JSON-маркер в Redis → None (правила действуют как обычно, не падаем)
def test_parse_grace_garbage() -> None:
    assert _parse_grace("не json") is None
    assert _parse_grace('{"нет": "until"}') is None


def test_parse_grace_rejects_unversioned_marker() -> None:
    assert (
        _parse_grace(
            '{"until":"2026-07-15T13:00:00+00:00",'
            '"spend_cap":"3.00","baseline_spend":"0.10",'
            '"cabinet_day_start":"2026-07-15T00:00:00+00:00"}'
        )
        is None
    )


def test_parse_grace_v2_requires_tz_aware_cabinet_day_start() -> None:
    missing = (
        '{"schema_version":2,"until":"2026-07-15T13:00:00+00:00",'
        '"spend_cap":"3.00","baseline_spend":"0.10"}'
    )
    naive = (
        '{"schema_version":2,"until":"2026-07-15T13:00:00+00:00",'
        '"spend_cap":"3.00","baseline_spend":"0.10",'
        '"cabinet_day_start":"2026-07-15T00:00:00"}'
    )

    assert _parse_grace(missing) is None
    assert _parse_grace(naive) is None


# Денежный cap должен быть конечным и строго положительным; иначе marker fail-safe отклоняем.
def test_parse_grace_rejects_non_finite_and_non_positive_cap() -> None:
    until = "2026-07-15T13:00:00+00:00"
    for cap in ("NaN", "Infinity", "-Infinity", "0", "-1"):
        assert (
            _parse_grace(
                f'{{"schema_version":2,"until":"{until}","spend_cap":"{cap}",'
                '"baseline_spend":"0.10",'
                '"cabinet_day_start":"2026-07-15T00:00:00+00:00"}'
            )
            is None
        )


def test_parse_grace_accepts_positive_finite_cap() -> None:
    grace = _parse_grace(
        '{"schema_version":2,"until":"2026-07-15T13:00:00+00:00",'
        '"spend_cap":"3.00","baseline_spend":"0.10",'
        '"cabinet_day_start":"2026-07-15T00:00:00+00:00"}'
    )
    assert grace is not None
    assert grace.spend_cap == Decimal("3.00")
    assert grace.schema_version == GRACE_SCHEMA_VERSION
    assert grace.cabinet_day_start == _CABINET_DAY_START


@pytest.mark.asyncio
async def test_set_enable_grace_uses_absolute_cap_not_baseline_plus_cpa() -> None:
    class _RedisStub:
        raw: str | None = None

        async def set(self, _key: str, value: str, *, ex: int) -> None:
            assert ex == 3660
            self.raw = value

    redis = _RedisStub()
    ok = await set_enable_grace(
        redis,
        fb_ad_id="2300112233",
        grace_seconds=3600,
        baseline_spend="2.99",
        spend_cap="3.00",
        cabinet_day_start=_CABINET_DAY_START,
    )

    assert ok is True
    assert redis.raw is not None
    grace = _parse_grace(redis.raw)
    assert grace is not None
    assert grace.baseline_spend == Decimal("2.99")
    assert grace.spend_cap == Decimal("3.00")
    assert grace.schema_version == GRACE_SCHEMA_VERSION
    assert grace.cabinet_day_start == _CABINET_DAY_START


@pytest.mark.asyncio
async def test_set_enable_grace_rejects_marker_without_cabinet_day_start() -> None:
    class _RedisStub:
        set = AsyncMock()

    redis = _RedisStub()
    ok = await set_enable_grace(
        redis,
        fb_ad_id="2300112233",
        grace_seconds=3600,
        baseline_spend="2.99",
        spend_cap="3.00",
        cabinet_day_start=None,
    )

    assert ok is False
    redis.set.assert_not_awaited()


# Рендер hold-рекомендации: заголовок про цену лида, кнопка ereco на месте
def test_render_hold_recommendation() -> None:
    decision = RecommendationDecision(
        recommend=True,
        level="warning",
        hold_until_cpl=True,
        reasons=("показов мало (108 < 500) при хорошем CTR (3.7% ≥ 3.0%)",),
        snapshot={
            "hold_until_cpl": True,
            "total_spend": "0.75",
            "grace_spend_cap": "3.00",
            "grace_spend_remaining": "2.25",
        },
    )
    text, markup = render_enable_reco_alert(
        EnableRecoRenderInput(
            recommendation_id="00000000-0000-0000-0000-000000000003",
            fb_ad_id="2300112233",
            ad_name="CR2_CR002",
            campaign_name="CR2 | DRC | MV",
            adset_name="EQ",
            offer_code="GH_CR2",
            decision=decision,
        )
    )
    assert "держать до цены лида" in text
    assert "абсолютный лимит CPA" in text
    assert "$0.75" in text
    assert "$3.00" in text
    assert "$2.25" in text
    assert "Уже накопленный расход входит в лимит" in text
    buttons = [b for row in markup["inline_keyboard"] for b in row]
    assert any(
        b.get("callback_data") == "ereco:00000000-0000-0000-0000-000000000003" for b in buttons
    )
