# -*- coding: utf-8 -*-
"""Удалить объекты Meta (campaign/adset/ad) по id через Graph API DELETE.

python scripts/delete_objects.py 120245234759090044 120245234543430044
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.meta_api.client import MetaApiClient


async def main(ids: list[str]) -> int:
    client = MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST", "localhost"), port=50051)
    await client.start()
    try:
        for oid in ids:
            try:
                r = await client.execute_graph_call(method="DELETE", endpoint=f"/{oid}")
                print(f"✅ {oid}: удалён {json.dumps(r, ensure_ascii=False)}")
            except Exception as exc:  # noqa: BLE001
                print(f"❌ {oid}: {type(exc).__name__}: {exc}")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
