# -*- coding: utf-8 -*-
"""Выгрузка для настройки залива: таймзона кабинета, все доступные FB-страницы
(с группировкой), creative-поля текущей кампании (что за «оптимизация текста»).

    python scripts/inspect_setup.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.meta_api.client import MetaApiClient

ACT = "act_26943307705301002"
CAMPAIGN_ID = "120245235800890044"


async def main() -> int:
    client = MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST", "localhost"), port=50051)
    await client.start()
    try:
        # 1) таймзона кабинета
        print("=== ТАЙМЗОНА КАБИНЕТА ===")
        acct = await client.execute_graph_call(
            method="GET",
            endpoint=f"/{ACT}",
            query_params={
                "fields": "name,timezone_name,timezone_offset_hours_utc,currency,account_status"
            },
        )
        print(json.dumps(acct, ensure_ascii=False, indent=2))

        # 2) страницы, доступные для рекламы в этом кабинете
        print("\n=== promote_pages (можно рекламировать в кабинете) ===")
        promo = await client.execute_graph_call(
            method="GET",
            endpoint=f"/{ACT}/promote_pages",
            query_params={
                "fields": "id,name,category,link,verification_status,fan_count",
                "limit": "100",
            },
        )
        for p in promo.get("data") or []:
            print(
                f"  • {p.get('name')}  id={p.get('id')}  cat={p.get('category')} "
                f"fans={p.get('fan_count')} verif={p.get('verification_status')}"
            )
        print(f"  (всего: {len(promo.get('data') or [])})")

        # 3) страницы пользователя (/me/accounts) — с правами и категорией
        print("\n=== /me/accounts (мои страницы: личные/бизнес + права) ===")
        try:
            accs = await client.execute_graph_call(
                method="GET",
                endpoint="/me/accounts",
                query_params={
                    "fields": "id,name,category,tasks,is_published,verification_status",
                    "limit": "100",
                },
            )
            for p in accs.get("data") or []:
                print(
                    f"  • {p.get('name')}  id={p.get('id')}  cat={p.get('category')} "
                    f"published={p.get('is_published')} tasks={p.get('tasks')}"
                )
            print(f"  (всего: {len(accs.get('data') or [])})")
        except Exception as exc:  # noqa: BLE001
            print(f"  /me/accounts недоступен: {exc}")

        # 4) текущий креатив — page_id + degrees_of_freedom_spec (оптимизация текста)
        print("\n=== ТЕКУЩИЙ КРЕАТИВ (страница + enhancements) ===")
        ads = await client.execute_graph_call(
            method="GET",
            endpoint=f"/{CAMPAIGN_ID}/ads",
            query_params={
                "fields": "name,creative{id,name,object_story_spec{page_id},"
                "degrees_of_freedom_spec,asset_feed_spec,contextual_multi_ads}",
                "limit": "2",
            },
        )
        print(json.dumps(ads, ensure_ascii=False, indent=2))
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
