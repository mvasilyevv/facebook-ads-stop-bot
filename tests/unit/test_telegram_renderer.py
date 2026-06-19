# -*- coding: utf-8 -*-
"""Unit-тесты core.telegram.renderer — минимал-формат алертов (pure-функции)."""

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
        matched_rule_codes=["spend_no_dep_range"],
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


# WARNING: заголовок с оффером, причина читаемо, расход с $; без техн. ID и дубля ad_name
def test_render_warning_minimal() -> None:
    text = render_alert_text(_input(stage="warning"))

    assert "ПРЕДУПРЕЖДЕНИЕ" in text
    assert "⚠️" in text
    assert "KE_CR2" in text  # оффер в заголовке
    assert "Расход без депозитов" in text  # причина человекочитаемо
    assert "spend_no_dep_range" not in text
    assert "$12.50" in text  # расход с $
    # Техническая ID-строка убрана из минимал-формата
    assert "ID:" not in text
    assert "230011223344" not in text


# STOP: красный эмодзи + слово СТОП в заголовке
def test_render_stop_head() -> None:
    text = render_alert_text(_input(stage="stop"))
    assert "🛑" in text
    assert "СТОП" in text


# Метрики в выровненном блоке: расход с $, подписи Деп/Рег/Клики; без сырых англ. ключей
def test_render_metrics_line() -> None:
    text = render_alert_text(_input(stage="stop"))
    assert "$12.50" in text  # расход
    assert "Деп" in text and "Рег" in text and "Клики" in text
    assert "50" in text  # клики
    # Сырые англоязычные ключи не протекают в карточку
    assert "spend:" not in text
    assert "clicks:" not in text


# Причина с порогом из _hits: 'CPL $9.56 ▸ порог $3.00 (×3.2)' — значение жирным, кратность
def test_render_hit_with_threshold() -> None:
    inp = _input(
        stage="stop",
        matched_rule_codes=["cpl_stop"],
        metrics={
            "spend": Decimal("47.80"),
            "deposits": 0,
            "registrations": 5,
            "clicks": 42,
            "_hits": [{"code": "cpl_stop", "stage": "stop", "value": "9.56", "threshold": "3.00"}],
        },
    )
    text = render_alert_text(inp)
    assert "CPL" in text
    assert "<b>$9.56</b>" in text  # факт выделен жирным
    assert "порог $3.00" in text
    assert "×3.2" in text  # кратность превышения (9.56 / 3.00)


# _hits показываются только для своего stage (warning-hit не лезет в stop-алерт)
def test_render_hits_filtered_by_stage() -> None:
    inp = _input(
        stage="stop",
        matched_rule_codes=["cpr_stop"],
        metrics={
            "spend": Decimal("30.00"),
            "deposits": 0,
            "registrations": 3,
            "clicks": 20,
            "_hits": [
                {"code": "cpr_stop", "stage": "stop", "value": "6.00", "threshold": "5.00"},
                {"code": "cpc_stop", "stage": "warning", "value": "0.50", "threshold": "0.40"},
            ],
        },
    )
    text = render_alert_text(inp)
    assert "CPR" in text and "<b>$6.00</b>" in text and "порог $5.00" in text
    assert "CPC" not in text  # warning-hit отфильтрован (и нет cpc-метрики в гриде)


# Процентное правило (spend/CPA) форматируется как % из _hits
def test_render_hit_percent_unit() -> None:
    inp = _input(
        stage="stop",
        matched_rule_codes=["spend_no_dep_range"],
        metrics={
            "spend": Decimal("40.00"),
            "deposits": 0,
            "registrations": 0,
            "clicks": 30,
            "_hits": [
                {"code": "spend_no_dep_range", "stage": "stop", "value": "56", "threshold": "40"}
            ],
        },
    )
    text = render_alert_text(inp)
    assert "Расход/CPA" in text
    assert "<b>56%</b>" in text
    assert "порог 40%" in text
    assert "×1.4" in text  # 56 / 40


# Неизвестный код правила (без _hits) — fallback на сам код, не падает
def test_render_unknown_rule_code_fallback() -> None:
    text = render_alert_text(_input(matched_rule_codes=["some_future_rule"]))
    assert "some_future_rule" in text


# HTML-escape опасных символов (через ad_name в заголовке при отсутствии оффера)
def test_html_escape_in_title() -> None:
    inp = _input(offer_code=None, ad_name='Aviator<script>alert("xss")</script>')
    text = render_alert_text(inp)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


# NULL метрики выводятся как '—', не падают
def test_handles_none_metrics() -> None:
    inp = _input(
        metrics={"spend": None, "deposits": None, "registrations": None, "clicks": None},
    )
    text = render_alert_text(inp)
    assert "—" in text


# Без rule_codes и без _hits — рендерим без падения (fallback-фраза)
def test_render_without_rule_codes() -> None:
    text = render_alert_text(_input(matched_rule_codes=[]))
    assert "сработало стоп-правило" in text


# Без offer_code — в заголовок попадает ad_name
def test_render_without_offer_code() -> None:
    text = render_alert_text(_input(offer_code=None))
    assert "Aviator001" in text


# Контекст содержит и кампанию, и адсет (дизамбигуация дублирующихся имён объявлений)
def test_render_includes_campaign_and_adset() -> None:
    text = render_alert_text(_input(campaign_name="CR2 | KE | MV", adset_name="EQ_KE broad"))
    assert "CR2 | KE | MV" in text  # кампания
    assert "EQ_KE broad" in text  # адсет
    assert "адсет" in text  # явная метка адсета


# Адсет с HTML-спецсимволами экранируется (не ломает parse_mode=HTML)
def test_render_escapes_adset_name() -> None:
    text = render_alert_text(_input(adset_name="A&B <broad>"))
    assert "A&amp;B &lt;broad&gt;" in text
    assert "<broad>" not in text


# Адсет показывается даже без названия кампании (без маркера вложенности)
def test_render_adset_without_campaign() -> None:
    text = render_alert_text(_input(campaign_name="", adset_name="EQ_KE broad"))
    assert "адсет: EQ_KE broad" in text


# keyboard для WARNING — только кнопка «Отключить» (snooze убран, решение владельца)
def test_keyboard_has_only_disable_button() -> None:
    kb = render_inline_keyboard(_input(stage="warning"))
    assert kb is not None
    btns = kb["inline_keyboard"][0]
    assert len(btns) == 1
    actions = [b["callback_data"].split(":")[0] for b in btns]
    assert "dis" in actions
    assert "snz" not in actions


# callback_data строго <= 64 байт (Telegram limit)
def test_callback_data_fits_telegram_limit() -> None:
    kb = render_inline_keyboard(_input(fb_ad_id="999999999999999"))
    for row in kb["inline_keyboard"]:
        for btn in row:
            assert len(btn["callback_data"].encode("utf-8")) <= 64, btn


# callback_data содержит fb_ad_id (caller извлекает)
def test_callback_data_encodes_fb_ad_id() -> None:
    kb = render_inline_keyboard(_input(fb_ad_id="42424242"))
    dis_btn = next(
        b for row in kb["inline_keyboard"] for b in row if b["callback_data"].startswith("dis:")
    )
    parts = dis_btn["callback_data"].split(":")
    assert parts[1] == "42424242"
