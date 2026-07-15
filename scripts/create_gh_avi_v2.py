# -*- coding: utf-8 -*-
"""Перезалив GH/Aviator — CBO 1-3-5, адсет = ТЕКСТОВЫЙ ПОДХОД. Две кампании A/B.

Структура на КАЖДУЮ кампанию: 1 CBO-кампания × 3 адсета × 5 объявлений = 15 ads.
  - 3 адсета = 3 ТЕКСТОВЫХ подхода (hook / clickbait / headline) — имя адсета = подход (чистый sub6);
  - 5 визуалов дублируются в каждом адсете → 15 креативов = 5 визуалов × 3 текста;
  - в статистике: sub3 = визуал (что за картинка), sub6 = подход (какой текст) → видно лучший визуал И лучший текст;
  - CBO $8.99 на кампании, bid LOWEST_COST_WITHOUT_CAP; эталон Olstep: age 22-65, Advantage+ audience +
    individual_setting{age,gender}, attribution CLICK 1d (требование байера), AQ+GH home/recent,
    promoted PURCHASE+smart_pse false, destination не задаём, text_optimizations OPT_OUT.

Тексты — результат роя-исследования (4 разведлинзы → синтез → 6 adversarial-скептиков), recon 2026-06-04.
Наборы: A = угол «Close Your Debts» (debt/эмоция), B = «Peanuts to Profit» (greed/динамика).

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
AGE_MIN, AGE_MAX = 22, 65
COUNTRIES = ["GH", "AQ"]
CTA_TYPE = "PLAY_GAME"
TEXT_OPT_ENROLL = "OPT_OUT"
N_VISUALS = 5

ATTRIBUTION = [
    {"event_type": "CLICK_THROUGH", "window_days": 1},  # атрибуция 1 день (требование байера)
]

# ====================== Тексты (рой-исследование, 3 подхода × 2 угла) ======================
# label = имя адсета (чистый sub6, латиница без спецсимволов)

TEXTS_A = [
    {
        "label": "hook",
        "primary": (
            "3 months rent. Nothing in my account. A friend showed me Aviator — "
            "I deposited GHS 10 for 20 free bets. Cashed out straight to MTN MoMo. "
            "Paid everything the next morning."
        ),
        "headline": "Paid my rent with a GHS 10 Aviator bet",
        "description": "Deposit GHS 10 → get 20 free Aviator bets. Withdraw to MTN MoMo, no bank needed.",
    },
    {
        "label": "clickbait",
        "primary": (
            "\U0001f4b8 My guy just cashed out from Aviator — paid his rent TONIGHT on MTN MoMo \U0001f1ec\U0001f1ed\n"
            "Deposit GHS 10, get 20 FREE bets. Cash out straight to MoMo, no wahala. ✈️\U0001f525\n"
            "How long will you keep watching others win?"
        ),
        "headline": "Rent money from Aviator — MTN MoMo, same night",
        "description": "GHS 10 deposit unlocks 20 Aviator free bets. Withdraw to MoMo instantly.",
    },
    {
        "label": "headline",
        "primary": (
            "Your friends are cashing out on Aviator. You're still waiting. "
            "GHS 10 gets you 20 free bets — withdraws straight to MTN MoMo."
        ),
        "headline": "Cash out to MoMo in one session",
        "description": "Deposit GHS 10, get 20 free Aviator bets. Cash out to MTN MoMo instantly.",
    },
]

TEXTS_B = [
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

SETS: dict[str, dict] = {
    "A": {
        "campaign_name": "MV | GH | AVI | UGC | adset.pro | 05.06",
        "creo_dir": os.path.expanduser("~/Documents/FB_Agent_Creo/GH_AVI_batch02/A"),
        "files": ["A1.jpeg", "A2.jpeg", "A3.jpeg", "A4.jpeg", "A5.jpeg"],
        "code_prefix": "GH_AVI_A",  # sub3 = GH_AVI_A1..A5 (визуал)
        "texts": TEXTS_A,
    },
    "B": {
        "campaign_name": "MV | GH | AVI | GAME | adset.pro | 05.06",
        "creo_dir": os.path.expanduser("~/Documents/FB_Agent_Creo/GH_AVI_batch02/B"),
        "files": ["B1.jpeg", "B2.jpeg", "B3.jpeg", "B4.jpeg", "B5.jpeg"],
        "code_prefix": "GH_AVI_B",
        "texts": TEXTS_B,
    },
}


def creo_files(cfg: dict) -> list[str]:
    return [os.path.join(cfg["creo_dir"], f) for f in cfg["files"]]


def visual_code(cfg: dict, v: int) -> str:
    return f"{cfg['code_prefix']}{v + 1}"  # GH_AVI_A1..A5 — sub3 (визуал)


def url_tags(cfg: dict, v: int) -> str:
    # sub3 = визуал (картинка), sub6 = {{adset.name}} = текстовый подход (hook/clickbait/headline)
    return (
        "sub2=MV"
        f"&sub3={visual_code(cfg, v)}"
        f"&sub4={ACT_NUM}"
        "&sub5={{campaign.name}}"
        "&sub6={{adset.name}}"
        "&sub7={{ad.name}}"
        "&sub8={{ad.id}}"
    )


# ====================== Тела entry ======================


def campaign_body(cfg: dict) -> dict:
    return {
        "name": cfg["campaign_name"],
        "objective": "OUTCOME_SALES",
        "status": "PAUSED",
        "special_ad_categories": ["NONE"],
        "daily_budget": DAILY_BUDGET_CENTS,
        "bid_strategy": BID_STRATEGY,
    }


def adset_body(campaign_id: str, label: str) -> dict:
    # CBO-адсет: без бюджета/стратегии (наследует с кампании). Имя = текстовый подход (чистый sub6).
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


def creative_body(cfg: dict, v: int, text: dict, image_hash: str) -> dict:
    return {
        "name": f"{visual_code(cfg, v)}_{text['label']}",
        "object_story_spec": {
            "page_id": PAGE_ID,
            "link_data": {
                "message": text["primary"],
                "link": LANDING,
                "image_hash": image_hash,
                "name": text["headline"],
                "description": text["description"],
                "call_to_action": {"type": CTA_TYPE, "value": {"link": LANDING}},
            },
        },
        "url_tags": url_tags(cfg, v),
        "degrees_of_freedom_spec": {
            "creative_features_spec": {"text_optimizations": {"enroll_status": TEXT_OPT_ENROLL}}
        },
    }


def ad_body(cfg: dict, v: int, text: dict, adset_id: str, creative_id: str) -> dict:
    return {
        "name": f"{visual_code(cfg, v)}_{text['label']}",
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
    texts = cfg["texts"]
    paths = creo_files(cfg)
    missing = [p for p in paths if not os.path.exists(p)]  # noqa: ASYNC240
    if missing:
        print(f"НЕТ файлов набора {set_key}:", *missing, sep="\n  ")
        return 2

    client = MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST", "localhost"), port=50051)
    await client.start()
    try:
        print(f"\nНАБОР {set_key}: {cfg['campaign_name']}  (CBO 1-3-5, адсет=текст)")
        print(
            f"  {ACT} · page {PAGE_ID} · пиксель {PIXEL_ID}·PURCHASE · гео {COUNTRIES} · age {AGE_MIN}-{AGE_MAX}"
        )
        print(
            f"  CBO ${DAILY_BUDGET_CENTS / 100:.2f}/день · {BID_STRATEGY} · Advantage+ · attr CLICK 1d · старт {START_TIME}"
        )
        print(
            f"  3 адсета (текст-подходы): {', '.join(t['label'] for t in texts)} · 5 визуалов × 3 = 15 ads · PAUSED"
        )
        for t in texts:
            print(f"    [{t['label']}] HL: «{t['headline']}»")

        if not go:
            print("\n[spec-print] боевого создания НЕ делаю (нет --go).")
            return 0

        # 1) кампания (CBO)
        print("\n1) Кампания (CBO)…")
        campaign_id = ids_from(
            await run_batch(
                client,
                [
                    make_batch_entry(
                        method="POST",
                        relative_url=f"{ACT}/campaigns",
                        body_params=campaign_body(cfg),
                    )
                ],
                "campaign",
            )
        )[0]
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

        # 3) 3 адсета (по текстовому подходу)
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
                    for t in texts
                ],
                "adsets",
            )
        )
        if not all(adset_ids):
            print(f"❌ адсеты неполные: {adset_ids}. Кампания {campaign_id} создана — чистить.")
            return 5
        adset_by_label = {t["label"]: adset_ids[i] for i, t in enumerate(texts)}
        print(f"  адсеты: {adset_by_label}")

        # 4) 15 креативов (5 визуалов × 3 текста)
        print("\n4) 15 креативов (5 визуалов × 3 текста)…")
        order: list[tuple[int, dict]] = [(v, t) for t in texts for v in range(N_VISUALS)]
        entries_cr = [
            make_batch_entry(
                method="POST",
                relative_url=f"{ACT}/adcreatives",
                body_params=creative_body(cfg, v, t, hashes[v]),
            )
            for (v, t) in order
        ]
        creative_ids = await run_chunked(client, entries_cr, "creatives", chunk=8)
        if not all(creative_ids):
            print(f"❌ креативы неполные: {creative_ids}. Чистить кампанию {campaign_id}.")
            return 6

        # 5) 15 ads (креатив → его адсет по тексту)
        print("\n5) 15 объявлений…")
        entries_ads = [
            make_batch_entry(
                method="POST",
                relative_url=f"{ACT}/ads",
                body_params=ad_body(cfg, v, t, adset_by_label[t["label"]], creative_ids[k]),
            )
            for k, (v, t) in enumerate(order)
        ]
        ad_ids = await run_chunked(client, entries_ads, "ads", chunk=8)
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
