from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from apps.api import bootstrap as api_bootstrap
from apps.api import deps as api_deps
from apps.api.main import app
from apps.api.services import health as health_service
from apps.notifier.events import TelegramEventType
from apps.notifier.formatter import TelegramMessageFormatter
from apps.notifier.sender import InMemoryDedupStore, TelegramSender
from apps.notifier.telegram import TelegramNotifier
from apps.worker.scan_service import WorkerScanService
from core.domain import DeliveryStatus, EntityType, ScanRunStatus, ScopePresence, TrackingMode
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
from tests.fixtures.worker_scan_helpers import (
    FakeScannerProvider,
    WorkerScanRow,
    seed_offer_with_rate,
    seed_worker_ad_graph,
)


def _load_system_sequence_contract() -> dict[str, object]:
    fixture_path = (
        Path(__file__).resolve().parents[1] / "fixtures" / "system_sequence_dashboard.json"
    )
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def _normalize_datetime_text(value: object) -> object:
    if not isinstance(value, str) or value == "":
        return value
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_runtime_ms(value: object) -> int:
    if isinstance(value, int) and value >= 0:
        return 0
    return 0


def _normalize_runtime_timestamp(value: object) -> str | None:
    if value in (None, ""):
        return None
    return "__runtime__"


@pytest.fixture
async def sequence_api_client(async_session_factory, monkeypatch):
    monkeypatch.setattr(api_deps, "get_session_factory", lambda: async_session_factory)
    monkeypatch.setattr(api_bootstrap, "get_session_factory", lambda: async_session_factory)

    async def fake_check_database_connection() -> bool:
        return True

    monkeypatch.setattr(health_service, "check_database_connection", fake_check_database_connection)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


def _normalize_sequence_payload(
    *,
    ads_payload: list[dict[str, object]],
    decisions_payload: list[dict[str, object]],
    scan_runs_payload: list[dict[str, object]],
) -> dict[str, object]:
    assert len(scan_runs_payload) == 1
    assert len(decisions_payload) == 1
    assert len(ads_payload) == 1

    scan_run_id = str(scan_runs_payload[0]["id"])

    return {
        "ads": [
            {
                "fb_ad_id": ads_payload[0]["fb_ad_id"],
                "campaign_name": ads_payload[0]["campaign_name"],
                "adset_name": ads_payload[0]["adset_name"],
                "ad_name": ads_payload[0]["ad_name"],
                "delivery_status": ads_payload[0]["delivery_status"],
                "tracking_mode": ads_payload[0]["tracking_mode"],
                "scope_presence": ads_payload[0]["scope_presence"],
                "last_seen_at": _normalize_datetime_text(ads_payload[0]["last_seen_at"]),
                "last_decision": ads_payload[0]["last_decision"],
                "resolved_cpa_usd": ads_payload[0]["resolved_cpa_usd"],
                "spend": ads_payload[0]["spend"],
                "clicks": ads_payload[0]["clicks"],
                "cpc": ads_payload[0]["cpc"],
                "leads": ads_payload[0]["leads"],
                "cost_per_lead": ads_payload[0]["cost_per_lead"],
                "registrations": ads_payload[0]["registrations"],
                "cost_per_registration": ads_payload[0]["cost_per_registration"],
                "deposits": ads_payload[0]["deposits"],
                "risk_band": ads_payload[0]["risk_band"],
                "fast_stop_state": ads_payload[0]["fast_stop_state"],
                "watch_reason": ads_payload[0]["watch_reason"],
                "queued_action_status": ads_payload[0]["queued_action_status"],
                "priority_score": ads_payload[0]["priority_score"],
            }
        ],
        "decisions": [
            {
                "id": "__decision__",
                "scan_run_id": "__scan_run__",
                "fb_ad_id": decisions_payload[0]["fb_ad_id"],
                "rule_id": decisions_payload[0]["rule_id"],
                "decision": decisions_payload[0]["decision"],
                "reason": decisions_payload[0]["reason"],
                "action_executed": decisions_payload[0]["action_executed"],
                "action_status": decisions_payload[0]["action_status"],
                "execution_state": decisions_payload[0]["execution_state"],
                "resolved_cpa_usd": decisions_payload[0]["resolved_cpa_usd"],
                "created_at": _normalize_datetime_text(decisions_payload[0]["created_at"]),
            }
        ],
        "scanRuns": [
            {
                "id": "__scan_run__",
                "browser_host_id": scan_runs_payload[0]["browser_host_id"],
                "profile_id": scan_runs_payload[0]["profile_id"],
                "status": scan_runs_payload[0]["status"],
                "pipeline_kind": scan_runs_payload[0]["pipeline_kind"],
                "trigger_source": scan_runs_payload[0]["trigger_source"],
                "target_fb_ad_ids": scan_runs_payload[0]["target_fb_ad_ids"],
                "rows_seen": scan_runs_payload[0]["rows_seen"],
                "rows_parsed": scan_runs_payload[0]["rows_parsed"],
                "collect_ms": _normalize_runtime_ms(scan_runs_payload[0]["collect_ms"]),
                "evaluate_ms": _normalize_runtime_ms(scan_runs_payload[0]["evaluate_ms"]),
                "persist_ms": _normalize_runtime_ms(scan_runs_payload[0]["persist_ms"]),
                "queue_ms": _normalize_runtime_ms(scan_runs_payload[0]["queue_ms"]),
                "action_jobs_enqueued": scan_runs_payload[0]["action_jobs_enqueued"],
                "scope_summary": {
                    "rows_seen": scan_runs_payload[0]["scope_summary"]["rows_seen"],
                    "rows_in_scope": scan_runs_payload[0]["scope_summary"]["rows_in_scope"],
                    "rows_not_seen_this_scan": scan_runs_payload[0]["scope_summary"][
                        "rows_not_seen_this_scan"
                    ],
                    "rows_out_of_scope_confirmed": scan_runs_payload[0]["scope_summary"][
                        "rows_out_of_scope_confirmed"
                    ],
                    "active_rows": scan_runs_payload[0]["scope_summary"]["active_rows"],
                    "paused_rows": scan_runs_payload[0]["scope_summary"]["paused_rows"],
                    "not_delivering_rows": scan_runs_payload[0]["scope_summary"][
                        "not_delivering_rows"
                    ],
                    "manual_blocked_rows": scan_runs_payload[0]["scope_summary"][
                        "manual_blocked_rows"
                    ],
                    "read_only_rows": scan_runs_payload[0]["scope_summary"]["read_only_rows"],
                    "unknown_rows": scan_runs_payload[0]["scope_summary"]["unknown_rows"],
                    "fb_ad_ids": scan_runs_payload[0]["scope_summary"]["fb_ad_ids"],
                },
                "error_message": scan_runs_payload[0]["error_message"],
                "started_at": _normalize_runtime_timestamp(scan_runs_payload[0]["started_at"]),
                "finished_at": _normalize_runtime_timestamp(scan_runs_payload[0]["finished_at"]),
            }
        ],
        "scanRunLinkCheck": {
            "decision_scan_run_matches": decisions_payload[0]["scan_run_id"] == scan_run_id,
        },
    }


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
        clicks=4,
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
        clicks=8,
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


# Проверяет сквозной сценарий: фейковый scan -> worker -> тестовая БД -> API возвращают единый контракт для UI.
@pytest.mark.asyncio
async def test_system_sequence_worker_to_api_contract(
    async_session_factory,
    sequence_api_client,
) -> None:
    contract = _load_system_sequence_contract()
    seed = await seed_worker_ad_graph(async_session_factory)
    await seed_offer_with_rate(
        async_session_factory,
        offer_code="offer-system-sequence",
        offer_name="DRC_CR2",
        cpa_usd=Decimal("5.00"),
        effective_from=datetime(2026, 3, 20, 11, 0, tzinfo=UTC),
    )

    provider = FakeScannerProvider(
        rows=[
            WorkerScanRow(
                campaign_scope_key=seed.campaign_scope_key,
                campaign_name=seed.campaign_name,
                adset_scope_key=seed.adset_scope_key,
                adset_name=seed.adset_name,
                fb_ad_id=seed.fb_ad_id,
                ad_name="DRC_CR2_CR001",
                delivery_status=DeliveryStatus.ACTIVE,
                tracking_mode=TrackingMode.TRACKED,
                scope_presence=ScopePresence.IN_SCOPE,
                spend=Decimal("0.38"),
                clicks=4,
                cpc=Decimal("0.11"),
                leads=0,
                cost_per_lead=None,
                registrations=0,
                cost_per_registration=None,
                deposits=0,
                captured_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
            )
        ]
    )
    service = WorkerScanService(
        async_session_factory=async_session_factory,
        scanner_provider=provider,
        auto_pause_enabled=True,
        auto_resume_enabled=False,
        observe_only_enabled=True,
    )

    result = await service.run_once(
        profile_id=seed.profile_id,
        browser_host_name=seed.browser_host_name,
    )
    ads_response = await sequence_api_client.get("/ads")
    decisions_response = await sequence_api_client.get("/decisions")
    scan_runs_response = await sequence_api_client.get("/scan-runs")

    assert result.status == ScanRunStatus.SUCCEEDED
    assert ads_response.status_code == 200
    assert decisions_response.status_code == 200
    assert scan_runs_response.status_code == 200

    normalized = _normalize_sequence_payload(
        ads_payload=ads_response.json(),
        decisions_payload=decisions_response.json(),
        scan_runs_payload=scan_runs_response.json(),
    )

    assert normalized["ads"] == contract["ads"]
    assert normalized["decisions"] == contract["decisions"]
    assert normalized["scanRuns"] == contract["scanRuns"]
    assert normalized["scanRunLinkCheck"] == {"decision_scan_run_matches": True}
