# -*- coding: utf-8 -*-
"""Enable Recommendation — pure-функции анализа.

Воркер (apps/enable_recommendation_worker) собирает кандидатов в STOP_SENT/DISABLED,
прогоняет их через `analyzer.should_recommend` и публикует typed notification event.
"""

from core.enable_reco.analyzer import (
    AnalyzerThresholds,
    MetricSnapshot,
    OfferThresholds,
    RecommendationDecision,
    should_recommend,
)

__all__ = [
    "AnalyzerThresholds",
    "MetricSnapshot",
    "OfferThresholds",
    "RecommendationDecision",
    "should_recommend",
]
