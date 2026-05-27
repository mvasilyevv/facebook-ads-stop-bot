# -*- coding: utf-8 -*-
"""Unit-тесты для tier_ranker.compute_tier (pure-функция)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from core.ad_library.tier_ranker import _days_running, compute_tier


# S-tier: 30+ дней + 2+ креатива со страницы → S
def test_tier_s_proven_winner() -> None:
    tier, score, reason = compute_tier(
        days_running=45,
        page_history_count=3,
        cluster_size=1,
        classification_score=1.0,
    )
    assert tier == "S"
    assert score > 0.8


# A-tier: 14-30 дней + сильная page history
def test_tier_a_gaining_momentum() -> None:
    tier, _score, _ = compute_tier(
        days_running=20,
        page_history_count=3,
        cluster_size=1,
        classification_score=1.0,
    )
    assert tier == "A"


# B-tier: 7-14 дней
def test_tier_b_new() -> None:
    tier, _, _ = compute_tier(
        days_running=10,
        page_history_count=1,
        cluster_size=1,
        classification_score=1.0,
    )
    assert tier == "B"


# C-tier: < 7 дней — тест/реджект
def test_tier_c_test() -> None:
    tier, _, _ = compute_tier(
        days_running=3,
        page_history_count=1,
        cluster_size=1,
        classification_score=1.0,
    )
    assert tier == "C"


# Низкий classification_score штрафует score
def test_low_relevance_lowers_score() -> None:
    _, high_score, _ = compute_tier(
        days_running=30,
        page_history_count=2,
        cluster_size=1,
        classification_score=1.0,
    )
    _, low_score, _ = compute_tier(
        days_running=30,
        page_history_count=2,
        cluster_size=1,
        classification_score=0.5,
    )
    assert low_score < high_score


# days_running: считает разницу в днях с now
def test_days_running() -> None:
    now = datetime(2026, 5, 26, tzinfo=timezone.utc)
    started = date(2026, 5, 1)
    assert _days_running(started, now=now) == 25


# days_running: None → 0
def test_days_running_none() -> None:
    assert _days_running(None) == 0


# reason содержит все ключевые поля
def test_tier_reason_structure() -> None:
    _, _, reason = compute_tier(
        days_running=20,
        page_history_count=3,
        cluster_size=1,
        classification_score=0.9,
    )
    assert "days_running" in reason
    assert "page_history_count" in reason
    assert "cluster_size" in reason
    assert "classification_score" in reason
    assert "assigned_tier" in reason
