"""Enable recommendation persistence and durable notification ordering."""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import apps.enable_recommendation_worker.main as worker
from core.enable_reco.analyzer import RecommendationDecision


def _candidate() -> worker.CandidateRow:
    return worker.CandidateRow(
        ad_id=uuid.uuid4(),
        fb_ad_id="123456",
        ad_name="Test Ad",
        campaign_name="Test Campaign",
        adset_name="Test AdSet",
        alert_state="disabled",
        last_transition_at=datetime(2026, 6, 1, tzinfo=UTC),
        snoozed_until=None,
        offer_code="CR2",
        cpa_threshold=None,
        ad_account_id="123",
        offer_currency="USD",
        open_state_token=uuid.uuid4(),
    )


def _decision() -> RecommendationDecision:
    return RecommendationDecision(
        recommend=True,
        level="warning",
        skip_reason=None,
        snapshot={},
    )


@pytest.mark.asyncio
async def test_enqueue_notification_returns_outbox_acceptance() -> None:
    notification_id = uuid.uuid4()
    notify = AsyncMock(return_value=True)
    with patch.object(worker, "notify_owners_in_transaction", notify):
        accepted = await worker.enqueue_recommendation_notification(
            MagicMock(),
            candidate=_candidate(),
            decision=_decision(),
            recommendation_id=notification_id,
        )

    assert accepted is True
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["dedupe_key"] == (f"enable-recommendation:{notification_id}")
    assert notify.await_args.kwargs.get("dedupe_ttl_seconds") is None


@pytest.mark.asyncio
async def test_enqueue_notification_reports_no_eligible_delivery() -> None:
    with patch.object(
        worker,
        "notify_owners_in_transaction",
        AsyncMock(return_value=False),
    ):
        accepted = await worker.enqueue_recommendation_notification(
            MagicMock(),
            candidate=_candidate(),
            decision=_decision(),
            recommendation_id=uuid.uuid4(),
        )

    assert accepted is False


async def _run_once_with_notification_result(
    notification_result: bool,
) -> tuple[dict[str, int], list[str]]:
    calls: list[str] = []

    async def insert(*args, **kwargs):
        calls.append("insert")
        return uuid.uuid4()

    async def enqueue(*args, **kwargs):
        calls.append("enqueue")
        return notification_result

    with (
        patch.object(worker, "load_scanning_enabled", AsyncMock(return_value=True)),
        patch.object(worker, "load_auto_enable_recommendations", AsyncMock(return_value=False)),
        patch.object(worker, "fetch_candidates", AsyncMock(return_value=[_candidate()])),
        patch.object(
            worker,
            "resolve_account_currencies",
            AsyncMock(return_value=MagicMock(currencies={"123": "USD"})),
        ),
        patch.object(worker, "fetch_metrics_since", AsyncMock(return_value=[])),
        patch.object(worker, "insert_recommendation", insert),
        patch.object(worker, "enqueue_recommendation_notification", enqueue),
        patch.object(worker, "should_recommend", return_value=_decision()),
    ):
        counts = await worker.run_once(MagicMock())
    return counts, calls


@pytest.mark.asyncio
async def test_outbox_rejection_keeps_persisted_recommendation_visible() -> None:
    counts, calls = await _run_once_with_notification_result(False)

    assert calls == ["insert", "enqueue"]
    assert counts["recommendations"] == 1
    assert counts["alerts_sent"] == 0
    assert counts["send_failed"] == 1


@pytest.mark.asyncio
async def test_outbox_acceptance_counts_alert() -> None:
    counts, calls = await _run_once_with_notification_result(True)

    assert calls == ["insert", "enqueue"]
    assert counts["recommendations"] == 1
    assert counts["alerts_sent"] == 1
    assert "send_failed" not in counts


def test_run_once_has_no_redis_or_telegram_gate() -> None:
    parameters = inspect.signature(worker.run_once).parameters
    source = inspect.getsource(worker)

    assert "redis_client" not in parameters
    assert "tg_client" not in parameters
    assert "enable_reco:last:" not in source
    assert "is_recently_recommended" not in source
    assert "mark_recommended" not in source
