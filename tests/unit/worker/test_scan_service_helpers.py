from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from apps.worker.scan_service import WorkerScanService
from core.domain import DeliveryStatus, ScopePresence, TrackingMode
from core.scanner import ScannedAdRow, build_adset_scope_key, build_campaign_scope_key


# Проверяет, что worker восстанавливает placeholder-имя кампании и scope key из уже известной связи объявления.
def test_restore_scope_from_existing_ad_reuses_known_campaign_scope() -> None:
    original_campaign_scope_key = build_campaign_scope_key("Кампания 120241420128910176")
    row = ScannedAdRow(
        fb_ad_id="120241420867490176",
        campaign_scope_key=original_campaign_scope_key,
        adset_scope_key=build_adset_scope_key("3", original_campaign_scope_key),
        campaign_name="Кампания 120241420128910176",
        adset_name="3",
        ad_name="DRC_CR2_CR016",
        delivery_status=DeliveryStatus.PAUSED,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        spend=Decimal("0.00"),
        clicks=0,
        cpc=None,
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        last_seen_at=datetime(2026, 3, 23, 16, 0, tzinfo=UTC),
    )
    service = WorkerScanService(async_session_factory=None, scanner_provider=SimpleNamespace())
    existing_ad = SimpleNamespace(
        name="DRC_CR2_CR016",
        campaign=SimpleNamespace(
            name="CR2 | DRC | MV | NEW | pwa.partners | 15.03",
            scope_key="campaign:cr2-drc-mv-new-pwa-partners-15-03",
        ),
        adset=SimpleNamespace(
            name="3",
            scope_key="adset:campaign:cr2-drc-mv-new-pwa-partners-15-03:3",
        ),
    )

    restored = service._restore_scope_from_existing_ad(row=row, existing_ad=existing_ad)

    assert restored.campaign_name == "CR2 | DRC | MV | NEW | pwa.partners | 15.03"
    assert restored.campaign_scope_key == "campaign:cr2-drc-mv-new-pwa-partners-15-03"
    assert restored.adset_scope_key == "adset:campaign:cr2-drc-mv-new-pwa-partners-15-03:3"
    assert restored.ad_name == "DRC_CR2_CR016"
