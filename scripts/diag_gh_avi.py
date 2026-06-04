# -*- coding: utf-8 -*-
"""Глубокая диагностика залива GH_AVI: breakdowns + статусы объектов + воронка.

Делает за один проход:
  - insights с breakdowns: country, publisher_platform+platform_position,
    impression_device, age, gender (на уровне campaign).
  - effective_status / issues_info / delivery / learning каждого adset и ad.
  - actions-воронку (impr → link_click → outbound_click → landing_page_view → FTD).

    python scripts/diag_gh_avi.py 120245242667780044
    python scripts/diag_gh_avi.py 120245242667780044 maximum
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.meta_api.client import MetaApiClient

DEFAULT_CAMPAIGN = "120245242667780044"

INS_FIELDS = (
    "spend,impressions,reach,frequency,clicks,ctr,cpc,cpm,"
    "inline_link_clicks,actions,cost_per_action_type"
)

# интересующие нас action_type для воронки
FUNNEL_KEYS = {
    "link_click": "link_click",
    "outbound_click": "outbound_click",
    "landing_page_view": "LPV",
    "offsite_conversion.fb_pixel_purchase": "FTD",
    "purchase": "purchase_all",
    "offsite_conversion.fb_pixel_lead": "lead_px",
    "offsite_conversion.fb_pixel_complete_registration": "reg_px",
    "offsite_conversion.fb_pixel_initiate_checkout": "ic_px",
    "offsite_conversion.fb_pixel_add_to_cart": "atc_px",
    "video_view": "video_view",
    "post_engagement": "post_eng",
}


def amap(actions: list | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for a in actions or []:
        t = a.get("action_type")
        v = float(a.get("value") or 0)
        out[t] = v  # сырое, по полному ключу
    return out


def funnel_line(actions: list | None) -> str:
    m = amap(actions)
    parts = []
    for raw, label in FUNNEL_KEYS.items():
        if raw in m:
            parts.append(f"{label}={int(m[raw])}")
    return ", ".join(parts) if parts else "(no actions)"


async def ins(client: MetaApiClient, node: str, level: str, preset: str, breakdowns: str | None):
    qp = {"level": level, "fields": INS_FIELDS, "date_preset": preset, "limit": "200"}
    if breakdowns:
        qp["breakdowns"] = breakdowns
    resp = await client.execute_graph_call(
        method="GET", endpoint=f"/{node}/insights", query_params=qp
    )
    return resp.get("data") or []


async def get_node(client: MetaApiClient, node: str, fields: str) -> dict:
    return await client.execute_graph_call(
        method="GET", endpoint=f"/{node}", query_params={"fields": fields}
    )


def brk_row(r: dict, dims: list[str]) -> str:
    key = " | ".join(str(r.get(d, "?")) for d in dims)
    spend = float(r.get("spend") or 0)
    impr = int(float(r.get("impressions") or 0))
    clk = int(float(r.get("clicks") or 0))
    lnk = int(float(r.get("inline_link_clicks") or 0))
    ctr = float(r.get("ctr") or 0)
    cpm = float(r.get("cpm") or 0)
    m = amap(r.get("actions"))
    lpv = int(m.get("landing_page_view") or 0)
    ftd = int(m.get("offsite_conversion.fb_pixel_purchase") or 0)
    return (
        f"  {key[:40]:40} spend=${spend:5.2f} impr={impr:5} clk={clk:3} "
        f"lnk={lnk:3} ctr={ctr:.2f}% cpm=${cpm:5.2f} LPV={lpv:3} FTD={ftd:2}"
    )


async def main(campaign_id: str, preset: str) -> int:
    client = MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST", "localhost"), port=50051)
    await client.start()
    try:
        # ---------- BREAKDOWNS на уровне кампании ----------
        for title, brk, dims in [
            ("COUNTRY", "country", ["country"]),
            (
                "PLATFORM x POSITION",
                "publisher_platform,platform_position",
                ["publisher_platform", "platform_position"],
            ),
            ("DEVICE", "impression_device", ["impression_device"]),
            ("AGE", "age", ["age"]),
            ("GENDER", "gender", ["gender"]),
            ("AGE x GENDER", "age,gender", ["age", "gender"]),
        ]:
            print(f"\n=== BREAKDOWN: {title} ({preset}) ===")
            try:
                rows = await ins(client, campaign_id, "campaign", preset, brk)
                if not rows:
                    print("  (нет данных)")
                for r in rows:
                    print(brk_row(r, dims))
            except Exception as exc:  # noqa: BLE001
                print(f"  ОШИБКА: {exc!r}")

        # ---------- ВОРОНКА на уровне кампании ----------
        print(f"\n=== ВОРОНКА campaign-total ({preset}) ===")
        for r in await ins(client, campaign_id, "campaign", preset, None):
            print(
                f"  impr={int(float(r.get('impressions') or 0))} "
                f"clicks(all)={int(float(r.get('clicks') or 0))} "
                f"inline_link={int(float(r.get('inline_link_clicks') or 0))}"
            )
            print(f"  actions: {funnel_line(r.get('actions'))}")
            print(f"  RAW actions: {json.dumps(r.get('actions'), ensure_ascii=False)}")
            print(
                f"  RAW cost_per_action: "
                f"{json.dumps(r.get('cost_per_action_type'), ensure_ascii=False)}"
            )

        # ---------- СТАТУСЫ campaign ----------
        print("\n=== STATUS: CAMPAIGN ===")
        c = await get_node(
            client,
            campaign_id,
            "name,effective_status,configured_status,objective,buying_type,"
            "special_ad_categories,daily_budget,lifetime_budget,bid_strategy,"
            "issues_info,created_time,start_time,stop_time",
        )
        print(json.dumps(c, ensure_ascii=False, indent=2))

        # ---------- adsets кампании ----------
        adsets_resp = await client.execute_graph_call(
            method="GET",
            endpoint=f"/{campaign_id}/adsets",
            query_params={
                "fields": (
                    "name,effective_status,configured_status,status,"
                    "daily_budget,bid_strategy,bid_amount,billing_event,"
                    "optimization_goal,destination_type,start_time,end_time,"
                    "learning_stage_info,issues_info,promoted_object,"
                    "attribution_spec,targeting"
                ),
                "limit": "50",
            },
        )
        print("\n=== STATUS: ADSETS ===")
        for a in adsets_resp.get("data") or []:
            print(f"\n--- adset: {a.get('name')} (id={a.get('id')}) ---")
            print(
                f"  effective_status = {a.get('effective_status')}  "
                f"configured = {a.get('configured_status')}"
            )
            print(
                f"  daily_budget = {a.get('daily_budget')}  "
                f"bid_strategy = {a.get('bid_strategy')}  bid_amount = {a.get('bid_amount')}"
            )
            print(
                f"  opt_goal = {a.get('optimization_goal')}  "
                f"billing = {a.get('billing_event')}  dest = {a.get('destination_type')}"
            )
            print(f"  start = {a.get('start_time')}  end = {a.get('end_time')}")
            print(
                f"  learning_stage_info = "
                f"{json.dumps(a.get('learning_stage_info'), ensure_ascii=False)}"
            )
            print(f"  issues_info = {json.dumps(a.get('issues_info'), ensure_ascii=False)}")
            print(f"  promoted_object = {json.dumps(a.get('promoted_object'), ensure_ascii=False)}")
            print(
                f"  attribution_spec = {json.dumps(a.get('attribution_spec'), ensure_ascii=False)}"
            )
            tg = a.get("targeting") or {}
            print(f"  targeting.geo = {json.dumps(tg.get('geo_locations'), ensure_ascii=False)}")
            print(
                f"  targeting.age = {tg.get('age_min')}-{tg.get('age_max')}  "
                f"automation = {json.dumps(tg.get('targeting_automation'), ensure_ascii=False)}"
            )
            print(
                f"  targeting.publisher_platforms = {tg.get('publisher_platforms')}  "
                f"positions(fb/ig/an) = {tg.get('facebook_positions')}/"
                f"{tg.get('instagram_positions')}/{tg.get('audience_network_positions')}"
            )

        # ---------- ads кампании ----------
        ads_resp = await client.execute_graph_call(
            method="GET",
            endpoint=f"/{campaign_id}/ads",
            query_params={
                "fields": (
                    "name,effective_status,configured_status,status,ad_review_feedback,issues_info"
                ),
                "limit": "50",
            },
        )
        print("\n=== STATUS: ADS ===")
        for a in ads_resp.get("data") or []:
            print(f"\n--- ad: {a.get('name')} (id={a.get('id')}) ---")
            print(
                f"  effective_status = {a.get('effective_status')}  "
                f"configured = {a.get('configured_status')}"
            )
            print(
                f"  ad_review_feedback = "
                f"{json.dumps(a.get('ad_review_feedback'), ensure_ascii=False)}"
            )
            print(f"  issues_info = {json.dumps(a.get('issues_info'), ensure_ascii=False)}")

        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CAMPAIGN
    pre = sys.argv[2] if len(sys.argv) > 2 else "maximum"
    raise SystemExit(asyncio.run(main(cid, pre)))
