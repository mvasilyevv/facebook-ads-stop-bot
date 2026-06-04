# -*- coding: utf-8 -*-
"""Метрики кампании через Graph API insights (диагностика залива).

Печатает per-ad и per-adset: spend, показы, охват, частота, клики, CTR, CPC,
CPM, link-клики, LPV, FTD (purchase) + CPA. Плюс campaign total.

    python scripts/get_insights.py 120245242667780044
    python scripts/get_insights.py 120245242667780044 last_7d
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.meta_api.client import MetaApiClient

DEFAULT_CAMPAIGN = "120245242667780044"

FIELDS = (
    "ad_name,adset_name,spend,impressions,reach,frequency,clicks,ctr,cpc,cpm,"
    "inline_link_clicks,inline_link_click_ctr,actions,cost_per_action_type"
)

# типы action, важные для гемблинга
ACT_KEYS = {
    "offsite_conversion.fb_pixel_purchase": "FTD",
    "purchase": "purchase",
    "offsite_conversion.fb_pixel_lead": "lead",
    "lead": "lead2",
    "landing_page_view": "LPV",
    "link_click": "link",
    "offsite_conversion.fb_pixel_complete_registration": "reg",
}


def actions_map(actions: list | None) -> dict:
    out: dict[str, float] = {}
    for a in actions or []:
        t = a.get("action_type")
        if t in ACT_KEYS:
            out[ACT_KEYS[t]] = float(a.get("value") or 0)
    return out


async def fetch(client: MetaApiClient, node: str, level: str, preset: str) -> list:
    resp = await client.execute_graph_call(
        method="GET",
        endpoint=f"/{node}/insights",
        query_params={"level": level, "fields": FIELDS, "date_preset": preset, "limit": "100"},
    )
    return resp.get("data") or []


def row(r: dict) -> str:
    am = actions_map(r.get("actions"))
    spend = float(r.get("spend") or 0)
    ftd = am.get("FTD") or am.get("purchase") or 0
    cpa = (spend / ftd) if ftd else None
    name = r.get("ad_name") or r.get("adset_name") or "?"
    return (
        f"  {name[:34]:34} spend=${spend:6.2f} impr={int(float(r.get('impressions') or 0)):6} "
        f"reach={int(float(r.get('reach') or 0)):6} freq={float(r.get('frequency') or 0):.2f} "
        f"clk={int(float(r.get('clicks') or 0)):4} ctr={float(r.get('ctr') or 0):.2f}% "
        f"cpc=${float(r.get('cpc') or 0):.2f} cpm=${float(r.get('cpm') or 0):.1f} "
        f"lnk={int(float(r.get('inline_link_clicks') or 0)):4} "
        f"LPV={int(am.get('LPV') or 0):3} FTD={int(ftd):2} "
        f"CPA={('$%.2f' % cpa) if cpa else '—'}"
    )


async def main(campaign_id: str, preset: str) -> int:
    client = MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST", "localhost"), port=50051)
    await client.start()
    try:
        print(f"=== CAMPAIGN total ({preset}) ===")
        for r in await fetch(client, campaign_id, "campaign", preset):
            print(row(r))

        print("\n=== по АДСЕТАМ ===")
        for r in sorted(
            await fetch(client, campaign_id, "adset", preset),
            key=lambda x: x.get("adset_name", ""),
        ):
            print(row(r))

        print("\n=== по ОБЪЯВЛЕНИЯМ ===")
        for r in sorted(
            await fetch(client, campaign_id, "ad", preset), key=lambda x: x.get("ad_name", "")
        ):
            print(row(r))
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CAMPAIGN
    preset = sys.argv[2] if len(sys.argv) > 2 else "maximum"
    raise SystemExit(asyncio.run(main(cid, preset)))
