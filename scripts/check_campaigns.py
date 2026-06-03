# -*- coding: utf-8 -*-
"""Верификатор кампаний в кабинете через Graph API (Vision-канал).

Ищет кампании по подстроке имени, печатает статус + число адсетов/объявлений.
Использование до залива (нет ли дубля / осиротевших от частичного прогона)
и после (сверка что создалось).

    python scripts/check_campaigns.py "GH | AVI"
    python scripts/check_campaigns.py "GH | AVI" act_26943307705301002
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.meta_api.client import MetaApiClient

DEFAULT_ACT = "act_26943307705301002"


async def main(needle: str, act: str) -> int:
    client = MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST", "localhost"), port=50051)
    await client.start()
    try:
        resp = await client.execute_graph_call(
            method="GET",
            endpoint=f"/{act}/campaigns",
            query_params={
                "fields": "id,name,status,effective_status,created_time",
                "filtering": f'[{{"field":"name","operator":"CONTAIN","value":"{needle}"}}]',
                "limit": "100",
            },
        )
        camps = resp.get("data") or []
        if not camps:
            print(f"✅ Кампаний с '{needle}' в {act} НЕТ — дубля нет, можно заливать.")
            return 0

        print(f"⚠️ Найдено кампаний с '{needle}': {len(camps)}")
        for c in camps:
            cid = c.get("id")
            adsets = await client.execute_graph_call(
                method="GET",
                endpoint=f"/{cid}/adsets",
                query_params={"fields": "id,name,status", "limit": "100"},
            )
            ads = await client.execute_graph_call(
                method="GET",
                endpoint=f"/{cid}/ads",
                query_params={"fields": "id,status", "limit": "200"},
            )
            n_adsets = len(adsets.get("data") or [])
            n_ads = len(ads.get("data") or [])
            print(
                f"  • {c.get('name')}\n"
                f"    id={cid} status={c.get('status')} eff={c.get('effective_status')}"
                f" created={c.get('created_time')}\n"
                f"    адсетов={n_adsets} объявлений={n_ads}"
            )
        print(
            "\n→ Если это осиротевшие/старые — удалить в Ads Manager ПЕРЕД новым заливом"
            " (иначе дубль)."
        )
        return 1
    finally:
        await client.close()


if __name__ == "__main__":
    needle = sys.argv[1] if len(sys.argv) > 1 else "GH | AVI"
    act = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_ACT
    raise SystemExit(asyncio.run(main(needle, act)))
