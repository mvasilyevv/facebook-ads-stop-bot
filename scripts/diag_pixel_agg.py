# -*- coding: utf-8 -*-
"""Агрегат активности пикселя по типам событий за доступный период."""

from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.meta_api.client import MetaApiClient

PIXEL = "1282495953856981"


async def main(pixel_id: str) -> int:
    client = MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST", "localhost"), port=50051)
    await client.start()
    try:
        # суммируем по event за всё, что отдаёт /stats (обычно ~7 дней почасово)
        resp = await client.execute_graph_call(
            method="GET",
            endpoint=f"/{pixel_id}/stats",
            query_params={"aggregation": "event", "limit": "500"},
        )
        totals: dict[str, int] = defaultdict(int)
        per_day: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        first_ts = last_ts = None
        for bucket in resp.get("data") or []:
            ts = bucket.get("timestamp") or bucket.get("start_time") or ""
            day = ts[:10]
            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts
            for ev in bucket.get("data") or []:
                v = ev.get("value")
                c = int(ev.get("count") or 0)
                totals[v] += c
                per_day[day][v] += c

        print(f"Период stats: {first_ts} .. {last_ts}")
        print("\n=== ИТОГО по типам событий (весь период stats) ===")
        for k in sorted(totals, key=lambda x: -totals[x]):
            print(f"  {k:28} {totals[k]}")

        print("\n=== по дням ===")
        for day in sorted(per_day):
            line = "  ".join(f"{k}={v}" for k, v in sorted(per_day[day].items()))
            print(f"  {day}: {line}")

        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    pix = sys.argv[1] if len(sys.argv) > 1 else PIXEL
    raise SystemExit(asyncio.run(main(pix)))
