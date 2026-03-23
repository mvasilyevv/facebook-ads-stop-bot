from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from core.config.settings import Settings
from core.domain import (
    ActionExecutionStatus,
    ActionType,
    DecisionType,
    DeliveryStatus,
    EntityType,
    ScanRunStatus,
    ScopePresence,
    TrackingMode,
)
from core.repositories import (
    AdsRepository,
    BrowserRepository,
    ControlFlagsRepository,
    DecisionsRepository,
    OffersRepository,
    RulesRepository,
    ScanRunsRepository,
    SystemSettingsRepository,
)
from core.repositories.rules import DEFAULT_RULES
from core.services import SERVICE_SETTING_AUTO_PAUSE_ENABLED, SERVICE_SETTING_SCAN_INTERVAL_SECONDS


# Проверяет полный поток offers -> rates -> bindings -> resolved binding/rate на async SQLite.
@pytest.mark.asyncio
async def test_offers_repository_resolves_binding_and_rate(async_session) -> None:
    repo = OffersRepository(async_session)

    offer = await repo.create_offer(code="offer-1", name="Оффер 1")
    rate = await repo.add_rate_version(
        offer_id=offer.id,
        cpa_usd=Decimal("5.00"),
        effective_from=datetime(2026, 3, 20, 10, 0, tzinfo=UTC),
    )
    campaign = AdsRepository(async_session)
    campaign_entity = await campaign.upsert_campaign(
        scope_key="campaign:account-1:campaign-1",
        name="Кампания 1",
    )
    adset = await campaign.upsert_adset(
        scope_key="adset:campaign:account-1:campaign-1:adset-1",
        campaign_id=campaign_entity.id,
        name="Адсет 1",
    )
    ad = await campaign.upsert_ad(
        fb_ad_id="ad-1",
        campaign_id=campaign_entity.id,
        adset_id=adset.id,
        name="Объявление 1",
        delivery_status=DeliveryStatus.ACTIVE,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        last_seen_at=datetime(2026, 3, 20, 10, 5, tzinfo=UTC),
    )
    await repo.upsert_binding(EntityType.ADSET, adset.scope_key, offer.id, priority=1)
    await repo.upsert_binding(EntityType.AD, ad.fb_ad_id, offer.id, priority=10)

    resolved_binding = await repo.resolve_binding(ad.fb_ad_id, adset.scope_key)
    resolved_rate = await repo.resolve_rate_version(
        offer.id, datetime(2026, 3, 20, 12, 0, tzinfo=UTC)
    )

    await async_session.commit()

    assert rate.offer_id == offer.id
    assert resolved_binding is not None
    assert resolved_binding.entity_type == EntityType.AD
    assert resolved_binding.entity_id == ad.fb_ad_id
    assert resolved_rate is not None
    assert resolved_rate.cpa_usd == Decimal("5.00")


# Проверяет, что репозиторий находит активный оффер по коду из имени объявления без ручной привязки.
@pytest.mark.asyncio
async def test_offers_repository_resolves_offer_from_ad_name(async_session) -> None:
    repo = OffersRepository(async_session)

    offer = await repo.create_offer(code="offer-auto-1", name="DRC_CR2")
    resolved = await repo.resolve_offer_for_ad(
        ad_name="DRC_CR2_CR001",
        ad_id="ad-778899",
        adset_scope_key="adset-scope-778899",
    )

    await async_session.commit()

    assert resolved is not None
    assert resolved.id == offer.id


# Проверяет, что репозиторий объявлений создаёт и обновляет кампанию, адсет и ad с правильными состояниями.
@pytest.mark.asyncio
async def test_ads_repository_upsert_and_list(async_session) -> None:
    repo = AdsRepository(async_session)

    campaign = await repo.upsert_campaign(
        scope_key="campaign:account-1:campaign-1",
        name="Кампания 1",
        tracking_mode=TrackingMode.TRACKED,
        last_seen_at=datetime(2026, 3, 20, 10, 0, tzinfo=UTC),
    )
    adset = await repo.upsert_adset(
        scope_key="adset:campaign:account-1:campaign-1:adset-1",
        campaign_id=campaign.id,
        name="Адсет 1",
        tracking_mode=TrackingMode.TRACKED,
        last_seen_at=datetime(2026, 3, 20, 10, 1, tzinfo=UTC),
    )
    ad = await repo.upsert_ad(
        fb_ad_id="ad-1",
        campaign_id=campaign.id,
        adset_id=adset.id,
        name="Объявление 1",
        delivery_status=DeliveryStatus.NOT_DELIVERING,
        tracking_mode=TrackingMode.MANUAL_BLOCK,
        scope_presence=ScopePresence.IN_SCOPE,
        last_seen_at=datetime(2026, 3, 20, 10, 2, tzinfo=UTC),
        last_action_source="operator",
        last_decision=DecisionType.ALERT_REJECTION,
    )

    ads = await repo.list_ads()
    fetched = await repo.get_ad_by_fb_id("ad-1")

    await async_session.commit()

    assert campaign.scope_key == "campaign:account-1:campaign-1"
    assert adset.scope_key == "adset:campaign:account-1:campaign-1:adset-1"
    assert ad.fb_ad_id == "ad-1"
    assert len(ads) == 1
    assert fetched is not None
    assert fetched.delivery_status == DeliveryStatus.NOT_DELIVERING


# Проверяет, что обновление review state не затирает ownership, если новые значения не переданы.
@pytest.mark.asyncio
async def test_ads_repository_update_review_state_preserves_last_action_when_not_provided(
    async_session,
) -> None:
    repo = AdsRepository(async_session)

    campaign = await repo.upsert_campaign(
        scope_key="campaign:account-1:campaign-2",
        name="Кампания 2",
    )
    adset = await repo.upsert_adset(
        scope_key="adset:campaign:account-1:campaign-2:adset-1",
        campaign_id=campaign.id,
        name="Адсет 2",
    )
    original_action_at = datetime(2026, 3, 20, 10, 3, tzinfo=UTC)
    await repo.upsert_ad(
        fb_ad_id="ad-2",
        campaign_id=campaign.id,
        adset_id=adset.id,
        name="Объявление 2",
        delivery_status=DeliveryStatus.ACTIVE,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        last_seen_at=datetime(2026, 3, 20, 10, 2, tzinfo=UTC),
        last_action_source="автопауза",
        last_action_at=original_action_at,
    )

    updated = await repo.update_ad_review_state(
        fb_ad_id="ad-2",
        tracking_mode=TrackingMode.MANUAL_BLOCK,
        last_decision=DecisionType.SKIPPED_BY_POLICY,
    )

    await async_session.commit()

    assert updated is not None
    assert updated.tracking_mode == TrackingMode.MANUAL_BLOCK
    assert updated.last_decision == DecisionType.SKIPPED_BY_POLICY
    assert updated.last_action_source == "автопауза"
    assert updated.last_action_at == original_action_at


# Проверяет, что репозиторий помечает только не увиденные в последнем скане объявления как NOT_SEEN_THIS_SCAN.
@pytest.mark.asyncio
async def test_ads_repository_marks_unseen_ads_as_not_seen_this_scan(async_session) -> None:
    repo = AdsRepository(async_session)
    scan_repo = ScanRunsRepository(async_session)

    campaign = await repo.upsert_campaign(
        scope_key="campaign:account-1:campaign-1",
        name="Кампания 1",
    )
    adset = await repo.upsert_adset(
        scope_key="adset:campaign:account-1:campaign-1:adset-1",
        campaign_id=campaign.id,
        name="Адсет 1",
    )
    scan_run = await scan_repo.create_scan_run(
        browser_host_id=uuid4(),
        profile_id=uuid4(),
        status=ScanRunStatus.SUCCEEDED,
        started_at=datetime(2026, 3, 20, 10, 0, tzinfo=UTC),
    )
    ad_seen_1 = await repo.upsert_ad(
        fb_ad_id="ad-1",
        campaign_id=campaign.id,
        adset_id=adset.id,
        name="Объявление 1",
        delivery_status=DeliveryStatus.ACTIVE,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        last_seen_at=datetime(2026, 3, 20, 10, 1, tzinfo=UTC),
        last_scan_run_id=scan_run.id,
    )
    ad_seen_2 = await repo.upsert_ad(
        fb_ad_id="ad-2",
        campaign_id=campaign.id,
        adset_id=adset.id,
        name="Объявление 2",
        delivery_status=DeliveryStatus.ACTIVE,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        last_seen_at=datetime(2026, 3, 20, 10, 1, tzinfo=UTC),
        last_scan_run_id=scan_run.id,
    )
    ad_missing = await repo.upsert_ad(
        fb_ad_id="ad-3",
        campaign_id=campaign.id,
        adset_id=adset.id,
        name="Объявление 3",
        delivery_status=DeliveryStatus.ACTIVE,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        last_seen_at=datetime(2026, 3, 20, 10, 1, tzinfo=UTC),
        last_scan_run_id=scan_run.id,
    )

    unseen_count = await repo.mark_unseen_ads(
        seen_fb_ad_ids=["ad-1", "ad-2"],
        profile_id=scan_run.profile_id,
    )
    ads = await repo.list_ads()

    await async_session.commit()

    assert unseen_count == 1
    assert ad_seen_1.scope_presence == ScopePresence.IN_SCOPE
    assert ad_seen_2.scope_presence == ScopePresence.IN_SCOPE
    assert ad_missing.scope_presence == ScopePresence.NOT_SEEN_THIS_SCAN
    assert (
        next(item for item in ads if item.fb_ad_id == "ad-3").scope_presence
        == ScopePresence.NOT_SEEN_THIS_SCAN
    )


# Проверяет, что репозиторий выбирает последний snapshot метрик по каждому объявлению.
@pytest.mark.asyncio
async def test_ads_repository_returns_latest_metric_snapshot(async_session) -> None:
    repo = AdsRepository(async_session)
    scan_repo = ScanRunsRepository(async_session)

    campaign = await repo.upsert_campaign(
        scope_key="campaign:account-1:campaign-1",
        name="Кампания 1",
    )
    adset = await repo.upsert_adset(
        scope_key="adset:campaign:account-1:campaign-1:adset-1",
        campaign_id=campaign.id,
        name="Адсет 1",
    )
    ad = await repo.upsert_ad(
        fb_ad_id="ad-1",
        campaign_id=campaign.id,
        adset_id=adset.id,
        name="Объявление 1",
    )
    scan_run = await scan_repo.create_scan_run(
        browser_host_id=uuid4(),
        profile_id=uuid4(),
        status=ScanRunStatus.SUCCEEDED,
        started_at=datetime(2026, 3, 20, 10, 0, tzinfo=UTC),
    )
    await repo.add_metric_snapshot(
        fb_ad_id=ad.fb_ad_id,
        ad_id=ad.id,
        scan_run_id=scan_run.id,
        captured_at=datetime(2026, 3, 20, 10, 1, tzinfo=UTC),
        spend=Decimal("1.00"),
        clicks=10,
    )
    await repo.add_metric_snapshot(
        fb_ad_id=ad.fb_ad_id,
        ad_id=ad.id,
        scan_run_id=scan_run.id,
        captured_at=datetime(2026, 3, 20, 10, 2, tzinfo=UTC),
        spend=Decimal("2.50"),
        clicks=25,
    )

    latest_snapshots = await repo.get_latest_metric_snapshots([ad.fb_ad_id])

    await async_session.commit()

    assert latest_snapshots[ad.fb_ad_id].spend == Decimal("2.50")
    assert latest_snapshots[ad.fb_ad_id].clicks == 25


# Проверяет, что системные настройки создаются, обновляются и читаются как плоский словарь.
@pytest.mark.asyncio
async def test_system_settings_repository_upserts_and_reads_values(async_session) -> None:
    repo = SystemSettingsRepository(async_session)

    created_pause = await repo.set_setting(
        SERVICE_SETTING_AUTO_PAUSE_ENABLED,
        "true",
        description="Автопауза включена",
    )
    created_interval = await repo.set_setting(
        SERVICE_SETTING_SCAN_INTERVAL_SECONDS,
        "60",
        description="Частота скана",
    )
    updated_pause = await repo.set_setting(
        SERVICE_SETTING_AUTO_PAUSE_ENABLED,
        "false",
        description="Автопауза обновлена",
    )
    all_settings = await repo.get_all_settings()
    fetched_pause = await repo.get_setting(SERVICE_SETTING_AUTO_PAUSE_ENABLED)

    await async_session.commit()

    assert created_pause.key == SERVICE_SETTING_AUTO_PAUSE_ENABLED
    assert created_interval.value == "60"
    assert updated_pause.value == "false"
    assert fetched_pause is not None
    assert fetched_pause.description == "Автопауза обновлена"
    assert all_settings[SERVICE_SETTING_AUTO_PAUSE_ENABLED] == "false"
    assert all_settings[SERVICE_SETTING_SCAN_INTERVAL_SECONDS] == "60"


# Проверяет, что scan-runs repository возвращает строки с читаемыми host/profile связями и пустыми значениями для незаполненных ссылок.
@pytest.mark.asyncio
async def test_scan_runs_repository_lists_rows_with_readable_links(async_session) -> None:
    browser_repo = BrowserRepository(async_session)
    repo = ScanRunsRepository(async_session)

    browser_host = await browser_repo.upsert_browser_host(
        name="vision-rows",
        vendor="vision",
        api_base_url="http://127.0.0.1:3030",
        is_enabled=True,
        last_heartbeat_at=datetime(2026, 3, 20, 14, 0, tzinfo=UTC),
    )
    profile = await browser_repo.upsert_profile(
        browser_host_id=browser_host.id,
        vendor_profile_id="vision-profile-rows",
        display_name="Vision профиль rows",
        is_active=True,
        last_launch_at=datetime(2026, 3, 20, 14, 1, tzinfo=UTC),
    )
    linked_run = await repo.create_scan_run(
        browser_host_id=browser_host.id,
        profile_id=profile.id,
        status=ScanRunStatus.SUCCEEDED,
        started_at=datetime(2026, 3, 20, 14, 2, tzinfo=UTC),
        finished_at=datetime(2026, 3, 20, 14, 3, tzinfo=UTC),
        rows_seen=1,
        rows_parsed=1,
        scope_summary={"rows_seen": 1, "fb_ad_ids": ["ad-1"]},
    )
    detached_run = await repo.create_scan_run(
        browser_host_id=None,
        profile_id=None,
        status=ScanRunStatus.FAILED,
        started_at=datetime(2026, 3, 20, 14, 4, tzinfo=UTC),
        finished_at=datetime(2026, 3, 20, 14, 5, tzinfo=UTC),
        rows_seen=0,
        rows_parsed=0,
        error_message="Тестовая ошибка",
    )

    rows = await repo.list_scan_run_rows()

    await async_session.commit()

    assert rows[0][0].id == detached_run.id
    assert rows[0][1] is None
    assert rows[0][2] is None
    assert rows[1][0].id == linked_run.id
    assert rows[1][1] == browser_host.name
    assert rows[1][2] == profile.vendor_profile_id


# Проверяет, что результат действия можно дозаписать в уже созданное решение.
@pytest.mark.asyncio
async def test_decisions_repository_can_update_action_result(async_session) -> None:
    scan_repo = ScanRunsRepository(async_session)
    decisions_repo = DecisionsRepository(async_session)

    scan_run = await scan_repo.create_scan_run(
        browser_host_id=uuid4(),
        profile_id=uuid4(),
        status=ScanRunStatus.RUNNING,
        started_at=datetime(2026, 3, 20, 15, 0, tzinfo=UTC),
    )
    decision = await decisions_repo.create_decision(
        scan_run_id=scan_run.id,
        fb_ad_id="ad-1",
        decision=DecisionType.WOULD_PAUSE,
        reason="Тестовая причина",
        resolved_cpa_usd=Decimal("5.00"),
    )

    updated = await decisions_repo.set_decision_action_result(
        decision.id,
        action_executed=True,
        action_status="SUCCEEDED",
    )
    missing = await decisions_repo.set_decision_action_result(
        str(uuid4()),
        action_executed=False,
        action_status="FAILED",
    )

    await async_session.commit()

    assert updated is not None
    assert updated.action_executed is True
    assert updated.action_status == "SUCCEEDED"
    assert missing is None


# Проверяет, что решения и действия сохраняются в правильной последовательности по одному скану.
@pytest.mark.asyncio
async def test_decisions_repository_persists_decision_and_action(async_session) -> None:
    scan_repo = ScanRunsRepository(async_session)
    decisions_repo = DecisionsRepository(async_session)

    scan_run = await scan_repo.create_scan_run(
        browser_host_id=uuid4(),
        profile_id=uuid4(),
        status=ScanRunStatus.RUNNING,
        started_at=datetime(2026, 3, 20, 10, 0, tzinfo=UTC),
    )
    decision = await decisions_repo.create_decision(
        scan_run_id=scan_run.id,
        fb_ad_id="ad-1",
        decision=DecisionType.WOULD_PAUSE,
        reason="Тестовая причина",
        resolved_cpa_usd=Decimal("5.00"),
    )
    action = await decisions_repo.add_action_execution(
        decision_id=decision.id,
        action_type=ActionType.PAUSE,
        status=ActionExecutionStatus.SUCCEEDED,
        started_at=datetime(2026, 3, 20, 10, 1, tzinfo=UTC),
        finished_at=datetime(2026, 3, 20, 10, 2, tzinfo=UTC),
        message="Пауза выполнена",
    )

    decisions = await decisions_repo.list_decisions(scan_run_id=scan_run.id)
    updated_scan = await scan_repo.update_scan_run(
        scan_run.id,
        status=ScanRunStatus.SUCCEEDED,
        finished_at=datetime(2026, 3, 20, 10, 3, tzinfo=UTC),
        rows_seen=1,
        rows_parsed=1,
        scope_summary={"ads": 1},
    )

    await async_session.commit()

    assert decision.reason == "Тестовая причина"
    assert action.status == ActionExecutionStatus.SUCCEEDED
    assert len(decisions) == 1
    assert updated_scan is not None
    assert updated_scan.status == ScanRunStatus.SUCCEEDED


# Проверяет, что browser repository скрывает профили на стопе из активного списка и возвращает их в списке проблемных.
@pytest.mark.asyncio
async def test_browser_repository_suspends_and_resets_profile_scan(async_session) -> None:
    repo = BrowserRepository(async_session)

    browser_host = await repo.upsert_browser_host(
        name="browser-host-local",
        vendor="vision",
        api_base_url=Settings().vision_local_api_url,
        is_enabled=True,
        last_heartbeat_at=datetime(2026, 3, 20, 10, 0, tzinfo=UTC),
    )
    profile = await repo.upsert_profile(
        browser_host_id=browser_host.id,
        vendor_profile_id="vision-profile-suspended",
        display_name="Vision профиль suspended",
        is_active=True,
        last_launch_at=datetime(2026, 3, 20, 10, 1, tzinfo=UTC),
    )

    active_before = await repo.list_active_profiles()
    suspended = await repo.suspend_profile_scan(
        vendor_profile_id="vision-profile-suspended",
        reason="Не удалось получить полный набор строк Ads Manager после одного обновления страницы",
    )
    active_after_suspend = await repo.list_active_profiles()
    suspended_profiles = await repo.list_suspended_profiles()

    assert len(active_before) == 1
    assert suspended is not None
    assert suspended.scan_suspended is True
    assert (
        suspended.scan_suspend_reason
        == "Не удалось получить полный набор строк Ads Manager после одного обновления страницы"
    )
    assert len(active_after_suspend) == 0
    assert len(suspended_profiles) == 1
    assert suspended_profiles[0].profile.vendor_profile_id == profile.vendor_profile_id

    reset = await repo.reset_profile_scan_suspension("vision-profile-suspended")
    active_after_reset = await repo.list_active_profiles()
    suspended_after_reset = await repo.list_suspended_profiles()

    await async_session.commit()

    assert reset is not None
    assert reset.scan_suspended is False
    assert len(active_after_reset) == 1
    assert suspended_after_reset == []


# Проверяет, что control flags можно создать, найти и удалить в рамках одного async-сеанса.
@pytest.mark.asyncio
async def test_control_flags_repository_crud(async_session) -> None:
    repo = ControlFlagsRepository(async_session)

    created = await repo.upsert_control_flag(
        entity_type=EntityType.AD,
        entity_id="ad-1",
        reason="Ручная блокировка",
        created_by="operator",
        tracking_mode=TrackingMode.MANUAL_BLOCK,
        expires_at=datetime(2026, 3, 21, 10, 0, tzinfo=UTC),
    )
    fetched = await repo.get_control_flag(EntityType.AD, "ad-1")
    deleted = await repo.delete_control_flag(EntityType.AD, "ad-1")
    remaining = await repo.list_control_flags()

    await async_session.commit()

    assert created.entity_id == "ad-1"
    assert fetched is not None
    assert deleted is True
    assert remaining == []


# Проверяет, что репозиторий сканов сохраняет временные метки и счетчики последовательно.
@pytest.mark.asyncio
async def test_scan_runs_repository_tracks_lifecycle(async_session) -> None:
    repo = ScanRunsRepository(async_session)

    started_at = datetime(2026, 3, 20, 10, 0, tzinfo=UTC)
    scan_run = await repo.create_scan_run(
        browser_host_id=uuid4(),
        profile_id=uuid4(),
        status=ScanRunStatus.PENDING,
        started_at=started_at,
        rows_seen=0,
        rows_parsed=0,
    )
    updated = await repo.update_scan_run(
        scan_run.id,
        status=ScanRunStatus.RUNNING,
        rows_seen=2,
        rows_parsed=2,
        scope_summary={"ads": 2},
    )

    all_runs = await repo.list_scan_runs()

    await async_session.commit()

    assert scan_run.started_at == started_at
    assert updated is not None
    assert updated.rows_seen == 2
    assert len(all_runs) == 1


# Проверяет, что bootstrap правил создает полный набор системных стоп-правил в базе и больше не зависит от памяти API.
@pytest.mark.asyncio
async def test_rules_repository_bootstraps_default_rules(async_session) -> None:
    repo = RulesRepository(async_session)

    await repo.ensure_default_rules()
    rules = await repo.list_rules()

    await async_session.commit()

    assert len(rules) == len(DEFAULT_RULES)
    assert [rule.code for rule in rules] == [rule.code for rule in DEFAULT_RULES]
    assert rules[0].config_json["priority"] == 10
    assert rules[0].config_json["cpa_multiplier"] == "0.02"


# Проверяет, что browser repository хранит последнюю сессию профиля и возвращает ее вместе с host/profile данными.
@pytest.mark.asyncio
async def test_browser_repository_returns_latest_session_per_profile(async_session) -> None:
    repo = BrowserRepository(async_session)

    browser_host = await repo.upsert_browser_host(
        name="browser-host-local",
        vendor="vision",
        api_base_url=Settings().vision_local_api_url,
        is_enabled=True,
        last_heartbeat_at=datetime(2026, 3, 20, 10, 0, tzinfo=UTC),
    )
    profile = await repo.upsert_profile(
        browser_host_id=browser_host.id,
        vendor_profile_id="vision-profile-1",
        display_name="Vision профиль 1",
        is_active=True,
        last_launch_at=datetime(2026, 3, 20, 10, 1, tzinfo=UTC),
    )
    await repo.create_browser_session(
        browser_host_id=browser_host.id,
        profile_id=profile.id,
        status="STOPPED",
        started_at=datetime(2026, 3, 20, 10, 2, tzinfo=UTC),
        finished_at=datetime(2026, 3, 20, 10, 3, tzinfo=UTC),
    )
    await repo.create_browser_session(
        browser_host_id=browser_host.id,
        profile_id=profile.id,
        status="ACTIVE",
        started_at=datetime(2026, 3, 20, 10, 4, tzinfo=UTC),
        cdp_url="http://127.0.0.1:54000",
    )

    latest = await repo.get_latest_session_by_vendor_profile_id("vision-profile-1")
    all_latest = await repo.list_latest_sessions()

    await async_session.commit()

    assert latest is not None
    assert latest.profile.vendor_profile_id == "vision-profile-1"
    assert latest.browser_host.name == "browser-host-local"
    assert latest.session.status == "ACTIVE"
    assert latest.session.cdp_url == "http://127.0.0.1:54000"
    assert len(all_latest) == 1
