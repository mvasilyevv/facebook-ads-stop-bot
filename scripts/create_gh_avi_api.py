# -*- coding: utf-8 -*-
"""Создание FB-кампании GH/Aviator через Graph API (Batch) — ЭТАП 1: тест креативов.

Структура 1 × 5 × 1 (ABO): 1 кампания × 5 адсетов × 1 объявление, статус PAUSED.
5 адсетов = 5 РАЗНЫХ картинок (CR001–005, 1-я копия) при ЕДИНОМ тексте — ищем
лучший ВИЗУАЛ по FTD. Углы по тексту — Этап 2 (отдельный залив на победителе).

Оформление по эталону ABO-кампаний кабинета (Artemteam 1-3-1):
  - bid_strategy LOWEST_COST_WITHOUT_CAP — на УРОВНЕ КАМПАНИИ (адсеты чистые);
  - promoted_object {pixel, PURCHASE, smart_pse_enabled:false};
  - attribution_spec CLICK_THROUGH 1 день;
  - targeting [GH, AQ] + location_types [home,recent] + Advantage+ audience + age 18-65;
  - destination WEBSITE, billing IMPRESSIONS, optimization OFFSITE_CONVERSIONS.

Каналы (через активную Vision-сессию, без httpx):
  1) кампания — отдельным 1-entry batch (на неё никто не ссылается → id приходит чисто);
  2) 5 картинок → image_hash (MediaUploader);
  3) batch адсетов (реальный campaign_id, без JSONPath);
  4) batch креативов; 5) batch ads.

Режимы:
    python scripts/create_gh_avi_api.py            # spec-print: page_id + тела, БЕЗ создания
    python scripts/create_gh_avi_api.py --go       # боевое создание PAUSED
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

# ====================== Параметры GH_AVI ======================

ACT = "act_26943307705301002"
ACT_NUM = ACT.removeprefix("act_")  # числовой id кабинета (sub4 трекинга — БЕЗ act_)
PIXEL_ID = "1282495953856981"
CAMPAIGN_NAME = "MV | GH | AVI | adset.pro | 04.06"  # дата = следующий день
LANDING = "https://space2go.forum/track/6a1f37ebf10ec2c6fce437c6/ads"
CREO_ROOT = os.path.expanduser("~/Documents/FB_Agent_Creo/GH_AVI_campaign01")

PAGE_ID = "103053722121477"  # Game star — выбрана байером из promote_pages кабинета
# Старт = следующие сутки 00:00 по TZ кабинета America/Hermosillo (UTC-7)
START_TIME = "2026-06-04T00:00:00-07:00"
# Advantage+ «оптимизация текста» (v22.0 — opt-in поштучно): OPT_OUT=выкл / OPT_IN=вкл
TEXT_OPT_ENROLL = "OPT_OUT"

COUNTRIES = ["GH", "AQ"]  # Гана + Антарктида
AGE_MIN, AGE_MAX = 18, 65  # как эталон
DAILY_BUDGET_CENTS = 299  # $2.99 ABO на адсет
BID_STRATEGY = "LOWEST_COST_WITHOUT_CAP"  # на адсете (ABO)
CTA_TYPE = "PLAY_GAME"
N_ADSETS = 5

# Концепт КАРТИНКИ (визуал) каждого адсета — не текстовый угол (текст единый)
VISUAL_LABELS = [
    "Proof Post",  # CR001
    "Before/After",  # CR002
    "FOMO",  # CR003
    "Adrenaline",  # CR004
    "Football",  # CR005
]

# Единый текст для всех 5 адсетов (тестируем картинку, не текст)
PRIMARY_TEXT = (
    "Deposit just GHS 10, play Aviator, and cash out straight to your MTN MoMo \U0001f4b8✈️\n"
    "New players get 20 FREE BETS on your first deposit! \U0001f1ec\U0001f1ed\n"
    "Small start, real wins — withdraw to MoMo anytime. Play now!"
)
HEADLINE = "Deposit GHS 10 → Get 20 Free Bets on Aviator"
DESCRIPTION = "Cash out wins straight to MTN MoMo. Fast & safe."


def format_code(a: int) -> str:
    return f"GH_AVI_CR00{a + 1}"


def adset_name(a: int) -> str:
    return f"CR00{a + 1} | {VISUAL_LABELS[a]}"


def creo_path(a: int) -> str:
    # 1-я копия каждого формата: CREO_ROOT/{a+1}/GH_AVI_CR00{a+1}_1.jpeg
    return os.path.join(CREO_ROOT, str(a + 1), f"GH_AVI_CR00{a + 1}_1.jpeg")


def url_tags(a: int) -> str:
    return (
        "sub2=MV"
        f"&sub3={format_code(a)}"
        f"&sub4={ACT_NUM}"
        "&sub5={{campaign.name}}"
        "&sub6={{adset.name}}"
        "&sub7={{ad.name}}"
        "&sub8={{ad.id}}"
    )


# ====================== Тела entry ======================


def campaign_body() -> dict:
    # ABO: кампания БЕЗ бюджета и БЕЗ bid_strategy (Meta не даёт стратегию на
    # кампании без её бюджета, subcode 1885737). Стратегия — на адсете.
    return {
        "name": CAMPAIGN_NAME,
        "objective": "OUTCOME_SALES",
        "status": "PAUSED",
        "special_ad_categories": ["NONE"],
    }


def adset_body(a: int, campaign_id: str) -> dict:
    return {
        "name": adset_name(a),
        "campaign_id": campaign_id,  # реальный id, без JSONPath
        "billing_event": "IMPRESSIONS",
        "optimization_goal": "OFFSITE_CONVERSIONS",
        "bid_strategy": BID_STRATEGY,  # на адсете (ABO) — проверено рабочим debug
        "daily_budget": DAILY_BUDGET_CENTS,
        "destination_type": "WEBSITE",
        "promoted_object": {
            "pixel_id": PIXEL_ID,
            "custom_event_type": "PURCHASE",
            "smart_pse_enabled": False,
        },
        "attribution_spec": [{"event_type": "CLICK_THROUGH", "window_days": 1}],  # 1 день
        "targeting": {
            "geo_locations": {"countries": COUNTRIES, "location_types": ["home", "recent"]},
            "age_min": AGE_MIN,
            "age_max": AGE_MAX,
            "targeting_automation": {"advantage_audience": 1},  # Advantage+ audience
        },
        "start_time": START_TIME,  # старт со следующих суток 00:00 (TZ кабинета)
        "status": "PAUSED",
    }


def creative_body(a: int, page_id: str, image_hash: str) -> dict:
    return {
        "name": f"{format_code(a)}_1",
        "object_story_spec": {
            "page_id": page_id,
            "link_data": {
                "message": PRIMARY_TEXT,
                "link": LANDING,
                "image_hash": image_hash,
                "name": HEADLINE,
                "description": DESCRIPTION,
                "call_to_action": {"type": CTA_TYPE, "value": {"link": LANDING}},
            },
        },
        "url_tags": url_tags(a),
        "degrees_of_freedom_spec": {
            "creative_features_spec": {
                # «оптимизация текста» — явный opt-in/out (v22.0 поштучно)
                "text_optimizations": {"enroll_status": TEXT_OPT_ENROLL},
            }
        },
    }


def ad_body(a: int, adset_id: str, creative_id: str) -> dict:
    return {
        "name": f"{format_code(a)}_1",
        "adset_id": adset_id,
        "creative": {"creative_id": creative_id},
        "status": "PAUSED",
    }


# ====================== page_id ======================


async def fetch_page_id(client: MetaApiClient) -> str | None:
    try:
        resp = await client.execute_graph_call(
            method="GET",
            endpoint=f"/{ACT}/promote_pages",
            query_params={"fields": "id,name", "limit": "5"},
        )
        data = resp.get("data") or []
        if data:
            print(f"  page из promote_pages: {data[0].get('name')} ({data[0].get('id')})")
            return str(data[0]["id"])
    except Exception as exc:  # noqa: BLE001
        print(f"  promote_pages не сработал: {exc}")
    try:
        resp = await client.execute_graph_call(
            method="GET",
            endpoint=f"/{ACT}/adcreatives",
            query_params={"fields": "object_story_spec{page_id}", "limit": "50"},
        )
        for cr in resp.get("data") or []:
            pid = (cr.get("object_story_spec") or {}).get("page_id")
            if pid:
                print(f"  page из существующего креатива: {pid}")
                return str(pid)
    except Exception as exc:  # noqa: BLE001
        print(f"  adcreatives-скан не сработал: {exc}")
    return None


# ====================== Batch-раннер ======================


async def run_batch(client: MetaApiClient, entries: list[dict], label: str) -> list[dict]:
    payload = build_batch_payload(entries)
    resp = await client.execute_graph_call(
        method="POST", endpoint="/", query_params={"batch": payload}
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


# ====================== main ======================


async def main(go: bool) -> int:
    missing = [creo_path(a) for a in range(N_ADSETS) if not os.path.exists(creo_path(a))]  # noqa: ASYNC240
    if missing:
        print("НЕТ файлов:", *missing, sep="\n  ")
        return 2

    client = MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST", "localhost"), port=50051)
    await client.start()
    try:
        page_id = PAGE_ID or await fetch_page_id(client)
        if not page_id:
            print("❌ page_id не найден.")
            return 3
        try:
            pg = await client.execute_graph_call(
                method="GET", endpoint=f"/{page_id}", query_params={"fields": "name,category"}
            )
            print(f"✅ page = {pg.get('name')} ({page_id}) · {pg.get('category')}")
        except Exception:  # noqa: BLE001
            print(f"✅ page_id = {page_id}")

        print(f"\nКАМПАНИЯ: {CAMPAIGN_NAME}  (ЭТАП 1 — тест креативов)")
        print(f"  {ACT} · пиксель {PIXEL_ID}·PURCHASE · гео {COUNTRIES} · age {AGE_MIN}-{AGE_MAX}")
        print(f"  ABO ${DAILY_BUDGET_CENTS / 100:.2f}/адсет · {BID_STRATEGY} (на адсете)")
        print("  OUTCOME_SALES/OFFSITE_CONVERSIONS · attribution CLICK 1d · Advantage+ · PAUSED")
        print(f"  старт: {START_TIME} · оптимизация текста: {TEXT_OPT_ENROLL}")
        print("  адсеты (1 ад каждый): " + " | ".join(adset_name(a) for a in range(N_ADSETS)))
        print(f"  единый текст: «{HEADLINE}»")

        if not go:
            print(
                "\n[spec-print] боевого создания НЕ делаю (нет --go). Тела валидны, page_id найден."
            )
            return 0

        # ---- 1) кампания (отдельный 1-entry batch) ----
        print("\n1) Создаю кампанию…")
        subs_c = await run_batch(
            client,
            [
                make_batch_entry(
                    method="POST", relative_url=f"{ACT}/campaigns", body_params=campaign_body()
                )
            ],
            "campaign",
        )
        campaign_id = ids_from(subs_c)[0]
        if not campaign_id:
            print("❌ кампания не создана — см. ошибку выше.")
            return 4
        print(f"  campaign_id={campaign_id}")

        # ---- 2) 5 картинок → image_hash ----
        from core.meta_api.upload import MediaUploader

        uploader = MediaUploader(client)
        hashes: list[str] = []
        print("\n2) Загружаю 5 картинок → image_hash…")
        for a in range(N_ADSETS):
            p = creo_path(a)
            with open(p, "rb") as f:  # noqa: ASYNC230
                data = f.read()
            h = await uploader.upload_image(
                ACT, data, filename=os.path.basename(p), content_type="image/jpeg"
            )
            hashes.append(h)
            print(f"  {os.path.basename(p)} → {h[:18]}…")

        # ---- 3) 5 адсетов (реальный campaign_id) ----
        print("\n3) Создаю 5 адсетов…")
        entries_a = [
            make_batch_entry(
                method="POST", relative_url=f"{ACT}/adsets", body_params=adset_body(a, campaign_id)
            )
            for a in range(N_ADSETS)
        ]
        adset_ids = ids_from(await run_batch(client, entries_a, "adsets"))
        if not all(adset_ids):
            print(f"❌ адсеты неполные: {adset_ids}. Кампания {campaign_id} создана — чистить.")
            return 5
        print(f"  adset_ids={adset_ids}")

        # ---- 4) 5 креативов ----
        print("\n4) Создаю 5 креативов…")
        entries_b = [
            make_batch_entry(
                method="POST",
                relative_url=f"{ACT}/adcreatives",
                body_params=creative_body(a, page_id, hashes[a]),
            )
            for a in range(N_ADSETS)
        ]
        creative_ids = ids_from(await run_batch(client, entries_b, "creatives"))
        if not all(creative_ids):
            print(f"❌ креативы неполные: {creative_ids}. Чистить кампанию {campaign_id}.")
            return 6

        # ---- 5) 5 ads ----
        print("\n5) Создаю 5 объявлений…")
        entries_c = [
            make_batch_entry(
                method="POST",
                relative_url=f"{ACT}/ads",
                body_params=ad_body(a, adset_ids[a], creative_ids[a]),
            )
            for a in range(N_ADSETS)
        ]
        ad_ids = ids_from(await run_batch(client, entries_c, "ads"))
        ok_ads = sum(1 for x in ad_ids if x)

        print("\n" + "=" * 60)
        print(f"✅ ГОТОВО: campaign_id={campaign_id} (PAUSED)")
        print(
            f"   адсетов: {len(adset_ids)} · креативов: {sum(1 for x in creative_ids if x)} · "
            f"объявлений: {ok_ads}/{N_ADSETS}"
        )
        print(f"   '{CAMPAIGN_NAME}' — ревью в Ads Manager → unpause сам.")
        if ok_ads < N_ADSETS:
            print(f"   ⚠️ объявлений {ok_ads}/{N_ADSETS} — проверь упавшие выше.")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    go = "--go" in sys.argv[1:]
    raise SystemExit(asyncio.run(main(go)))
