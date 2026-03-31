# -*- coding: utf-8 -*-
"""Рендеринг Telegram-сообщений: форматирование алертов с inline-кнопками."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from core.domain import AlertStage, AlertState, EnableRecommendationLevel

# Человекочитаемые названия правил
_RULE_LABELS: dict[str, str] = {
    "cpc_stop": "Дорогой клик",
    "cpl_stop": "Дорогой лид",
    "cpr_stop": "Дорогая рега",
    "regs_no_dep_stop": "Реги без депозитов",
    "spend_no_dep_range": "Расход без депа",
    "spend_with_dep_range": "Расход с депом",
    "early_outbound_ctr_signal": "Слабый CTR исходящих кликов",
    "early_lpv_ratio_signal": "Слабая доходимость до лендинга",
    "early_cost_per_lpv_signal": "Дорогой просмотр лендинга",
}

_NEUTRAL_ENABLE_RECOMMENDATION_REASON_TITLE = "Нет блокирующих сигналов"
_NEUTRAL_ENABLE_RECOMMENDATION_REASON_TEXT = "По текущим правилам блокирующих сигналов нет."
_LEGACY_OK_ENABLE_RECOMMENDATION_REASON_TITLE = "Метрики в норме"
_LEGACY_OK_ENABLE_RECOMMENDATION_REASON_TEXT = "Объявление снова проходит по текущим правилам."
_GENERIC_ENABLE_RECOMMENDATION_REASON_TITLES = {
    _LEGACY_OK_ENABLE_RECOMMENDATION_REASON_TITLE,
    "Строгая проверка пройдена",
}
_GENERIC_ENABLE_RECOMMENDATION_REASON_TEXTS = {
    _LEGACY_OK_ENABLE_RECOMMENDATION_REASON_TEXT,
    "Есть подтверждённые конверсии, и объявление проходит строгую проверку на включение.",
}


def _rule_label(code: str) -> str:
    return _RULE_LABELS.get(code, code)


def rule_label(code: str) -> str:
    """Возвращает человекочитаемое название правила."""
    return _rule_label(code)


def build_ad_identity_lines(
    *,
    campaign_name: str | None,
    adset_name: str | None,
    ad_name: str,
    fb_ad_id: str | None = None,
) -> list[str]:
    """Строит иерархию campaign -> adset -> ad для Telegram-сообщения."""
    lines: list[str] = []
    if campaign_name:
        lines.append(f"📁 {html.escape(campaign_name)}")
    if adset_name:
        lines.append(f"  └ {html.escape(adset_name)}")
    if campaign_name or adset_name:
        lines.append(f"  └ 📢 <b>{html.escape(ad_name)}</b>")
    else:
        lines.append(f"📢 <b>{html.escape(ad_name)}</b>")
    if fb_ad_id:
        lines.append(f"🆔 <code>{html.escape(fb_ad_id)}</code>")
    return lines


def build_diagnosis_lines(
    *,
    reason_title: str | None,
    reason_text: str | None,
    matched_rule_codes: list[str] | None = None,
    rule_summaries: list[str] | None = None,
) -> list[str]:
    """Формирует блок диагностики с причиной и пороговыми деталями."""
    lines: list[str] = []
    if reason_title:
        lines.append(f"🧭 <b>{html.escape(reason_title)}</b>")
    if reason_text:
        lines.append(f"Причина: {html.escape(reason_text)}")

    summaries = [str(item).strip() for item in (rule_summaries or []) if str(item).strip()]
    if summaries:
        lines.append("📏 Пороговые детали:")
        for summary in summaries:
            lines.append(f"• {html.escape(summary)}")
    elif matched_rule_codes:
        labels = ", ".join(_rule_label(code) for code in matched_rule_codes if code)
        if labels:
            lines.append(f"🎯 Сигналы: {html.escape(labels)}")

    return lines


def build_metric_lines(metrics_json: dict[str, Any]) -> list[str]:
    """Формирует компактные строки метрик средней плотности."""
    metrics = metrics_json or {}
    lines: list[str] = []

    spend = metrics.get("spend")
    if spend is not None:
        lines.append(f"💰 Расход: <b>${spend}</b>")

    traffic_parts: list[str] = []
    cpc = metrics.get("cpc")
    clicks = metrics.get("clicks")
    if cpc is not None:
        traffic_parts.append(f"CPC: ${cpc}")
    if clicks is not None and (clicks > 0 or cpc is not None):
        traffic_parts.append(f"Кликов: {clicks}")

    outbound_clicks = metrics.get("outbound_clicks")
    outbound_ctr = metrics.get("outbound_ctr")
    if not traffic_parts and (outbound_clicks is not None or outbound_ctr is not None):
        if outbound_clicks is not None and (outbound_clicks > 0 or outbound_ctr is not None):
            traffic_parts.append(f"Исх. клики: {outbound_clicks}")
        if outbound_ctr is not None:
            traffic_parts.append(f"CTR исх.: {outbound_ctr}%")

    lpv = metrics.get("landing_page_views")
    cost_per_lpv = metrics.get("cost_per_landing_page_view")
    if not traffic_parts and (lpv is not None or cost_per_lpv is not None):
        if lpv is not None and (lpv > 0 or cost_per_lpv is not None):
            traffic_parts.append(f"LPV: {lpv}")
        if cost_per_lpv is not None:
            traffic_parts.append(f"Цена LPV: ${cost_per_lpv}")

    if traffic_parts:
        lines.append(f"🖱 {' · '.join(traffic_parts)}")

    lead_parts: list[str] = []
    leads = metrics.get("leads")
    cpl = metrics.get("cost_per_lead")
    if leads is not None and (leads > 0 or cpl is not None):
        lead_parts.append(f"Лидов: {leads}")
    if cpl is not None:
        lead_parts.append(f"CPL: ${cpl}")

    regs = metrics.get("registrations")
    cpr = metrics.get("cost_per_registration")
    if regs is not None or cpr is not None:
        if regs is not None and (regs > 0 or cpr is not None):
            lead_parts.append(f"Реги: {regs}")
        if cpr is not None:
            lead_parts.append(f"CPR: ${cpr}")

    deps = metrics.get("deposits")
    if deps is not None and deps > 0:
        lead_parts.append(f"Депозитов: {deps}")

    if lead_parts:
        lines.append(f"📋 {' · '.join(lead_parts)}")

    diagnostics = metrics.get("traffic_diagnostics")
    if isinstance(diagnostics, dict):
        summary_text = str(diagnostics.get("summary_text") or "").strip()
        highlighted_lines: list[str] = []
        for key, icon, title in (
            ("cpm", "📈", "CPM"),
            ("frequency", "🔁", "Частота"),
        ):
            payload = diagnostics.get(key)
            if not isinstance(payload, dict):
                continue
            if str(payload.get("status") or "").lower() not in {"elevated", "critical"}:
                continue
            text = str(payload.get("text") or "").strip()
            if text:
                highlighted_lines.append(f"{icon} {title}: {html.escape(text)}")
        if highlighted_lines:
            if summary_text:
                lines.append(f"🧠 {html.escape(summary_text)}")
            lines.extend(highlighted_lines)

    return lines


def normalize_enable_recommendation_reason(
    *,
    recommendation_level: EnableRecommendationLevel,
    reason_title: str | None,
    reason_text: str | None,
) -> tuple[str | None, str | None]:
    """Убирает вводящий в заблуждение позитивный дефолт для generic OK-рекомендаций."""
    if str(recommendation_level).upper() != "OK":
        return reason_title, reason_text

    normalized_title = reason_title
    normalized_text = reason_text
    if normalized_title is None or normalized_title in _GENERIC_ENABLE_RECOMMENDATION_REASON_TITLES:
        normalized_title = _NEUTRAL_ENABLE_RECOMMENDATION_REASON_TITLE
    if normalized_text is None or normalized_text in _GENERIC_ENABLE_RECOMMENDATION_REASON_TEXTS:
        normalized_text = _NEUTRAL_ENABLE_RECOMMENDATION_REASON_TEXT
    return normalized_title, normalized_text


@dataclass(slots=True, frozen=True)
class TelegramAlertItem:
    """Одно объявление в TG-сообщении."""

    snapshot_id: str
    fb_ad_id: str
    ad_name: str
    campaign_name: str
    adset_name: str
    offer_code: str | None
    stage: AlertStage
    alert_state: AlertState
    matched_rule_codes: list[str]
    reason_title: str | None
    reason_text: str | None
    metrics_json: dict[str, Any]


@dataclass(slots=True, frozen=True)
class TelegramOutgoingMessage:
    """Готовое к отправке сообщение."""

    text: str
    reply_markup: dict | None


@dataclass(slots=True, frozen=True)
class TelegramEnableRecommendationItem:
    """Данные рекомендации на включение для Telegram."""

    event_id: str
    fb_ad_id: str
    ad_name: str
    delivery_status: str
    recommendation_level: EnableRecommendationLevel
    matched_rule_codes: list[str]
    reason_title: str | None
    reason_text: str | None
    metrics_json: dict[str, Any]
    campaign_name: str | None = None
    adset_name: str | None = None


def render_alert_message(
    *,
    stage: AlertStage,
    items: list[TelegramAlertItem],
    snooze_note: str | None = None,
) -> TelegramOutgoingMessage:
    """Формирует TG-сообщение (одно на объявление) с кнопкой «Отключить» для STOP."""
    lines: list[str] = []
    keyboard: list[list[dict[str, str]]] = []

    for item in items:
        if stage == AlertStage.STOP:
            lines.append("🛑 <b>СТОП</b>")
        elif stage == AlertStage.EARLY_SIGNAL:
            lines.append("🔎 <b>Ранний сигнал</b>")
        else:
            lines.append("⚠️ <b>Предупреждение</b>")

        lines.append("")

        lines.extend(
            build_ad_identity_lines(
                campaign_name=item.campaign_name,
                adset_name=item.adset_name,
                ad_name=item.ad_name,
                fb_ad_id=item.fb_ad_id,
            )
        )

        lines.append("")

        rule_summaries = item.metrics_json.get("rule_summaries")
        if not isinstance(rule_summaries, list):
            rule_summaries = None
        diagnosis_lines = build_diagnosis_lines(
            reason_title=item.reason_title,
            reason_text=item.reason_text,
            matched_rule_codes=item.matched_rule_codes,
            rule_summaries=rule_summaries,
        )
        if diagnosis_lines:
            lines.extend(diagnosis_lines)
            lines.append("")

        metric_lines = build_metric_lines(item.metrics_json)
        if metric_lines:
            lines.append("📌 <b>Ключевые метрики</b>")
            lines.extend(metric_lines)
            lines.append("")

        if stage == AlertStage.STOP:
            lines.append("ℹ️ Следующее действие: ждать завершения цепочки STOP.")
        else:
            lines.append(
                "ℹ️ Следующее действие: задача на отключение создаётся отдельной цепочкой в STOP."
            )

        if snooze_note:
            lines.append(html.escape(snooze_note))

        if stage in {AlertStage.WARNING, AlertStage.EARLY_SIGNAL}:
            keyboard.append(
                [
                    {
                        "text": f"🛑 Создать задачу: {item.ad_name[:24].rstrip()}",
                        "callback_data": f"disable:{item.snapshot_id}",
                    }
                ]
            )
            keyboard.append(
                [
                    {
                        "text": "⏸ 30м",
                        "callback_data": f"snooze:{item.snapshot_id}:30",
                    },
                    {
                        "text": "⏸ 1ч",
                        "callback_data": f"snooze:{item.snapshot_id}:60",
                    },
                    {
                        "text": "⏸ 2ч",
                        "callback_data": f"snooze:{item.snapshot_id}:120",
                    },
                ]
            )
        else:
            lines.append("⚡ Авто-отключение уже запущено.")

    return TelegramOutgoingMessage(
        text="\n".join(lines).strip(),
        reply_markup={"inline_keyboard": keyboard} if keyboard else None,
    )


def render_enable_recommendation_message(
    *,
    item: TelegramEnableRecommendationItem,
) -> TelegramOutgoingMessage:
    """Формирует Telegram-сообщение с рекомендацией на включение."""
    lines: list[str] = []
    metrics = item.metrics_json or {}
    reason_title, reason_text = normalize_enable_recommendation_reason(
        recommendation_level=item.recommendation_level,
        reason_title=item.reason_title,
        reason_text=item.reason_text,
    )

    if item.recommendation_level == EnableRecommendationLevel.WARNING:
        lines.append("⚠️ <b>Рекомендация требует проверки перед включением</b>")
    elif item.recommendation_level == EnableRecommendationLevel.EARLY_SIGNAL:
        lines.append("🔎 <b>Ранний сигнал восстановления</b>")
    else:
        lines.append("ℹ️ <b>Нет блокирующих сигналов</b>")
    lines.append("")
    lines.extend(
        build_ad_identity_lines(
            campaign_name=item.campaign_name,
            adset_name=item.adset_name,
            ad_name=item.ad_name,
            fb_ad_id=item.fb_ad_id,
        )
    )
    lines.append("")

    rule_summaries = metrics.get("rule_summaries")
    if not isinstance(rule_summaries, list):
        rule_summaries = None
    diagnosis_lines = build_diagnosis_lines(
        reason_title=reason_title,
        reason_text=reason_text,
        matched_rule_codes=item.matched_rule_codes,
        rule_summaries=rule_summaries,
    )
    if diagnosis_lines:
        lines.extend(diagnosis_lines)
        lines.append("")

    metric_lines = build_metric_lines(metrics)
    if metric_lines:
        lines.append("📌 <b>Ключевые метрики</b>")
        lines.extend(metric_lines)
        lines.append("")

    lines.append(f"📡 Статус доставки Meta: <b>{html.escape(item.delivery_status)}</b>")
    if item.recommendation_level == EnableRecommendationLevel.OK:
        lines.append("ℹ️ Следующее действие: создать задачу на включение из этого сообщения.")
    else:
        lines.append("ℹ️ Следующее действие: проверьте сигнал вручную перед включением.")

    return TelegramOutgoingMessage(
        text="\n".join(lines).strip(),
        reply_markup={
            "inline_keyboard": [
                [
                    {
                        "text": "✅ Создать задачу на включение",
                        "callback_data": f"enable_reco:task:{item.event_id}",
                    }
                ]
            ]
        },
    )


def _render_state(state: AlertState) -> str:
    """Человекочитаемый статус."""
    mapping = {
        AlertState.CLAIMED: "🔄 в работе",
        AlertState.DISABLED: "✅ выключено",
        AlertState.EARLY_SIGNAL_SENT: "🔎 ранний сигнал",
        AlertState.STOP_SENT: "⏳ ждёт подтверждения",
        AlertState.WARNING_SENT: "⚠️ предупреждение",
    }
    return mapping.get(state, "обычный")


def _button_text(item: TelegramAlertItem) -> str:
    """Текст на inline-кнопке."""
    short = item.ad_name[:28].rstrip()
    if item.alert_state == AlertState.CLAIMED:
        return f"🔄 В работе: {short}"
    if item.alert_state == AlertState.DISABLED:
        return f"✅ Выключено: {short}"
    return f"🛑 Отключить: {short}"


def _button_callback(item: TelegramAlertItem) -> str:
    """Callback data для inline-кнопки."""
    if item.alert_state in {AlertState.CLAIMED, AlertState.DISABLED}:
        return f"noop:{item.snapshot_id}"
    return f"disable:{item.snapshot_id}"
