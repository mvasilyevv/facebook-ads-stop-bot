# -*- coding: utf-8 -*-
"""Тесты inline-клавиатуры алерт-сообщений рендерера."""

from __future__ import annotations

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


# Проверяем, что клавиатура содержит 4 кнопки в 2 рядах при наличии account_id.
def test_render_alert_message_4_buttons_with_account_id():
    """При account_id клавиатура должна содержать 2 ряда по 2 кнопки (итого 4)."""
    item = _make_item(fb_ad_id="12345", snapshot_id="tokenABC")
    msg = render_alert_message(
        stage=AlertStage.WARNING,
        items=[item],
        account_id="987654321",
    )
    keyboard = msg.reply_markup["inline_keyboard"]
    assert len(keyboard) == 2, "Должно быть 2 ряда"
    assert len(keyboard[0]) == 2, "Ряд 1: 2 кнопки"
    assert len(keyboard[1]) == 2, "Ряд 2: 2 кнопки (Снять алерт + Открыть)"


# Проверяем правильные callback_data в рядах кнопок.
def test_render_alert_message_correct_callback_data():
    """Кнопки должны иметь правильные callback_data и url."""
    fb_ad_id = "12345"
    snapshot_id = "tok42"
    item = _make_item(fb_ad_id=fb_ad_id, snapshot_id=snapshot_id)
    msg = render_alert_message(
        stage=AlertStage.WARNING,
        items=[item],
        account_id="act_999",
    )
    keyboard = msg.reply_markup["inline_keyboard"]

    # Ряд 1
    disable_btn = keyboard[0][0]
    snooze_btn = keyboard[0][1]
    assert disable_btn["callback_data"] == f"disable:{fb_ad_id}:{snapshot_id}"
    assert snooze_btn["callback_data"] == f"snooze:{fb_ad_id}:30:{snapshot_id}"

    # Ряд 2
    claim_btn = keyboard[1][0]
    url_btn = keyboard[1][1]
    assert claim_btn["callback_data"] == f"claim:{fb_ad_id}:{snapshot_id}"
    assert "url" in url_btn
    assert "act=act_999" in url_btn["url"]
    assert f"selected_ad_ids={fb_ad_id}" in url_btn["url"]


# Проверяем, что при account_id=None кнопка Ads Manager не добавляется (3 кнопки).
def test_render_alert_message_3_buttons_without_account_id():
    """Без account_id должны быть 2 ряда: ряд 1 — 2 кнопки, ряд 2 — 1 кнопка."""
    item = _make_item(fb_ad_id="12345", snapshot_id="tokenXYZ")
    msg = render_alert_message(
        stage=AlertStage.WARNING,
        items=[item],
        account_id=None,
    )
    keyboard = msg.reply_markup["inline_keyboard"]
    assert len(keyboard) == 2
    assert len(keyboard[0]) == 2
    assert len(keyboard[1]) == 1, "Ряд 2: только кнопка Снять алерт"
    # Нет url-кнопки
    assert "url" not in keyboard[1][0]


# Проверяем, что при пустом account_id кнопка Ads Manager тоже не добавляется.
def test_render_alert_message_no_url_button_when_account_id_empty():
    """Пустая строка account_id — то же, что None: url-кнопки нет."""
    item = _make_item()
    msg = render_alert_message(
        stage=AlertStage.WARNING,
        items=[item],
        account_id="",
    )
    keyboard = msg.reply_markup["inline_keyboard"]
    assert len(keyboard[1]) == 1


# Проверяем URL содержит act= и selected_ad_ids=.
def test_render_alert_message_url_contains_act_and_ad_id():
    """URL кнопки Ads Manager должен содержать act= и selected_ad_ids=."""
    fb_ad_id = "55566677"
    account_id = "111222333"
    item = _make_item(fb_ad_id=fb_ad_id)
    msg = render_alert_message(
        stage=AlertStage.STOP,
        items=[item],
        account_id=account_id,
    )
    keyboard = msg.reply_markup["inline_keyboard"]
    url_btn = keyboard[1][1]
    assert f"act={account_id}" in url_btn["url"]
    assert f"selected_ad_ids={fb_ad_id}" in url_btn["url"]
    assert "adsmanager.facebook.com" in url_btn["url"]
