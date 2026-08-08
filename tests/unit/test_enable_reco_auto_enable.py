from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import apps.enable_recommendation_worker.main as worker
from core.enable_reco.analyzer import RecommendationDecision


def _candidate() -> worker.CandidateRow:
    return worker.CandidateRow(
        ad_id=uuid.uuid4(),
        fb_ad_id="123",
        ad_name="Ad",
        campaign_name="Campaign",
        adset_name="Adset",
        alert_state="disabled",
        last_transition_at=datetime(2026, 7, 17, 8, tzinfo=UTC),
        snoozed_until=None,
        offer_code="CR2",
        cpa_threshold=None,
        ad_account_id="123",
        offer_currency="USD",
        open_state_token=uuid.uuid4(),
        delivery_status="OFF",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auto_enabled", "level", "expected_promotions"),
    [
        (False, "ok", 0),
        (True, "warning", 0),
        (True, "ok", 1),
    ],
)
async def test_auto_enable_executes_only_ok_with_master_switch(
    auto_enabled: bool,
    level: str,
    expected_promotions: int,
) -> None:
    promotions: list[dict] = []
    sent: list[dict] = []

    async def promote(*_, **kwargs):
        promotions.append(kwargs)

    async def send(*_, **kwargs):
        sent.append(kwargs)
        return True

    with (
        patch.object(worker, "load_scanning_enabled", AsyncMock(return_value=True)),
        patch.object(
            worker, "load_auto_enable_recommendations", AsyncMock(return_value=auto_enabled)
        ),
        patch.object(worker, "fetch_candidates", AsyncMock(return_value=[_candidate()])),
        patch.object(
            worker,
            "resolve_account_currencies",
            AsyncMock(return_value=MagicMock(currencies={"123": "USD"})),
        ),
        patch.object(worker, "fetch_metrics_since", AsyncMock(return_value=[])),
        patch.object(worker, "insert_recommendation", AsyncMock(return_value=uuid.uuid4())),
        patch.object(
            worker,
            "should_recommend",
            return_value=RecommendationDecision(
                recommend=True,
                level=level,
                snapshot={},
            ),
        ),
        patch.object(worker, "promote_enable_recommendation", promote),
        patch.object(worker, "enqueue_recommendation_notification", send),
    ):
        counts = await worker.run_once(
            MagicMock(),
            now=datetime(2026, 7, 17, 12, tzinfo=UTC),
        )

    assert len(promotions) == expected_promotions
    assert sent[0]["auto_promoted"] is bool(expected_promotions)
    if expected_promotions:
        assert promotions[0]["requested_by"] == "auto_enable_recommendation_worker"
        assert promotions[0]["auto_mode"] is True
        assert counts["auto_promoted"] == 1
