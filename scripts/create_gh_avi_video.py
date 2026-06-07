# -*- coding: utf-8 -*-
"""Видео-кампания GH/Aviator — CBO 1-3-N, адсет = текстовый подход. Как статика v2, но ВИДЕО.

Структура: 1 CBO-кампания × 3 адсета (hook/clickbait/headline) × N видео = N×3 объявлений.
  - upload_video каждого mp4 → video_id;
  - thumbnail (обязателен для video_data) — первый кадр через ffmpeg → upload_image → image_hash;
  - creative.object_story_spec.video_data (video_id + image_hash-превью + message/title/desc + CTA);
  - остальное идентично create_gh_avi_v2: CBO $8.99, эталон Olstep, attribution CLICK 1d, age 22-65,
    Advantage+, AQ+GH, PURCHASE, Game star, text_optimizations OPT_OUT.
  - sub3 = код видео, sub6 = текст-подход (адсет) → разрез видео × текст.

    python scripts/create_gh_avi_video.py            # spec-print
    python scripts/create_gh_avi_video.py --go        # боевое создание PAUSED
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.meta_api.client import MetaApiClient
from core.meta_api.mutations._batch_helpers import (
    build_batch_payload,
    make_batch_entry,
    parse_batch_response,
)

# ====================== Параметры ======================

ACT = "act_26943307705301002"
ACT_NUM = ACT.removeprefix("act_")
PIXEL_ID = "1282495953856981"
PAGE_ID = "103053722121477"  # Game star
LANDING = "https://space2go.forum/track/6a1f37ebf10ec2c6fce437c6/ads"
START_TIME = "2026-06-06T00:00:00-07:00"  # завтра 00:00 TZ кабинета (Hermosillo UTC-7)
CAMPAIGN_NAME = "MV | GH | AVI | VIDEO | adset.pro | 06.06"

DAILY_BUDGET_CENTS = 899  # $8.99 CBO
BID_STRATEGY = "LOWEST_COST_WITHOUT_CAP"
AGE_MIN, AGE_MAX = 22, 65
COUNTRIES = ["GH", "AQ"]
CTA_TYPE = "PLAY_GAME"
TEXT_OPT_ENROLL = "OPT_OUT"

ATTRIBUTION = [{"event_type": "CLICK_THROUGH", "window_days": 1}]  # 1 день (как у статики)

VIDEO_DIR = os.path.expanduser("~/Documents/FB_Agent_Creo/GH_AVI_video01")
# Видео в порядке (sub3 = GH_AVI_V1..VN). Имена ровно такие кладёт генератор.
VIDEO_FILES = ["G1.mp4", "G2.mp4", "G3.mp4", "E1.mp4"]

# 3 текстовых подхода (адсет = подход), game-угол под геймплей-видео, сумма-агностик.
TEXTS = [
    {
        "label": "hook",
        "primary": (
            "GHS 10 in. Caught the multiplier. Cashed out straight to MTN MoMo — before the next round started.\n\n"
            "Your friends are cashing out daily. Don't watch. \U0001f4b8\n\n"
            "Deposit GHS 10 → get 20 FREE Aviator bets. Risk small, stack fast."
        ),
        "headline": "GHS 10 in → cashed out on MTN MoMo",
        "description": "Deposit GHS 10, unlock 20 free Aviator bets. Cash out to MTN MoMo instantly.",
    },
    {
        "label": "clickbait",
        "primary": (
            "✈️ GHS 10 in. While you're reading this, someone in Ghana just cashed out big. \U0001f1ec\U0001f1ed\n"
            "Your friends are already playing Aviator — and they're not telling you why their MoMo keeps topping up.\n"
            "Deposit GHS 10. Get 20 FREE bets. Cash out straight to MoMo. ⚡"
        ),
        "headline": "GHS 10 in. Real wins to your MoMo.",
        "description": "20 free Aviator bets on your first GHS 10 deposit. MoMo cashout, instant, no bank needed.",
    },
    {
        "label": "headline",
        "primary": "GHS 10 deposit → 20 free Aviator bets. Cash out straight to MTN MoMo.",
        "headline": "GHS 10 in. Real wins out.",
        "description": "Deposit GHS 10, get 20 free bets on Aviator. Withdraw to MTN MoMo instantly.",
    },
]


def video_paths() -> list[str]:
    return [os.path.join(VIDEO_DIR, f) for f in VIDEO_FILES]


def video_code(v: int) -> str:
    return f"GH_AVI_V{v + 1}"  # sub3


def url_tags(v: int) -> str:
    return (
        "sub2=MV"
        f"&sub3={video_code(v)}"
        f"&sub4={ACT_NUM}"
        "&sub5={{campaign.name}}"
        "&sub6={{adset.name}}"
        "&sub7={{ad.name}}"
    )


def extract_thumbnail(video_path: str) -> bytes:
    """Первый кадр видео через ffmpeg → JPEG bytes (для video_data.image_hash)."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vframes", "1", "-q:v", "2", tmp_path],
            check=True,
            capture_output=True,
        )
        with open(tmp_path, "rb") as f:  # noqa: ASYNC230
            return f.read()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ====================== Тела ======================


def campaign_body() -> dict:
    return {
        "name": CAMPAIGN_NAME,
        "objective": "OUTCOME_SALES",
        "status": "PAUSED",
        "special_ad_categories": ["NONE"],
        "daily_budget": DAILY_BUDGET_CENTS,
        "bid_strategy": BID_STRATEGY,
    }


def adset_body(campaign_id: str, label: str) -> dict:
    return {
        "name": label,
        "campaign_id": campaign_id,
        "billing_event": "IMPRESSIONS",
        "optimization_goal": "OFFSITE_CONVERSIONS",
        "promoted_object": {
            "pixel_id": PIXEL_ID,
            "custom_event_type": "PURCHASE",
            "smart_pse_enabled": False,
        },
        "attribution_spec": ATTRIBUTION,
        "targeting": {
            "geo_locations": {"countries": COUNTRIES, "location_types": ["home", "recent"]},
            "age_min": AGE_MIN,
            "age_max": AGE_MAX,
            "targeting_automation": {
                "advantage_audience": 1,
                "individual_setting": {"age": 1, "gender": 1},
            },
        },
        "start_time": START_TIME,
        "status": "PAUSED",
    }


def creative_body(v: int, text: dict, video_id: str, thumb_hash: str) -> dict:
    return {
        "name": f"{video_code(v)}_{text['label']}",
        "object_story_spec": {
            "page_id": PAGE_ID,
            "video_data": {
                "video_id": video_id,
                "message": text["primary"],
                "title": text["headline"],
                "link_description": text["description"],
                "image_hash": thumb_hash,  # превью (обязателен)
                "call_to_action": {"type": CTA_TYPE, "value": {"link": LANDING}},
            },
        },
        "url_tags": url_tags(v),
        "degrees_of_freedom_spec": {
            "creative_features_spec": {"text_optimizations": {"enroll_status": TEXT_OPT_ENROLL}}
        },
    }


def ad_body(v: int, text: dict, adset_id: str, creative_id: str) -> dict:
    return {
        "name": f"{video_code(v)}_{text['label']}",
        "adset_id": adset_id,
        "creative": {"creative_id": creative_id},
        "status": "PAUSED",
    }


# ====================== Batch helpers ======================


async def run_batch(client: MetaApiClient, entries: list[dict], label: str) -> list[dict]:
    resp = await client.execute_graph_call(
        method="POST", endpoint="/", query_params={"batch": build_batch_payload(entries)}
    )
    subs = parse_batch_response(resp, expected_count=len(entries))
    ok = sum(1 for s in subs if s["success"])
    print(f"  [{label}] {ok}/{len(entries)} ok")
    for s in subs:
        if not s["success"]:
            err = s.get("body") if isinstance(s.get("body"), dict) else s.get("error")
            print(f"    ✗ entry#{s['index']} code={s['code']} err={err}")
    return subs


def ids_from(subs: list[dict]) -> list[str | None]:
    out: list[str | None] = []
    for s in subs:
        body = s.get("body") if s["success"] else None
        out.append(str(body["id"]) if isinstance(body, dict) and body.get("id") else None)
    return out


async def run_chunked(client: MetaApiClient, entries: list[dict], label: str, chunk: int = 8):
    all_ids: list[str | None] = []
    for i in range(0, len(entries), chunk):
        part = entries[i : i + chunk]
        subs = await run_batch(client, part, f"{label} {i}-{i + len(part) - 1}")
        all_ids.extend(ids_from(subs))
    return all_ids


# ====================== main ======================


async def main(go: bool) -> int:
    paths = video_paths()
    missing = [p for p in paths if not os.path.exists(p)]  # noqa: ASYNC240
    if missing:
        print("НЕТ видео (ждём генерацию):", *missing, sep="\n  ")
        return 2
    n_videos = len(paths)

    client = MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST", "localhost"), port=50051)
    await client.start()
    try:
        print(f"\n{CAMPAIGN_NAME}  (CBO 1-3-{n_videos}, ВИДЕО, адсет=текст)")
        print(f"  {ACT} · page {PAGE_ID} · PURCHASE · гео {COUNTRIES} · age {AGE_MIN}-{AGE_MAX}")
        print(
            f"  CBO ${DAILY_BUDGET_CENTS / 100:.2f}/день · {BID_STRATEGY} · Advantage+ · attr CLICK 1d · старт {START_TIME}"
        )
        print(
            f"  3 адсета: {', '.join(t['label'] for t in TEXTS)} · {n_videos} видео × 3 = {n_videos * 3} ads · PAUSED"
        )
        print(f"  видео: {', '.join(VIDEO_FILES)}")

        if not go:
            print("\n[spec-print] боевого создания НЕ делаю (нет --go).")
            return 0

        from core.meta_api.upload import MediaUploader

        uploader = MediaUploader(client)

        # 1) кампания
        print("\n1) Кампания (CBO)…")
        campaign_id = ids_from(
            await run_batch(
                client,
                [
                    make_batch_entry(
                        method="POST", relative_url=f"{ACT}/campaigns", body_params=campaign_body()
                    )
                ],
                "campaign",
            )
        )[0]
        if not campaign_id:
            print("❌ кампания не создана.")
            return 4
        print(f"  campaign_id={campaign_id}")

        # 2) видео → video_id + thumbnail → image_hash
        print(f"\n2) Загружаю {n_videos} видео (+ thumbnail каждого)…")
        video_ids: list[str] = []
        thumb_hashes: list[str] = []
        for p in paths:
            vid = await uploader.upload_video(ACT, p)
            thumb = extract_thumbnail(p)
            th = await uploader.upload_image(ACT, thumb, filename=os.path.basename(p) + ".jpg")
            video_ids.append(vid)
            thumb_hashes.append(th)
            print(f"  {os.path.basename(p)} → video_id={vid} thumb={th[:14]}…")

        # 3) 3 адсета
        print("\n3) 3 адсета (текст-подходы)…")
        adset_ids = ids_from(
            await run_batch(
                client,
                [
                    make_batch_entry(
                        method="POST",
                        relative_url=f"{ACT}/adsets",
                        body_params=adset_body(campaign_id, t["label"]),
                    )
                    for t in TEXTS
                ],
                "adsets",
            )
        )
        if not all(adset_ids):
            print(f"❌ адсеты неполные: {adset_ids}. Кампания {campaign_id} — чистить.")
            return 5
        adset_by_label = {t["label"]: adset_ids[i] for i, t in enumerate(TEXTS)}
        print(f"  адсеты: {adset_by_label}")

        # 4) N×3 креативов (видео × текст)
        print(f"\n4) {n_videos * 3} креативов ({n_videos} видео × 3 текста)…")
        order: list[tuple[int, dict]] = [(v, t) for t in TEXTS for v in range(n_videos)]
        entries_cr = [
            make_batch_entry(
                method="POST",
                relative_url=f"{ACT}/adcreatives",
                body_params=creative_body(v, t, video_ids[v], thumb_hashes[v]),
            )
            for (v, t) in order
        ]
        creative_ids = await run_chunked(client, entries_cr, "creatives", chunk=6)
        if not all(creative_ids):
            print(f"❌ креативы неполные: {creative_ids}. Чистить кампанию {campaign_id}.")
            return 6

        # 5) N×3 ads
        print(f"\n5) {n_videos * 3} объявлений…")
        entries_ads = [
            make_batch_entry(
                method="POST",
                relative_url=f"{ACT}/ads",
                body_params=ad_body(v, t, adset_by_label[t["label"]], creative_ids[k]),
            )
            for k, (v, t) in enumerate(order)
        ]
        ad_ids = await run_chunked(client, entries_ads, "ads", chunk=8)
        ok_ads = sum(1 for x in ad_ids if x)

        print("\n" + "=" * 60)
        print(f"✅ ГОТОВО: campaign_id={campaign_id} (PAUSED)")
        print(
            f"   адсетов {len([x for x in adset_ids if x])} · креативов {len([x for x in creative_ids if x])} · ads {ok_ads}/{n_videos * 3}"
        )
        print(f"   '{CAMPAIGN_NAME}' — ревью в Ads Manager → unpause сам.")
        if ok_ads < n_videos * 3:
            print(f"   ⚠️ ads {ok_ads}/{n_videos * 3} — проверь упавшие.")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    go = "--go" in sys.argv[1:]
    raise SystemExit(asyncio.run(main(go)))
