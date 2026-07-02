# -*- coding: utf-8 -*-
"""
Разведка видео-объявлений FB Ad Library — GH/Aviator/Crash games.
Фокус: только VIDEO-объявления, сигналы скейла (дата + копии).

Профиль: data/recon_adlib_profile (залогинен в FB)
Запуск: .venv/bin/python3 scripts/recon_video.py
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

PROFILE = Path("data/recon_adlib_profile")
OUT_DIR = Path("docs/creatives/geo/GH/reports/media_video")

# URL с фильтром media_type=video
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

LIMIT = 20  # карточек на запрос


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


# JS-сниппет — извлекает карточки видео-объявлений с расширенными метаданными
EXTRACT_JS = """
(limit) => {
    const results = [];
    const articles = document.querySelectorAll('[role="article"]');

    for (let i = 0; i < Math.min(articles.length, limit); i++) {
        const el = articles[i];
        const rawText = el.innerText || '';
        const lines = rawText.split('\\n').map(l => l.trim()).filter(l => l.length > 1);

        // Library ID
        const idM = rawText.match(/Library ID:\\s*(\\d+)/);
        const libraryId = idM ? idM[1] : '';

        // Диапазон дат
        const dateM = rawText.match(/(\\d{1,2}\\s+\\w+\\s+\\d{4})\\s*[-–—]\\s*(\\d{1,2}\\s+\\w+\\s+\\d{4}|Present|present)/i);
        const dateRange = dateM ? dateM[0] : '';
        const startDateRaw = dateM ? dateM[1] : '';

        // Число копий (сигнал скейла)
        const copiesM = rawText.match(/(\\d+)\\s+ads?\\s+use this creative/i);
        const copies = copiesM ? parseInt(copiesM[1]) : 1;

        // Статус active/inactive
        const inactive = !!el.querySelector('[aria-label*="Inactive"], [aria-label*="inactive"]');
        const active = !!el.querySelector('[aria-label*="Active"], [aria-label*="active"]');

        // Рекламодатель — ищем по нескольким стратегиям
        let advertiser = '';
        const bolds = el.querySelectorAll('strong, b');
        if (bolds.length > 0) advertiser = bolds[0].innerText.trim().substring(0, 80);
        if (!advertiser) {
            const profileLink = el.querySelector('a[href*="facebook.com/"]');
            if (profileLink) advertiser = profileLink.innerText.trim().substring(0, 80);
        }
        if (!advertiser && lines.length > 0) advertiser = lines[0].substring(0, 80);

        // Основной текст объявления
        let primaryText = '';
        let maxLen = 0;
        const textNodes = el.querySelectorAll('div, span, p');
        for (const tn of textNodes) {
            const t = tn.innerText.trim();
            if (t.length > maxLen && t.length < 2000 && !t.startsWith('http') && !t.includes('Library ID')) {
                maxLen = t.length;
                primaryText = t;
            }
        }

        // Headline
        let headline = '';
        const headlineEl = el.querySelector('h2, h3, [data-ad-preview="headline"]');
        if (headlineEl) headline = headlineEl.innerText.trim().substring(0, 200);

        // Платформы
        const platforms = [];
        const platformMap = {
            'Facebook': !!el.querySelector('[aria-label="Facebook"], [data-testid="facebook-icon"]'),
            'Instagram': !!el.querySelector('[aria-label="Instagram"]'),
            'Messenger': !!el.querySelector('[aria-label="Messenger"]'),
            'WhatsApp': !!el.querySelector('[aria-label="WhatsApp"]'),
            'Audience Network': !!el.querySelector('[aria-label="Audience Network"]'),
        };
        for (const [p, has] of Object.entries(platformMap)) {
            if (has) platforms.push(p);
        }

        // Наличие видео в карточке
        const videoEl = el.querySelector('video');
        const videoSrc = videoEl ? (videoEl.src || videoEl.querySelector('source')?.src || '') : '';
        const hasVideo = !!videoEl || rawText.includes('video');

        // Превью-изображение
        const imgEl = el.querySelector('img[src*="fbcdn"], img[src*="facebook"]');
        const previewImg = imgEl ? imgEl.src : '';

        // Все значимые строки текста
        const textLines = lines.slice(0, 30);

        results.push({
            idx: i,
            libraryId,
            dateRange,
            startDateRaw,
            copies,
            status: active ? 'active' : (inactive ? 'inactive' : 'unknown'),
            advertiser,
            primaryText: primaryText.substring(0, 800),
            headline,
            platforms,
            hasVideo,
            videoSrc,
            previewImg,
            textLines,
        });
    }
    return results;
}
"""


async def scrape_video_query(page, geo: str, query: str, limit: int) -> list[dict]:
    """Скрапит видео-объявления по одному запросу."""
    url = VIDEO_URL_TEMPLATE.format(geo=geo, query=query.replace(" ", "%20"))
    print(f"  → {url}", flush=True)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=40_000)
    except Exception as e:
        print(f"  [WARN] goto: {e}", flush=True)

    # Ожидаем карточки
    try:
        await page.wait_for_selector('[role="article"]', timeout=18_000)
    except Exception:
        print("  [WARN] карточки не появились", flush=True)

    # Даём lazy-load
    await asyncio.sleep(3)

    # Скролл для загрузки дополнительных карточек
    try:
        await page.evaluate("window.scrollBy(0, 1200)")
        await asyncio.sleep(1.5)
        await page.evaluate("window.scrollBy(0, 1200)")
        await asyncio.sleep(1.5)
    except Exception:
        pass

    # Раскрываем «See more»
    try:
        btns = await page.query_selector_all('[aria-label*="See more"], [aria-label*="see more"]')
        for btn in btns[: limit * 2]:
            try:
                await btn.click(timeout=1000)
                await asyncio.sleep(0.15)
            except Exception:
                pass
    except Exception:
        pass

    # Извлекаем данные
    try:
        cards = await page.evaluate(EXTRACT_JS, limit)
        print(f"  [OK] '{query}': {len(cards)} карточек", flush=True)
        for c in cards:
            c["query"] = query
        return cards
    except Exception as e:
        print(f"  [ERROR] evaluate: {e}", flush=True)

    # Fallback — весь текст страницы
    try:
        body = await page.evaluate("() => document.body.innerText || ''")
        # Ищем Library ID-ы и даты в тексте
        ids = re.findall(r"Library ID:\s*(\d+)", body)
        dates = re.findall(
            r"(\d{1,2}\s+\w+\s+\d{4})\s*[-–]\s*(\d{1,2}\s+\w+\s+\d{4}|Present)", body, re.I
        )
        copies_all = re.findall(r"(\d+)\s+ads?\s+use this creative", body, re.I)
        print(f"  [FALLBACK] IDs={ids[:5]}, dates={dates[:3]}, copies={copies_all[:5]}", flush=True)
        return [
            {
                "query": query,
                "idx": 0,
                "libraryId": ids[0] if ids else "",
                "dateRange": f"{dates[0][0]} - {dates[0][1]}" if dates else "",
                "copies": int(copies_all[0]) if copies_all else 1,
                "status": "unknown",
                "advertiser": "(fallback)",
                "primaryText": body[:2000],
                "headline": "",
                "platforms": [],
                "hasVideo": True,
                "videoSrc": "",
                "previewImg": "",
                "textLines": [],
                "_fallback": True,
                "_all_ids": ids[:10],
                "_all_copies": [int(x) for x in copies_all[:10]],
                "_all_dates": [f"{d[0]} - {d[1]}" for d in dates[:10]],
            }
        ]
    except Exception as e2:
        print(f"  [ERROR] fallback: {e2}", flush=True)
        return []


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
                "--disable-background-networking",
                "--disable-extensions",
                "--mute-audio",
                "--no-first-run",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # Проверяем сессию FB
        try:
            await page.goto(
                "https://www.facebook.com/", wait_until="domcontentloaded", timeout=25_000
            )
            await asyncio.sleep(2)
            body = await page.evaluate("() => document.body.innerText || ''")
            logged_in = not re.search(r"log in to facebook|create new account", body[:1500], re.I)
            print(
                f"[INFO] FB session: {'ACTIVE ✓' if logged_in else 'NOT LOGGED IN ✗'}", flush=True
            )
        except Exception as e:
            print(f"[WARN] FB check: {e}", flush=True)
            logged_in = False

        for q in QUERIES:
            print(f"\n[QUERY] '{q}'", flush=True)
            try:
                cards = await scrape_video_query(page, "GH", q, LIMIT)
                all_cards.extend(cards)
            except Exception as e:
                print(f"  [ERROR] {q}: {e}", flush=True)
            await asyncio.sleep(2)

        await ctx.close()

    # Дедупликация по libraryId
    seen_ids: set[str] = set()
    deduped: list[dict] = []
    for c in all_cards:
        lid = c.get("libraryId", "")
        key = lid if lid else c.get("primaryText", "")[:80]
        if key and key not in seen_ids:
            seen_ids.add(key)
            deduped.append(c)

    # Сортируем по числу копий (скейл-сигнал)
    deduped.sort(key=lambda x: x.get("copies", 1), reverse=True)

    print(f"\n[RESULT] всего={len(all_cards)}, после дедупа={len(deduped)}", flush=True)

    # Печатаем топ-скейлеров
    print("\n" + "=" * 70)
    print("  ТОП ВИДЕО-СКЕЙЛЕРЫ (сортировка по копиям)")
    print("=" * 70)
    for i, c in enumerate(deduped[:20], 1):
        print(f"\n[{i:02d}] {c.get('advertiser', '?')[:50]}")
        print(
            f"     ID: {c.get('libraryId', '')}  |  копий: {c.get('copies', 1)}  |  дата: {c.get('dateRange', '')}"
        )
        print(f"     query: {c.get('query', '')}  |  статус: {c.get('status', '')}")
        txt = c.get("primaryText", "")[:200].replace("\n", " ")
        if txt:
            print(f"     TEXT: {txt}")
        hl = c.get("headline", "")
        if hl:
            print(f"     HL:   {hl}")

    # Сохраняем JSON
    ts = _ts()
    out_path = OUT_DIR / f"gh_video_recon_{ts}.json"
    out_path.write_text(json.dumps(deduped, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[SAVED] {out_path}", flush=True)

    return deduped


if __name__ == "__main__":
    asyncio.run(main())
