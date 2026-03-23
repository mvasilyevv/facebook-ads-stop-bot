from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.domain import (
    ActionExecutionStatus,
    ActionType,
    DecisionType,
    DeliveryStatus,
    EntityType,
    ScanRunStatus,
    ScopePresence,
    TelegramEventType,
    TrackingMode,
)
from core.models import (
    ActionExecution,
    Ad,
    AdSet,
    BrowserHost,
    Campaign,
    ControlFlag,
    Cooldown,
    Decision,
    EntityOfferBinding,
    MetricSnapshot,
    Offer,
    OfferRateVersion,
    Profile,
    Rule,
    RuleSet,
    ScanRun,
    TelegramEvent,
)
from core.models.operations import SystemSetting
from core.services import preview_advertising_history_reset, reset_advertising_history


async def _count_rows(session: AsyncSession, model: type) -> int:
    rows = await session.scalar(select(func.count()).select_from(model))
    return int(rows or 0)


async def _seed_advertising_history(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 3, 23, 15, 0, tzinfo=UTC)

    async with async_session_factory() as session:
        browser_host = BrowserHost(
            name="vision-reset-test",
            vendor="vision",
            api_base_url="http://vision-reset-test.local",
            is_enabled=True,
            last_heartbeat_at=now,
        )
        offer = Offer(code="offer-reset-test", name="Тестовый оффер", is_active=True)
        rule_set = RuleSet(
            code="rules-reset-test",
            name="Тестовый набор правил",
            is_active=True,
            config_json={},
        )
        setting = SystemSetting(
            key="reset-test-setting",
            value="enabled",
            description="Настройка для проверки сохранности",
        )
        session.add_all([browser_host, offer, rule_set, setting])
        await session.flush()

        profile = Profile(
            browser_host_id=browser_host.id,
            vendor_profile_id="profile-reset-test",
            display_name="Профиль для очистки",
            is_active=True,
            last_launch_at=now,
            scan_suspended=False,
            scan_suspend_reason=None,
            scan_suspend_at=None,
        )
        offer_rate = OfferRateVersion(
            offer_id=offer.id,
            cpa_usd=Decimal("5.00"),
            effective_from=now,
            effective_to=None,
            note="Базовая ставка",
        )
        rule = Rule(
            rule_set_id=rule_set.id,
            code="stop_high_cpc_reset_test",
            name="Стоп по дорогому клику",
            description="Тестовое правило",
            is_enabled=True,
            config_json={"cpa_multiplier": "0.02"},
        )
        session.add_all([profile, offer_rate, rule])
        await session.flush()

        scan_run = ScanRun(
            browser_host_id=browser_host.id,
            profile_id=profile.id,
            started_at=now,
            finished_at=now,
            status=ScanRunStatus.SUCCEEDED,
            rows_seen=1,
            rows_parsed=1,
            scope_summary={"expected_rows": 1},
            error_message=None,
        )
        campaign = Campaign(
            scope_key="campaign-reset-test",
            fb_campaign_id="120241420000000001",
            name="Кампания для очистки",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=now,
        )
        session.add_all([scan_run, campaign])
        await session.flush()

        adset = AdSet(
            scope_key="adset-reset-test",
            fb_adset_id="120241420000000002",
            campaign_id=campaign.id,
            name="Адсет для очистки",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=now,
        )
        session.add(adset)
        await session.flush()

        ad = Ad(
            fb_ad_id="120241420000000003",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="Объявление для очистки",
            delivery_status=DeliveryStatus.ACTIVE,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=now,
            last_action_source="scanner",
            last_action_at=now,
            last_decision=DecisionType.WOULD_PAUSE,
            last_scan_run_id=scan_run.id,
        )
        session.add(ad)
        await session.flush()

        binding = EntityOfferBinding(
            entity_type=EntityType.AD,
            entity_id=str(ad.id),
            offer_id=offer.id,
            priority=0,
            is_active=True,
            adset_id=adset.id,
            ad_id=ad.id,
        )
        snapshot = MetricSnapshot(
            fb_ad_id=ad.fb_ad_id,
            ad_id=ad.id,
            scan_run_id=scan_run.id,
            offer_id=offer.id,
            offer_rate_version_id=offer_rate.id,
            resolved_cpa_usd=Decimal("5.00"),
            spend=Decimal("0.16"),
            clicks=0,
            cpc=None,
            leads=0,
            cost_per_lead=None,
            registrations=0,
            cost_per_registration=None,
            deposits=0,
            captured_at=now,
        )
        decision = Decision(
            scan_run_id=scan_run.id,
            ad_id=ad.id,
            fb_ad_id=ad.fb_ad_id,
            rule_id=rule.id,
            offer_id=offer.id,
            offer_rate_version_id=offer_rate.id,
            resolved_cpa_usd=Decimal("5.00"),
            decision=DecisionType.WOULD_PAUSE,
            reason="Тестовое решение на паузу",
            action_executed=True,
            action_status=ActionExecutionStatus.SUCCEEDED.value,
            created_at=now,
        )
        session.add_all([binding, snapshot, decision])
        await session.flush()

        action_execution = ActionExecution(
            decision_id=decision.id,
            action_type=ActionType.PAUSE,
            status=ActionExecutionStatus.SUCCEEDED,
            message="Тестовая пауза выполнена",
            started_at=now,
            finished_at=now,
        )
        telegram_event = TelegramEvent(
            decision_id=decision.id,
            event_type=TelegramEventType.AD_PAUSED_BY_BOT,
            payload_json={"fb_ad_id": ad.fb_ad_id},
            sent_at=now,
            status="sent",
        )
        control_flag = ControlFlag(
            entity_type=EntityType.AD,
            entity_id=str(ad.id),
            tracking_mode=TrackingMode.MANUAL_BLOCK,
            reason="Тестовый флаг",
            created_by="pytest",
            expires_at=None,
        )
        cooldown = Cooldown(
            entity_type=EntityType.AD,
            entity_id=str(ad.id),
            until_at=now,
            reason="Тестовый cooldown",
        )
        session.add_all([action_execution, telegram_event, control_flag, cooldown])
        await session.commit()


# Проверяет, что превью показывает объём очистки, но не удаляет рекламную историю из базы.
@pytest.mark.asyncio
async def test_preview_advertising_history_reset_keeps_data(async_session_factory) -> None:
    await _seed_advertising_history(async_session_factory)

    async with async_session_factory() as session:
        report = await preview_advertising_history_reset(session)

    stats = {stat.table_name: stat.rows for stat in report.table_stats}

    assert report.is_dry_run is True
    assert stats["ads"] == 1
    assert stats["campaigns"] == 1
    assert stats["scan_runs"] == 1
    assert report.total_rows == 11

    async with async_session_factory() as session:
        assert await _count_rows(session, Ad) == 1
        assert await _count_rows(session, Campaign) == 1
        assert await _count_rows(session, Offer) == 1
        assert await _count_rows(session, Rule) == 1


# Проверяет, что реальная очистка удаляет только рекламную историю и сохраняет конфигурационные сущности.
@pytest.mark.asyncio
async def test_reset_advertising_history_deletes_only_advertising_layer(
    async_session_factory,
) -> None:
    await _seed_advertising_history(async_session_factory)

    async with async_session_factory() as session:
        report = await reset_advertising_history(session)

    stats = {stat.table_name: stat.rows for stat in report.table_stats}

    assert report.is_dry_run is False
    assert stats["action_executions"] == 1
    assert stats["telegram_events"] == 1
    assert stats["decisions"] == 1
    assert stats["metric_snapshots"] == 1
    assert stats["entity_offer_bindings"] == 1
    assert stats["control_flags"] == 1
    assert stats["cooldowns"] == 1
    assert stats["ads"] == 1
    assert stats["adsets"] == 1
    assert stats["campaigns"] == 1
    assert stats["scan_runs"] == 1
    assert report.total_rows == 11

    async with async_session_factory() as session:
        assert await _count_rows(session, ActionExecution) == 0
        assert await _count_rows(session, TelegramEvent) == 0
        assert await _count_rows(session, Decision) == 0
        assert await _count_rows(session, MetricSnapshot) == 0
        assert await _count_rows(session, EntityOfferBinding) == 0
        assert await _count_rows(session, ControlFlag) == 0
        assert await _count_rows(session, Cooldown) == 0
        assert await _count_rows(session, Ad) == 0
        assert await _count_rows(session, AdSet) == 0
        assert await _count_rows(session, Campaign) == 0
        assert await _count_rows(session, ScanRun) == 0

        assert await _count_rows(session, BrowserHost) == 1
        assert await _count_rows(session, Profile) == 1
        assert await _count_rows(session, Offer) == 1
        assert await _count_rows(session, OfferRateVersion) == 1
        assert await _count_rows(session, RuleSet) == 1
        assert await _count_rows(session, Rule) == 1
        assert await _count_rows(session, SystemSetting) == 1
