# -*- coding: utf-8 -*-
"""Unit: build_postback_url — подстановка макросов и URL-кодирование (pure, без I/O)."""

from __future__ import annotations

from decimal import Decimal

from core.adset_pro.outgoing import OutgoingPostback, build_postback_url


# Базовая подстановка: все макросы заменяются значениями конверсии.
def test_build_url_substitutes_macros() -> None:
    pb = OutgoingPostback(
        click_id="abc123",
        event_type="ftd",
        revenue=Decimal("25.50"),
        currency="USD",
        fb_ad_id="2300999",
        country="GH",
    )
    tpl = (
        "https://t.example.com/pb?cid={click_id}&goal={event_type}"
        "&sum={revenue}&cur={currency}&ad={fb_ad_id}&geo={country}"
    )
    url = build_postback_url(tpl, pb)
    assert url == (
        "https://t.example.com/pb?cid=abc123&goal=ftd&sum=25.50&cur=USD&ad=2300999&geo=GH"
    )


# Алиасы goal/payout — синонимы event_type/revenue (affiliate-конвенция).
def test_build_url_aliases_goal_and_payout() -> None:
    pb = OutgoingPostback(click_id="c1", event_type="redep", revenue=Decimal("10"))
    url = build_postback_url("https://x/pb?goal={goal}&payout={payout}", pb)
    assert url == "https://x/pb?goal=redep&payout=10"


# URL-кодирование: спецсимволы в значениях экранируются (защита query-string).
def test_build_url_encodes_special_chars() -> None:
    pb = OutgoingPostback(click_id="a b&c=1", event_type="ft d", revenue=None)
    url = build_postback_url("https://x/pb?cid={click_id}&g={event_type}", pb)
    # Пробел → %20, & → %26, = → %3D.
    assert url == "https://x/pb?cid=a%20b%26c%3D1&g=ft%20d"


# None revenue/fb_ad_id/country → пустые значения (не строка "None").
def test_build_url_none_values_become_empty() -> None:
    pb = OutgoingPostback(click_id="c", event_type="reg", revenue=None, fb_ad_id=None, country=None)
    url = build_postback_url("https://x/pb?s={revenue}&ad={fb_ad_id}&geo={country}", pb)
    assert url == "https://x/pb?s=&ad=&geo="


# Неизвестный макрос в шаблоне остаётся как есть — билдер не падает.
def test_build_url_unknown_macro_left_intact() -> None:
    pb = OutgoingPostback(click_id="c", event_type="ftd")
    url = build_postback_url("https://x/pb?cid={click_id}&z={unknown}", pb)
    assert url == "https://x/pb?cid=c&z={unknown}"
