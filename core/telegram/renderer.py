# -*- coding: utf-8 -*-
"""Рендеринг Telegram-сообщений: форматирование алертов с inline-кнопками."""

from __future__ import annotations

from dataclasses import dataclass

from core.domain import AlertStage, AlertState


@dataclass(slots=True, frozen=True)
class TelegramAlertItem:
    """Одно объявление в TG-сообщении."""
    snapshot_id: str
    fb_ad_id: str
    ad_name: str
    offer_code: str | None
    stage: AlertStage
    alert_state: AlertState
    matched_rule_codes: list[str]
    metrics_json: dict[str, str | int | None]


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
    """Формирует TG-сообщение с алертами и inline-кнопками «Отключить»."""
    header = (
        "⚠️ Предупреждение по стоп-метрикам"
        if stage == AlertStage.WARNING
        else "🛑 Стоп-метрика требует действия"
    )
    lines = [header, "", f"Объявлений: {len(items)}", ""]
    keyboard: list[list[dict[str, str]]] = []

    for idx, item in enumerate(items, start=1):
        m = item.metrics_json
        lines.extend([
            f"{idx}. {item.ad_name}",
            f"   Ad ID: {item.fb_ad_id}",
            f"   Оффер: {item.offer_code or 'не определён'}",
            f"   Причины: {', '.join(item.matched_rule_codes) if item.matched_rule_codes else 'нет'}",
            (
                f"   Расход: {m.get('spend')}, CPC: {m.get('cpc') or '-'}, "
                f"CPL: {m.get('cost_per_lead') or '-'}, "
                f"CPR: {m.get('cost_per_registration') or '-'}, "
                f"рег: {m.get('registrations')}, деп: {m.get('deposits')}"
            ),
            f"   Статус: {_render_state(item.alert_state)}",
            "",
        ])

        # Кнопка «Отключить» только для STOP-стадии
        if stage == AlertStage.STOP:
            keyboard.append([{
                "text": _button_text(item),
                "callback_data": _button_callback(item),
            }])

    return TelegramOutgoingMessage(
        text="\n".join(lines).strip(),
        reply_markup={"inline_keyboard": keyboard} if keyboard else None,
    )


def _render_state(state: AlertState) -> str:
    """Человекочитаемый статус."""
    mapping = {
        AlertState.CLAIMED: "🔄 в работе",
        AlertState.DISABLED: "✅ выключено",
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
