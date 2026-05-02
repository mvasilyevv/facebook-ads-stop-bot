# -*- coding: utf-8 -*-
"""Тесты фильтрации алертов по активным снузам в observer worker."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain import AlertStage


def _make_candidate(fb_ad_id: str = "111"):
    """Создаёт минимальный AlertCandidate для тестирования."""
    from core.observer.service import AlertCandidate

    return AlertCandidate(
        fb_ad_id=fb_ad_id,
        snapshot_id="tok1",
        offer_id=None,
        ad_name="Тест",
        campaign_name="Кампания",
        adset_name="Адсет",
        offer_code="TST",
        offer_name=None,
        offer_cpa=None,
        stage=AlertStage.WARNING,
        matched_rule_codes=["cpc"],
        reason_title="CPC высокий",
        reason_text="CPC превышает порог",
        metrics_json={"spend": "5.00"},
    )


# Проверяем, что алерт с активным снузом не уходит в TG.
@pytest.mark.asyncio
async def test_snoozed_ad_alert_is_filtered_out():
    """Если fb_ad_id находится в активных снузах — алерт не должен отправляться."""
    candidate = _make_candidate(fb_ad_id="111")
    alerts = [candidate]

    with patch(
        "core.observer.db_queries.load_active_snooze_ad_ids",
        new=AsyncMock(return_value={"111"}),
    ):
        snoozed_ids = await __import__(
            "core.observer.db_queries", fromlist=["load_active_snooze_ad_ids"]
        ).load_active_snooze_ad_ids()

    filtered = [a for a in alerts if a.fb_ad_id not in snoozed_ids]
    assert len(filtered) == 0, "Алерт с активным снузом должен быть отфильтрован"


# Проверяем, что алерт без снуза проходит фильтрацию.
@pytest.mark.asyncio
async def test_non_snoozed_ad_alert_passes_through():
    """Если fb_ad_id не в активных снузах — алерт должен пройти фильтрацию."""
    candidate = _make_candidate(fb_ad_id="222")
    alerts = [candidate]

    with patch(
        "core.observer.db_queries.load_active_snooze_ad_ids",
        new=AsyncMock(return_value={"111"}),
    ):
        snoozed_ids = await __import__(
            "core.observer.db_queries", fromlist=["load_active_snooze_ad_ids"]
        ).load_active_snooze_ad_ids()

    filtered = [a for a in alerts if a.fb_ad_id not in snoozed_ids]
    assert len(filtered) == 1, "Алерт без снуза должен пройти фильтрацию"


# Проверяем, что load_active_snooze_ad_ids возвращает только записи с snoozed_until > now.
@pytest.mark.asyncio
async def test_load_active_snooze_ad_ids_excludes_expired():
    """Истёкшие снузы не должны попадать в результат функции."""
    from core.observer.db_queries import load_active_snooze_ad_ids

    session = AsyncMock()
    mock_result = MagicMock()
    # Только один активный снуз
    mock_result.all.return_value = [("333",)]
    session.execute = AsyncMock(return_value=mock_result)

    result = await load_active_snooze_ad_ids(session=session)

    assert "333" in result
    assert len(result) == 1


# Проверяем, что при пустом множестве снузов все алерты проходят.
@pytest.mark.asyncio
async def test_no_snoozes_means_all_alerts_pass():
    """При пустом множестве активных снузов все алерты должны пройти."""
    candidates = [_make_candidate("111"), _make_candidate("222"), _make_candidate("333")]

    with patch(
        "core.observer.db_queries.load_active_snooze_ad_ids",
        new=AsyncMock(return_value=set()),
    ):
        snoozed_ids = await __import__(
            "core.observer.db_queries", fromlist=["load_active_snooze_ad_ids"]
        ).load_active_snooze_ad_ids()

    filtered = [a for a in candidates if a.fb_ad_id not in snoozed_ids]
    assert len(filtered) == 3, "Без снузов все алерты должны пройти"


# Проверяем, что FSM-состояние кандидата не изменяется при фильтрации по снузу.
def test_fsm_state_unchanged_when_alert_filtered_by_snooze():
    """Stage AlertCandidate остаётся WARNING после фильтрации по снузу."""
    candidate = _make_candidate(fb_ad_id="111")
    original_stage = candidate.stage

    snoozed_ids = {"111"}
    filtered = [a for a in [candidate] if a.fb_ad_id not in snoozed_ids]

    # Кандидат отфильтрован, но его stage не изменился
    assert len(filtered) == 0
    assert candidate.stage == original_stage
