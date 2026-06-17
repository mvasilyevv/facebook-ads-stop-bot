# -*- coding: utf-8 -*-
"""Проверка содержимого кампании (адсеты → ads → creatives: медиа + url_tags) через прямой stub.

Парный инструмент к fb_launch.py. Работает в обход proto-бага ad_account_id (как и движок).
Запуск:  python scripts/fb_verify.py <campaign_id>
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grpc

from clients.python_grpc.v1 import meta_api_pb2, meta_api_pb2_grpc

if len(sys.argv) < 2:
    print("usage: python scripts/fb_verify.py <campaign_id>")
    raise SystemExit(2)
CAMP = sys.argv[1]


async def gc(stub, endpoint, fields):
    req = meta_api_pb2.ExecuteGraphCallRequest(
        session_id="",
        method="GET",
        endpoint=endpoint,
        query_params={"fields": fields, "limit": "100"},
    )
    resp = await stub.ExecuteGraphCall(req, timeout=60.0)
    if resp.HasField("error"):
        e = resp.error
        raise RuntimeError(f"code={e.code} sub={e.subcode} {e.message}")
    return json.loads(resp.response_json) if resp.response_json else {}


async def main() -> int:
    ch = grpc.aio.insecure_channel(
        "localhost:50051", options=[("grpc.max_receive_message_length", 50 * 1024 * 1024)]
    )
    stub = meta_api_pb2_grpc.MetaApiServiceStub(ch)
    try:
        camp = await gc(stub, f"/{CAMP}", "name,status,objective,daily_budget,bid_strategy")
        print(f"КАМПАНИЯ {CAMP}: {json.dumps(camp, ensure_ascii=False)}")
        adsets = await gc(stub, f"/{CAMP}/adsets", "id,name,status,effective_status")
        for a in adsets.get("data", []):
            print(
                f"\nАДСЕТ {a['id']} «{a.get('name')}» status={a.get('status')} eff={a.get('effective_status')}"
            )
            ads = await gc(
                stub,
                f"/{a['id']}/ads",
                "name,status,effective_status,creative{id,name,video_id,url_tags,object_story_spec}",
            )
            data = ads.get("data", [])
            print(f"  ads: {len(data)}")
            for ad in data:
                cr = ad.get("creative", {})
                oss = cr.get("object_story_spec", {})
                vid = cr.get("video_id") or (oss.get("video_data") or {}).get("video_id")
                img = (oss.get("link_data") or {}).get("image_hash")
                kind = (
                    f"video_id={vid}"
                    if vid
                    else (f"image_hash={img[:12]}…" if img else "НЕТ медиа")
                )
                print(
                    f"    - {ad.get('name')} status={ad.get('status')} eff={ad.get('effective_status')} creative={cr.get('id')} [{kind}]"
                )
                if cr.get("url_tags"):
                    print(f"        url_tags: {cr['url_tags']}")
        return 0
    finally:
        await ch.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
