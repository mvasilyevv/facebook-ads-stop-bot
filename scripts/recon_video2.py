# -*- coding: utf-8 -*-
"""
Разведка видео-объявлений FB Ad Library — через text-fallback.
Работает без FB-логина: извлекает Library ID, даты, копии из body.innerText.
Профиль: data/recon_adlib_profile
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

PROFILE = Path("data/recon_adlib_profile")
OUT_DIR = Path("docs/creatives/geo/GH/reports/media_video")

VIDEO_URL_TEMPLATE = (
    "https://www.facebook.com/ads/library/"
    "?active_status=all&ad_type=all&country={geo}"
    "&q={query}&search_type=keyword_unordered&media_type=video"
)

QUERIES = [
    "aviator",
    "aviator ghana",
    "1xbet",
    "betway",
    "bang",
    "spribe",
    "crash game",
    "mostbet",
    "melbet",
]


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def parse_page_text(body: str, query: str) -> list[dict]:
    """Парсит body.innerText страницы Ad Library без авторизации."""
    cards = []

    # Извлекаем все Library ID
    ids = re.findall(r"Library ID:\s*(\d+)", body)
    # Все диапазоны дат рядом с ID
    date_ranges = re.findall(
        r"(\d{1,2}\s+\w+\s+\d{4})\s*[-–—]\s*(\d{1,2}\s+\w+\s+\d{4}|Present|present)", body, re.I
    )
    # Числа копий
    copies_all = re.findall(r"(\d+)\s+ads?\s+use this creative", body, re.I)
    # Статусы
    statuses = re.findall(r"(Active|Inactive)\s*\n", body, re.I)
    # Рекламодатели — строки после "Sponsored"
    # Разбиваем на блоки по Library ID
    blocks = re.split(r"Library ID:\s*\d+", body)

    for i, lid in enumerate(ids):
        block = blocks[i + 1] if i + 1 < len(blocks) else ""

        # Дата из блока
        date_m = re.search(
            r"(\d{1,2}\s+\w+\s+\d{4})\s*[-–—]\s*(\d{1,2}\s+\w+\s+\d{4}|Present|present)",
            block,
            re.I,
        )
        date_range = f"{date_m.group(1)} - {date_m.group(2)}" if date_m else ""
        start_raw = date_m.group(1) if date_m else ""

        # Расчёт длины кампании в днях
        duration_days = 0
        if date_m:
            try:
                from datetime import datetime as dt

                fmt = "%d %b %Y"
                start = dt.strptime(date_m.group(1).strip(), fmt)
                end_str = date_m.group(2).strip()
                if end_str.lower() in ("present", "ещё активно"):
                    end = dt.now()
                else:
                    end = dt.strptime(end_str, fmt)
                duration_days = (end - start).days
            except Exception:
                pass

        # Копии из блока
        copies_m = re.search(r"(\d+)\s+ads?\s+use this creative", block, re.I)
        copies = int(copies_m.group(1)) if copies_m else 1

        # Статус (Active/Inactive) в блоке
        status_m = re.search(r"(Active|Inactive)", block[:200], re.I)
        status = status_m.group(1).lower() if status_m else "unknown"

        # Рекламодатель — текст до "Sponsored" в блоке
        adv_m = re.search(r"([A-Za-z0-9\s&\.\-\']{3,60})\s*\n\s*Sponsored", block[:300])
        advertiser = adv_m.group(1).strip() if adv_m else ""

        # Если рекламодатель не найден — берём первую строку блока
        if not advertiser:
            first_lines = [l.strip() for l in block[:200].split("\n") if len(l.strip()) > 2]
            advertiser = first_lines[0][:60] if first_lines else "(unknown)"

        # Основной текст — самый длинный кусок блока
        block_lines = [l.strip() for l in block.split("\n") if len(l.strip()) > 10]
        primary_text = max(block_lines, key=len, default="")[:400]

        # Сигнал скейла: копии * дни / 100 (относительный score)
        scale_score = (copies * max(duration_days, 1)) / 100

        cards.append(
            {
                "query": query,
                "libraryId": lid,
                "dateRange": date_range,
                "startDateRaw": start_raw,
                "durationDays": duration_days,
                "copies": copies,
                "scaleScore": round(scale_score, 1),
                "status": status,
                "advertiser": advertiser,
                "primaryText": primary_text,
            }
        )

    return cards


async def scrape_query_fallback(page, geo: str, query: str) -> list[dict]:
    """Загружает страницу и извлекает данные через text-парсинг."""
    url = VIDEO_URL_TEMPLATE.format(geo=geo, query=query.replace(" ", "%20"))
    print(f"  GET {url}", flush=True)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=40_000)
    except Exception as e:
        print(f"  [WARN] goto: {e}", flush=True)

    # Ожидаем загрузки контента
    await asyncio.sleep(4)

    # Пытаемся кликнуть "See all results" если есть логин-промпт
    try:
        login_btn = await page.query_selector('a[href*="login"], button:has-text("Log in")')
        if login_btn:
            print("  [INFO] есть login-промпт, пробуем продолжить без логина", flush=True)
    except Exception:
        pass

    # Скролл для lazy-load
    try:
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 1200)")
            await asyncio.sleep(1.2)
    except Exception:
        pass

    # Читаем весь текст страницы
    try:
        body = await page.evaluate("() => document.body.innerText || ''")
    except Exception as e:
        print(f"  [ERROR] innerText: {e}", flush=True)
        return []

    # Проверяем количество результатов
    results_m = re.search(r"~?([\d,]+)\s+results?", body, re.I)
    total_results = results_m.group(1).replace(",", "") if results_m else "?"
    print(f"  [INFO] ~{total_results} результатов на странице", flush=True)

    # Парсим карточки
    cards = parse_page_text(body, query)
    print(f"  [OK] '{query}': {len(cards)} карточек распознано", flush=True)

    return cards


async def main():
    from playwright.async_api import async_playwright

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)

    all_cards: list[dict] = []

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(PROFILE),
            headless=True,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            args=[
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--mute-audio",
                "--no-first-run",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        for q in QUERIES:
            print(f"\n[QUERY] '{q}'", flush=True)
            try:
                cards = await scrape_query_fallback(page, "GH", q)
                all_cards.extend(cards)
            except Exception as e:
                print(f"  [ERROR] {q}: {e}", flush=True)
            await asyncio.sleep(2)

        await ctx.close()

    # Дедупликация по libraryId
    seen: set[str] = set()
    deduped: list[dict] = []
    for c in all_cards:
        key = c.get("libraryId") or c.get("primaryText", "")[:60]
        if key and key not in seen:
            seen.add(key)
            deduped.append(c)

    # Сортируем по scaleScore (скейл-сигнал = копии × дни)
    deduped.sort(key=lambda x: (x.get("scaleScore", 0), x.get("copies", 1)), reverse=True)

    # Выводим топ скейлеров
    print("\n" + "=" * 70)
    print(f"  ВИДЕО GH/Aviator — {len(deduped)} уникальных (топ по scale score)")
    print("=" * 70)
    for i, c in enumerate(deduped[:25], 1):
        score = c.get("scaleScore", 0)
        copies = c.get("copies", 1)
        days = c.get("durationDays", 0)
        print(f"\n[{i:02d}] {c.get('advertiser', '?')[:45]}")
        print(
            f"     LibID: {c.get('libraryId', '')}  |  копий: {copies}  |  дней: {days}  |  score: {score}"
        )
        print(f"     дата: {c.get('dateRange', '')}  |  статус: {c.get('status', '')}")
        txt = c.get("primaryText", "")[:180].replace("\n", " ")
        if txt:
            print(f"     TEXT: {txt}")
        print(f"     query: {c.get('query', '')}")

    # Сохраняем JSON
    ts = _ts()
    out_path = OUT_DIR / f"gh_video_recon_{ts}.json"
    out_path.write_text(json.dumps(deduped, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[SAVED] {out_path}", flush=True)

    return deduped


if __name__ == "__main__":
    asyncio.run(main())
