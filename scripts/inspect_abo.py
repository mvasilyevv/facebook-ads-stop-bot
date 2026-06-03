# -*- coding: utf-8 -*-
"""Инспектор кабинета: как оформлены ABO-кампании (эталон для залива).

ABO = бюджет на уровне АДСЕТА (у кампании нет daily/lifetime budget).
Тянет кампании, классифицирует ABO/CBO, по свежим ABO показывает адсеты
с ключевыми полями (bid_strategy, optimization_goal, promoted_object,
attribution_spec, targeting) + число объявлений на адсет.

    python scripts/inspect_abo.py
    python scripts/inspect_abo.py OUTCOME_SALES    # фильтр по objective
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.meta_api.client import MetaApiClient

ACT = "act_26943307705301002"

TARGET_KEYS = [
    "geo_locations",
    "age_min",
    "age_max",
    "genders",
    "publisher_platforms",
    "facebook_positions",
    "instagram_positions",
    "device_platforms",
    "targeting_automation",
    "targeting_relaxation_types",
]


def compact_targeting(t: dict | None) -> dict:
    if not isinstance(t, dict):
        return {}
    return {k: t[k] for k in TARGET_KEYS if k in t}


async def adset_ads_count(client: MetaApiClient, adset_id: str) -> int:
    try:
        r = await client.execute_graph_call(
            method="GET",
            endpoint=f"/{adset_id}/ads",
            query_params={"fields": "id", "limit": "50"},
        )
        return len(r.get("data") or [])
    except Exception:  # noqa: BLE001
        return -1


async def main(objective_filter: str | None) -> int:
    client = MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST", "localhost"), port=50051)
    await client.start()
    try:
        resp = await client.execute_graph_call(
            method="GET",
            endpoint=f"/{ACT}/campaigns",
            query_params={
                "fields": "id,name,objective,buying_type,status,effective_status,"
                "daily_budget,lifetime_budget,bid_strategy,created_time",
                "limit": "200",
            },
        )
        camps = resp.get("data") or []
        if not camps:
            print("Кампаний в кабинете нет.")
            return 0

        abo, cbo = [], []
        for c in camps:
            has_camp_budget = bool(c.get("daily_budget")) or bool(c.get("lifetime_budget"))
            (cbo if has_camp_budget else abo).append(c)

        print(
            f"Всего кампаний: {len(camps)} · ABO (бюджет на адсете): {len(abo)} · "
            f"CBO (бюджет на кампании): {len(cbo)}"
        )
        # разбивка по objective среди ABO
        by_obj: dict[str, int] = {}
        for c in abo:
            by_obj[c.get("objective") or "?"] = by_obj.get(c.get("objective") or "?", 0) + 1
        print("ABO по objective:", json.dumps(by_obj, ensure_ascii=False))

        # кандидаты для детального разбора: ABO, свежие, опц. фильтр по objective
        cand = [c for c in abo if not objective_filter or c.get("objective") == objective_filter]
        cand.sort(key=lambda c: c.get("created_time") or "", reverse=True)
        cand = cand[:3]

        if not cand:
            print(f"\nНет ABO-кампаний под фильтр {objective_filter!r}. Список ABO (кратко):")
            for c in abo[:15]:
                print(
                    f"  • {c.get('name')} [{c.get('objective')}] {c.get('status')} "
                    f"created={c.get('created_time')}"
                )
            return 0

        for c in cand:
            cid = c.get("id")
            print("\n" + "=" * 70)
            print(f"КАМПАНИЯ: {c.get('name')}")
            print(
                f"  id={cid} objective={c.get('objective')} buying={c.get('buying_type')} "
                f"status={c.get('status')}/{c.get('effective_status')} "
                f"camp_bid_strategy={c.get('bid_strategy')} created={c.get('created_time')}"
            )
            adsets_resp = await client.execute_graph_call(
                method="GET",
                endpoint=f"/{cid}/adsets",
                query_params={
                    "fields": "name,daily_budget,lifetime_budget,bid_strategy,bid_amount,"
                    "billing_event,optimization_goal,destination_type,promoted_object,"
                    "attribution_spec,start_time,status,targeting",
                    "limit": "15",
                },
            )
            adsets = adsets_resp.get("data") or []
            print(f"  адсетов: {len(adsets)}")
            for a in adsets:
                n_ads = await adset_ads_count(client, a.get("id"))
                budget = a.get("daily_budget") or a.get("lifetime_budget")
                blvl = (
                    "daily"
                    if a.get("daily_budget")
                    else ("lifetime" if a.get("lifetime_budget") else "—")
                )
                print(f"\n  ── адсет: {a.get('name')}  (объявлений: {n_ads})")
                print(
                    f"     budget={budget} ({blvl}) bid_strategy={a.get('bid_strategy')} "
                    f"bid_amount={a.get('bid_amount')}"
                )
                print(
                    f"     billing={a.get('billing_event')} optimization={a.get('optimization_goal')} "
                    f"destination={a.get('destination_type')}"
                )
                print(
                    f"     promoted_object={json.dumps(a.get('promoted_object'), ensure_ascii=False)}"
                )
                print(
                    f"     attribution_spec={json.dumps(a.get('attribution_spec'), ensure_ascii=False)}"
                )
                print(
                    f"     targeting={json.dumps(compact_targeting(a.get('targeting')), ensure_ascii=False)}"
                )
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    obj = sys.argv[1] if len(sys.argv) > 1 else None
    raise SystemExit(asyncio.run(main(obj)))
