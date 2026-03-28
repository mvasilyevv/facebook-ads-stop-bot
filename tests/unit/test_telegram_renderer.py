# -*- coding: utf-8 -*-
"""Тесты рендера Telegram-уведомлений."""

from __future__ import annotations

from core.domain import AlertStage, AlertState
from core.telegram.renderer import TelegramAlertItem, render_alert_message


# Проверяем что Telegram показывает фактический сработавший порог из rule_summaries
def test_render_alert_message_includes_rule_summaries():
    item = TelegramAlertItem(
        snapshot_id="snap-1",
        fb_ad_id="ad-1",
        ad_name="DRC_CR2_CR017",
        campaign_name="Campaign A",
        adset_name="Adset A",
        offer_code="offer-a",
        stage=AlertStage.STOP,
        alert_state=AlertState.CLAIMED,
        matched_rule_codes=["cpc_stop"],
        reason_title="Дорогой клик",
        reason_text="Цена клика вышла за допустимую границу.",
        metrics_json={
            "spend": "0.09",
            "clicks": 1,
            "cpc": "0.0900",
            "leads": 0,
            "cost_per_lead": None,
            "registrations": 0,
            "cost_per_registration": None,
            "deposits": 0,
            "rule_summaries": ["CPC 0.09 > стоп 0.08 (базовый 0.10)"],
        },
    )

    message = render_alert_message(stage=AlertStage.STOP, items=[item])

    assert "Сработавший порог" in message.text
    assert "стоп 0.08" in message.text
    assert "базовый 0.10" in message.text
    assert "Дорогой клик" in message.text
    assert "Цена клика вышла за допустимую границу." in message.text


# Проверяем что ранний сигнал рендерится отдельным статусом и показывает полную причину.
def test_render_alert_message_for_early_signal():
    item = TelegramAlertItem(
        snapshot_id="snap-2",
        fb_ad_id="ad-2",
        ad_name="DRC_CR2_CR018",
        campaign_name="Campaign B",
        adset_name="Adset B",
        offer_code="offer-b",
        stage=AlertStage.EARLY_SIGNAL,
        alert_state=AlertState.EARLY_SIGNAL_SENT,
        matched_rule_codes=["early_lpv_ratio_signal"],
        reason_title="Слабая доходимость до лендинга",
        reason_text="Переходы теряются между кликом и загрузкой страницы.",
        metrics_json={
            "spend": "0.20",
            "clicks": 10,
            "cpc": "0.0200",
            "outbound_clicks": 10,
            "landing_page_views": 3,
            "cpm": "7.5000",
            "frequency": "1.4000",
            "leads": 0,
            "registrations": 0,
            "deposits": 0,
        },
    )

    message = render_alert_message(stage=AlertStage.EARLY_SIGNAL, items=[item])

    assert "Ранний сигнал" in message.text
    assert "Причина:" in message.text
    assert "Слабая доходимость до лендинга" in message.text
    assert "Переходы теряются" in message.text
    assert "CPM: $7.5000" in message.text
