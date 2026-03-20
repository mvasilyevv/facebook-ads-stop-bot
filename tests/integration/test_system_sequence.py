from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from apps.api.schemas.common import ControlFlagTarget
from apps.notifier.events import TelegramEventType
from apps.notifier.formatter import TelegramMessageFormatter
from apps.notifier.sender import InMemoryDedupStore, TelegramSender
from apps.notifier.telegram import TelegramNotifier
from core.domain import DeliveryStatus
from core.rules import (
    CleanScanState,
    MetricsSnapshot,
    build_threshold_pack,
    evaluate_pause_reasons,
    evaluate_resume,
)
from tests.fixtures.integration_helpers import (
    MemoryTelegramTransport,
    build_demo_state,
    build_low_risk_metrics,
    build_telegram_event,
    create_bound_offer_with_rate,
)


# Проверяет полный рабочий путь: оффер и ставка рассчитывают CPA, стоп-правило срабатывает, а Telegram-фасад отправляет русское сообщение.
def test_full_pause_sequence_with_telegram_notification() -> None:
    state, offers_service = build_demo_state()
    created_at = datetime(2026, 3, 20, 12, 0, tzinfo=UTC)

    create_bound_offer_with_rate(
        offers_service,
        offer_code="offer-sequence",
        offer_name="Последовательный оффер",
        cpa_usd=Decimal("5.00"),
        effective_from=created_at,
        entity_type=ControlFlagTarget.ADSET,
        entity_external_id="demo-adset-1",
    )

    assert state.store.ads["demo-ad-1"].resolved_cpa_usd == Decimal("5.00")

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
def test_resume_sequence_after_metrics_catch_up() -> None:
    state, offers_service = build_demo_state()
    created_at = datetime(2026, 3, 20, 12, 0, tzinfo=UTC)

    create_bound_offer_with_rate(
        offers_service,
        offer_code="offer-resume",
        offer_name="Оффер для возврата",
        cpa_usd=Decimal("5.00"),
        effective_from=created_at,
        entity_type=ControlFlagTarget.ADSET,
        entity_external_id="demo-adset-1",
    )

    assert state.store.ads["demo-ad-1"].resolved_cpa_usd == Decimal("5.00")

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
