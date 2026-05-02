# -*- coding: utf-8 -*-
"""Тесты рендера Telegram-уведомлений."""

from __future__ import annotations

from core.domain import AlertStage, AlertState, EnableRecommendationLevel
from core.telegram.renderer import (
    TelegramAlertItem,
    TelegramEnableRecommendationItem,
    build_ad_identity_lines,
    build_detailed_metrics_block,
    build_key_metric_line,
    render_alert_message,
    render_enable_recommendation_message,
)

# --- build_ad_identity_lines ---


# Проверяем compact-режим: одна строка кампании, blockquote, без fb_ad_id.
def test_build_ad_identity_lines_compact():
    lines = build_ad_identity_lines(
        campaign_name="Campaign A",
        adset_name="Adset A",
        ad_name="Ad Name",
        fb_ad_id="123456",
        compact=True,
    )
    text = "\n".join(lines)
    assert "<blockquote>" in text
    assert "Campaign A › Adset A" in text
    assert "Ad Name" in text
    assert "123456" not in text  # fb_ad_id скрыт в compact


# Проверяем обычный режим: иерархия с отступами, fb_ad_id показан.
def test_build_ad_identity_lines_default():
    lines = build_ad_identity_lines(
        campaign_name="Campaign A",
        adset_name="Adset A",
        ad_name="Ad Name",
        fb_ad_id="123456",
    )
    text = "\n".join(lines)
    assert "📁 Campaign A" in text
    assert "  └ Adset A" in text
    assert "🆔" in text
    assert "123456" in text


# --- build_key_metric_line ---


# Проверяем inline rule_summary в строке расхода.
def test_build_key_metric_line_with_summaries():
    lines = build_key_metric_line(
        {"spend": "0.09"},
        rule_summaries=["CPC 0.09 > стоп 0.08 (базовый 0.10)"],
    )
    text = "\n".join(lines)
    assert "💰 Расход: <b>$0.09</b>" in text
    assert "📏 CPC 0.09 &gt; стоп 0.08" in text


# --- build_detailed_metrics_block ---


# Проверяем, что expandable-блок содержит секции Трафик/Конверсии/Аукцион.
def test_build_detailed_metrics_block_full():
    lines = build_detailed_metrics_block(
        {
            "cpc": "0.09",
            "clicks": 5,
            "outbound_clicks": 3,
            "outbound_ctr": "1.20",
            "landing_page_views": 2,
            "cost_per_landing_page_view": "0.05",
            "leads": 1,
            "cost_per_lead": "0.09",
            "registrations": 1,
            "cost_per_registration": "0.09",
            "deposits": 1,
            "traffic_diagnostics": {
                "cpm": {"status": "critical", "text": "CPM выше медианы"},
                "frequency": {"status": "elevated", "text": "Частота растёт"},
            },
        }
    )
    text = "\n".join(lines)
    assert "<blockquote expandable>" in text
    assert "Трафик" in text
    assert "CPC: $0.09" in text
    assert "Исх. клики: 3" in text
    assert "LPV: 2" in text
    assert "Конверсии" in text
    assert "Лидов: 1" in text
    assert "Депозитов: 1" in text
    assert "Аукцион" in text
    assert "CPM выше медианы" in text
    assert "Частота растёт" in text


# Проверяем, что нулевые метрики скрываются.
def test_build_detailed_metrics_block_hides_zeros():
    lines = build_detailed_metrics_block(
        {
            "clicks": 0,
            "outbound_clicks": 0,
            "leads": 0,
            "cost_per_lead": None,
            "registrations": 0,
            "cost_per_registration": None,
            "deposits": 0,
        }
    )
    # Всё нулевое — пустой результат (нет блока)
    assert lines == []


# --- render_alert_message ---


# Проверяем, что STOP рендерит полный формат без обрезки названий.
def test_render_alert_message_includes_rule_summaries():
    item = TelegramAlertItem(
        snapshot_id="snap-1",
        fb_ad_id="ad-1",
        ad_name="DRC_CR2_CR017_FULL_AD_NAME_WITH_LONG_SUFFIX",
        campaign_name="Campaign A With Long Full Name",
        adset_name="Adset A With Long Full Name",
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

    assert "<b>STOP</b>" in message.text
    assert "Campaign A With Long Full Name" in message.text
    assert "Adset A With Long Full Name" in message.text
    assert "DRC_CR2_CR017_FULL_AD_NAME_WITH_LONG_SUFFIX" in message.text
    assert "…" not in message.text
    assert "Дорогой клик" in message.text
    assert "<b>Метрики на момент стопа:</b>" in message.text
    assert "Расход: $0.09" in message.text
    assert "CPC: $0.0900" in message.text
    assert "Создана задача на отключение." in message.text
    # Убраны старые элементы и эмодзи из alert-тела
    assert "🔴" not in message.text
    assert "💸" not in message.text
    assert "<blockquote expandable>" not in message.text
    assert "Пороговые детали" not in message.text
    assert "Следующее действие" not in message.text


# Проверяем, что traffic_diagnostics отображается отдельным блоком.
def test_render_alert_message_uses_human_readable_traffic_diagnostics():
    item = TelegramAlertItem(
        snapshot_id="snap-2b",
        fb_ad_id="ad-2b",
        ad_name="DRC_CR2_CR018B",
        campaign_name="Campaign B",
        adset_name="Adset B",
        offer_code="offer-b",
        stage=AlertStage.WARNING,
        alert_state=AlertState.WARNING_SENT,
        matched_rule_codes=["cpl_stop"],
        reason_title="Близко к порогу",
        reason_text="Есть признаки перегрева трафика.",
        metrics_json={
            "spend": "0.20",
            "clicks": 10,
            "cpc": "0.0200",
            "cpm": "7.5000",
            "frequency": "1.4000",
            "traffic_diagnostics": {
                "summary_text": "Трафик начал заметно дорожать.",
                "cpm": {
                    "status": "critical",
                    "text": "CPM заметно выше недавней медианы и может ухудшать окупаемость.",
                },
                "frequency": {
                    "status": "elevated",
                    "text": "Частота уже растёт и может ускорять выгорание аудитории.",
                },
            },
        },
    )

    message = render_alert_message(stage=AlertStage.WARNING, items=[item])

    # Диагностика в отдельном блоке
    assert "<b>WARNING</b>" in message.text
    assert "<b>Диагностика:</b>" in message.text
    assert "CPM заметно выше недавней медианы" in message.text
    assert "Частота уже растёт" in message.text
    # Сырые числа CPM/Frequency НЕ дублируются
    assert "CPM: $7.5000" not in message.text
    assert "Частота: 1.4000" not in message.text


# Проверяем, что STOP-сообщение не имеет кнопок.
def test_render_alert_message_stop_has_no_global_buttons():
    item = TelegramAlertItem(
        snapshot_id="snap-3",
        fb_ad_id="ad-3",
        ad_name="DRC_CR2_CR019",
        campaign_name="Campaign C",
        adset_name="Adset C",
        offer_code="offer-c",
        stage=AlertStage.STOP,
        alert_state=AlertState.CLAIMED,
        matched_rule_codes=["cpc_stop"],
        reason_title="Дорогой клик",
        reason_text="Цена клика вышла за допустимую границу.",
        metrics_json={
            "spend": "0.50",
            "clicks": 1,
            "cpc": "0.5000",
            "leads": 0,
            "registrations": 0,
            "deposits": 0,
        },
    )

    message = render_alert_message(stage=AlertStage.STOP, items=[item])

    assert "Создана задача на отключение." in message.text
    # STOP-алерт тоже содержит inline-клавиатуру с кнопками управления (Wave A.2)
    assert message.reply_markup is not None
    keyboard = message.reply_markup["inline_keyboard"]
    assert len(keyboard) == 2
    assert keyboard[0][0]["callback_data"] == "disable:ad-3:snap-3"


# --- render_enable_recommendation_message ---


# Проверяем, что generic OK-рекомендация нейтральна и compact.
def test_render_enable_recommendation_message_for_off_status():
    message = render_enable_recommendation_message(
        item=TelegramEnableRecommendationItem(
            event_id="event-1",
            fb_ad_id="ad-100",
            ad_name="OFF Ad",
            delivery_status="OFF",
            recommendation_level=EnableRecommendationLevel.OK,
            matched_rule_codes=[],
            reason_title="Метрики в норме",
            reason_text="Объявление снова проходит по текущим правилам.",
            metrics_json={
                "spend": "12.00",
                "cpc": "0.1200",
                "leads": 1,
                "cost_per_lead": "12.0000",
                "registrations": 1,
                "cost_per_registration": "12.0000",
                "deposits": 0,
            },
        )
    )

    assert "Нет блокирующих сигналов" in message.text
    assert "Метрики в норме" not in message.text
    assert "Доставка Meta: <b>OFF</b>" in message.text
    # Метрики в expandable-блоке
    assert "Лидов: 1" in message.text
    assert "Реги: 1" in message.text
    assert "CPR: $12.0000" in message.text
    # Убраны старые элементы
    assert "Ключевые метрики" not in message.text
    assert "Следующее действие" not in message.text
    assert message.reply_markup is not None
    assert (
        message.reply_markup["inline_keyboard"][0][0]["callback_data"] == "enable_reco:task:event-1"
    )


# Проверяем, что явный recovery-кейс сохраняет свой позитивный текст.
def test_render_enable_recommendation_message_preserves_explicit_recovery_copy():
    message = render_enable_recommendation_message(
        item=TelegramEnableRecommendationItem(
            event_id="event-1b",
            fb_ad_id="ad-101",
            ad_name="Recovery Ad",
            delivery_status="OFF",
            recommendation_level=EnableRecommendationLevel.OK,
            matched_rule_codes=[],
            reason_title="Можно вернуть в работу",
            reason_text="Проверка пройдена вручную и блокирующих сигналов нет.",
            metrics_json={"spend": "0.00"},
        )
    )

    # reason_title в заголовке, reason_text убран (compact-формат)
    assert "Можно вернуть в работу" in message.text


# Проверяем, что warning-рекомендация предупреждает о близости к порогу.
def test_render_enable_recommendation_message_for_warning():
    message = render_enable_recommendation_message(
        item=TelegramEnableRecommendationItem(
            event_id="event-3",
            fb_ad_id="ad-300",
            ad_name="Warn Ad",
            delivery_status="OFF",
            recommendation_level=EnableRecommendationLevel.WARNING,
            matched_rule_codes=["cpr_stop"],
            reason_title="Близко к порогу",
            reason_text="По CPR запас маленький, но стопа ещё нет.",
            metrics_json={"registrations": 2, "deposits": 1},
        )
    )

    assert "Требует проверки" in message.text
    assert "Близко к порогу" in message.text
    assert "Доставка Meta: <b>OFF</b>" in message.text
