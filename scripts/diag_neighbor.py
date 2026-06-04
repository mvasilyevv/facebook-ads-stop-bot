# -*- coding: utf-8 -*-
"""Разбор соседней рабочей 1-3-5 кампании (эталон структуры под перезалив GH_AVI)."""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.meta_api.client import MetaApiClient

ACT = "act_26943307705301002"
NEEDLE = "1-3-5 | Ghana | Olstep"


async def call(client, endpoint, qp):
    return await client.execute_graph_call(method="GET", endpoint=endpoint, query_params=qp)


async def main() -> int:
    client = MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST", "localhost"), port=50051)
    await client.start()
    try:
        c = await call(
            client,
            f"/{ACT}/campaigns",
            {
                "fields": "name,effective_status,objective,daily_budget,bid_strategy",
                "limit": "100",
            },
        )
        target = None
        for camp in c.get("data") or []:
            nm = camp.get("name") or ""
            if "1-3-5" in nm and "Olstep" in nm and camp.get("effective_status") == "ACTIVE":
                target = camp
                break
        if not target:
            print("не нашёл активную 1-3-5 Olstep")
            return 1
        cid = target["id"]
        print(f"=== {target['name']} (id={cid}) ===")
        print(
            f"  objective={target.get('objective')} daily_budget={target.get('daily_budget')} "
            f"bid={target.get('bid_strategy')}  -> "
            f"{'CBO (бюджет на кампании)' if target.get('daily_budget') else 'ABO'}"
        )

        # adsets
        ad = await call(
            client,
            f"/{cid}/adsets",
            {
                "fields": "name,effective_status,daily_budget,optimization_goal,billing_event,"
                "destination_type,promoted_object,attribution_spec,targeting,"
                "learning_stage_info,bid_strategy",
                "limit": "50",
            },
        )
        adsets = ad.get("data") or []
        print(f"\n  адсетов: {len(adsets)}")
        for a in adsets:
            tg = a.get("targeting") or {}
            print(f"\n  --- {a.get('name')} [{a.get('effective_status')}] ---")
            print(
                f"    daily_budget={a.get('daily_budget')} (None=CBO)  "
                f"opt={a.get('optimization_goal')} dest={a.get('destination_type')}"
            )
            print(f"    promoted_object={json.dumps(a.get('promoted_object'), ensure_ascii=False)}")
            print(f"    attribution={json.dumps(a.get('attribution_spec'), ensure_ascii=False)}")
            print(f"    geo={json.dumps(tg.get('geo_locations'), ensure_ascii=False)}")
            print(
                f"    age={tg.get('age_min')}-{tg.get('age_max')} "
                f"automation={json.dumps(tg.get('targeting_automation'), ensure_ascii=False)}"
            )
            print(
                f"    publisher_platforms={tg.get('publisher_platforms')} "
                f"fb_pos={tg.get('facebook_positions')}"
            )
            print(f"    learning={json.dumps(a.get('learning_stage_info'), ensure_ascii=False)}")

        # сколько ads на адсет
        ads = await call(
            client,
            f"/{cid}/ads",
            {
                "fields": "name,adset_id,effective_status",
                "limit": "100",
            },
        )
        by_adset: dict[str, int] = {}
        for x in ads.get("data") or []:
            by_adset[x.get("adset_id")] = by_adset.get(x.get("adset_id"), 0) + 1
        print(f"\n  ads всего: {len(ads.get('data') or [])}, по адсетам: {by_adset}")

        # как откручивается (per adset spend/FTD за maximum)
        print("\n  === insights per adset (maximum) ===")
        ins = await call(
            client,
            f"/{cid}/insights",
            {
                "level": "adset",
                "fields": "adset_name,spend,impressions,clicks,actions",
                "date_preset": "maximum",
                "limit": "50",
            },
        )
        for r in ins.get("data") or []:
            acts = {
                a.get("action_type"): a.get("value")
                for a in (r.get("actions") or [])
                if a.get("action_type")
                in ("offsite_conversion.fb_pixel_purchase", "landing_page_view", "link_click")
            }
            print(
                f"    {str(r.get('adset_name'))[:30]:30} spend=${float(r.get('spend') or 0):.2f} "
                f"impr={int(float(r.get('impressions') or 0))} "
                f"FTD={acts.get('offsite_conversion.fb_pixel_purchase', '0')} "
                f"LPV={acts.get('landing_page_view', '0')} link={acts.get('link_click', '0')}"
            )

        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
