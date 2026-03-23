from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from core.domain import DecisionType, DeliveryStatus, ScopePresence, TrackingMode
from core.rules.types import RulePercentages
from core.scanner import (
    ScannedAdRow,
    ScannerPolicyFlags,
    build_adset_scope_key,
    build_campaign_scope_key,
    build_scope_summary,
    evaluate_scanned_row,
    normalize_scope_fragment,
    to_metrics_snapshot,
)


def _build_row(
    delivery_status: DeliveryStatus = DeliveryStatus.ACTIVE,
    tracking_mode: TrackingMode = TrackingMode.TRACKED,
    scope_presence: ScopePresence = ScopePresence.IN_SCOPE,
) -> ScannedAdRow:
    campaign_scope_key = build_campaign_scope_key("Кампания 1", "Аккаунт 1")
    return ScannedAdRow(
        fb_ad_id="ad-1",
        campaign_scope_key=campaign_scope_key,
        adset_scope_key=build_adset_scope_key("Адсет 1", campaign_scope_key),
        campaign_name="Кампания 1",
        adset_name="Адсет 1",
        ad_name="Объявление 1",
        delivery_status=delivery_status,
        tracking_mode=tracking_mode,
        scope_presence=scope_presence,
        spend=Decimal("0.38"),
        clicks=4,
        cpc=Decimal("0.11"),
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        account_name="Аккаунт 1",
    )


# Проверяет, что сканер переводит нормализованную строку в доменный снимок метрик без потерь.
def test_to_metrics_snapshot_maps_row_fields() -> None:
    row = _build_row()

    snapshot = to_metrics_snapshot(row)

    assert snapshot.spend == Decimal("0.38")
    assert snapshot.clicks == 4
    assert snapshot.cpc == Decimal("0.11")
    assert snapshot.leads == 0


# Проверяет, что сводка scope считает статусы и флаги строки по текущему набору объявлений.
def test_build_scope_summary_counts_rows_by_status() -> None:
    rows = (
        _build_row(DeliveryStatus.ACTIVE, TrackingMode.TRACKED, ScopePresence.IN_SCOPE),
        _build_row(
            DeliveryStatus.PAUSED, TrackingMode.MANUAL_BLOCK, ScopePresence.NOT_SEEN_THIS_SCAN
        ),
        _build_row(
            DeliveryStatus.NOT_DELIVERING,
            TrackingMode.READ_ONLY,
            ScopePresence.OUT_OF_SCOPE_CONFIRMED,
        ),
    )

    summary = build_scope_summary(rows, scanned_at=datetime(2026, 3, 20, 12, 5, tzinfo=UTC))

    assert summary.rows_seen == 3
    assert summary.rows_in_scope == 1
    assert summary.rows_not_seen_this_scan == 1
    assert summary.rows_out_of_scope_confirmed == 1
    assert summary.active_rows == 1
    assert summary.paused_rows == 1
    assert summary.not_delivering_rows == 1
    assert summary.manual_blocked_rows == 1
    assert summary.read_only_rows == 1
    assert summary.fb_ad_ids == ("ad-1", "ad-1", "ad-1")


# Проверяет, что scanner строит внутренние scope key из человеческих названий, а не из Facebook ID.
def test_build_scope_key_helpers_normalize_names() -> None:
    campaign_scope_key = build_campaign_scope_key("Кампания 1", "Аккаунт 1")
    adset_scope_key = build_adset_scope_key("Адсет 1", campaign_scope_key)

    assert campaign_scope_key == "campaign:аккаунт-1:кампания-1"
    assert adset_scope_key == "adset:campaign:аккаунт-1:кампания-1:адсет-1"
    assert normalize_scope_fragment("  Аккаунт / 1  ") == "аккаунт-1"


# Проверяет, что объявление со статусом «не показывается» всегда уходит в alert rejection.
def test_evaluate_scanned_row_marks_not_delivering_as_alert_rejection() -> None:
    row = _build_row(delivery_status=DeliveryStatus.NOT_DELIVERING)

    result = evaluate_scanned_row(
        row=row,
        resolved_cpa_usd=Decimal("5.00"),
        policy_flags=ScannerPolicyFlags(),
        clean_streak=2,
    )

    assert result.decision == DecisionType.ALERT_REJECTION
    assert result.reason == "Объявление не показывается и требует ручной проверки"


# Проверяет, что отсутствие CPA не позволяет scanner принять решение о pause или resume.
def test_evaluate_scanned_row_reports_insufficient_data_without_cpa() -> None:
    row = _build_row()

    result = evaluate_scanned_row(
        row=row,
        resolved_cpa_usd=None,
        policy_flags=ScannerPolicyFlags(),
        clean_streak=2,
    )

    assert result.decision == DecisionType.INSUFFICIENT_DATA
    assert result.reason == "Не удалось определить CPA объявления"


# Проверяет, что policy block переводит объявление в SKIPPED_BY_POLICY и не запускает pause-логику.
def test_evaluate_scanned_row_skips_blocked_ad() -> None:
    row = _build_row()

    result = evaluate_scanned_row(
        row=row,
        resolved_cpa_usd=Decimal("5.00"),
        policy_flags=ScannerPolicyFlags(is_blocked=True),
        clean_streak=2,
    )

    assert result.decision == DecisionType.SKIPPED_BY_POLICY
    assert result.reason == "Объявление заблокировано политикой"


# Проверяет, что при превышении порогов scanner возвращает WOULD_PAUSE с русской причиной и списком причин.
def test_evaluate_scanned_row_detects_pause_reasons() -> None:
    row = _build_row()

    result = evaluate_scanned_row(
        row=row,
        resolved_cpa_usd=Decimal("5.00"),
        policy_flags=ScannerPolicyFlags(),
        clean_streak=2,
    )

    assert result.decision == DecisionType.WOULD_PAUSE
    assert result.reason == "Клик превысил допустимую долю CPA"
    assert "Клик превысил допустимую долю CPA" in result.stop_reasons


# Проверяет, что scanner останавливает объявление по накопленному расходу, даже если первого клика ещё не было.
def test_evaluate_scanned_row_detects_click_stage_spend_overrun() -> None:
    campaign_scope_key = build_campaign_scope_key("Кампания 1", "Аккаунт 1")
    row = ScannedAdRow(
        fb_ad_id="ad-1",
        campaign_scope_key=campaign_scope_key,
        adset_scope_key=build_adset_scope_key("Адсет 1", campaign_scope_key),
        campaign_name="Кампания 1",
        adset_name="Адсет 1",
        ad_name="Объявление 1",
        delivery_status=DeliveryStatus.ACTIVE,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        spend=Decimal("0.16"),
        clicks=0,
        cpc=None,
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        account_name="Аккаунт 1",
    )

    result = evaluate_scanned_row(
        row=row,
        resolved_cpa_usd=Decimal("5.00"),
        policy_flags=ScannerPolicyFlags(),
        clean_streak=2,
    )

    assert result.decision == DecisionType.WOULD_PAUSE
    assert result.reason == "Расход уже превысил порог клика без самого клика"
    assert "Расход уже превысил порог клика без самого клика" in result.stop_reasons


# Проверяет, что scanner использует кастомный lead-share, если он пришёл из runtime-правил.
def test_evaluate_scanned_row_respects_custom_lead_share() -> None:
    campaign_scope_key = build_campaign_scope_key("Кампания 1", "Аккаунт 1")
    row = ScannedAdRow(
        fb_ad_id="ad-1",
        campaign_scope_key=campaign_scope_key,
        adset_scope_key=build_adset_scope_key("Адсет 1", campaign_scope_key),
        campaign_name="Кампания 1",
        adset_name="Адсет 1",
        ad_name="Объявление 1",
        delivery_status=DeliveryStatus.ACTIVE,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        spend=Decimal("0.46"),
        clicks=8,
        cpc=Decimal("0.08"),
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        account_name="Аккаунт 1",
    )

    result = evaluate_scanned_row(
        row=row,
        resolved_cpa_usd=Decimal("5.00"),
        policy_flags=ScannerPolicyFlags(),
        clean_streak=2,
        percentages=RulePercentages(lead_share=Decimal("0.09")),
    )

    assert result.decision == DecisionType.WOULD_PAUSE
    assert result.reason == "Расход уже превысил порог лида без самого лида"


# Проверяет, что пауза может перейти в resume только при явном feature flag и двух чистых сканах подряд.
def test_evaluate_scanned_row_allows_resume_only_with_flag() -> None:
    campaign_scope_key = build_campaign_scope_key("Кампания 1", "Аккаунт 1")
    row = ScannedAdRow(
        fb_ad_id="ad-1",
        campaign_scope_key=campaign_scope_key,
        adset_scope_key=build_adset_scope_key("Адсет 1", campaign_scope_key),
        campaign_name="Кампания 1",
        adset_name="Адсет 1",
        ad_name="Объявление 1",
        delivery_status=DeliveryStatus.PAUSED,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        spend=Decimal("0.20"),
        clicks=2,
        cpc=Decimal("0.05"),
        leads=1,
        cost_per_lead=Decimal("0.20"),
        registrations=1,
        cost_per_registration=Decimal("0.20"),
        deposits=0,
        last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        account_name="Аккаунт 1",
    )

    no_resume_result = evaluate_scanned_row(
        row=row,
        resolved_cpa_usd=Decimal("5.00"),
        policy_flags=ScannerPolicyFlags(auto_resume_enabled=False),
        clean_streak=2,
    )
    resume_result = evaluate_scanned_row(
        row=row,
        resolved_cpa_usd=Decimal("5.00"),
        policy_flags=ScannerPolicyFlags(
            auto_resume_enabled=True,
            resume_owned_by_system=True,
        ),
        clean_streak=2,
    )

    assert no_resume_result.decision == DecisionType.NO_ACTION
    assert resume_result.decision == DecisionType.WOULD_RESUME
    assert resume_result.reason == "Объявление снова безопасно для запуска"


# Проверяет, что авторезюм не включает объявления, которые не были поставлены на паузу самой системой.
def test_evaluate_scanned_row_blocks_resume_without_system_pause_source() -> None:
    campaign_scope_key = build_campaign_scope_key("Кампания 1", "Аккаунт 1")
    row = ScannedAdRow(
        fb_ad_id="ad-1",
        campaign_scope_key=campaign_scope_key,
        adset_scope_key=build_adset_scope_key("Адсет 1", campaign_scope_key),
        campaign_name="Кампания 1",
        adset_name="Адсет 1",
        ad_name="Объявление 1",
        delivery_status=DeliveryStatus.PAUSED,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        spend=Decimal("0.20"),
        clicks=2,
        cpc=Decimal("0.05"),
        leads=1,
        cost_per_lead=Decimal("0.20"),
        registrations=1,
        cost_per_registration=Decimal("0.20"),
        deposits=0,
        last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        account_name="Аккаунт 1",
    )

    result = evaluate_scanned_row(
        row=row,
        resolved_cpa_usd=Decimal("5.00"),
        policy_flags=ScannerPolicyFlags(auto_resume_enabled=True),
        clean_streak=2,
    )

    assert result.decision == DecisionType.KEPT_PAUSED_BY_VIABILITY
    assert (
        result.reason
        == "Автовключение разрешено только для объявлений, поставленных на паузу системой"
    )


# Проверяет, что paused-объявление сначала накапливает чистые сканы после автопаузы и не уходит в NO_ACTION.
def test_evaluate_scanned_row_keeps_safe_paused_ad_in_resume_warmup() -> None:
    campaign_scope_key = build_campaign_scope_key("Кампания 1", "Аккаунт 1")
    row = ScannedAdRow(
        fb_ad_id="ad-1",
        campaign_scope_key=campaign_scope_key,
        adset_scope_key=build_adset_scope_key("Адсет 1", campaign_scope_key),
        campaign_name="Кампания 1",
        adset_name="Адсет 1",
        ad_name="Объявление 1",
        delivery_status=DeliveryStatus.PAUSED,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        spend=Decimal("0.20"),
        clicks=2,
        cpc=Decimal("0.05"),
        leads=1,
        cost_per_lead=Decimal("0.20"),
        registrations=1,
        cost_per_registration=Decimal("0.20"),
        deposits=0,
        last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        account_name="Аккаунт 1",
    )

    result = evaluate_scanned_row(
        row=row,
        resolved_cpa_usd=Decimal("5.00"),
        policy_flags=ScannerPolicyFlags(
            auto_resume_enabled=True,
            resume_owned_by_system=True,
        ),
        clean_streak=1,
    )

    assert result.decision == DecisionType.KEPT_PAUSED_BY_VIABILITY
    assert result.reason == "Недостаточно чистых сканов подряд для безопасного включения"


# Проверяет, что без двух чистых сканов подряд включение обратно не происходит даже при включенном feature flag.
def test_evaluate_scanned_row_blocks_resume_without_clean_streak() -> None:
    row = _build_row(delivery_status=DeliveryStatus.PAUSED)

    result = evaluate_scanned_row(
        row=row,
        resolved_cpa_usd=Decimal("5.00"),
        policy_flags=ScannerPolicyFlags(
            auto_resume_enabled=True,
            resume_owned_by_system=True,
        ),
        clean_streak=1,
    )

    assert result.decision == DecisionType.KEPT_PAUSED_BY_VIABILITY
    assert result.reason == "Объявление остается на паузе — метрики всё ещё нарушают стоп-правила"
