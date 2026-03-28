# -*- coding: utf-8 -*-
"""Рендеринг Telegram-сообщений: форматирование алертов с inline-кнопками."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from core.domain import AlertStage, AlertState

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


def _rule_label(code: str) -> str:
    return _RULE_LABELS.get(code, code)


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


def render_alert_message(
    *,
    stage: AlertStage,
    items: list[TelegramAlertItem],
) -> TelegramOutgoingMessage:
    """Формирует TG-сообщение (одно на объявление) с кнопкой «Отключить» для STOP."""
    lines: list[str] = []
    keyboard: list[list[dict[str, str]]] = []

    for item in items:
        m = item.metrics_json
        rules_text = (
            ", ".join(_rule_label(c) for c in item.matched_rule_codes)
            if item.matched_rule_codes
            else "нет"
        )

        if stage == AlertStage.STOP:
            lines.append(f"🛑 <b>СТОП</b> — {html.escape(rules_text)}")
        elif stage == AlertStage.EARLY_SIGNAL:
            lines.append(f"🔎 <b>Ранний сигнал</b> — {html.escape(rules_text)}")
        else:
            lines.append(f"⚠️ <b>Предупреждение</b> — {html.escape(rules_text)}")

        lines.append("")

        # Иерархия: Кампания → Адсет → Объявление
        if item.campaign_name:
            lines.append(f"📁 {html.escape(item.campaign_name)}")
        if item.adset_name:
            lines.append(f"  └ {html.escape(item.adset_name)}")
        lines.append(f"  └ 📢 <b>{html.escape(item.ad_name)}</b>")

        lines.append("")

        if item.reason_title:
            lines.append(f"🧭 <b>{html.escape(item.reason_title)}</b>")
        if item.reason_text:
            lines.append("Причина:")
            lines.append(html.escape(item.reason_text))
            lines.append("")

        rule_summaries = m.get("rule_summaries")
        if isinstance(rule_summaries, list) and rule_summaries:
            lines.append("🎯 Сработавший порог:")
            for summary in rule_summaries:
                lines.append(f"• {html.escape(str(summary))}")
            lines.append("")

        # Метрики
        spend = m.get("spend")
        if spend is not None:
            lines.append(f"💰 Расход: <b>${spend}</b>")

        cpc = m.get("cpc")
        clicks = m.get("clicks")
        if cpc is not None:
            lines.append(f"🖱 CPC: ${cpc} · Кликов: {clicks or 0}")

        leads = m.get("leads")
        cpl = m.get("cost_per_lead")
        if leads is not None:
            cpl_str = f" · CPL: ${cpl}" if cpl else ""
            lines.append(f"📋 Лидов: {leads}{cpl_str}")

        regs = m.get("registrations")
        cpr = m.get("cost_per_registration")
        if regs is not None:
            cpr_str = f" · CPR: ${cpr}" if cpr else ""
            lines.append(f"📝 Реги: {regs}{cpr_str}")

        deps = m.get("deposits")
        if deps is not None:
            lines.append(f"💵 Депозитов: {deps}")

        outbound_clicks = m.get("outbound_clicks")
        outbound_ctr = m.get("outbound_ctr")
        if outbound_clicks is not None or outbound_ctr is not None:
            parts = []
            if outbound_clicks is not None:
                parts.append(f"Исх. клики: {outbound_clicks}")
            if outbound_ctr is not None:
                parts.append(f"CTR исх.: {outbound_ctr}%")
            if parts:
                lines.append(f"🌐 {' · '.join(parts)}")

        lpv = m.get("landing_page_views")
        cost_per_lpv = m.get("cost_per_landing_page_view")
        if lpv is not None or cost_per_lpv is not None:
            parts = []
            if lpv is not None:
                parts.append(f"LPV: {lpv}")
            if cost_per_lpv is not None:
                parts.append(f"Цена LPV: ${cost_per_lpv}")
            if parts:
                lines.append(f"🧪 {' · '.join(parts)}")

        cpm = m.get("cpm")
        frequency = m.get("frequency")
        if cpm is not None or frequency is not None:
            parts = []
            if cpm is not None:
                parts.append(f"CPM: ${cpm}")
            if frequency is not None:
                parts.append(f"Частота: {frequency}")
            if parts:
                lines.append(f"📈 {' · '.join(parts)}")

        lines.append("")

        if stage in {AlertStage.WARNING, AlertStage.EARLY_SIGNAL}:
            footer = "ℹ️ Объявление продолжает работать"
            if stage == AlertStage.EARLY_SIGNAL:
                footer = "ℹ️ Это ранний сигнал, авто-отключение не запускалось"
            lines.append(footer)
            keyboard.append(
                [{"text": f"🛑 Отключить: {item.ad_name[:28].rstrip()}", "callback_data": f"disable:{item.snapshot_id}"}]
            )
            keyboard.append(
                [{"text": "✅ Оставить на 3 часа", "callback_data": f"snooze:{item.snapshot_id}:3"}]
            )
        elif stage == AlertStage.STOP:
            lines.append("⚡ Авто-отключение запущено")
            keyboard.append(
                [{"text": f"🛑 Отключить: {item.ad_name[:28].rstrip()}", "callback_data": f"disable:{item.snapshot_id}"}]
            )
            keyboard.append(
                [{"text": "✅ Оставить на 1 час", "callback_data": f"snooze:{item.snapshot_id}:1"}]
            )

    return TelegramOutgoingMessage(
        text="\n".join(lines).strip(),
        reply_markup={"inline_keyboard": keyboard} if keyboard else None,
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
