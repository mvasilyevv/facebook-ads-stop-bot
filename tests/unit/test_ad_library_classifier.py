# -*- coding: utf-8 -*-
"""Unit-тесты для core/ad_library/classifier.py."""

from __future__ import annotations

from core.ad_library.classifier import (
    detect_vertical,
    extract_ad_text,
    score_relevance_to_slot,
)


# Verticals: gambling определяется по casino/bet/slot keywords
def test_detect_vertical_gambling() -> None:
    assert detect_vertical("Lucky Casino", "play and win bonus") == "gambling"


# Verticals: nutra определяется по weight loss / supplement
def test_detect_vertical_nutra() -> None:
    assert detect_vertical("Slim Forever", "lose weight fast with our keto pills") == "nutra"


# Если ни одна вертикаль не угадывается — None
def test_detect_vertical_unknown() -> None:
    assert detect_vertical("Random Page", "buy our chairs") is None


# Score relevance: точное совпадение slot и ad_text → score = 1.0
def test_score_relevance_exact() -> None:
    result = score_relevance_to_slot("chicken road 2", "Play Chicken Road 2 — win prizes")
    assert result.score == 1.0
    assert "chicken" in result.matched_terms
    assert "road" in result.matched_terms


# Score relevance: 0 совпадений → 0.0
def test_score_relevance_zero() -> None:
    result = score_relevance_to_slot("chicken road 2", "Lose weight fast")
    assert result.score == 0.0
    assert result.matched_terms == []


# Score relevance: пустой slot — score 0, не падает
def test_score_relevance_empty_slot() -> None:
    result = score_relevance_to_slot("", "some text")
    assert result.score == 0.0


# Score relevance: 2 из 3 терминов = ~0.67
def test_score_relevance_partial() -> None:
    result = score_relevance_to_slot("aviator crash game", "Aviator crash style spinner")
    assert 0.5 < result.score < 1.0
    assert "aviator" in result.matched_terms
    assert "crash" in result.matched_terms


# extract_ad_text работает на типовой Meta GraphQL структуре
def test_extract_ad_text_basic() -> None:
    raw = {
        "snapshot": {
            "title": "Big Bonus",
            "body": "Get $100 free",
            "page_name": "Lucky Casino",
        }
    }
    text = extract_ad_text(raw)
    assert "Big Bonus" in text
    assert "Get $100 free" in text
    assert "Lucky Casino" in text


# extract_ad_text обрабатывает body как dict {text: "..."}
def test_extract_ad_text_dict_body() -> None:
    raw = {"snapshot": {"body": {"text": "Inner text content"}}}
    text = extract_ad_text(raw)
    assert "Inner text content" in text


# extract_ad_text не падает если snapshot отсутствует
def test_extract_ad_text_missing_snapshot() -> None:
    assert extract_ad_text({}) == ""
