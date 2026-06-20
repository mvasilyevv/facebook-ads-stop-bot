# -*- coding: utf-8 -*-
"""Unit-тесты для core/telegram/digest_renderer.render_digest.

Все сценарии — без БД, через ручной DigestPayload.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from core.telegram.digest_builder import DigestPayload, TopAdRow
from core.telegram.digest_renderer import render_digest


def _ts(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)


def _payload_empty() -> DigestPayload:
    return DigestPayload(
        window_start_utc=_ts(2026, 5, 26, 9),
        window_end_utc=_ts(2026, 5, 27, 9),
        alerts_warning_count=0,
        alerts_stop_count=0,
        top_ads_by_spend=[],
        disable_tasks_succeeded=0,
        disable_tasks_failed=0,
        active_offers_count=0,
        active_ads_count=0,
        total_spend_window_usd=Decimal("0"),
    )


# Заголовок и дата окна формируются из window_start_utc
def test_render_contains_header_and_date() -> None:
    text = render_digest(_payload_empty())
    assert "Дайджест" in text
    assert "2026-05-26" in text
    assert "2026-05-27 09:00 UTC" in text


# Пустой день: блок «нет активности» появляется внизу
def test_render_empty_day_shows_idle_notice() -> None:
    text = render_digest(_payload_empty())
    assert "За окно не было активности" in text
    assert "⚠️" in text and "🛑" in text  # счётчики алертов всё равно показываем


# Нулевые счётчики форматируются как «0», а не «—»
def test_render_zero_counters_show_zero() -> None:
    text = render_digest(_payload_empty())
    # Алерты ⚠️ 0 · 🛑 0 и отключения ✅ 0 · ❌ 0 — все нули
    assert "⚠️ <b>0</b>" in text
    assert "🛑 <b>0</b>" in text
    assert "✅ <b>0</b>" in text
    assert "❌ <b>0</b>" in text


# Обычный день: топ-5 объявлений отрисованы строкой каждое
def test_render_full_day_top_ads_listed() -> None:
    payload = DigestPayload(
        window_start_utc=_ts(2026, 5, 26, 9),
        window_end_utc=_ts(2026, 5, 27, 9),
        alerts_warning_count=12,
        alerts_stop_count=3,
        top_ads_by_spend=[
            TopAdRow(
                ad_id=uuid.uuid4(),
                fb_ad_id="2300000000001",
                ad_name="CR2 | DRC | hero",
                offer_code="DRC_CR2",
                spend_usd=Decimal("142.55"),
                clicks=210,
                leads=18,
                cpc=Decimal("0.679"),
                cost_per_lead=Decimal("7.92"),
            ),
            TopAdRow(
                ad_id=uuid.uuid4(),
                fb_ad_id="2300000000002",
                ad_name="CR2 | KE | hook",
                offer_code="KE_CR2",
                spend_usd=Decimal("98.10"),
                clicks=150,
                leads=10,
                cpc=Decimal("0.654"),
                cost_per_lead=Decimal("9.81"),
            ),
        ],
        disable_tasks_succeeded=4,
        disable_tasks_failed=1,
        active_offers_count=7,
        active_ads_count=42,
        total_spend_window_usd=Decimal("1234.50"),
    )

    text = render_digest(payload)

    # Алерты
    assert "⚠️ <b>12</b>" in text
    assert "🛑 <b>3</b>" in text
    # Топ-таблица: офферы, spend и метрики (CPC/CPL — отдельные колонки)
    assert "DRC_CR2" in text
    assert "KE_CR2" in text
    assert "$142.55" in text
    assert "$98.10" in text
    assert "0.679" in text  # CPC в колонке
    assert "7.92" in text  # CPL в колонке
    # Отключения
    assert "✅ <b>4</b>" in text
    assert "❌ <b>1</b>" in text
    # Итоги
    assert "$1 234.50" in text
    assert "офферов <b>7</b>" in text
    assert "ads <b>42</b>" in text
    # «Тихий день» не должен появиться, активность есть
    assert "За окно не было активности" not in text


# Длинное имя объявления обрезается, чтобы не выходить за разумную ширину
def test_render_truncates_long_ad_name() -> None:
    long_name = "A" * 200
    payload = DigestPayload(
        window_start_utc=_ts(2026, 5, 26, 9),
        window_end_utc=_ts(2026, 5, 27, 9),
        alerts_warning_count=0,
        alerts_stop_count=0,
        top_ads_by_spend=[
            TopAdRow(
                ad_id=uuid.uuid4(),
                fb_ad_id="2300000000003",
                ad_name=long_name,
                offer_code=None,
                spend_usd=Decimal("10"),
                clicks=1,
                leads=0,
                cpc=None,
                cost_per_lead=None,
            )
        ],
        disable_tasks_succeeded=0,
        disable_tasks_failed=0,
        active_offers_count=1,
        active_ads_count=1,
        total_spend_window_usd=Decimal("10"),
    )
    text = render_digest(payload)
    assert "AAAAA" in text
    assert "..." in text
    # 200 «A» подряд внутри строки не остаётся
    assert "A" * 200 not in text


# Когда offer_code отсутствует, рисуем прочерк, не падаем
def test_render_no_offer_code() -> None:
    payload = DigestPayload(
        window_start_utc=_ts(2026, 5, 26, 9),
        window_end_utc=_ts(2026, 5, 27, 9),
        alerts_warning_count=0,
        alerts_stop_count=0,
        top_ads_by_spend=[
            TopAdRow(
                ad_id=uuid.uuid4(),
                fb_ad_id="2300000000004",
                ad_name="bare ad",
                offer_code=None,
                spend_usd=Decimal("5.00"),
                clicks=2,
                leads=0,
                cpc=Decimal("2.5"),
                cost_per_lead=None,
            )
        ],
        disable_tasks_succeeded=0,
        disable_tasks_failed=0,
        active_offers_count=0,
        active_ads_count=0,
        total_spend_window_usd=Decimal("5.00"),
    )
    text = render_digest(payload)
    assert "bare ad" in text
    assert "—" in text  # cost_per_lead=None → «—» в колонке CPL


# HTML-теги в названии объявления экранируются — не ломаем parse_mode=HTML
def test_render_escapes_html_in_ad_name() -> None:
    payload = DigestPayload(
        window_start_utc=_ts(2026, 5, 26, 9),
        window_end_utc=_ts(2026, 5, 27, 9),
        alerts_warning_count=1,
        alerts_stop_count=0,
        top_ads_by_spend=[
            TopAdRow(
                ad_id=uuid.uuid4(),
                fb_ad_id="2300000000005",
                ad_name="<script>alert(1)</script>",
                offer_code="X<Y>",
                spend_usd=Decimal("1"),
                clicks=0,
                leads=0,
                cpc=None,
                cost_per_lead=None,
            )
        ],
        disable_tasks_succeeded=0,
        disable_tasks_failed=0,
        active_offers_count=1,
        active_ads_count=1,
        total_spend_window_usd=Decimal("1"),
    )
    text = render_digest(payload)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "X&lt;Y&gt;" in text


# Регресс (баг дайджеста 06-02): непустой Топ-5 из НУЛЕВЫХ строк не должен прятать
# «тихий» блок. Раньше has_activity включал `or top_ads_by_spend`, и непустой список
# нулей ложно давал has_activity=True. Теперь активность — только алерты и реальный spend.
def test_render_zero_spend_top_ads_still_idle() -> None:
    payload = DigestPayload(
        window_start_utc=_ts(2026, 5, 26, 9),
        window_end_utc=_ts(2026, 5, 27, 9),
        alerts_warning_count=0,
        alerts_stop_count=0,
        top_ads_by_spend=[
            TopAdRow(
                ad_id=uuid.uuid4(),
                fb_ad_id="2300000000099",
                ad_name="zero spend ad",
                offer_code="KE_CR2",
                spend_usd=Decimal("0"),
                clicks=0,
                leads=0,
                cpc=None,
                cost_per_lead=None,
            )
        ],
        disable_tasks_succeeded=0,
        disable_tasks_failed=0,
        active_offers_count=0,
        active_ads_count=60,
        total_spend_window_usd=Decimal("0"),
    )
    text = render_digest(payload)
    assert "За окно не было активности" in text
