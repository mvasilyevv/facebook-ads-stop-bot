# -*- coding: utf-8 -*-
"""
Видео-разведка FB Ad Library v2 — GH/Aviator/Crash games (2026-06-07).
Расширенные запросы + скачивание mp4 + раскадровка ffmpeg.

Профиль: data/recon_adlib_profile (залогинен в FB)
Запуск: .venv/bin/python3 scripts/recon_video_v2.py [--no-download]

Новые запросы vs v1: добавлены sportybet, pawa254, betika, bangbet ghana,
  jackpot, win money ghana, mobile money bet.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

PROFILE = Path("data/recon_adlib_profile")
OUT_DIR = Path("docs/creatives/geo/GH/reports/media_video")
FRAMES_DIR = OUT_DIR / "frames_v2"

# URL с фильтром video
VIDEO_URL_TEMPLATE = (
    "https://www.facebook.com/ads/library/"
    "?active_status=all&ad_type=all&country={geo}"
    "&q={query}&search_type=keyword_unordered&media_type=video"
)

# Расширенный набор запросов (новые по сравнению с v1)
QUERIES = [
    # Гемблинг-игра
    "aviator game",
    "aviator ghana",
    "sportybet aviator",
    "bangbet",
    "jackpot ghana",
    # Бренды оператора
    "sportybet ghana",
    "betika ghana",
    "pawa254",
    "betway aviator",
    "1xbet aviator",
    "mostbet aviator",
    # Мобильный платёж — ключевой хук
    "momo win",
    "mtn momo bet",
    "win money ghana",
    "mobile money jackpot",
    # Crash game как жанр
    "crash game win",
    "crash game ghana",
]

LIMIT_PER_QUERY = 12   # карточек на запрос

# Ключевые слова гемблинга — фильтровать чужой шум
GAMBLING_KEYWORDS = [
    "aviator", "crash", "bet", "jackpot", "win", "momo", "sportybet",
    "bangbet", "betika", "1xbet", "mostbet", "pawa", "betway", "spribe",
    "flew away", "cashout", "multiplier", "stake", "ghana", "mobile money",
    "mtn", "deposit", "withdraw",
]
# Явный шум (нерелевантные рекламодатели)
NOISE_ADVERTISERS = [
    "content creator", "newcastle united", "character.ai", "meta for developers",
    "betway scores", "betconstruct", "goal africa", "pub g", "pubg",
    "psg", "paris saint", "xiaomi", "great wall motor", "leilo",
    "alpha books", "mob cooking", "holy quran",
]


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def is_gambling_relevant(card: dict) -> bool:
    """Проверяет релевантность карточки к гемблинг-теме."""
    advertiser = (card.get("advertiser") or "").lower()
    text = (card.get("primaryText") or "").lower()
    combined = advertiser + " " + text

    # Исключаем явный шум
    for noise in NOISE_ADVERTISERS:
        if noise in combined:
            return False

    # Карточки «removed / disabled» с датой — всегда сигнал (конкурент срезан)
    if "didn't follow our advertising" in combined or "was run by an account" in combined:
        return True  # сохраняем для анализа паттерна disabled

    # Проверяем наличие gambling-слов
    return any(kw in combined for kw in GAMBLING_KEYWORDS)


# JS-сниппет — расширенная версия с извлечением mp4-src и preview
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
        const endDateRaw = dateM ? dateM[2] : '';

        // Число копий — сигнал скейла
        const copiesM = rawText.match(/(\\d+)\\s+ads?\\s+use this creative/i);
        const copies = copiesM ? parseInt(copiesM[1]) : 1;

        // Статус
        const inactive = rawText.toLowerCase().includes('inactive');
        const active = rawText.toLowerCase().includes('active');

        // Рекламодатель
        let advertiser = '';
        const bolds = el.querySelectorAll('strong, b');
        if (bolds.length > 0) advertiser = bolds[0].innerText.trim().substring(0, 100);
        if (!advertiser) {
            const profileLink = el.querySelector('a[href*="facebook.com/"]');
            if (profileLink) advertiser = profileLink.innerText.trim().substring(0, 100);
        }
        if (!advertiser && lines.length > 0) advertiser = lines[0].substring(0, 100);

        // Основной текст
        let primaryText = '';
        let maxLen = 0;
        const textNodes = el.querySelectorAll('div, span, p');
        for (const tn of textNodes) {
            const t = tn.innerText.trim();
            if (t.length > maxLen && t.length < 2000 && !t.startsWith('http')
                && !t.includes('Library ID') && t.split(' ').length > 3) {
                maxLen = t.length;
                primaryText = t;
            }
        }

        // Headline
        let headline = '';
        const headlineEl = el.querySelector('h2, h3, [data-ad-preview="headline"]');
        if (headlineEl) headline = headlineEl.innerText.trim().substring(0, 200);

        // Видео-src (если загружено в DOM)
        const videoEl = el.querySelector('video');
        let videoSrc = '';
        if (videoEl) {
            videoSrc = videoEl.src || '';
            if (!videoSrc) {
                const source = videoEl.querySelector('source');
                if (source) videoSrc = source.src || '';
            }
        }

        // Preview/постер — ищем img с данными fbcdn
        let previewSrc = '';
        const imgs = el.querySelectorAll('img');
        for (const img of imgs) {
            const src = img.src || '';
            if (src.includes('fbcdn') || src.includes('facebookcdn') || src.includes('scontent')) {
                previewSrc = src;
                break;
            }
        }

        // Платформы
        const platforms = [];
        if (el.querySelector('[aria-label="Facebook"]')) platforms.push('Facebook');
        if (el.querySelector('[aria-label="Instagram"]')) platforms.push('Instagram');
        if (el.querySelector('[aria-label="Messenger"]')) platforms.push('Messenger');
        if (el.querySelector('[aria-label="Audience Network"]')) platforms.push('Audience Network');

        // Тип назначения (app / web)
        let destination = 'unknown';
        const links = el.querySelectorAll('a[href]');
        for (const a of links) {
            const href = a.href || '';
            if (href.includes('play.google.com') || href.includes('apps.apple.com')) {
                destination = 'app_store';
                break;
            }
            if (href.includes('l.facebook.com') || href.includes('fb.me')) {
                destination = 'web_redirect';
            }
        }

        // Длина видео (из текста карточки если есть)
        const durationM = rawText.match(/(\\d{1,2}:\\d{2})/);
        const duration = durationM ? durationM[1] : '';

        results.push({
            idx: i,
            libraryId,
            dateRange,
            startDateRaw,
            endDateRaw,
            copies,
            status: inactive ? 'inactive' : (active ? 'active' : 'unknown'),
            advertiser: advertiser.substring(0, 100),
            primaryText: primaryText.substring(0, 1000),
            headline,
            platforms,
            videoSrc,
            previewSrc,
            destination,
            duration,
            textLines: lines.slice(0, 20),
        });
    }
    return results;
}
"""


async def try_download_media(url: str, out_path: Path, timeout: int = 20) -> bool:
    """Скачивает mp4 или jpeg по URL. Возвращает True при успехе."""
    if not url or not url.startswith("http"):
        return False
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and len(r.content) > 5000:
                out_path.write_bytes(r.content)
                return True
    except Exception:
        pass
    return False


def run_ffmpeg_frames(mp4_path: Path, frames_subdir: Path, fps: float = 1.0) -> list[Path]:
    """Раскадровывает mp4 через ffmpeg. Возвращает список jpg-кадров."""
    frames_subdir.mkdir(parents=True, exist_ok=True)
    pattern = str(frames_subdir / "frame_%02d.jpg")
    cmd = [
        "ffmpeg", "-y", "-i", str(mp4_path),
        "-vf", f"fps={fps}",
        "-q:v", "3",
        pattern,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        frames = sorted(frames_subdir.glob("frame_*.jpg"))
        return frames
    except Exception as e:
        print(f"  [FFMPEG] ошибка: {e}", flush=True)
        return []


def ffprobe_info(mp4_path: Path) -> dict:
    """Извлекает длительность и разрешение через ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=width,height,codec_name",
             "-of", "json", str(mp4_path)],
            capture_output=True, timeout=15,
        )
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        streams = data.get("streams", [{}])
        w = streams[0].get("width", 0) if streams else 0
        h = streams[0].get("height", 0) if streams else 0
        codec = streams[0].get("codec_name", "") if streams else ""
        return {"duration_s": round(duration, 1), "width": w, "height": h, "codec": codec}
    except Exception:
        return {}


async def scrape_query(page, geo: str, query: str, limit: int) -> list[dict]:
    """Скрапит видео-карточки по одному запросу."""
    url = VIDEO_URL_TEMPLATE.format(geo=geo, query=query.replace(" ", "%20"))
    print(f"  -> {url}", flush=True)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    except Exception as e:
        print(f"  [WARN] goto: {e}", flush=True)

    try:
        await page.wait_for_selector('[role="article"]', timeout=20_000)
    except Exception:
        print("  [WARN] карточки не появились за 20s", flush=True)

    await asyncio.sleep(3.5)

    # Скролл для lazy-load
    for _ in range(3):
        try:
            await page.evaluate("window.scrollBy(0, 1400)")
            await asyncio.sleep(1.2)
        except Exception:
            pass

    # Раскрываем «See more»
    try:
        btns = await page.query_selector_all(
            '[aria-label*="See more"], [aria-label*="see more"], '
            '[data-ad-preview="See more"]'
        )
        for btn in btns[:limit * 3]:
            try:
                await btn.click(timeout=1000)
                await asyncio.sleep(0.1)
            except Exception:
                pass
    except Exception:
        pass

    try:
        cards = await page.evaluate(EXTRACT_JS, limit)
        for c in cards:
            c["query"] = query
        print(f"  [OK] '{query}': {len(cards)} карточек", flush=True)
        return cards
    except Exception as e:
        print(f"  [ERROR] evaluate: {e}", flush=True)

    # Fallback: text-parse
    try:
        body = await page.evaluate("() => document.body.innerText || ''")
        ids = re.findall(r'Library ID:\s*(\d+)', body)
        copies_all = re.findall(r'(\d+)\s+ads?\s+use this creative', body, re.I)
        dates = re.findall(r'(\d{1,2}\s+\w+\s+\d{4})\s*[-–]\s*(\d{1,2}\s+\w+\s+\d{4}|Present)', body, re.I)
        print(f"  [FALLBACK] IDs={ids[:5]}, copies={copies_all[:5]}", flush=True)
        results = []
        for i, lid in enumerate(ids[:limit]):
            results.append({
                "query": query,
                "idx": i,
                "libraryId": lid,
                "dateRange": f"{dates[i][0]} - {dates[i][1]}" if i < len(dates) else "",
                "startDateRaw": dates[i][0] if i < len(dates) else "",
                "endDateRaw": dates[i][1] if i < len(dates) else "",
                "copies": int(copies_all[i]) if i < len(copies_all) else 1,
                "status": "unknown",
                "advertiser": "(fallback)",
                "primaryText": "",
                "headline": "",
                "platforms": [],
                "videoSrc": "",
                "previewSrc": "",
                "destination": "unknown",
                "duration": "",
                "textLines": [],
                "_fallback": True,
            })
        return results
    except Exception as e2:
        print(f"  [ERROR] fallback: {e2}", flush=True)
        return []


async def main(download: bool = True):
    from playwright.async_api import async_playwright

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
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
                "--disable-gpu",
            ],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # Проверяем FB-сессию
        try:
            await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=25_000)
            await asyncio.sleep(2.5)
            body = await page.evaluate("() => document.body.innerText || ''")
            logged_in = not re.search(r"log in to facebook|create new account", body[:2000], re.I)
            print(f"[INFO] FB session: {'ACTIVE' if logged_in else 'NOT LOGGED IN — меньше данных'}", flush=True)
        except Exception as e:
            print(f"[WARN] FB check: {e}", flush=True)

        for q in QUERIES:
            print(f"\n[QUERY] '{q}'", flush=True)
            try:
                cards = await scrape_query(page, "GH", q, LIMIT_PER_QUERY)
                all_cards.extend(cards)
            except Exception as e:
                print(f"  [ERROR] {q}: {e}", flush=True)
            await asyncio.sleep(2.0)

        await ctx.close()

    # Фильтрация — оставляем только gambling-релевантное
    relevant = [c for c in all_cards if is_gambling_relevant(c)]
    print(f"\n[FILTER] всего={len(all_cards)}, gambling-релевантных={len(relevant)}", flush=True)

    # Дедупликация по libraryId
    seen: set[str] = set()
    deduped: list[dict] = []
    for c in relevant:
        lid = c.get("libraryId", "")
        key = lid if lid else c.get("primaryText", "")[:80].lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(c)

    # Сортируем по копиям (скейл-сигнал)
    deduped.sort(key=lambda x: x.get("copies", 1), reverse=True)

    print(f"[DEDUP] уникальных gambling-карточек: {len(deduped)}", flush=True)

    # Скачиваем mp4 и постеры (для топ-20)
    if download:
        print("\n[DOWNLOAD] скачиваем медиа для топ-20 карточек...", flush=True)
        for c in deduped[:20]:
            lid = c.get("libraryId", f"card_{c.get('idx', 0)}")
            adv = re.sub(r'[^a-z0-9]', '', (c.get("advertiser") or "unknown").lower())[:15]
            prefix = f"{adv}_{lid}"

            # Постер
            if c.get("previewSrc"):
                poster_path = OUT_DIR / f"v2_{prefix}_poster.jpg"
                if not poster_path.exists():
                    ok = await try_download_media(c["previewSrc"], poster_path)
                    if ok:
                        print(f"  [DL] постер: {poster_path.name}", flush=True)
                    else:
                        print(f"  [WARN] постер не скачался: {lid}", flush=True)
                c["local_poster"] = str(poster_path) if poster_path.exists() else ""

            # mp4
            if c.get("videoSrc"):
                mp4_path = OUT_DIR / f"v2_{prefix}.mp4"
                if not mp4_path.exists():
                    ok = await try_download_media(c["videoSrc"], mp4_path)
                    if ok:
                        print(f"  [DL] mp4: {mp4_path.name}", flush=True)
                    else:
                        print(f"  [WARN] mp4 не скачался: {lid}", flush=True)

                if mp4_path.exists():
                    c["local_mp4"] = str(mp4_path)
                    # Метаданные
                    info = ffprobe_info(mp4_path)
                    if info:
                        c["video_info"] = info
                        print(f"  [INFO] {mp4_path.name}: {info}", flush=True)
                    # Раскадровка
                    frames_sub = FRAMES_DIR / prefix
                    frames = run_ffmpeg_frames(mp4_path, frames_sub)
                    if frames:
                        c["frames"] = [str(f) for f in frames]
                        print(f"  [FRAMES] {len(frames)} кадров -> {frames_sub}", flush=True)
                else:
                    c["local_mp4"] = ""

    # Печатаем сводку
    print("\n" + "="*70)
    print(f"  GH/AVI ВИДЕО-СКЕЙЛЕРЫ v2 — {len(deduped)} карточек")
    print("="*70)
    for i, c in enumerate(deduped[:25], 1):
        adv = (c.get("advertiser") or "?")[:50]
        lid = c.get("libraryId", "")
        copies = c.get("copies", 1)
        date_range = c.get("dateRange", "")
        status = c.get("status", "")
        query = c.get("query", "")
        dest = c.get("destination", "")
        txt = (c.get("primaryText") or "")[:200].replace("\n", " ")
        video_info = c.get("video_info", {})
        frames_count = len(c.get("frames", []))

        print(f"\n[{i:02d}] {adv}")
        print(f"     LibraryID: {lid}  | копий: {copies}  | {date_range}  | {status}")
        print(f"     query: {query}  | dest: {dest}")
        if video_info:
            dur = video_info.get("duration_s", "?")
            w = video_info.get("width", "?")
            h = video_info.get("height", "?")
            print(f"     Видео: {dur}s  {w}x{h}  | кадров раскадровки: {frames_count}")
        if txt:
            print(f"     TEXT: {txt}")

    # Сохраняем JSON
    ts = _ts()
    out_path = OUT_DIR / f"gh_video_recon_v2_{ts}.json"
    out_path.write_text(json.dumps(deduped, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[SAVED] {out_path}", flush=True)
    print(f"[DONE] Папка: {OUT_DIR}", flush=True)

    return deduped


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-download", action="store_true", help="Не скачивать mp4/постеры")
    args = parser.parse_args()
    asyncio.run(main(download=not args.no_download))
