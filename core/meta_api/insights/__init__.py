# -*- coding: utf-8 -*-
"""Insights helpers поверх MetaApiClient.

InsightsFetcher — высокоуровневые методы fetch_for_ads/campaigns/account
с автоматической пагинацией и парсингом в MetaInsightsRow.
"""

from core.meta_api.insights.fetcher import InsightsFetcher

__all__ = ["InsightsFetcher"]
