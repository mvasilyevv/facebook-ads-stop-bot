# -*- coding: utf-8 -*-
"""Регрессия: Meta IN-filter принимает массив, а не JSON-строку массива."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.meta_api.insights.fetcher import InsightsFetcher


@pytest.mark.asyncio
async def test_fetch_for_ads_serializes_in_value_as_array() -> None:
    client = MagicMock()
    client.execute_graph_call = AsyncMock(return_value={"data": []})

    await InsightsFetcher(client).fetch_for_ads(
        ad_account_id="act_42",
        ad_ids=["100", "200"],
        date_preset="today",
    )

    filtering = json.loads(client.execute_graph_call.await_args.kwargs["query_params"]["filtering"])
    assert filtering == [{"field": "ad.id", "operator": "IN", "value": ["100", "200"]}]


@pytest.mark.asyncio
async def test_fetch_for_campaigns_serializes_in_value_as_array() -> None:
    client = MagicMock()
    client.execute_graph_call = AsyncMock(return_value={"data": []})

    await InsightsFetcher(client).fetch_for_campaigns(
        ad_account_id="act_42",
        campaign_ids=["300", "400"],
        date_preset="today",
    )

    filtering = json.loads(client.execute_graph_call.await_args.kwargs["query_params"]["filtering"])
    assert filtering == [{"field": "campaign.id", "operator": "IN", "value": ["300", "400"]}]
