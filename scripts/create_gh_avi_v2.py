# -*- coding: utf-8 -*-
"""Перезалив GH/Aviator — CBO 1-3-5 (по эталону соседа Olstep). Две кампании A/B.

Структура на КАЖДУЮ кампанию: 1 CBO-кампания × 3 адсета-дубля × 5 объявлений = 15 ads.
  - CBO: daily_budget на КАМПАНИИ ($8.99), bid_strategy LOWEST_COST_WITHOUT_CAP на кампании;
  - 3 адсета-дубля (имена «1»/«2»/«3» — БЕЗ спецсимволов, фикс sub6-URL-encode);
  - 5 креативов (по 1 на визуал набора), переиспользуются в 3 адсетах → 15 ads;
  - эталон Olstep: age 22-65, Advantage+ audience + individual_setting{age,gender},
    attribution CLICK 7d + VIEW 1d + EVV 1d, AQ+GH home/recent, promoted PURCHASE+smart_pse false,
    destination не задаём (UNDEFINED), text_optimizations OPT_OUT.

Наборы (2 кампании = разные наборы креативов):
  A = UGC/эмоция (A1..A5), B = геймплей/динамика (B1..B5).

    python scripts/create_gh_avi_v2.py A          # spec-print набора A
    python scripts/create_gh_avi_v2.py A --go      # боевое создание кампании A (PAUSED)
    python scripts/create_gh_avi_v2.py B --go      # кампания B
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.meta_api.client import MetaApiClient
from core.meta_api.mutations._batch_helpers import (
    build_batch_payload,
    make_batch_entry,
    parse_batch_response,
)

# ====================== Общие параметры ======================

ACT = "act_26943307705301002"
ACT_NUM = ACT.removeprefix("act_")
PIXEL_ID = "1282495953856981"
PAGE_ID = "103053722121477"  # Game star
LANDING = "https://space2go.forum/track/6a1f37ebf10ec2c6fce437c6/ads"  # гео-фильтр GH (404 вне GH — норма)
START_TIME = "2026-06-05T00:00:00-07:00"  # следующие сутки 00:00 TZ кабинета (Hermosillo UTC-7)

DAILY_BUDGET_CENTS = 899  # $8.99 CBO на кампанию
BID_STRATEGY = "LOWEST_COST_WITHOUT_CAP"
AGE_MIN, AGE_MAX = 22, 65  # как эталон Olstep (не 18)
COUNTRIES = ["GH", "AQ"]
CTA_TYPE = "PLAY_GAME"
TEXT_OPT_ENROLL = "OPT_OUT"
N_ADSETS = 3
N_CREATIVES = 5

ATTRIBUTION = [
    {"event_type": "CLICK_THROUGH", "window_days": 7},
    {"event_type": "VIEW_THROUGH", "window_days": 1},
    {"event_type": "ENGAGED_VIDEO_VIEW", "window_days": 1},
]

# ====================== Конфиг наборов ======================

SETS: dict[str, dict] = {
    "A": {
        "campaign_name": "MV | GH | AVI | UGC | adset.pro | 05.06",
        "creo_dir": os.path.expanduser("~/Documents/FB_Agent_Creo/GH_AVI_batch02/A"),
        "files": ["A1.jpeg", "A2.jpeg", "A3.jpeg", "A4.jpeg", "A5.jpeg"],
        "code_prefix": "GH_AVI_A",  # sub3 = GH_AVI_A1..A5
        # Угол A «Close Your Debts» — копия Luck&strategy UGC-образца (recon 2026-06-04)
        "primary_text": (
            "Tired of the month finishing before your money? \U0001f4b8\n"
            "Deposit just GHS 10, play Aviator, cash out to MTN MoMo.\n"
            "Get 20 FREE BETS when you deposit — real wins, real cashout.\n"
            "Start today. Close your debts."
        ),
        "headline": "Deposit GHS 10 → 20 Free Bets on Aviator",
        "description": "Cash out straight to MTN MoMo. Fast payouts, no wahala.",
    },
    "B": {
        "campaign_name": "MV | GH | AVI | GAME | adset.pro | 05.06",
        "creo_dir": os.path.expanduser("~/Documents/FB_Agent_Creo/GH_AVI_batch02/B"),
        "files": ["B1.jpeg", "B2.jpeg", "B3.jpeg", "B4.jpeg", "B5.jpeg"],
        "code_prefix": "GH_AVI_B",
        # Угол B «Peanuts to Profit» — копия Luck&strategy дизайн-образца (recon 2026-06-04)
        "primary_text": (
            "From GHS 10 to GHS 4,850 — that's Aviator. ✈️\U0001f4b0\n"
            "New players: deposit GHS 10, get 20 FREE BETS instantly.\n"
            "Catch the multiplier before it flies away.\n"
            "Cash out to MTN MoMo anytime. Join now — don't miss your flight!"
        ),
        "headline": "Start With GHS 10. Cash Out Big.",
        "description": "20 Free Bets on first deposit. MTN MoMo payouts. Play Aviator now.",
    },
}


def creo_files(cfg: dict) -> list[str]:
    return [os.path.join(cfg["creo_dir"], f) for f in cfg["files"]]


def format_code(cfg: dict, j: int) -> str:
    return f"{cfg['code_prefix']}{j + 1}"  # GH_AVI_A1..A5


def url_tags(cfg: dict, j: int) -> str:
    return (
        "sub2=MV"
        f"&sub3={format_code(cfg, j)}"
        f"&sub4={ACT_NUM}"
        "&sub5={{campaign.name}}"
        "&sub6={{adset.name}}"
        "&sub7={{ad.name}}"
    )


# ====================== Тела entry ======================


def campaign_body(cfg: dict) -> dict:
    # CBO: бюджет И стратегия на КАМПАНИИ (Meta это разрешает при наличии бюджета).
    return {
        "name": cfg["campaign_name"],
        "objective": "OUTCOME_SALES",
        "status": "PAUSED",
        "special_ad_categories": ["NONE"],
        "daily_budget": DAILY_BUDGET_CENTS,
        "bid_strategy": BID_STRATEGY,
    }


def adset_body(campaign_id: str, i: int) -> dict:
    # CBO-адсет: без бюджета и без bid_strategy (наследует с кампании). Имя без «|».
    return {
        "name": str(i + 1),  # «1»/«2»/«3» — чистый sub6
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


def creative_body(cfg: dict, j: int, image_hash: str) -> dict:
    return {
        "name": format_code(cfg, j),
        "object_story_spec": {
            "page_id": PAGE_ID,
            "link_data": {
                "message": cfg["primary_text"],
                "link": LANDING,
                "image_hash": image_hash,
                "name": cfg["headline"],
                "description": cfg["description"],
                "call_to_action": {"type": CTA_TYPE, "value": {"link": LANDING}},
            },
        },
        "url_tags": url_tags(cfg, j),
        "degrees_of_freedom_spec": {
            "creative_features_spec": {"text_optimizations": {"enroll_status": TEXT_OPT_ENROLL}}
        },
    }


def ad_body(cfg: dict, adset_id: str, creative_id: str, i: int, j: int) -> dict:
    return {
        "name": f"{format_code(cfg, j)}_as{i + 1}",
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


async def run_chunked(client: MetaApiClient, entries: list[dict], label: str, chunk: int = 10):
    all_ids: list[str | None] = []
    for i in range(0, len(entries), chunk):
        part = entries[i : i + chunk]
        subs = await run_batch(client, part, f"{label} {i}-{i + len(part) - 1}")
        all_ids.extend(ids_from(subs))
    return all_ids


# ====================== main ======================


async def main(set_key: str, go: bool) -> int:
    cfg = SETS[set_key]
    paths = creo_files(cfg)
    missing = [p for p in paths if not os.path.exists(p)]  # noqa: ASYNC240
    if missing:
        print(f"НЕТ файлов набора {set_key} (ждём syntx):", *missing, sep="\n  ")
        return 2

    client = MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST", "localhost"), port=50051)
    await client.start()
    try:
        print(f"\nНАБОР {set_key}: {cfg['campaign_name']}  (CBO 1-3-5)")
        print(
            f"  {ACT} · page {PAGE_ID} · пиксель {PIXEL_ID}·PURCHASE · гео {COUNTRIES} · age {AGE_MIN}-{AGE_MAX}"
        )
        print(
            f"  CBO ${DAILY_BUDGET_CENTS / 100:.2f}/день · {BID_STRATEGY} · Advantage+ · attribution 7d/1d/EVV"
        )
        print(
            f"  старт {START_TIME} · 3 адсета × 5 ads = 15 · опт.текста {TEXT_OPT_ENROLL} · PAUSED"
        )
        print(f"  креативы: {', '.join(cfg['files'])}")
        print(f"  текст: «{cfg['headline']}»")

        if not go:
            print("\n[spec-print] боевого создания НЕ делаю (нет --go).")
            return 0

        # 1) кампания (CBO) — отдельный 1-entry batch
        print("\n1) Кампания (CBO)…")
        subs_c = await run_batch(
            client,
            [
                make_batch_entry(
                    method="POST", relative_url=f"{ACT}/campaigns", body_params=campaign_body(cfg)
                )
            ],
            "campaign",
        )
        campaign_id = ids_from(subs_c)[0]
        if not campaign_id:
            print("❌ кампания не создана.")
            return 4
        print(f"  campaign_id={campaign_id}")

        # 2) 5 картинок → image_hash
        from core.meta_api.upload import MediaUploader

        uploader = MediaUploader(client)
        hashes: list[str] = []
        print("\n2) Загружаю 5 картинок…")
        for p in paths:
            with open(p, "rb") as f:  # noqa: ASYNC230
                data = f.read()
            h = await uploader.upload_image(
                ACT, data, filename=os.path.basename(p), content_type="image/jpeg"
            )
            hashes.append(h)
            print(f"  {os.path.basename(p)} → {h[:18]}…")

        # 3) 3 адсета-дубля (CBO)
        print("\n3) 3 адсета (CBO-дубли)…")
        adset_ids = ids_from(
            await run_batch(
                client,
                [
                    make_batch_entry(
                        method="POST",
                        relative_url=f"{ACT}/adsets",
                        body_params=adset_body(campaign_id, i),
                    )
                    for i in range(N_ADSETS)
                ],
                "adsets",
            )
        )
        if not all(adset_ids):
            print(f"❌ адсеты неполные: {adset_ids}. Кампания {campaign_id} создана — чистить.")
            return 5
        print(f"  adset_ids={adset_ids}")

        # 4) 5 креативов
        print("\n4) 5 креативов…")
        creative_ids = ids_from(
            await run_batch(
                client,
                [
                    make_batch_entry(
                        method="POST",
                        relative_url=f"{ACT}/adcreatives",
                        body_params=creative_body(cfg, j, hashes[j]),
                    )
                    for j in range(N_CREATIVES)
                ],
                "creatives",
            )
        )
        if not all(creative_ids):
            print(f"❌ креативы неполные: {creative_ids}. Чистить кампанию {campaign_id}.")
            return 6

        # 5) 15 ads (3 адсета × 5 креативов)
        print("\n5) 15 объявлений (3×5)…")
        entries_ads = [
            make_batch_entry(
                method="POST",
                relative_url=f"{ACT}/ads",
                body_params=ad_body(cfg, adset_ids[i], creative_ids[j], i, j),
            )
            for i in range(N_ADSETS)
            for j in range(N_CREATIVES)
        ]
        ad_ids = await run_chunked(client, entries_ads, "ads", chunk=10)
        ok_ads = sum(1 for x in ad_ids if x)

        print("\n" + "=" * 60)
        print(f"✅ ГОТОВО набор {set_key}: campaign_id={campaign_id} (PAUSED)")
        print(
            f"   адсетов {len([x for x in adset_ids if x])} · креативов {len([x for x in creative_ids if x])} · ads {ok_ads}/15"
        )
        print(f"   '{cfg['campaign_name']}' — ревью в Ads Manager → unpause сам.")
        if ok_ads < 15:
            print(f"   ⚠️ ads {ok_ads}/15 — проверь упавшие.")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    set_key = (args[0] if args else "A").upper()
    if set_key not in SETS:
        print(f"набор должен быть A или B, получено {set_key!r}")
        raise SystemExit(1)
    go = "--go" in sys.argv[1:]
    raise SystemExit(asyncio.run(main(set_key, go)))
