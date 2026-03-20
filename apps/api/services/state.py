from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from apps.api.config import ApiSettings
from apps.api.schemas.ads import AdDetail
from apps.api.schemas.common import (
    DecisionKind,
    DeliveryStatus,
    ScopePresence,
    SessionStatus,
    TrackingMode,
)
from apps.api.schemas.control_flags import ControlFlagItem
from apps.api.schemas.decisions import DecisionItem
from apps.api.schemas.offers import OfferBindingItem, OfferItem, OfferRateItem
from apps.api.schemas.rules import RuleItem
from apps.api.schemas.scan_runs import ScanRunItem
from apps.api.schemas.sessions import BrowserSessionItem

if TYPE_CHECKING:
    from apps.api.services.ads import AdsService
    from apps.api.services.control_flags import ControlFlagsService
    from apps.api.services.decisions import DecisionsService
    from apps.api.services.health import HealthService
    from apps.api.services.offers import OffersService
    from apps.api.services.rules import RulesService
    from apps.api.services.scan_runs import ScanRunsService
    from apps.api.services.sessions import SessionsService


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(slots=True)
class ApiStore:
    ads: dict[str, AdDetail] = field(default_factory=dict)
    decisions: list[DecisionItem] = field(default_factory=list)
    scan_runs: list[ScanRunItem] = field(default_factory=list)
    rules: dict[str, RuleItem] = field(default_factory=dict)
    control_flags: dict[str, ControlFlagItem] = field(default_factory=dict)
    sessions: dict[str, BrowserSessionItem] = field(default_factory=dict)
    offers: dict[str, OfferItem] = field(default_factory=dict)
    offer_rates: dict[str, list[OfferRateItem]] = field(default_factory=dict)
    offer_bindings: dict[str, OfferBindingItem] = field(default_factory=dict)


@dataclass(slots=True)
class ApiState:
    settings: ApiSettings
    store: ApiStore
    health_service: HealthService
    ads_service: AdsService
    decisions_service: DecisionsService
    scan_runs_service: ScanRunsService
    rules_service: RulesService
    offers_service: OffersService
    control_flags_service: ControlFlagsService
    sessions_service: SessionsService


def _seed_ads(now: datetime) -> Iterable[AdDetail]:
    yield AdDetail(
        fb_ad_id="demo-ad-1",
        campaign_id="demo-campaign-1",
        adset_id="demo-adset-1",
        campaign_name="Демо кампания",
        adset_name="Демо адсет",
        ad_name="Демо объявление",
        delivery_status=DeliveryStatus.ACTIVE,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        last_seen_at=now,
        last_decision=DecisionKind.NO_ACTION,
        resolved_cpa_usd=Decimal("5.00"),
        last_scan_run_id="scan-run-demo-1",
        created_at=now,
        updated_at=now,
    )


def _seed_rules(now: datetime) -> Iterable[RuleItem]:
    yield RuleItem(
        id="rule-cpc",
        code="stop_high_cpc",
        title="Стоп по дорогому клику",
        description="Останавливает объявление при превышении порога CPC относительно CPA.",
        is_enabled=True,
        priority=10,
        cpa_multiplier=Decimal("0.02"),
        updated_at=now,
    )
    yield RuleItem(
        id="rule-lead",
        code="stop_high_cpl",
        title="Стоп по дорогому лида",
        description="Останавливает объявление при превышении порога CPL относительно CPA.",
        is_enabled=True,
        priority=20,
        cpa_multiplier=Decimal("0.10"),
        updated_at=now,
    )


def _seed_sessions(now: datetime, settings: ApiSettings) -> Iterable[BrowserSessionItem]:
    yield BrowserSessionItem(
        profile_id=settings.default_profile_id,
        browser_host_id=settings.default_browser_host_id,
        status=SessionStatus.STOPPED,
        cdp_url=None,
        webdriver_url=None,
        last_started_at=None,
        last_stopped_at=now,
        last_message="Сессия ожидает запуска",
    )


def _seed_offers(now: datetime) -> tuple[list[OfferItem], dict[str, list[OfferRateItem]]]:
    offer = OfferItem(
        id="offer-demo-1",
        code="demo-offer",
        name="Демо оффер",
        is_active=True,
        current_cpa_usd=Decimal("5.00"),
        created_at=now,
        updated_at=now,
    )
    rate = OfferRateItem(
        id="rate-demo-1",
        offer_id=offer.id,
        cpa_usd=Decimal("5.00"),
        effective_from=now,
        effective_to=None,
        note="Базовая ставка для демонстрации",
        created_at=now,
    )
    return [offer], {offer.id: [rate]}


def _seed_scan_runs(now: datetime, settings: ApiSettings) -> Iterable[ScanRunItem]:
    yield ScanRunItem(
        id="scan-run-demo-1",
        browser_host_id=settings.default_browser_host_id,
        profile_id=settings.default_profile_id,
        status="COMPLETED",
        rows_seen=1,
        rows_parsed=1,
        scope_summary="Демо-скан текущего scope",
        error_message=None,
        started_at=now,
        finished_at=now,
    )


def build_api_state(settings: ApiSettings) -> ApiState:
    now = utcnow()
    store = ApiStore()
    store.ads = {item.fb_ad_id: item for item in _seed_ads(now)}
    store.rules = {item.id: item for item in _seed_rules(now)}
    store.sessions = {item.profile_id: item for item in _seed_sessions(now, settings)}
    store.scan_runs = list(_seed_scan_runs(now, settings))
    offers, offer_rates = _seed_offers(now)
    store.offers = {item.id: item for item in offers}
    store.offer_rates = offer_rates

    from apps.api.services.ads import AdsService
    from apps.api.services.control_flags import ControlFlagsService
    from apps.api.services.decisions import DecisionsService
    from apps.api.services.health import HealthService
    from apps.api.services.offers import OffersService
    from apps.api.services.rules import RulesService
    from apps.api.services.scan_runs import ScanRunsService
    from apps.api.services.sessions import SessionsService

    health_service = HealthService(settings=settings)
    control_flags_service = ControlFlagsService(store=store)
    ads_service = AdsService(store=store, control_flags_service=control_flags_service)
    decisions_service = DecisionsService(store=store)
    scan_runs_service = ScanRunsService(store=store)
    rules_service = RulesService(store=store)
    offers_service = OffersService(store=store)
    sessions_service = SessionsService(store=store)
    return ApiState(
        settings=settings,
        store=store,
        health_service=health_service,
        ads_service=ads_service,
        decisions_service=decisions_service,
        scan_runs_service=scan_runs_service,
        rules_service=rules_service,
        offers_service=offers_service,
        control_flags_service=control_flags_service,
        sessions_service=sessions_service,
    )
