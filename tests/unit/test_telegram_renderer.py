# -*- coding: utf-8 -*-
"""Unit-тесты core.telegram.renderer — pure-функции форматирования."""

from __future__ import annotations

from decimal import Decimal

from core.telegram.renderer import (
    AlertRenderInput,
    render_alert_text,
    render_inline_keyboard,
)


def _input(stage="warning", **overrides) -> AlertRenderInput:
    defaults = dict(
        fb_ad_id="230011223344",
        ad_name="Aviator001",
        campaign_name="CR2 | KE | MV",
        adset_name="EQ_KE",
        offer_code="KE_CR2",
        stage=stage,
        matched_rule_codes=["spend_no_dep_stop"],
        metrics={
            "spend": Decimal("12.50"),
            "cpc": Decimal("0.234"),
            "ctr": Decimal("2.5"),
            "cpm": Decimal("3.10"),
            "clicks": 50,
            "landing_page_views": 20,
            "leads": 5,
            "registrations": 2,
            "deposits": 0,
        },
        open_state_token="abcdef1234567890",
    )
    defaults.update(overrides)
    return AlertRenderInput(**defaults)


# Сценарий: WARNING рендер содержит правильный prefix и все метрики
def test_render_warning_contains_all_fields() -> None:
    inp = _input(stage="warning")
    text = render_alert_text(inp)

    assert "WARNING" in text
    assert "⚠️" in text
    assert "Aviator001" in text
    assert "KE_CR2" in text
    assert "spend_no_dep_stop" in text
    assert "12.50" in text  # spend
    assert "0.234" in text  # cpc
    assert "230011223344" in text


# Сценарий: STOP рендер с правильным эмодзи
def test_render_stop_uses_red_emoji() -> None:
    text = render_alert_text(_input(stage="stop"))
    assert "🛑" in text
    assert "STOP" in text


# Сценарий: HTML-escape опасных символов — нельзя сломать parse через ad_name
def test_html_escape_in_ad_name() -> None:
    inp = _input(ad_name='Aviator<script>alert("xss")</script>')
    text = render_alert_text(inp)
    # Символы экранированы
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


# Сценарий: NULL метрики выводятся как '—', не падают
def test_handles_none_metrics() -> None:
    inp = _input(
        metrics={
            "spend": None,
            "cpc": None,
            "ctr": None,
            "cpm": None,
            "clicks": None,
            "leads": None,
            "deposits": None,
        },
    )
    text = render_alert_text(inp)
    assert "—" in text


# Сценарий: keyboard для WARNING содержит две кнопки — Отключить и Снуз
def test_keyboard_has_disable_and_snooze_buttons() -> None:
    kb = render_inline_keyboard(_input(stage="warning"))
    assert kb is not None
    btns = kb["inline_keyboard"][0]
    assert len(btns) == 2
    actions = [b["callback_data"].split(":")[0] for b in btns]
    assert "dis" in actions
    assert "snz" in actions


# Сценарий: callback_data строго < 64 байт (Telegram limit)
def test_callback_data_fits_telegram_limit() -> None:
    kb = render_inline_keyboard(_input(fb_ad_id="999999999999999"))
    for row in kb["inline_keyboard"]:
        for btn in row:
            assert len(btn["callback_data"].encode("utf-8")) <= 64, btn


# Сценарий: callback_data содержит fb_ad_id (caller должен его извлечь)
def test_callback_data_encodes_fb_ad_id() -> None:
    kb = render_inline_keyboard(_input(fb_ad_id="42424242"))
    dis_btn = next(
        b for row in kb["inline_keyboard"] for b in row if b["callback_data"].startswith("dis:")
    )
    parts = dis_btn["callback_data"].split(":")
    assert parts[1] == "42424242"


# Сценарий: пустой список rule_codes — рендерим без падения
def test_render_without_rule_codes() -> None:
    text = render_alert_text(_input(matched_rule_codes=[]))
    assert "нет деталей" in text


# Сценарий: без offer_code — секция оффера не печатается
def test_render_without_offer_code() -> None:
    text = render_alert_text(_input(offer_code=None))
    assert "Offer:" not in text
