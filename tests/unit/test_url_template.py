# -*- coding: utf-8 -*-
"""Тесты build_url_params — проверка sub-параметров трекинга."""

from __future__ import annotations

from core.campaign_creator.steps.set_tracking_url import build_url_params


# Сценарий: операторские инициалы зашиты как MV (sub2).
def test_sub2_operator_initials():
    params = build_url_params(ad_name="ad_1", cabinet_id="act_999")
    assert "sub2=MV" in params


# Сценарий: cabinet_id попадает в sub4.
def test_sub4_contains_cabinet_id():
    params = build_url_params(ad_name="ad_1", cabinet_id="act_999")
    assert "sub4=act_999" in params


# Сценарий: ad_name попадает в sub3.
def test_sub3_contains_ad_name():
    params = build_url_params(ad_name="My Ad #1", cabinet_id="act_999")
    assert "sub3=My Ad #1" in params


# Сценарий: FB-макросы остаются в двойных скобках без экранирования.
def test_fb_macros_preserved():
    params = build_url_params(ad_name="x", cabinet_id="y")
    assert "sub5={{campaign.name}}" in params
    assert "sub6={{adset.name}}" in params
    assert "sub7={{ad.name}}" in params
