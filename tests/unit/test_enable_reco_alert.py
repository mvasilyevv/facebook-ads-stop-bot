# -*- coding: utf-8 -*-
"""Юнит-тесты web_app deep-link кнопки под enable_reco алертом."""

from __future__ import annotations

from core.enable_reco.alert import EnableRecoRenderInput, render_enable_reco_alert
from core.enable_reco.analyzer import RecommendationDecision


def _decision() -> RecommendationDecision:
    """Минимальное решение-рекомендация для рендера (level=warning).

    Поля сверены с core/enable_reco/analyzer.py::RecommendationDecision:
    recommend/level/reasons(tuple)/skip_reason/snapshot. reasons — tuple.
    """
    return RecommendationDecision(
        recommend=True, level="warning", reasons=("CPL выправился",), snapshot={}
    )


def _inp(**over) -> EnableRecoRenderInput:
    base = dict(
        recommendation_id="00000000-0000-0000-0000-000000000900",
        fb_ad_id="900",
        ad_name="Ad",
        campaign_name="CR2|KE",
        adset_name="EQ",
        offer_code="KE",
        decision=_decision(),
    )
    base.update(over)
    return EnableRecoRenderInput(**base)


# при https-base web_app-кнопка идёт первой строкой и ведёт на /ads/900
def test_web_app_button_present_when_base_set():
    _text, markup = render_enable_reco_alert(_inp(web_app_base="https://h.ts.net/tma"))
    rows = markup["inline_keyboard"]
    assert rows[0][0]["text"] == "🔎 Открыть в Mini App"
    assert rows[0][0]["web_app"]["url"] == "https://h.ts.net/tma/ads/900"
    assert rows[1][0]["text"] == "▶️ Включить"


# без base — только «Включить» (текущее поведение)
def test_web_app_button_absent_when_base_none():
    _text, markup = render_enable_reco_alert(_inp(web_app_base=None))
    rows = markup["inline_keyboard"]
    assert len(rows) == 1
    assert rows[0][0]["text"] == "▶️ Включить"
