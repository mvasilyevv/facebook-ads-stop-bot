# -*- coding: utf-8 -*-
"""Композитные view-функции для дашборда фронта.

Объединяют каталог (fb_ads/fb_adsets/fb_campaigns/offers), FSM-состояние
(ad_alert_state), последние метрики (ad_metrics partitioned) и опциональный
Meta API observation в один плоский dict — основная точка соприкосновения
с DashboardPage и AdsPage.
"""

from __future__ import annotations

from core.dashboard.snapshot import build_ad_snapshot

__all__ = ["build_ad_snapshot"]
