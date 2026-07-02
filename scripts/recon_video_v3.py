# -*- coding: utf-8 -*-
"""
Видео-разведка FB Ad Library v3 — GH/Aviator/Crash games (2026-06-07).
Без логина — парсим page_text напрямую (структура DOM без role=article).
Скачиваем mp4/постеры по прямым ссылкам из DOM.

Профиль: data/recon_adlib_profile (headless)
Запуск: .venv/bin/python3 scripts/recon_video_v3.py [--no-download]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx

PROFILE = Path("data/recon_adlib_profile")
OUT_DIR = Path("docs/creatives/geo/GH/reports/media_video")
FRAMES_DIR = OUT_DIR / "frames_v3"

VIDEO_URL_TEMPLATE = (
    "https://www.facebook.com/ads/library/"
    "?active_status=all&ad_type=all&country={geo}"
    "&q={query}&search_type=keyword_unordered&media_type=video"
    "&sort_data[mode]=total_impressions&sort_data[direction]=desc"
)

QUERIES_V3 = [
    # Прямые gambling-запросы
    "aviator",
    "aviator bet",
    "crash bet",
    "bangbet",
    "sportybet",
    # Мобильные деньги
    "momo aviator",
    "mobile money win",
    # Операторы с GH-фокусом
    "1xbet ghana",
    "betway ghana",
    "mostbet ghana",
    "melbet ghana",
    # Crash как жанр
    "crash game bet",
    # Денежный хук
    "win cash ghana",
    "jackpot bet ghana",
]

GAMBLING_KW = [
    "aviator",
    "crash",
    "bet",
    "jackpot",
    "win",
    "momo",
    "sportybet",
    "bangbet",
    "betika",
    "1xbet",
    "mostbet",
    "pawa",
    "betway",
    "spribe",
    "flew away",
    "cashout",
    "multiplier",
    "stake",
    "ghana",
    "mobile money",
    "mtn",
    "deposit",
    "withdraw",
    "lucky",
    "odds",
    "casino",
    "slot",
    "bonus",
    "promo",
]

NOISE_KW = [
    "content creator.com",
    "newcastle united",
    "character.ai",
    "meta for developers",
    "betway scores",
    "betconstruct",
    "goal africa",
    "pubg mobile",
    "psg - paris saint",
    "xiaomi",
    "great wall motor",
    "leilo",
    "mob cooking",
    "holy quran",
    "alpha books",
    "satellite view",
    "translator",
    "share location",
    "gps camera",
    "ai photo",
    "smart translator",
]


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


# JS-сниппет v3: не зависит от role=article, ищет карточки по Library ID в тексте
# и извлекает media-src из DOM
EXTRACT_JS_V3 = """
() => {
    const results = [];
    const fullText = document.body.innerText || '';

    // Находим все блоки карточек между Library ID упоминаниями
    // Стратегия: разбиваем по паттерну "Library ID: XXXXXX"
    const idPattern = /Library ID:\\s*(\\d{8,20})/g;
    let match;
    const positions = [];
    while ((match = idPattern.exec(fullText)) !== null) {
        positions.push({ id: match[1], pos: match.index });
    }

    // Для каждого ID извлекаем блок текста до следующего ID
    for (let i = 0; i < positions.length; i++) {
        const start = positions[i].pos;
        const end = i + 1 < positions.length ? positions[i + 1].pos : start + 2000;
        const block = fullText.substring(start, end);

        // Даты
        const dateM = block.match(/(\\d{1,2}\\s+\\w+\\s+\\d{4})\\s*[-–—]\\s*(\\d{1,2}\\s+\\w+\\s+\\d{4}|Present|present)/i);
        const dateRange = dateM ? dateM[0] : '';
        const startDate = dateM ? dateM[1] : '';
        const endDate = dateM ? dateM[2] : '';

        // Копии
        const copiesM = block.match(/(\\d+)\\s+ads?\\s+use this creative/i);
        const copies = copiesM ? parseInt(copiesM[1]) : 1;

        // Статус
        const status = block.toLowerCase().includes('inactive') ? 'inactive' :
                       (block.toLowerCase().includes('active') ? 'active' : 'unknown');

        // Disabled/removed
        const isDisabled = block.includes("didn't follow") || block.includes("was run by an account");

        // Текст объявления (после "Sponsored" или основной текст)
        let adText = '';
        const sponsoredM = block.match(/Sponsored\\n([^]+?)(?=Library ID|$)/);
        if (sponsoredM) {
            adText = sponsoredM[1].trim().substring(0, 800);
        } else {
            // Берём всё после первой строки с Library ID
            adText = block.substring(block.indexOf('\\n')).trim().substring(0, 800);
        }

        results.push({
            libraryId: positions[i].id,
            dateRange,
            startDate,
            endDate,
            copies,
            status,
            isDisabled,
            adText: adText.replace(/\\n/g, ' ').trim(),
            blockText: block.substring(0, 500),
        });
    }

    // Отдельно собираем video src и poster из DOM
    const mediaItems = [];
    const videos = document.querySelectorAll('video');
    for (const v of videos) {
        const src = v.src || (v.querySelector('source') ? v.querySelector('source').src : '');
        const poster = v.poster || '';
        if (src || poster) {
            mediaItems.push({ type: 'video', src, poster });
        }
    }

    // Preview-изображения (thumbnail карточек)
    const imgs = document.querySelectorAll('img[src*="fbcdn"], img[src*="scontent"]');
    for (const img of imgs) {
        if (img.src && img.naturalWidth > 50) {
            mediaItems.push({ type: 'image', src: img.src, poster: '' });
        }
    }

    return { cards: results, mediaItems: mediaItems.slice(0, 50) };
}
"""


def is_gambling(card: dict) -> bool:
    text = (card.get("adText") or "").lower() + " " + (card.get("blockText") or "").lower()
    for n in NOISE_KW:
        if n in text:
            return False
    if card.get("isDisabled"):
        return True  # disabled = всегда сохранить для разведки
    return any(kw in text for kw in GAMBLING_KW)


def parse_date_days(start_date_str: str) -> int:
    """Примерное число дней с даты старта."""
    if not start_date_str:
        return 0
    try:
        d = datetime.strptime(start_date_str.strip(), "%d %b %Y")
        delta = datetime.now() - d
        return max(0, delta.days)
    except Exception:
        return 0


async def try_download(url: str, out_path: Path, timeout: int = 25) -> bool:
    if not url or not url.startswith("http"):
        return False
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            r = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Referer": "https://www.facebook.com/",
                },
            )
            if r.status_code == 200 and len(r.content) > 2000:
                out_path.write_bytes(r.content)
                return True
    except Exception:
        pass
    return False


def ffprobe_info(mp4_path: Path) -> dict:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=width,height,codec_name",
                "-of",
                "json",
                str(mp4_path),
            ],
            capture_output=True,
            timeout=15,
        )
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        streams = data.get("streams", [{}])
        return {
            "duration_s": round(duration, 1),
            "width": streams[0].get("width", 0) if streams else 0,
            "height": streams[0].get("height", 0) if streams else 0,
            "codec": streams[0].get("codec_name", "") if streams else "",
        }
    except Exception:
        return {}


def run_ffmpeg_frames(mp4_path: Path, frames_subdir: Path, fps: float = 0.5) -> list[Path]:
    frames_subdir.mkdir(parents=True, exist_ok=True)
    pattern = str(frames_subdir / "frame_%02d.jpg")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(mp4_path), "-vf", f"fps={fps}", "-q:v", "3", pattern],
            capture_output=True,
            timeout=60,
        )
        return sorted(frames_subdir.glob("frame_*.jpg"))
    except Exception as e:
        print(f"  [FFMPEG ERR] {e}", flush=True)
        return []


async def scrape_one(page, geo: str, query: str) -> dict:
    url = VIDEO_URL_TEMPLATE.format(geo=geo, query=query.replace(" ", "%20"))
    print(f"  -> {url}", flush=True)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    except Exception as e:
        print(f"  [WARN] goto: {e}", flush=True)

    # Ожидаем контент
    try:
        await page.wait_for_selector("text=Library ID", timeout=18_000)
    except Exception:
        pass

    await asyncio.sleep(4)

    # Скролл
    for _ in range(4):
        try:
            await page.evaluate("window.scrollBy(0, 1600)")
            await asyncio.sleep(1.0)
        except Exception:
            pass

    # Извлекаем данные
    try:
        data = await page.evaluate(EXTRACT_JS_V3)
        cards = data.get("cards", [])
        media = data.get("mediaItems", [])
        for c in cards:
            c["query"] = query
            c["duration_days"] = parse_date_days(c.get("startDate", ""))
        print(
            f"  [OK] '{query}': {len(cards)} ID-карточек, {len(media)} медиа-элементов", flush=True
        )
        return {"cards": cards, "media": media, "query": query}
    except Exception as e:
        print(f"  [ERROR] evaluate: {e}", flush=True)

    # Полный fallback — text-parse
    try:
        body = await page.evaluate("() => document.body.innerText || ''")
        ids = re.findall(r"Library ID:\s*(\d+)", body)
        copies_all = re.findall(r"(\d+)\s+ads?\s+use this creative", body, re.I)
        dates_all = re.findall(
            r"(\d{1,2}\s+\w+\s+\d{4})\s*[-–]\s*(\d{1,2}\s+\w+\s+\d{4}|Present)", body, re.I
        )
        print(
            f"  [FALLBACK] IDs={ids[:5]}, copies={copies_all[:5]}, dates={dates_all[:3]}",
            flush=True,
        )
        cards = []
        for i, lid in enumerate(ids[:20]):
            cards.append(
                {
                    "libraryId": lid,
                    "dateRange": f"{dates_all[i][0]} - {dates_all[i][1]}"
                    if i < len(dates_all)
                    else "",
                    "startDate": dates_all[i][0] if i < len(dates_all) else "",
                    "endDate": dates_all[i][1] if i < len(dates_all) else "",
                    "copies": int(copies_all[i]) if i < len(copies_all) else 1,
                    "status": "unknown",
                    "isDisabled": False,
                    "adText": "",
                    "blockText": "",
                    "query": query,
                    "duration_days": parse_date_days(dates_all[i][0] if i < len(dates_all) else ""),
                }
            )
        return {"cards": cards, "media": [], "query": query}
    except Exception as e2:
        print(f"  [ERROR] fallback: {e2}", flush=True)
        return {"cards": [], "media": [], "query": query}


async def main(download: bool = True):
    from playwright.async_api import async_playwright

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)

    all_cards: list[dict] = []
    all_media: list[dict] = []

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(PROFILE),
            headless=True,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            args=[
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--disable-gpu",
                "--mute-audio",
            ],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        for q in QUERIES_V3:
            print(f"\n[QUERY] '{q}'", flush=True)
            try:
                result = await scrape_one(page, "GH", q)
                all_cards.extend(result["cards"])
                all_media.extend(result["media"])
            except Exception as e:
                print(f"  [ERROR] {q}: {e}", flush=True)
            await asyncio.sleep(2.5)

        await ctx.close()

    # Gambling-фильтр и дедупликация
    relevant = [c for c in all_cards if is_gambling(c)]
    print(f"\n[FILTER] gambling={len(relevant)}/{len(all_cards)}", flush=True)

    seen: set[str] = set()
    deduped: list[dict] = []
    for c in relevant:
        lid = c.get("libraryId", "")
        if lid and lid not in seen:
            seen.add(lid)
            deduped.append(c)

    # Сортировка: scale score = copies * ln(duration_days + 2)
    import math

    for c in deduped:
        d = max(1, c.get("duration_days", 1))
        copies = max(1, c.get("copies", 1))
        c["scale_score"] = round(copies * math.log(d + 2), 2)

    deduped.sort(key=lambda x: x.get("scale_score", 0), reverse=True)

    print(f"[DEDUP] уникальных gambling: {len(deduped)}", flush=True)

    # Скачиваем медиа (видео из DOM — часто не отдаются без сессии, но пробуем)
    if download and all_media:
        print(f"\n[DOWNLOAD] медиа из DOM: {len(all_media)} элементов...", flush=True)
        downloaded_videos = 0
        for i, m in enumerate(all_media[:30]):
            src = m.get("src") or m.get("poster", "")
            if not src:
                continue
            ext = ".mp4" if m.get("type") == "video" else ".jpg"
            out_path = OUT_DIR / f"v3_media_{i:03d}{ext}"
            if not out_path.exists():
                ok = await try_download(src, out_path)
                if ok and ext == ".mp4":
                    downloaded_videos += 1
                    info = ffprobe_info(out_path)
                    if info:
                        print(f"  [MP4] {out_path.name}: {info}", flush=True)
                        frames_sub = FRAMES_DIR / f"media_{i:03d}"
                        frames = run_ffmpeg_frames(out_path, frames_sub)
                        print(f"  [FRAMES] {len(frames)} кадров", flush=True)
                elif ok:
                    print(f"  [IMG] {out_path.name}", flush=True)

    # Сводка
    print("\n" + "=" * 72)
    print(f"  GH/AVI ВИДЕО v3 — {len(deduped)} gambling-карточек")
    print("=" * 72)
    for i, c in enumerate(deduped[:30], 1):
        lid = c.get("libraryId", "")
        copies = c.get("copies", 1)
        dr = c.get("dateRange", "")
        days = c.get("duration_days", 0)
        ss = c.get("scale_score", 0)
        status = c.get("status", "")
        disabled = " [DISABLED]" if c.get("isDisabled") else ""
        query = c.get("query", "")
        txt = (c.get("adText") or "")[:200].replace("\n", " ")

        print(f"\n[{i:02d}] LibraryID={lid}  | копий={copies}  | {days}дн  | score={ss}")
        print(f"     {dr}  | {status}{disabled}  | query={query}")
        if txt:
            print(f"     TEXT: {txt}")

    # JSON
    ts = _ts()
    out_path = OUT_DIR / f"gh_video_recon_v3_{ts}.json"
    out_path.write_text(json.dumps(deduped, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[SAVED] {out_path}")
    print(f"[DIR] {OUT_DIR.resolve()}")

    return deduped


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-download", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(download=not args.no_download))
