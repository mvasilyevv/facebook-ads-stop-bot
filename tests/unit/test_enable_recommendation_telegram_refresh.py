# -*- coding: utf-8 -*-
"""Тесты сброса telegram_chat_id/message_id при апдейте recommendation event."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.domain import EnableRecommendationLevel
from core.enable_recommendations.service import (
    EnableRecommendationCandidate,
    persist_enable_recommendation_candidates,
)


def _candidate(*, level: EnableRecommendationLevel, reason_title: str):
    """Возвращает recommendation candidate с заданным level и причиной."""
    return EnableRecommendationCandidate(
        ad_id=uuid.uuid4(),
        snapshot_id=uuid.uuid4(),
        offer_id=uuid.uuid4(),
        fb_ad_id="ad-refresh",
        ad_name="Ad Refresh",
        delivery_status="OFF",
        recommendation_level=level,
        matched_rule_codes=[],
        reason_title=reason_title,
        reason_text="Текст причины",
        metrics_json={"spend": "1.00"},
        live_batch_started_at=datetime(2026, 4, 25, 10, 0, tzinfo=UTC),
    )


def _make_session(*, existing):
    """Готовит async-сессию с session.scalar возвращающим existing."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.scalar = AsyncMock(return_value=existing)
    return session


# При изменении recommendation_level telegram_chat_id и telegram_message_id сбрасываются.
@pytest.mark.asyncio
async def test_telegram_refresh_when_level_changes():
    existing = SimpleNamespace(
        snapshot_id=None,
        offer_id=None,
        delivery_status="OFF",
        recommendation_level=EnableRecommendationLevel.OK,
        matched_rule_codes=[],
        reason_title="Старая причина",
        reason_text="Старый текст",
        metrics_json={"spend": "0.00"},
        telegram_chat_id="chat-123",
        telegram_message_id=42,
    )
    session = _make_session(existing=existing)

    candidate = _candidate(
        level=EnableRecommendationLevel.WARNING,
        reason_title="Старая причина",  # title тот же
    )

    created = await persist_enable_recommendation_candidates(session, [candidate])

    assert created == []
    assert existing.recommendation_level == EnableRecommendationLevel.WARNING
    # Сброс должен произойти — level изменился
    assert existing.telegram_chat_id is None
    assert existing.telegram_message_id is None


# При изменении reason_title (без смены level) telegram_* также сбрасываются.
@pytest.mark.asyncio
async def test_telegram_refresh_when_reason_title_changes():
    existing = SimpleNamespace(
        snapshot_id=None,
        offer_id=None,
        delivery_status="OFF",
        recommendation_level=EnableRecommendationLevel.OK,
        matched_rule_codes=[],
        reason_title="Старая причина",
        reason_text="Старый текст",
        metrics_json={"spend": "0.00"},
        telegram_chat_id="chat-456",
        telegram_message_id=99,
    )
    session = _make_session(existing=existing)

    candidate = _candidate(
        level=EnableRecommendationLevel.OK,  # уровень тот же
        reason_title="Новая причина",
    )

    await persist_enable_recommendation_candidates(session, [candidate])

    assert existing.reason_title == "Новая причина"
    assert existing.telegram_chat_id is None
    assert existing.telegram_message_id is None


# При неизменённом level и title telegram_chat_id/message_id сохраняются.
@pytest.mark.asyncio
async def test_telegram_refresh_skipped_when_level_and_title_same():
    existing = SimpleNamespace(
        snapshot_id=None,
        offer_id=None,
        delivery_status="OFF",
        recommendation_level=EnableRecommendationLevel.WARNING,
        matched_rule_codes=[],
        reason_title="Одна и та же причина",
        reason_text="Старый текст",
        metrics_json={"spend": "0.00"},
        telegram_chat_id="chat-keep",
        telegram_message_id=777,
    )
    session = _make_session(existing=existing)

    candidate = _candidate(
        level=EnableRecommendationLevel.WARNING,
        reason_title="Одна и та же причина",
    )

    await persist_enable_recommendation_candidates(session, [candidate])

    # Payload обновился, но telegram-ссылки сохранены
    assert existing.reason_text == "Текст причины"
    assert existing.metrics_json == {"spend": "1.00"}
    assert existing.telegram_chat_id == "chat-keep"
    assert existing.telegram_message_id == 777
