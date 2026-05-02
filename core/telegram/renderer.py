# -*- coding: utf-8 -*-
"""Рендеринг Telegram-сообщений: форматирование алертов с inline-кнопками."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from core.domain import AlertStage, AlertState, EnableRecommendationLevel

# Человекочитаемые названия правил
from core.rules.labels import RULE_LABELS as _RULE_LABELS
from core.rules.labels import RULE_LABELS_SHORT as _RULE_LABELS_SHORT

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


def _truncate(text: str, max_len: int) -> str:
    """Обрезает строку до max_len символов с многоточием."""
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"


def _build_compact_metrics_line(metrics_json: dict[str, Any]) -> list[str]:
    """Две строки метрик: расход/клики/лиды/депозиты и CPR/CPC/CTR.

    Нулевые метрики не показываются.
    """
    metrics = metrics_json or {}
    lines: list[str] = []

    # Строка 1: расход · клики · лиды · депозиты
    row1: list[str] = []
    spend = metrics.get("spend")
    if spend is not None:
        row1.append(f"💸 {spend}$")
    clicks = metrics.get("clicks")
    if clicks:
        row1.append(f"👆 {clicks}")
    leads = metrics.get("leads")
    if leads:
        row1.append(f"🎯 {leads}")
    deps = metrics.get("deposits")
    if deps:
        row1.append(f"💰 {deps}")
    if row1:
        lines.append(" · ".join(row1))

    # Строка 2: CPR · CPC · CTR
    row2: list[str] = []
    cpr = metrics.get("cost_per_registration")
    if cpr is not None:
        row2.append(f"CPR: {cpr}$")
    cpc = metrics.get("cpc")
    if cpc is not None:
        row2.append(f"CPC: {cpc}$")
    ctr = metrics.get("outbound_ctr")
    if ctr is not None:
        row2.append(f"CTR: {ctr}%")
    if row2:
        lines.append(f"📊 {' · '.join(row2)}")

    return lines


def _stage_title(stage: AlertStage) -> str:
    """Возвращает короткий заголовок стадии алерта."""
    return "STOP" if stage == AlertStage.STOP else "WARNING"


def _append_label_block(lines: list[str], label: str, value: str | None) -> None:
    """Добавляет блок с подписью и полным значением."""
    text = str(value or "").strip()
    if not text:
        return
    lines.append(f"<b>{html.escape(label)}:</b>")
    lines.append(html.escape(text))
    lines.append("")


def _format_money_metric(value: Any) -> str:
    """Форматирует денежную метрику с долларом перед значением."""
    return f"${value}"


def _append_metric_line(
    lines: list[str],
    *,
    label: str,
    value: Any,
    money: bool = False,
    suffix: str = "",
) -> None:
    """Добавляет строку метрики, если значение есть."""
    if value is None:
        return
    formatted = _format_money_metric(value) if money else str(value)
    lines.append(f"{html.escape(label)}: {html.escape(formatted)}{html.escape(suffix)}")


def _build_alert_metric_lines(metrics_json: dict[str, Any]) -> list[str]:
    """Формирует ключевые метрики алерта в читаемом вертикальном виде."""
    metrics = metrics_json or {}
    lines: list[str] = []

    _append_metric_line(lines, label="Расход", value=metrics.get("spend"), money=True)
    _append_metric_line(lines, label="Клики", value=metrics.get("clicks"))
    _append_metric_line(lines, label="Исходящие клики", value=metrics.get("outbound_clicks"))
    _append_metric_line(lines, label="Лиды", value=metrics.get("leads"))
    _append_metric_line(lines, label="Регистрации", value=metrics.get("registrations"))
    _append_metric_line(lines, label="Депозиты", value=metrics.get("deposits"))
    _append_metric_line(lines, label="CPC", value=metrics.get("cpc"), money=True)
    _append_metric_line(lines, label="CPL", value=metrics.get("cost_per_lead"), money=True)
    _append_metric_line(
        lines,
        label="CPR",
        value=metrics.get("cost_per_registration"),
        money=True,
    )
    _append_metric_line(
        lines,
        label="CTR исходящий",
        value=metrics.get("outbound_ctr"),
        suffix="%",
    )
    _append_metric_line(lines, label="LPV", value=metrics.get("landing_page_views"))
    _append_metric_line(
        lines,
        label="Цена LPV",
        value=metrics.get("cost_per_landing_page_view"),
        money=True,
    )

    return lines


def _build_alert_reason_lines(item: TelegramAlertItem, *, stage: AlertStage) -> list[str]:
    """Формирует причины алерта без технических кодов."""
    lines: list[str] = []
    seen: set[str] = set()
    if item.reason_title:
        reason_title = str(item.reason_title).strip()
        if reason_title:
            lines.append(html.escape(reason_title))
            seen.add(reason_title.casefold())

    for code in item.matched_rule_codes:
        label = str(_RULE_LABELS_SHORT.get(code, code)).strip()
        if not label or label.casefold() in seen:
            continue
        lines.append(html.escape(label))
        seen.add(label.casefold())

    if not lines and item.reason_text:
        lines.append(html.escape(str(item.reason_text).strip()))

    if not lines:
        return []

    title = "Причина стопа" if stage == AlertStage.STOP else "Причина"
    return [f"<b>{title}:</b>", *lines]


def _build_alert_diagnostics_lines(metrics_json: dict[str, Any]) -> list[str]:
    """Формирует человекочитаемую диагностику трафика."""
    metrics = metrics_json or {}
    diagnostics = metrics.get("traffic_diagnostics")
    lines: list[str] = []

    if isinstance(diagnostics, dict):
        for key in ("cpm", "frequency"):
            payload = diagnostics.get(key)
            if not isinstance(payload, dict):
                continue
            text = str(payload.get("text") or "").strip()
            if text:
                lines.append(html.escape(text))
    else:
        cpm = metrics.get("cpm")
        frequency = metrics.get("frequency")
        if cpm is not None:
            lines.append(f"CPM: {html.escape(str(cpm))}")
        if frequency is not None:
            lines.append(f"Частота: {html.escape(str(frequency))}")

    return lines


def render_alert_message(
    *,
    stage: AlertStage,
    items: list[TelegramAlertItem],
    snooze_note: str | None = None,
    account_id: str | None = None,
) -> TelegramOutgoingMessage:
    """Формирует Telegram-alert с полной иерархией объявления и метриками."""
    lines: list[str] = []
    keyboard: list[list[dict[str, str]]] = []

    for index, item in enumerate(items):
        if index > 0:
            lines.append("")
            lines.append("-----")
            lines.append("")

        lines.append(f"<b>{_stage_title(stage)}</b>")
        lines.append("")

        _append_label_block(lines, "Кампания", item.campaign_name)
        _append_label_block(lines, "Адсет", item.adset_name)
        _append_label_block(lines, "Объявление", item.ad_name)

        reason_lines = _build_alert_reason_lines(item, stage=stage)
        if reason_lines:
            lines.extend(reason_lines)
            lines.append("")

        metric_lines = _build_alert_metric_lines(item.metrics_json)
        if metric_lines:
            metrics_title = (
                "Метрики на момент стопа" if stage == AlertStage.STOP else "Ключевые метрики"
            )
            lines.append(f"<b>{metrics_title}:</b>")
            lines.extend(metric_lines)
            lines.append("")

        if snooze_note:
            lines.append(html.escape(snooze_note))
            lines.append("")

        if stage == AlertStage.STOP:
            lines.append("Создана задача на отключение.")
            lines.append("")

        diagnostics_lines = _build_alert_diagnostics_lines(item.metrics_json or {})
        if diagnostics_lines:
            lines.append("<b>Диагностика:</b>")
            lines.extend(diagnostics_lines)
            lines.append("")

        # Ряд 1: Отключить | Снуз 30 мин
        row1: list[dict[str, str]] = [
            {
                "text": "⛔ Отключить",
                "callback_data": f"disable:{item.fb_ad_id}:{item.snapshot_id}",
            },
            {
                "text": "😴 Снуз 30 мин",
                "callback_data": f"snooze:{item.fb_ad_id}:30:{item.snapshot_id}",
            },
        ]
        keyboard.append(row1)

        # Ряд 2: Снять алерт [+ Открыть в Ads Manager]
        row2: list[dict[str, str]] = [
            {
                "text": "✅ Снять алерт",
                "callback_data": f"claim:{item.fb_ad_id}:{item.snapshot_id}",
            },
        ]
        if account_id:
            row2.append(
                {
                    "text": "🔗 Открыть в Ads Manager",
                    "url": (
                        f"https://adsmanager.facebook.com/adsmanager/manage/ads"
                        f"?act={account_id}&selected_ad_ids={item.fb_ad_id}"
                    ),
                }
            )
        keyboard.append(row2)

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
