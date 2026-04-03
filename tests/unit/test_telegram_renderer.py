# -*- coding: utf-8 -*-
"""Тесты рендера Telegram-уведомлений."""

from __future__ import annotations

from core.domain import AlertStage, AlertState, EnableRecommendationLevel
from core.telegram.renderer import (
    TelegramAlertItem,
    TelegramEnableRecommendationItem,
    render_alert_message,
    render_enable_recommendation_message,
)


# Проверяем, что Telegram показывает фактический сработавший порог из rule_summaries.
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

    assert "Пороговые детали" in message.text
    assert "стоп 0.08" in message.text
    assert "базовый 0.10" in message.text
    assert "Дорогой клик" in message.text
    assert "Цена клика вышла за допустимую границу." in message.text


# Проверяем, что ранний сигнал рендерится с confirm-flow и обновлёнными кнопками snooze.
def test_render_alert_message_for_early_signal_has_confirm_and_snooze_buttons():
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
        reason_title="Мало открытий PWA после клика",
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
    assert "Мало открытий PWA после клика" in message.text
    assert "Переходы теряются" in message.text
    assert "CPM: $7.5000" not in message.text
    assert "Частота: 1.4000" not in message.text
    assert message.reply_markup is not None
    keyboard = message.reply_markup["inline_keyboard"]
    assert keyboard[0][0]["text"].startswith("🛑 Создать задачу:")
    assert keyboard[0][0]["callback_data"] == "disable:snap-2"
    assert [button["text"] for button in keyboard[1]] == ["⏸ 30м", "⏸ 1ч", "⏸ 2ч"]
    assert [button["callback_data"] for button in keyboard[1]] == [
        "snooze:snap-2:30",
        "snooze:snap-2:60",
        "snooze:snap-2:120",
    ]


# Проверяем, что Telegram показывает короткую диагностику UI-уровня вместо сухих CPM/Frequency.
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

    assert "Трафик начал заметно дорожать." in message.text
    assert "CPM заметно выше недавней медианы" in message.text
    assert "Частота уже растёт" in message.text
    assert "CPM: $7.5000" not in message.text
    assert "Частота: 1.4000" not in message.text


# Проверяем, что STOP-сообщение больше не тащит глобальную навигацию и кнопки задач.
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

    assert "Авто-отключение уже запущено" in message.text
    assert message.reply_markup is None


# Проверяем, что generic OK-рекомендация не обещает безопасное включение.
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
    assert "Можно включить" not in message.text
    assert "Статус доставки Meta: <b>OFF</b>" in message.text
    assert "Лидов: 1" in message.text
    assert "Реги: 1" in message.text
    assert "CPR: $12.0000" in message.text
    assert "Следующее действие: создать задачу на включение из этого сообщения." in message.text
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

    assert "Можно вернуть в работу" in message.text
    assert "Проверка пройдена вручную" in message.text


# Проверяем, что рекомендация не скрывает статус NOT_DELIVERING и помечает ранний сигнал.
def test_render_enable_recommendation_message_for_not_delivering_with_early_signal():
    message = render_enable_recommendation_message(
        item=TelegramEnableRecommendationItem(
            event_id="event-2",
            fb_ad_id="ad-200",
            ad_name="ND Ad",
            delivery_status="NOT_DELIVERING",
            recommendation_level=EnableRecommendationLevel.EARLY_SIGNAL,
            matched_rule_codes=["early_outbound_ctr_signal"],
            reason_title="Ранний сигнал",
            reason_text="Конверсий пока нет, но объявление уже можно вернуть в работу.",
            metrics_json={"outbound_ctr": "0.80"},
        )
    )

    assert "Статус доставки Meta: <b>NOT_DELIVERING</b>" in message.text
    assert "Есть ранний сигнал" not in message.text
    assert "Мало переходов на PWA" in message.text
    assert "Следующее действие: проверьте сигнал вручную перед включением." in message.text


# Проверяем, что warning-рекомендация явно предупреждает о близости к порогу.
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

    assert "Рекомендация требует проверки перед включением" in message.text
    assert "Можно включить" not in message.text
    assert "Близко к порогу" in message.text
    assert "Дорогая рега" in message.text
    assert "Статус доставки Meta: <b>OFF</b>" in message.text
