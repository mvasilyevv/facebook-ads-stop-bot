# -*- coding: utf-8 -*-
"""Доп.диагностика GH_AVI: временной разрез открутки + история пикселя.

- insights time_increment=1 (по дням) и hourly (за сегодня/вчера) → когда крутилось.
- pixel stats: были ли вообще purchase-события на пикселе (холодный?).
- ad-level effective_status повторно + recommendations.

  python scripts/diag_gh_avi_time.py 120245242667780044 1282495953856981
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.meta_api.client import MetaApiClient

CAMPAIGN = "120245242667780044"
PIXEL = "1282495953856981"


async def call(client, endpoint, qp):
    return await client.execute_graph_call(method="GET", endpoint=endpoint, query_params=qp)


async def main(campaign_id: str, pixel_id: str) -> int:
    client = MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST", "localhost"), port=50051)
    await client.start()
    try:
        # 1. Дневной разрез открутки (когда были показы)
        print("=== insights time_increment=1 (по дням, maximum) ===")
        r = await call(
            client,
            f"/{campaign_id}/insights",
            {
                "level": "campaign",
                "fields": "spend,impressions,clicks,reach",
                "date_preset": "maximum",
                "time_increment": "1",
                "limit": "100",
            },
        )
        for d in r.get("data") or []:
            print(
                f"  {d.get('date_start')} → spend=${float(d.get('spend') or 0):.2f} "
                f"impr={int(float(d.get('impressions') or 0))} "
                f"clk={int(float(d.get('clicks') or 0))} "
                f"reach={int(float(d.get('reach') or 0))}"
            )

        # 2. Часовой разрез за сегодня (когда именно крутилось)
        print("\n=== insights hourly (today) ===")
        try:
            r = await call(
                client,
                f"/{campaign_id}/insights",
                {
                    "level": "campaign",
                    "fields": "spend,impressions,clicks",
                    "date_preset": "today",
                    "breakdowns": "hourly_stats_aggregated_by_advertiser_time_zone",
                    "limit": "100",
                },
            )
            for d in r.get("data") or []:
                print(
                    f"  {d.get('hourly_stats_aggregated_by_advertiser_time_zone')} → "
                    f"spend=${float(d.get('spend') or 0):.2f} "
                    f"impr={int(float(d.get('impressions') or 0))}"
                )
            if not (r.get("data")):
                print("  (нет показов сегодня)")
        except Exception as exc:  # noqa: BLE001
            print(f"  ОШИБКА: {exc!r}")

        # 3. Часовой за вчера
        print("\n=== insights hourly (yesterday) ===")
        try:
            r = await call(
                client,
                f"/{campaign_id}/insights",
                {
                    "level": "campaign",
                    "fields": "spend,impressions,clicks",
                    "date_preset": "yesterday",
                    "breakdowns": "hourly_stats_aggregated_by_advertiser_time_zone",
                    "limit": "100",
                },
            )
            for d in r.get("data") or []:
                print(
                    f"  {d.get('hourly_stats_aggregated_by_advertiser_time_zone')} → "
                    f"spend=${float(d.get('spend') or 0):.2f} "
                    f"impr={int(float(d.get('impressions') or 0))}"
                )
            if not (r.get("data")):
                print("  (нет показов вчера)")
        except Exception as exc:  # noqa: BLE001
            print(f"  ОШИБКА: {exc!r}")

        # 4. Пиксель: общая инфа + активность (холодный?)
        print("\n=== PIXEL info ===")
        try:
            p = await call(
                client,
                f"/{pixel_id}",
                {
                    "fields": "name,last_fired_time,is_unavailable,data_use_setting,"
                    "automatic_matching_fields,enable_automatic_matching",
                },
            )
            print(json.dumps(p, ensure_ascii=False, indent=2))
        except Exception as exc:  # noqa: BLE001
            print(f"  ОШИБКА pixel info: {exc!r}")

        print("\n=== PIXEL stats (events за 7 дней) ===")
        try:
            ps = await call(
                client,
                f"/{pixel_id}/stats",
                {
                    "aggregation": "event",
                    "limit": "50",
                },
            )
            print(json.dumps(ps, ensure_ascii=False, indent=2)[:2000])
        except Exception as exc:  # noqa: BLE001
            print(f"  ОШИБКА pixel stats: {exc!r}")

        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else CAMPAIGN
    pix = sys.argv[2] if len(sys.argv) > 2 else PIXEL
    raise SystemExit(asyncio.run(main(cid, pix)))
