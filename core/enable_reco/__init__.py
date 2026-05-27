# -*- coding: utf-8 -*-
"""Enable Recommendation — pure-функции анализа и рендера алерта.

Воркер (apps/enable_recommendation_worker) собирает кандидатов в STOP_SENT/DISABLED,
прогоняет их через `analyzer.should_recommend` и шлёт TG-алерт с inline-кнопкой
«Включить». Нажатие кнопки создаёт `task_queue.task_type='enable'`.
"""

from core.enable_reco.alert import build_enable_reco_callback, render_enable_reco_alert
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
    "build_enable_reco_callback",
    "render_enable_reco_alert",
    "should_recommend",
]
