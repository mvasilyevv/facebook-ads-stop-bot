# -*- coding: utf-8 -*-
"""Сценарные тесты observer-пайплайна: bulk parse -> evaluate -> alert -> snapshot."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

from core.diagnostics import (
    AdQualityDiagnostics,
    build_ad_quality_diagnostics,
    build_diagnostics_context_text,
    compute_cpm_baselines_by_offer,
)
from core.domain import AlertStage, AlertState
from core.observer.service import (
    AlertCandidate,
    _compose_reason_text,
    build_metrics_json,
    evaluate_row,
    resolve_offer_code,
)
from core.observer.state_machine import _state_for_emitted_stage, resolve_transition
from core.scanner.models import ScannedAdRow
from core.telegram.renderer import TelegramAlertItem, render_alert_message

# Оригинальные функции парсинга из core/scanner/parser.py (удалён при миграции)
_MONEY_RE = re.compile(r"[\d]+[.,]?\d*")


def _detect_delivery_status(text: str) -> str:
    lowered = text.lower()
    if "active" in lowered or "активно" in lowered:
        return "ACTIVE"
    if "paused" in lowered or "пауза" in lowered:
        return "PAUSED"
    if "not delivering" in lowered or "не показывается" in lowered:
        return "NOT_DELIVERING"
    if "learning" in lowered or "обучение" in lowered:
        return "LEARNING"
    if "выкл" in lowered or "off" in lowered or "disabled" in lowered:
        return "OFF"
    return "UNKNOWN"


def _parse_money(text: str, default: Decimal) -> Decimal:
    if not text or text.strip() in ("\u2014", "-", "\u2013", "n/a", ""):
        return default
    cleaned = text.replace(",", ".").replace("\xa0", "").replace(" ", "")
    match = _MONEY_RE.search(cleaned)
    if not match:
        return default
    try:
        return Decimal(match.group())
    except Exception:
        return default


def _parse_money_or_none(text: str) -> Decimal | None:
    if not text or text.strip() in ("\u2014", "-", "\u2013", "n/a", ""):
        return None
    cleaned = text.replace(",", ".").replace("\xa0", "").replace(" ", "")
    match = _MONEY_RE.search(cleaned)
    if not match:
        return None
    try:
        return Decimal(match.group())
    except Exception:
        return None


def _parse_int_value(text: str) -> int:
    if not text or text.strip() in ("\u2014", "-", "\u2013", "n/a", ""):
        return 0
    cleaned = re.sub(r"[^\d]", "", text)
    return int(cleaned) if cleaned else 0


def _build_row_from_fields(fields: dict) -> ScannedAdRow | None:
    ad_name = fields.get("ad_name", "")
    if not ad_name or ad_name in ("\u2014", "-"):
        return None
    fb_ad_id = fields.get("_row_id", "")
    if not fb_ad_id or len(fb_ad_id) < 10:
        return None
    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name=fields.get("campaign_name", ""),
        adset_name=fields.get("adset_name", ""),
        ad_name=ad_name,
        delivery_status=_detect_delivery_status(fields.get("delivery_status", "")),
        spend=_parse_money(fields.get("spend", ""), Decimal("0")),
        budget=fields.get("budget", ""),
        reach=_parse_int_value(fields.get("reach", "")),
        impressions=_parse_int_value(fields.get("impressions", "")),
        clicks=_parse_int_value(fields.get("clicks", "")),
        cpc=_parse_money_or_none(fields.get("cpc", "")),
        ctr=_parse_money_or_none(fields.get("ctr", "")),
        cost_per_result=_parse_money_or_none(fields.get("cost_per_result", "")),
        cpm=_parse_money_or_none(fields.get("cpm", "")),
        frequency=_parse_money_or_none(fields.get("frequency", "")),
        leads=_parse_int_value(fields.get("leads", "")),
        cost_per_lead=_parse_money_or_none(fields.get("cost_per_lead", "")),
        registrations=_parse_int_value(fields.get("registrations", "")),
        cost_per_registration=_parse_money_or_none(fields.get("cost_per_registration", "")),
        deposits=_parse_int_value(fields.get("deposits", "")),
        outbound_clicks=_parse_int_value(fields.get("outbound_clicks", "")),
        outbound_ctr=_parse_money_or_none(fields.get("outbound_ctr", "")),
        landing_page_views=_parse_int_value(fields.get("landing_page_views", "")),
        cost_per_landing_page_view=_parse_money_or_none(
            fields.get("cost_per_landing_page_view", "")
        ),
        resolved_offer_code=None,
    )


def _parse_bulk_result(raw_rows: list[dict]) -> list[ScannedAdRow]:
    rows: list[ScannedAdRow] = []
    for fields in raw_rows:
        try:
            row = _build_row_from_fields(fields)
            if row is not None:
                rows.append(row)
        except Exception:
            continue
    return rows


@dataclass(slots=True, frozen=True)
class ScenarioResult:
    """Результат одного полного сценарного цикла observer."""

    parsed_rows: list[ScannedAdRow]
    row: ScannedAdRow
    offer_code: str | None
    evaluation: object
    diagnostics: AdQualityDiagnostics | None
    snapshot: dict[str, object]
    alert_candidate: AlertCandidate | None
    alert_message_text: str | None
    should_emit: bool


def _make_rule_config(**overrides):
    """Собирает минимальную конфигурацию правил как в OfferRuleConfig."""

    defaults = {
        "cpc_percent_enabled": True,
        "cpc_percent_stop": Decimal("2"),
        "cpl_percent_enabled": True,
        "cpl_percent_stop": Decimal("10"),
        "cpr_percent_enabled": True,
        "cpr_percent_stop": Decimal("20"),
        "regs_no_dep_enabled": True,
        "regs_no_dep_stop_count": 5,
        "spend_no_dep_enabled": True,
        "spend_no_dep_from_percent": Decimal("50"),
        "spend_no_dep_to_percent": Decimal("70"),
        "spend_with_dep_enabled": True,
        "spend_with_dep_from_percent": Decimal("70"),
        "spend_with_dep_to_percent": Decimal("90"),
        "early_outbound_ctr_signal_enabled": True,
        "early_outbound_ctr_signal_min_percent": Decimal("0.80"),
        "early_outbound_ctr_signal_min_spend_percent": Decimal("5"),
        "early_lpv_ratio_signal_enabled": True,
        "early_lpv_ratio_signal_min_percent": Decimal("60"),
        "early_lpv_ratio_signal_min_outbound_clicks": 5,
        "early_cost_per_lpv_signal_enabled": True,
        "early_cost_per_lpv_signal_percent_of_cpa": Decimal("5"),
        "early_cost_per_lpv_signal_min_views": 2,
        "frequency_elevated_threshold": Decimal("2"),
        "frequency_critical_threshold": Decimal("3"),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_offer_bundle(
    *,
    offer_code: str,
    cpa_amount: str | Decimal = "10.00",
    rule_overrides: dict[str, object] | None = None,
) -> tuple[dict[str, dict[str, object]], object]:
    """Строит offers-map в том же формате, что использует observer."""

    rule_config = _make_rule_config(**(rule_overrides or {}))
    offer = SimpleNamespace(
        id=uuid.uuid4(),
        code=offer_code,
        name=f"Оффер {offer_code}",
        cpa_amount=Decimal(cpa_amount),
        rule_config=rule_config,
    )
    offers = {offer_code.casefold(): {"offer": offer, "rule_config": rule_config}}
    return offers, rule_config


def _make_raw_row(
    *,
    row_id: str,
    offer_code: str = "DRC_CR2",
    campaign_name: str | None = None,
    adset_name: str = "Adset 1",
    ad_name: str | None = None,
    delivery_status: str = "Active",
    spend: str = "$0.00",
    clicks: str = "0",
    cpc: str = "-",
    outbound_clicks: str = "0",
    outbound_ctr: str = "-",
    landing_page_views: str = "0",
    cost_per_landing_page_view: str = "-",
    cpm: str = "-",
    frequency: str = "-",
    leads: str = "0",
    cost_per_lead: str = "-",
    registrations: str = "0",
    cost_per_registration: str = "-",
    deposits: str = "0",
) -> dict[str, str]:
    """Готовит raw-строку в формате bulk evaluate от Ads Manager."""

    return {
        "_row_id": row_id,
        "campaign_name": campaign_name or f"{offer_code} | Campaign",
        "adset_name": adset_name,
        "ad_name": ad_name or f"{offer_code}_CR015",
        "delivery_status": delivery_status,
        "spend": spend,
        "clicks": clicks,
        "cpc": cpc,
        "outbound_clicks": outbound_clicks,
        "outbound_ctr": outbound_ctr,
        "landing_page_views": landing_page_views,
        "cost_per_landing_page_view": cost_per_landing_page_view,
        "cpm": cpm,
        "frequency": frequency,
        "leads": leads,
        "cost_per_lead": cost_per_lead,
        "registrations": registrations,
        "cost_per_registration": cost_per_registration,
        "deposits": deposits,
    }


def _run_scenario(
    *,
    raw_rows: list[dict[str, str]],
    target_fb_ad_id: str,
    offer_code: str = "DRC_CR2",
    cpa_amount: str | Decimal = "10.00",
    warning_percent_of_stop: Decimal = Decimal("80"),
    stop_percent_of_base: Decimal = Decimal("100"),
    current_state: AlertState | None = AlertState.NORMAL,
    current_token: str | None = None,
    rule_overrides: dict[str, object] | None = None,
) -> ScenarioResult:
    """Прогоняет один сценарий через реальный parse/evaluate/FSM/render pipeline."""

    offers, rule_config = _make_offer_bundle(
        offer_code=offer_code,
        cpa_amount=cpa_amount,
        rule_overrides=rule_overrides,
    )
    parsed_rows = _parse_bulk_result(raw_rows)
    offer_codes_by_ad = {
        row.fb_ad_id: resolve_offer_code(row.ad_name, row.campaign_name, offers)
        for row in parsed_rows
    }
    cpm_baselines = compute_cpm_baselines_by_offer(
        [row for row in parsed_rows if offer_codes_by_ad.get(row.fb_ad_id)],
        offer_code_getter=lambda row: offer_codes_by_ad.get(row.fb_ad_id),
        cpm_getter=lambda row: row.cpm,
    )

    row = next(item for item in parsed_rows if item.fb_ad_id == target_fb_ad_id)
    resolved_offer_code = offer_codes_by_ad.get(row.fb_ad_id)

    evaluation = evaluate_row(
        row=row,
        offer_cpa=Decimal(cpa_amount),
        rule_config=rule_config,
        warning_percent_of_stop=warning_percent_of_stop,
        stop_percent_of_base=stop_percent_of_base,
    )
    diagnostics = build_ad_quality_diagnostics(
        cpm_value=row.cpm,
        cpm_baseline=cpm_baselines.get(resolved_offer_code or ""),
        frequency_value=row.frequency,
        frequency_elevated_threshold=rule_config.frequency_elevated_threshold,
        frequency_critical_threshold=rule_config.frequency_critical_threshold,
    )
    next_state, token, should_emit = resolve_transition(
        current_state=current_state,
        current_token=current_token,
        next_stage=evaluation.stage,
    )

    snapshot = {
        "fb_ad_id": row.fb_ad_id,
        "campaign_name": row.campaign_name,
        "adset_name": row.adset_name,
        "ad_name": row.ad_name,
        "delivery_status": row.delivery_status,
        "offer_id": offers[offer_code.casefold()]["offer"].id,
        "resolved_offer_code": resolved_offer_code,
        "spend": row.spend,
        "clicks": row.clicks,
        "cpc": row.cpc,
        "outbound_clicks": row.outbound_clicks,
        "outbound_ctr": row.outbound_ctr,
        "landing_page_views": row.landing_page_views,
        "cost_per_landing_page_view": row.cost_per_landing_page_view,
        "cpm": row.cpm,
        "frequency": row.frequency,
        "leads": row.leads,
        "cost_per_lead": row.cost_per_lead,
        "registrations": row.registrations,
        "cost_per_registration": row.cost_per_registration,
        "deposits": row.deposits,
        "alert_state": next_state,
        "current_stage": evaluation.stage,
        "early_signal_rule_codes": [],
        "warning_rule_codes": evaluation.warning_rule_codes,
        "stop_rule_codes": evaluation.stop_rule_codes,
        "open_state_token": token,
    }

    alert_candidate = None
    alert_message_text = None
    if should_emit and evaluation.stage is not None:
        diagnostics_text = build_diagnostics_context_text(diagnostics)
        alert_candidate = AlertCandidate(
            snapshot_id=token or uuid.uuid4().hex,
            offer_id=offers[offer_code.casefold()]["offer"].id,
            fb_ad_id=row.fb_ad_id,
            ad_name=row.ad_name,
            campaign_name=row.campaign_name,
            adset_name=row.adset_name,
            offer_code=resolved_offer_code,
            offer_name=offers[offer_code.casefold()]["offer"].name,
            offer_cpa=str(offers[offer_code.casefold()]["offer"].cpa_amount),
            stage=evaluation.stage,
            matched_rule_codes=evaluation.matched_rule_codes,
            reason_title=evaluation.reason_title,
            reason_text=_compose_reason_text(evaluation.reason_text, diagnostics_text),
            metrics_json=build_metrics_json(
                row,
                rule_summaries=[hit.summary for hit in evaluation.matched_hits],
            ),
        )
        message = render_alert_message(
            stage=alert_candidate.stage,
            items=[
                TelegramAlertItem(
                    snapshot_id=alert_candidate.snapshot_id,
                    fb_ad_id=alert_candidate.fb_ad_id,
                    ad_name=alert_candidate.ad_name,
                    campaign_name=alert_candidate.campaign_name,
                    adset_name=alert_candidate.adset_name,
                    offer_code=alert_candidate.offer_code,
                    stage=alert_candidate.stage,
                    alert_state=_state_for_emitted_stage(alert_candidate.stage),
                    matched_rule_codes=alert_candidate.matched_rule_codes,
                    reason_title=alert_candidate.reason_title,
                    reason_text=alert_candidate.reason_text,
                    metrics_json=alert_candidate.metrics_json,
                )
            ],
        )
        alert_message_text = message.text

    return ScenarioResult(
        parsed_rows=parsed_rows,
        row=row,
        offer_code=resolved_offer_code,
        evaluation=evaluation,
        diagnostics=diagnostics,
        snapshot=snapshot,
        alert_candidate=alert_candidate,
        alert_message_text=alert_message_text,
        should_emit=should_emit,
    )


# Проверяем, что metrics_json приводит вложенные Decimal в diagnostics к JSON-совместимому виду.
def test_build_metrics_json_makes_traffic_diagnostics_json_safe():
    row = SimpleNamespace(
        spend=Decimal("12.34"),
        budget=None,
        reach=120,
        impressions=150,
        clicks=7,
        cpc=Decimal("1.7625"),
        ctr=Decimal("4.6667"),
        outbound_clicks=5,
        outbound_ctr=Decimal("3.3333"),
        landing_page_views=4,
        cost_per_result=None,
        cost_per_landing_page_view=Decimal("3.0850"),
        cpm=Decimal("14.1000"),
        frequency=Decimal("1.4000"),
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
    )

    metrics_json = build_metrics_json(
        row,
        traffic_diagnostics={
            "summary_text": "Трафик начал дорожать.",
            "cpm": {
                "status": "critical",
                "value": Decimal("14.1000"),
                "baseline": Decimal("10.2500"),
                "ratio_percent": Decimal("137.56"),
            },
        },
    )

    assert metrics_json["traffic_diagnostics"]["cpm"]["value"] == "14.1000"
    assert metrics_json["traffic_diagnostics"]["cpm"]["baseline"] == "10.2500"
    assert metrics_json["traffic_diagnostics"]["cpm"]["ratio_percent"] == "137.56"
    assert json.dumps(metrics_json)


# Проверяем полный сценарий стопа по расходу до лида с диагностикой частоты.
def test_scenario_click_guardrail_creates_stop_alert_and_snapshot():
    """Расход до лида должен перевести объявление в STOP и собрать человекочитаемый алерт."""

    result = _run_scenario(
        raw_rows=[
            _make_raw_row(
                row_id="120241979860890176",
                spend="$1.20",
                clicks="8",
                cpc="$0.06",
                outbound_clicks="8",
                outbound_ctr="1.30%",
                landing_page_views="7",
                cost_per_landing_page_view="$0.17",
                cpm="$12.00",
                frequency="3.20",
            )
        ],
        target_fb_ad_id="120241979860890176",
    )

    assert result.row.cpc == Decimal("0.06")
    assert result.evaluation.stage == AlertStage.STOP
    assert result.evaluation.matched_rule_codes == ["cpl_stop"]
    assert result.snapshot["alert_state"] == AlertState.STOP_SENT
    assert result.snapshot["current_stage"] == AlertStage.STOP
    assert result.alert_candidate is not None
    assert result.alert_candidate.metrics_json["rule_summaries"] == [
        "Расход 1.20 превысил стоп CPL 1.00 без лидов"
    ]
    assert "частота 3.20 при критической границе 3.00" in (result.alert_candidate.reason_text or "")
    assert "<b>STOP</b>" in (result.alert_message_text or "")
    assert "<b>Диагностика:</b>" in (result.alert_message_text or "")


# Проверяем что наличие лида подавляет более раннее правило клика.
def test_scenario_lead_stage_suppresses_click_rule_and_skips_alert():
    """При наличии лида правило клика больше не должно создавать алерт."""

    result = _run_scenario(
        raw_rows=[
            _make_raw_row(
                row_id="120241979860770176",
                spend="$0.50",
                clicks="1",
                cpc="$0.50",
                outbound_clicks="1",
                outbound_ctr="1.20%",
                landing_page_views="1",
                cost_per_landing_page_view="$0.50",
                leads="1",
                cost_per_lead="$0.50",
            )
        ],
        target_fb_ad_id="120241979860770176",
    )

    assert result.row.leads == 1
    assert result.evaluation.stage is None
    assert result.evaluation.matched_rule_codes == []
    assert result.snapshot["alert_state"] == AlertState.NORMAL
    assert result.snapshot["current_stage"] is None
    assert result.alert_candidate is None
    assert result.alert_message_text is None


# Проверяем приоритет правила "5 рег и 0 депов" над spend-range на той же стадии.
def test_scenario_regs_without_deposits_has_priority_over_spend_range():
    """На стадии регистраций стоп по количеству рег должен сработать раньше spend-range."""

    result = _run_scenario(
        raw_rows=[
            _make_raw_row(
                row_id="120241979860780176",
                spend="$6.00",
                clicks="20",
                cpc="$0.30",
                outbound_clicks="15",
                outbound_ctr="1.50%",
                landing_page_views="12",
                cost_per_landing_page_view="$0.50",
                leads="6",
                cost_per_lead="$1.00",
                registrations="5",
                cost_per_registration="$1.20",
            )
        ],
        target_fb_ad_id="120241979860780176",
    )

    assert result.evaluation.stage == AlertStage.STOP
    assert result.evaluation.matched_rule_codes == ["regs_no_dep_stop"]
    assert result.snapshot["stop_rule_codes"] == ["regs_no_dep_stop"]
    assert result.alert_candidate is not None
    assert result.alert_candidate.reason_title == "Реги без депозитов"
    assert "Регистрации: 5" in (result.alert_message_text or "")


# Проверяем что после первого депозита ранние ступени подавляются и остаётся только депозитная.
def test_scenario_deposit_stage_ignores_earlier_metrics_and_adds_cpm_context():
    """При наличии депозита observer должен стопать только по депозитной ступени."""

    raw_rows = [
        _make_raw_row(
            row_id="120241979860790176",
            spend="$8.00",
            clicks="4",
            cpc="$2.00",
            outbound_clicks="4",
            outbound_ctr="1.10%",
            landing_page_views="3",
            cost_per_landing_page_view="$2.67",
            cpm="$16.00",
            frequency="1.50",
            leads="1",
            cost_per_lead="$8.00",
            registrations="1",
            cost_per_registration="$8.00",
            deposits="1",
        ),
        _make_raw_row(
            row_id="120241979860800176",
            cpm="$10.00",
            frequency="1.20",
            clicks="5",
            cpc="$0.05",
            outbound_clicks="5",
            outbound_ctr="1.40%",
            landing_page_views="4",
            cost_per_landing_page_view="$0.10",
        ),
        _make_raw_row(
            row_id="120241979860810176",
            cpm="$10.00",
            frequency="1.30",
            clicks="5",
            cpc="$0.05",
            outbound_clicks="5",
            outbound_ctr="1.50%",
            landing_page_views="4",
            cost_per_landing_page_view="$0.10",
        ),
        _make_raw_row(
            row_id="120241979860820176",
            cpm="$10.00",
            frequency="1.40",
            clicks="5",
            cpc="$0.05",
            outbound_clicks="5",
            outbound_ctr="1.60%",
            landing_page_views="4",
            cost_per_landing_page_view="$0.10",
        ),
    ]

    result = _run_scenario(
        raw_rows=raw_rows,
        target_fb_ad_id="120241979860790176",
    )

    assert result.row.deposits == 1
    assert result.evaluation.stage == AlertStage.STOP
    assert result.evaluation.matched_rule_codes == ["spend_with_dep_range"]
    assert result.snapshot["stop_rule_codes"] == ["spend_with_dep_range"]
    assert result.alert_candidate is not None
    assert "CPM $16.00" in (result.alert_candidate.reason_text or "")
    assert "медианы оффера $10.00" in (result.alert_candidate.reason_text or "")


# Проверяем что CPM и Frequency остаются только диагностикой и не создают алерт сами по себе.
def test_scenario_diagnostics_only_do_not_create_alert():
    """Высокий CPM и частота должны попадать в диагностику без создания алерта."""

    raw_rows = [
        _make_raw_row(
            row_id="120241979860840176",
            spend="$0.08",
            clicks="8",
            cpc="$0.01",
            outbound_clicks="8",
            outbound_ctr="1.30%",
            landing_page_views="7",
            cost_per_landing_page_view="$0.01",
            cpm="$15.00",
            frequency="3.20",
        ),
        _make_raw_row(
            row_id="120241979860850176",
            spend="$0.08",
            clicks="8",
            cpc="$0.01",
            outbound_clicks="8",
            outbound_ctr="1.30%",
            landing_page_views="7",
            cost_per_landing_page_view="$0.01",
            cpm="$10.00",
            frequency="1.20",
        ),
        _make_raw_row(
            row_id="120241979860860176",
            spend="$0.08",
            clicks="8",
            cpc="$0.01",
            outbound_clicks="8",
            outbound_ctr="1.30%",
            landing_page_views="7",
            cost_per_landing_page_view="$0.01",
            cpm="$10.00",
            frequency="1.30",
        ),
        _make_raw_row(
            row_id="120241979860870176",
            spend="$0.08",
            clicks="8",
            cpc="$0.01",
            outbound_clicks="8",
            outbound_ctr="1.30%",
            landing_page_views="7",
            cost_per_landing_page_view="$0.01",
            cpm="$10.00",
            frequency="1.40",
        ),
    ]

    result = _run_scenario(
        raw_rows=raw_rows,
        target_fb_ad_id="120241979860840176",
    )

    assert result.evaluation.stage is None
    assert result.snapshot["alert_state"] == AlertState.NORMAL
    assert result.alert_candidate is None
    assert result.diagnostics is not None
    assert result.diagnostics.cpm.status == "critical"
    assert result.diagnostics.frequency.status == "critical"
    assert (
        result.diagnostics.summary_text
        == "И аукцион, и частота показывают ухудшение качества трафика."
    )
