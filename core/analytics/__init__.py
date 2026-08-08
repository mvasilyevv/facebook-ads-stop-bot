"""Operator-facing performance analytics."""

from datetime import timedelta

from core.analytics.budget import LiveBudget, calculate_live_budget

DEFAULT_ANALYTICS_WINDOW = timedelta(days=7)

__all__ = [
    "DEFAULT_ANALYTICS_WINDOW",
    "LiveBudget",
    "calculate_live_budget",
]
