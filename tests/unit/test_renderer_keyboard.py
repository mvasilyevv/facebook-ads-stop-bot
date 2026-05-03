# -*- coding: utf-8 -*-
"""Тесты inline-клавиатуры алерт-сообщений рендерера (новый формат: 1 WebApp-кнопка)."""

from __future__ import annotations

from unittest.mock import patch

from core.domain import AlertStage, AlertState
from core.telegram.renderer import TelegramAlertItem, render_alert_message


def _make_item(fb_ad_id: str = "111", snapshot_id: str = "tok1") -> TelegramAlertItem:
    return TelegramAlertItem(
        snapshot_id=snapshot_id,
        fb_ad_id=fb_ad_id,
        ad_name="Тест Объявление",
        campaign_name="Кампания",
        adset_name="Адсет",
        offer_code="TST",
        stage=AlertStage.WARNING,
        alert_state=AlertState.WARNING_SENT,
        matched_rule_codes=["cpc"],
        reason_title="CPC высокий",
        reason_text="CPC превышает порог",
        metrics_json={"spend": "5.00"},
    )


# Проверяем, что при заданном web_app_url рендерер возвращает 1 ряд с 1 WebApp-кнопкой.
def test_render_alert_message_single_webapp_button():
    """При наличии web_app_url клавиатура должна содержать 1 ряд с 1 web_app-кнопкой."""
    item = _make_item(fb_ad_id="12345", snapshot_id="tokenABC")
    with patch("core.telegram.renderer.get_settings") as mock_settings:
        mock_settings.return_value.web_app_url = "https://example.com"
        msg = render_alert_message(
            stage=AlertStage.WARNING,
            items=[item],
            account_id=None,
        )
    keyboard = msg.reply_markup["inline_keyboard"]
    assert len(keyboard) == 1, "Должен быть 1 ряд"
    assert len(keyboard[0]) == 1, "В ряду должна быть 1 кнопка"
    btn = keyboard[0][0]
    assert "web_app" in btn, "Кнопка должна быть web_app-типа"
    assert "12345" in btn["web_app"]["url"]


# Проверяем, что без web_app_url клавиатура не добавляется.
def test_render_alert_message_no_keyboard_without_webapp_url():
    """Без web_app_url reply_markup должен быть None."""
    item = _make_item()
    with patch("core.telegram.renderer.get_settings") as mock_settings:
        mock_settings.return_value.web_app_url = None
        msg = render_alert_message(
            stage=AlertStage.WARNING,
            items=[item],
            account_id=None,
        )
    assert msg.reply_markup is None or msg.reply_markup == {"inline_keyboard": []}
