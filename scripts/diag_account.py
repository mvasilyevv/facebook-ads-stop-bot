# -*- coding: utf-8 -*-
"""Диагностика кабинета: лимиты/статус + эталонные ABO-кампании конкурентов."""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.meta_api.client import MetaApiClient

ACT = "act_26943307705301002"


async def call(client, endpoint, qp):
    return await client.execute_graph_call(method="GET", endpoint=endpoint, query_params=qp)


async def main(act: str) -> int:
    client = MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST", "localhost"), port=50051)
    await client.start()
    try:
        print("=== ACCOUNT info ===")
        a = await call(
            client,
            f"/{act}",
            {
                "fields": "name,account_status,disable_reason,currency,timezone_name,"
                "spend_cap,amount_spent,balance,funding_source,"
                "is_prepay_account,min_daily_budget,capabilities",
            },
        )
        print(json.dumps(a, ensure_ascii=False, indent=2))

        # активные кампании кабинета — посмотреть бюджеты/objective соседей по кабинету
        print("\n=== ACTIVE кампании в кабинете (бюджеты соседей) ===")
        c = await call(
            client,
            f"/{act}/campaigns",
            {
                "fields": "name,effective_status,objective,buying_type,daily_budget,"
                "lifetime_budget,bid_strategy",
                "effective_status": '["ACTIVE"]',
                "limit": "60",
            },
        )
        for camp in c.get("data") or []:
            db = camp.get("daily_budget")
            lb = camp.get("lifetime_budget")
            print(
                f"  [{camp.get('effective_status')}] {camp.get('name')[:50]:50} "
                f"obj={camp.get('objective')} daily={db} life={lb} "
                f"bid={camp.get('bid_strategy')}"
            )
        if not (c.get("data")):
            print("  (нет активных)")

        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    act = sys.argv[1] if len(sys.argv) > 1 else ACT
    raise SystemExit(asyncio.run(main(act)))
