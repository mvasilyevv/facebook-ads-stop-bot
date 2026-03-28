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
