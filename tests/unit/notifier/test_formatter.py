from __future__ import annotations

from datetime import UTC, datetime

from apps.notifier.events import TelegramEvent, TelegramEventPayload, TelegramEventType
from apps.notifier.formatter import TelegramMessageFormatter


def _build_event(event_type: TelegramEventType) -> TelegramEvent:
    return TelegramEvent(
        event_type=event_type,
        dedupe_key=f"key-{event_type.value}",
        created_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        payload=TelegramEventPayload(
            host="browser-host-01",
            account_name="acc-1",
            campaign_name="Кампания",
            adset_name="Адсет",
            ad_name="Объявление",
            fb_ad_id="123",
            reason="Причина теста",
            metrics={
                "spend": "2.50",
                "clicks": 10,
                "cpc": "0.10",
                "leads": 2,
                "cost_per_lead": "0.50",
                "registrations": 1,
                "cost_per_registration": "1.00",
                "deposits": 0,
            },
            delivery_before="ACTIVE",
            delivery_after="PAUSED",
            rule_id="stop_high_cpc",
        ),
    )


# Проверяет, что форматтер собирает русскоязычное сообщение о паузе с причиной и метриками.
def test_formatter_builds_pause_message() -> None:
    formatter = TelegramMessageFormatter()

    message = formatter.format(_build_event(TelegramEventType.AD_PAUSED_BY_BOT))

    assert "Объявление выключено ботом" in message
    assert "Причина: Причина теста" in message
    assert "CPL: 0.50" in message


# Проверяет, что форматтер собирает отдельный текст для кейса «не показывается».
def test_formatter_builds_rejected_message() -> None:
    formatter = TelegramMessageFormatter()

    message = formatter.format(_build_event(TelegramEventType.AD_REJECTED_OR_NOT_DELIVERING))

    assert "Объявление не показывается" in message
    assert "Причина: Причина теста" in message
