from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.notifier.events import TelegramEventType
from apps.notifier.formatter import TelegramMessageFormatter
from apps.notifier.sender import InMemoryDedupStore, TelegramSender
from apps.notifier.telegram import TelegramNotifier
from core.domain import DeliveryStatus, EntityType
from core.rules import (
    CleanScanState,
    MetricsSnapshot,
    build_threshold_pack,
    evaluate_pause_reasons,
    evaluate_resume,
)
from tests.fixtures.integration_helpers import (
    MemoryTelegramTransport,
    build_low_risk_metrics,
    build_telegram_event,
    create_bound_offer_with_rate,
    resolve_current_cpa,
    seed_demo_ad,
)


# Проверяет полный рабочий путь: оффер и ставка рассчитывают CPA, стоп-правило срабатывает, а Telegram-фасад отправляет русское сообщение.
@pytest.mark.asyncio
async def test_full_pause_sequence_with_telegram_notification(async_session) -> None:
    created_at = datetime(2026, 3, 20, 12, 0, tzinfo=UTC)
    fb_ad_id, adset_scope_key = await seed_demo_ad(async_session)

    await create_bound_offer_with_rate(
        async_session,
        offer_code="offer-sequence",
        offer_name="Последовательный оффер",
        cpa_usd=Decimal("5.00"),
        effective_from=created_at,
        entity_type=EntityType.ADSET,
        entity_id=adset_scope_key,
    )

    assert await resolve_current_cpa(
        async_session, fb_ad_id=fb_ad_id, adset_scope_key=adset_scope_key
    ) == Decimal("5.00")

    thresholds = build_threshold_pack(Decimal("5.00"))
    pause_snapshot = MetricsSnapshot(
        spend=Decimal("0.38"),
        cpc=Decimal("0.11"),
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
    )

    pause_reasons = evaluate_pause_reasons(pause_snapshot, thresholds)

    assert pause_reasons == ["Клик превысил допустимую долю CPA"]

    transport = MemoryTelegramTransport()
    notifier = TelegramNotifier(
        formatter=TelegramMessageFormatter(),
        sender=TelegramSender(transport=transport, dedup_store=InMemoryDedupStore(ttl_seconds=60)),
    )
    sent = notifier.notify(
        build_telegram_event(
            TelegramEventType.AD_PAUSED_BY_BOT,
            reason="Клик превысил допустимую долю CPA",
            metrics=build_low_risk_metrics(
                spend=Decimal("0.38"),
                cpc=Decimal("0.11"),
                leads=0,
                cost_per_lead=None,
                registrations=0,
                cost_per_registration=None,
                deposits=0,
            ),
            delivery_before=DeliveryStatus.ACTIVE.value,
            delivery_after=DeliveryStatus.PAUSED.value,
            rule_id="stop_high_cpc",
        )
    )

    assert sent is True
    assert len(transport.messages) == 1
    assert "Объявление выключено ботом" in transport.messages[0]
    assert "Клик превысил допустимую долю CPA" in transport.messages[0]


# Проверяет, что после долета лидов объявление снова становится пригодным к включению при двух чистых сканах подряд.
@pytest.mark.asyncio
async def test_resume_sequence_after_metrics_catch_up(async_session) -> None:
    created_at = datetime(2026, 3, 20, 12, 0, tzinfo=UTC)
    fb_ad_id, adset_scope_key = await seed_demo_ad(async_session)

    await create_bound_offer_with_rate(
        async_session,
        offer_code="offer-resume",
        offer_name="Оффер для возврата",
        cpa_usd=Decimal("5.00"),
        effective_from=created_at,
        entity_type=EntityType.ADSET,
        entity_id=adset_scope_key,
    )

    assert await resolve_current_cpa(
        async_session, fb_ad_id=fb_ad_id, adset_scope_key=adset_scope_key
    ) == Decimal("5.00")

    thresholds = build_threshold_pack(Decimal("5.00"))
    resume_snapshot = MetricsSnapshot(
        spend=Decimal("0.51"),
        cpc=Decimal("0.06"),
        leads=2,
        cost_per_lead=Decimal("0.25"),
        registrations=1,
        cost_per_registration=Decimal("0.25"),
        deposits=0,
    )

    resume_decision = evaluate_resume(
        snapshot=resume_snapshot,
        thresholds=thresholds,
        clean_scans=CleanScanState(streak=2),
        delivery_status=DeliveryStatus.PAUSED,
        is_blocked=False,
    )

    assert resume_decision.should_resume is True
    assert resume_decision.reason == "Объявление снова безопасно для запуска"

    transport = MemoryTelegramTransport()
    notifier = TelegramNotifier(
        formatter=TelegramMessageFormatter(),
        sender=TelegramSender(transport=transport, dedup_store=InMemoryDedupStore(ttl_seconds=60)),
    )
    sent = notifier.notify(
        build_telegram_event(
            TelegramEventType.AD_RESUMED_BY_BOT,
            reason="Объявление снова безопасно для запуска",
            metrics=build_low_risk_metrics(
                spend=Decimal("0.51"),
                cpc=Decimal("0.06"),
                leads=2,
                cost_per_lead=Decimal("0.25"),
                registrations=1,
                cost_per_registration=Decimal("0.25"),
                deposits=0,
            ),
            delivery_before=DeliveryStatus.PAUSED.value,
            delivery_after=DeliveryStatus.ACTIVE.value,
            rule_id="resume_safe_zone",
        )
    )

    assert sent is True
    assert len(transport.messages) == 1
    assert "Объявление снова включено ботом" in transport.messages[0]
    assert "Объявление снова безопасно для запуска" in transport.messages[0]
