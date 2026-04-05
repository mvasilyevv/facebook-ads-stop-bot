# -*- coding: utf-8 -*-
"""Рендеринг Telegram-сообщений: форматирование алертов с inline-кнопками."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from core.domain import AlertStage, AlertState, EnableRecommendationLevel

# Человекочитаемые названия правил
from core.rules.labels import RULE_LABELS as _RULE_LABELS

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


def build_ad_identity_lines(
    *,
    campaign_name: str | None,
    adset_name: str | None,
    ad_name: str,
    fb_ad_id: str | None = None,
    compact: bool = False,
) -> list[str]:
    """Строит иерархию campaign -> adset -> ad для Telegram-сообщения.

    compact=True: blockquote-обёртка, campaign › adset одной строкой, без fb_ad_id.
    """
    if compact:
        lines: list[str] = ["<blockquote>"]
        # Кампания › Адсет одной строкой
        hierarchy_parts: list[str] = []
        if campaign_name:
            hierarchy_parts.append(html.escape(campaign_name))
        if adset_name:
            hierarchy_parts.append(html.escape(adset_name))
        if hierarchy_parts:
            lines.append(f"📁 {' › '.join(hierarchy_parts)}")
        lines.append(f"📢 <b>{html.escape(ad_name)}</b>")
        lines.append("</blockquote>")
        return lines

    lines = []
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
        labels = ", ".join(_RULE_LABELS.get(code, code) for code in matched_rule_codes if code)
        if labels:
            lines.append(f"🎯 Сигналы: {html.escape(labels)}")

    return lines


def build_key_metric_line(
    metrics_json: dict[str, Any],
    rule_summaries: list[str] | None = None,
) -> list[str]:
    """Возвращает 1-2 строки: расход + первый rule_summary inline."""
    metrics = metrics_json or {}
    lines: list[str] = []

    spend = metrics.get("spend")
    if spend is not None:
        lines.append(f"💰 Расход: <b>${spend}</b>")

    summaries = [str(s).strip() for s in (rule_summaries or []) if str(s).strip()]
    if summaries:
        lines.append(f"📏 {html.escape(summaries[0])}")

    return lines


def build_detailed_metrics_block(metrics_json: dict[str, Any]) -> list[str]:
    """Возвращает <blockquote expandable> с секциями Трафик / Конверсии / Аукцион.

    Скрывает нулевые/None метрики без связанной ненулевой пары.
    Если блок пуст — возвращает пустой список.
    """
    metrics = metrics_json or {}
    sections: list[str] = []

    # --- Трафик ---
    traffic: list[str] = []
    cpc = metrics.get("cpc")
    clicks = metrics.get("clicks")
    if cpc is not None:
        traffic.append(f"CPC: ${cpc}")
    if clicks is not None and clicks > 0:
        traffic.append(f"Кликов: {clicks}")

    outbound_clicks = metrics.get("outbound_clicks")
    outbound_ctr = metrics.get("outbound_ctr")
    if outbound_clicks is not None and outbound_clicks > 0:
        traffic.append(f"Исх. клики: {outbound_clicks}")
    if outbound_ctr is not None:
        traffic.append(f"CTR исх.: {outbound_ctr}%")

    lpv = metrics.get("landing_page_views")
    cost_per_lpv = metrics.get("cost_per_landing_page_view")
    if lpv is not None and lpv > 0:
        traffic.append(f"LPV: {lpv}")
    if cost_per_lpv is not None:
        traffic.append(f"Цена LPV: ${cost_per_lpv}")

    if traffic:
        sections.append("🖱 <b>Трафик</b>")
        sections.append(" · ".join(traffic))

    # --- Конверсии ---
    conversions: list[str] = []
    leads = metrics.get("leads")
    cpl = metrics.get("cost_per_lead")
    if leads is not None and (leads > 0 or cpl is not None):
        conversions.append(f"Лидов: {leads}")
    if cpl is not None:
        conversions.append(f"CPL: ${cpl}")

    regs = metrics.get("registrations")
    cpr = metrics.get("cost_per_registration")
    if regs is not None and (regs > 0 or cpr is not None):
        conversions.append(f"Реги: {regs}")
    if cpr is not None:
        conversions.append(f"CPR: ${cpr}")

    deps = metrics.get("deposits")
    if deps is not None and deps > 0:
        conversions.append(f"Депозитов: {deps}")

    if conversions:
        sections.append("📋 <b>Конверсии</b>")
        sections.append(" · ".join(conversions))

    # --- Аукцион ---
    auction: list[str] = []
    diagnostics = metrics.get("traffic_diagnostics")
    if isinstance(diagnostics, dict):
        for key, icon, title in (
            ("cpm", "📈", "CPM"),
            ("frequency", "🔁", "Частота"),
        ):
            payload = diagnostics.get(key)
            if not isinstance(payload, dict):
                continue
            text = str(payload.get("text") or "").strip()
            if text:
                auction.append(f"{icon} {title}: {html.escape(text)}")
    else:
        # Fallback: показать CPM/frequency если есть
        cpm = metrics.get("cpm")
        frequency = metrics.get("frequency")
        if cpm is not None:
            auction.append(f"📈 CPM: ${cpm}")
        if frequency is not None:
            auction.append(f"🔁 Частота: {frequency}")

    if auction:
        sections.append("📈 <b>Аукцион</b>")
        sections.extend(auction)

    if not sections:
        return []

    return [
        "<blockquote expandable>",
        *sections,
        "</blockquote>",
    ]


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
    """Формирует компактное TG-сообщение с blockquote-секциями."""
    lines: list[str] = []
    keyboard: list[list[dict[str, str]]] = []

    for item in items:
        # --- Заголовок: стадия · причина ---
        reason = html.escape(item.reason_title) if item.reason_title else ""
        if stage == AlertStage.STOP:
            header = "🛑 <b>СТОП</b>"
        elif stage == AlertStage.EARLY_SIGNAL:
            header = "🔎 <b>Ранний сигнал</b>"
        else:
            header = "⚠️ <b>Предупреждение</b>"
        lines.append(f"{header} · {reason}" if reason else header)
        lines.append("")

        # --- Identity: compact blockquote ---
        lines.extend(
            build_ad_identity_lines(
                campaign_name=item.campaign_name,
                adset_name=item.adset_name,
                ad_name=item.ad_name,
                compact=True,
            )
        )

        # --- Расход + правило (всегда видно) ---
        rule_summaries = item.metrics_json.get("rule_summaries")
        if not isinstance(rule_summaries, list):
            rule_summaries = None
        key_lines = build_key_metric_line(item.metrics_json, rule_summaries)
        if key_lines:
            lines.append("")
            lines.extend(key_lines)

        # --- Подробные метрики (сворачиваемый блок) ---
        detail_lines = build_detailed_metrics_block(item.metrics_json)
        if detail_lines:
            lines.append("")
            lines.extend(detail_lines)

        # --- Snooze-примечание ---
        if snooze_note:
            lines.append("")
            lines.append(html.escape(snooze_note))

        # --- Футер / кнопки ---
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
            lines.append("")
            lines.append("⚡ Авто-отключение запущено")

    return TelegramOutgoingMessage(
        text="\n".join(lines).strip(),
        reply_markup={"inline_keyboard": keyboard} if keyboard else None,
    )


def render_enable_recommendation_message(
    *,
    item: TelegramEnableRecommendationItem,
) -> TelegramOutgoingMessage:
    """Формирует компактное Telegram-сообщение с рекомендацией на включение."""
    lines: list[str] = []
    metrics = item.metrics_json or {}
    reason_title, reason_text = normalize_enable_recommendation_reason(
        recommendation_level=item.recommendation_level,
        reason_title=item.reason_title,
        reason_text=item.reason_text,
    )

    # --- Заголовок: уровень · причина ---
    reason = html.escape(reason_title) if reason_title else ""
    if item.recommendation_level == EnableRecommendationLevel.WARNING:
        header = "⚠️ <b>Требует проверки</b>"
    elif item.recommendation_level == EnableRecommendationLevel.EARLY_SIGNAL:
        header = "🔎 <b>Ранний сигнал восстановления</b>"
    else:
        header = "ℹ️ <b>Нет блокирующих сигналов</b>"
    lines.append(f"{header} · {reason}" if reason else header)
    lines.append("")

    # --- Identity: compact blockquote ---
    lines.extend(
        build_ad_identity_lines(
            campaign_name=item.campaign_name,
            adset_name=item.adset_name,
            ad_name=item.ad_name,
            compact=True,
        )
    )

    # --- Расход + правило ---
    rule_summaries = metrics.get("rule_summaries")
    if not isinstance(rule_summaries, list):
        rule_summaries = None
    key_lines = build_key_metric_line(metrics, rule_summaries)
    if key_lines:
        lines.append("")
        lines.extend(key_lines)

    # --- Подробные метрики (сворачиваемый блок) ---
    detail_lines = build_detailed_metrics_block(metrics)
    if detail_lines:
        lines.append("")
        lines.extend(detail_lines)

    # --- Статус доставки ---
    lines.append("")
    lines.append(f"📡 Доставка Meta: <b>{html.escape(item.delivery_status)}</b>")

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
