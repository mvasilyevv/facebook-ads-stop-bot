# -*- coding: utf-8 -*-
"""Unit-тесты enricher.analyze_one_ad."""

from __future__ import annotations

from core.ad_library.enricher import analyze_one_ad


# Free money hook определяется по "free", "bonus", "$N"
def test_detects_free_money_hook() -> None:
    raw = {"snapshot": {"body": "Get $100 free bonus today!"}}
    result = analyze_one_ad(raw)
    assert "free_money_hook" in result["hooks"]


# CTA "play now" находится
def test_detects_cta() -> None:
    raw = {"snapshot": {"body": "Play now and win big!"}}
    result = analyze_one_ad(raw)
    assert "play now" in result["ctas"]


# Tone aggressive по UPPERCASE + !!!
def test_detects_aggressive_tone() -> None:
    raw = {"snapshot": {"body": "PLAY NOW URGENT!!! INSTANT WIN"}}
    result = analyze_one_ad(raw)
    assert result["tone"] == "aggressive"


# Если текста нет — возвращает структуру с пустыми полями
def test_empty_ad_returns_structure() -> None:
    result = analyze_one_ad({})
    assert result["hooks"] == []
    assert result["ctas"] == []
    assert result["tone"] is None
    assert result["text_length"] == 0


# Enricher version всегда выставлен
def test_enricher_version_present() -> None:
    result = analyze_one_ad({"snapshot": {"body": "hi"}})
    assert result["enricher_version"] == "heuristic_v1"
