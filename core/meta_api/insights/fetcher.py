# -*- coding: utf-8 -*-
"""InsightsFetcher — высокоуровневые запросы к /insights с пагинацией.

Поверх MetaApiClient. Возвращает list[MetaInsightsRow] вместо сырого dict.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

from core.meta_api.adapters import meta_insights_row_from_dict
from core.meta_api.client import MetaApiClient
from core.meta_api.schemas import MetaInsightsRequest, MetaInsightsRow

logger = logging.getLogger(__name__)

# Хард-лимит на количество страниц одной пагинации (защита от бесконечного цикла).
_MAX_PAGES = 20


class InsightsFetcher:
    """Высокоуровневые методы для GET /act_X/insights.

    Использование:
        fetcher = InsightsFetcher(client)
        rows = await fetcher.fetch_for_ads(
            ad_account_id="act_123",
            ad_ids=["ad1", "ad2"],
            date_preset="today",
        )
    """

    def __init__(self, client: MetaApiClient) -> None:
        self._client = client

    async def fetch_for_request(self, req: MetaInsightsRequest) -> list[MetaInsightsRow]:
        """Полная пагинация по MetaInsightsRequest → плоский list[MetaInsightsRow].

        Если paging.cursors.after есть — продолжаем тянуть страницы (до _MAX_PAGES).
        """
        params: dict[str, str] = {
            "level": req.level,
            "fields": ",".join(req.fields),
            "limit": str(req.limit),
            "action_attribution_windows": json.dumps(list(req.action_attribution_windows)),
        }
        if req.date_preset:
            params["date_preset"] = req.date_preset
        if req.since and req.until:
            params["time_range"] = json.dumps(
                {"since": req.since.isoformat(), "until": req.until.isoformat()}
            )
        if req.filtering:
            params["filtering"] = json.dumps([dict(f) for f in req.filtering])
        if req.breakdowns:
            params["breakdowns"] = ",".join(req.breakdowns)

        endpoint = f"/{req.ad_account_id}/insights"
        rows: list[MetaInsightsRow] = []
        next_after: str | None = None

        for _page_idx in range(_MAX_PAGES):
            page_params = dict(params)
            if next_after:
                page_params["after"] = next_after

            response = await self._client.execute_graph_call(
                method="GET", endpoint=endpoint, query_params=page_params
            )
            data = response.get("data") or []
            for item in data:
                rows.append(meta_insights_row_from_dict(item, ad_account_id=req.ad_account_id))

            paging = response.get("paging") or {}
            cursors = paging.get("cursors") or {}
            next_after = cursors.get("after") if paging.get("next") else None
            if not next_after:
                break
        else:
            logger.warning(
                "fetch_for_request: достигнут лимит %d страниц для %s",
                _MAX_PAGES,
                endpoint,
            )

        return rows

    # ====================== удобные шорткаты ======================

    async def fetch_for_ads(
        self,
        *,
        ad_account_id: str,
        ad_ids: list[str],
        date_preset: str | None = "today",
        fields: tuple[str, ...] | None = None,
    ) -> list[MetaInsightsRow]:
        """Insights по конкретным ad_id (filtering=ad.id IN [...])."""
        kwargs: dict = {
            "ad_account_id": ad_account_id,
            "level": "ad",
            "date_preset": date_preset,
            "filtering": ({"field": "ad.id", "operator": "IN", "value": json.dumps(ad_ids)},),
            "limit": min(max(len(ad_ids), 25), 500),
        }
        if fields is not None:
            kwargs["fields"] = fields
        req = MetaInsightsRequest(**kwargs)
        return await self.fetch_for_request(req)

    async def fetch_for_campaigns(
        self,
        *,
        ad_account_id: str,
        campaign_ids: list[str],
        date_preset: str | None = "today",
        level: str = "campaign",
    ) -> list[MetaInsightsRow]:
        """Insights по конкретным campaign_id (filtering=campaign.id IN [...])."""
        req = MetaInsightsRequest(
            ad_account_id=ad_account_id,
            level=level,
            date_preset=date_preset,
            filtering=(
                {"field": "campaign.id", "operator": "IN", "value": json.dumps(campaign_ids)},
            ),
            limit=min(max(len(campaign_ids) * 5, 25), 500),
        )
        return await self.fetch_for_request(req)

    async def fetch_account_summary(
        self,
        *,
        ad_account_id: str,
        date_preset: str | None = "today",
        fields: tuple[str, ...] = ("spend", "impressions", "clicks", "ctr", "cpc", "cpm"),
    ) -> MetaInsightsRow | None:
        """Сводка по кабинету (level=account, обычно 1 строка)."""
        req = MetaInsightsRequest(
            ad_account_id=ad_account_id,
            level="account",
            date_preset=date_preset,
            fields=fields,
            limit=1,
        )
        rows = await self.fetch_for_request(req)
        return rows[0] if rows else None


def merge_insights_rows_by_ad(rows: list[MetaInsightsRow]) -> dict[str, MetaInsightsRow]:
    """ad_id → последняя строка (для быстрого lookup в pipeline).

    Если ad_id повторяется (например, разные breakdowns) — берётся последняя.
    """
    out: dict[str, MetaInsightsRow] = {}
    for row in rows:
        if row.ad_id:
            out[row.ad_id] = row
    return out


def sum_spend(rows: list[MetaInsightsRow]) -> Decimal:
    """Сумма spend по списку — для bulk-аналитики."""
    return sum((row.spend for row in rows), start=Decimal("0"))
