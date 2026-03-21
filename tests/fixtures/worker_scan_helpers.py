from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.domain import DeliveryStatus, EntityType, ScopePresence, TrackingMode
from core.repositories import AdsRepository, BrowserRepository, OffersRepository


@dataclass(slots=True, frozen=True)
class WorkerScanRow:
    """Нормализованная строка скана для будущего worker runtime."""

    campaign_scope_key: str
    campaign_name: str
    adset_scope_key: str
    adset_name: str
    fb_ad_id: str
    ad_name: str
    delivery_status: DeliveryStatus
    tracking_mode: TrackingMode
    scope_presence: ScopePresence
    spend: Decimal
    clicks: int
    cpc: Decimal | None
    leads: int
    cost_per_lead: Decimal | None
    registrations: int
    cost_per_registration: Decimal | None
    deposits: int
    captured_at: datetime


class FakeScannerProvider:
    """Фейковый provider сканирования для интеграционных сценариев worker."""

    def __init__(self, rows: list[WorkerScanRow]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, str]] = []

    async def scan_rows(self, profile_id: str, browser_host_name: str) -> list[WorkerScanRow]:
        self.calls.append((profile_id, browser_host_name))
        return self.rows


@dataclass(slots=True, frozen=True)
class WorkerScenarioSeed:
    """Справочные идентификаторы для одной worker-сценарной заготовки."""

    browser_host_name: str
    profile_id: str
    campaign_name: str
    adset_name: str
    ad_name: str
    campaign_scope_key: str
    adset_scope_key: str
    fb_ad_id: str


async def seed_worker_ad_graph(async_session_factory) -> WorkerScenarioSeed:
    """Создает базовую связку browser host -> profile -> campaign -> adset -> ad."""

    async with async_session_factory() as session:
        browser_repo = BrowserRepository(session)
        ads_repo = AdsRepository(session)

        browser_host = await browser_repo.upsert_browser_host(
            name="browser-host-local",
            vendor="vision",
            api_base_url="http://127.0.0.1:3030",
            is_enabled=True,
            last_heartbeat_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        profile = await browser_repo.upsert_profile(
            browser_host_id=browser_host.id,
            vendor_profile_id="vision-profile-1",
            display_name="Vision профиль 1",
            is_active=True,
            last_launch_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        campaign = await ads_repo.upsert_campaign(
            scope_key="campaign-scope-1",
            name="Кампания 1",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        adset = await ads_repo.upsert_adset(
            scope_key="adset-scope-1",
            campaign_id=campaign.id,
            name="Адсет 1",
            tracking_mode=TrackingMode.TRACKED,
            last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        ad = await ads_repo.upsert_ad(
            fb_ad_id="ad-1",
            campaign_id=campaign.id,
            adset_id=adset.id,
            name="Объявление 1",
            delivery_status=DeliveryStatus.ACTIVE,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        await session.commit()

        return WorkerScenarioSeed(
            browser_host_name=browser_host.name,
            profile_id=profile.vendor_profile_id,
            campaign_name=campaign.name,
            adset_name=adset.name,
            ad_name=ad.name,
            campaign_scope_key="campaign-scope-1",
            adset_scope_key="adset-scope-1",
            fb_ad_id=ad.fb_ad_id,
        )


async def seed_offer_with_binding(
    async_session_factory,
    *,
    entity_type: EntityType,
    entity_id: str,
    offer_code: str,
    cpa_usd: Decimal,
    effective_from: datetime,
) -> str:
    """Создает оффер, ставку и привязку к сущности."""

    async with async_session_factory() as session:
        offers_repo = OffersRepository(session)
        offer = await offers_repo.create_offer(code=offer_code, name=offer_code)
        await offers_repo.add_rate_version(
            offer_id=offer.id,
            cpa_usd=cpa_usd,
            effective_from=effective_from,
        )
        await offers_repo.upsert_binding(
            entity_type=entity_type,
            entity_id=entity_id,
            offer_id=offer.id,
        )
        await session.commit()
        return offer.id


def load_worker_scan_service_class() -> type[Any]:
    """Пробует найти будущий WorkerScanService в ожидаемых модулях."""

    candidates = (
        "apps.worker.scan_service",
        "apps.worker.scanner_service",
        "apps.worker.runtime",
        "apps.worker.service",
    )
    for module_name in candidates:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        service_cls = getattr(module, "WorkerScanService", None)
        if service_cls is not None:
            return service_cls
    pytest.skip("WorkerScanService еще не добавлен в кодовую базу")


def build_worker_service(
    *,
    async_session_factory,
    scanner_provider: FakeScannerProvider,
) -> Any:
    """Собирает сервис через наиболее вероятные зависимости без жесткой привязки к внутренностям."""

    service_cls = load_worker_scan_service_class()
    signature = inspect.signature(service_cls)
    kwargs: dict[str, Any] = {}

    for name, parameter in signature.parameters.items():
        if name in {"scanner_provider", "scanner", "provider", "scan_provider"}:
            kwargs[name] = scanner_provider
        elif name in {"async_session_factory", "session_factory", "db_session_factory"}:
            kwargs[name] = async_session_factory
        elif name in {"notifier", "telegram_notifier"}:
            kwargs[name] = _SilentNotifier()
        elif name == "settings":
            from core.config import get_settings

            kwargs[name] = get_settings()
        elif parameter.default is inspect._empty:
            continue

    try:
        return service_cls(**kwargs)
    except TypeError as exc:  # pragma: no cover - защищает от несовместимого сигнатурного контракта
        raise AssertionError(
            "Не удалось собрать WorkerScanService по ожидаемому контракту конструктора"
        ) from exc


class _SilentNotifier:
    """Глушит Telegram-уведомления в интеграционных сценариях worker."""

    def notify(self, event: object) -> bool:
        return True
