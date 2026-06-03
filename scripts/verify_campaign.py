# -*- coding: utf-8 -*-
"""Верификация кампании по факту: start_time адсетов, страница и text_optimizations
в объявлениях, ключевые поля. Money-проверка перед unpause.

    python scripts/verify_campaign.py 120245240270270044
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.meta_api.client import MetaApiClient

DEFAULT_CAMPAIGN = "120245240270270044"


async def main(campaign_id: str) -> int:
    client = MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST", "localhost"), port=50051)
    await client.start()
    try:
        camp = await client.execute_graph_call(
            method="GET",
            endpoint=f"/{campaign_id}",
            query_params={"fields": "name,objective,status,buying_type"},
        )
        print(f"КАМПАНИЯ: {camp.get('name')}  [{camp.get('objective')}] {camp.get('status')}")

        adsets = await client.execute_graph_call(
            method="GET",
            endpoint=f"/{campaign_id}/adsets",
            query_params={
                "fields": "name,start_time,status,daily_budget,bid_strategy,"
                "targeting,optimization_goal",
                "limit": "20",
            },
        )
        print("\n— АДСЕТЫ (start_time / бюджет / гео) —")
        for a in sorted(adsets.get("data") or [], key=lambda x: x.get("name", "")):
            geo = (a.get("targeting") or {}).get("geo_locations", {}).get("countries")
            print(
                f"  {a.get('name')}: start={a.get('start_time')} budget={a.get('daily_budget')} "
                f"bid={a.get('bid_strategy')} geo={geo} status={a.get('status')}"
            )

        ads = await client.execute_graph_call(
            method="GET",
            endpoint=f"/{campaign_id}/ads",
            query_params={
                "fields": "name,creative{object_story_spec{page_id},degrees_of_freedom_spec,url_tags}",
                "limit": "20",
            },
        )
        print("\n— ОБЪЯВЛЕНИЯ (страница / оптимизация текста / url_tags) —")
        for ad in sorted(ads.get("data") or [], key=lambda x: x.get("name", "")):
            cr = ad.get("creative") or {}
            pid = (cr.get("object_story_spec") or {}).get("page_id")
            dof = cr.get("degrees_of_freedom_spec") or {}
            topt = (
                (dof.get("creative_features_spec") or {})
                .get("text_optimizations", {})
                .get("enroll_status")
            )
            print(f"  {ad.get('name')}: page_id={pid} text_optimizations={topt}")
            print(f"     url_tags: {cr.get('url_tags')}")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CAMPAIGN
    raise SystemExit(asyncio.run(main(cid)))
