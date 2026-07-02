# -*- coding: utf-8 -*-
"""Разведка Facebook Ad Library — GH/Aviator/Crash games.

Изолированный профиль recon_adlib_profile (залогинен в FB).
НЕ боевой Vision. НЕ recon_profile (syntx).

Запуск:
    python scripts/recon_adlib.py --geo GH                          # все дефолтные запросы
    python scripts/recon_adlib.py --geo GH --query "aviator,1xbet"  # свои запросы
    python scripts/recon_adlib.py --geo GH --limit 12               # карточек на запрос

v1: полноценный сбор primary_text / headline / description / CTA + JSON-отчёт.
    Predates v0 (диагностика login-wall + скриншот) — логика login-check сохранена.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PROFILE = Path("data/recon_adlib_profile")
OUT_DIR = Path("data/recon_out")

# Фильтруем по СТРАНЕ АУДИТОРИИ (не рекламодателя)
AD_LIBRARY_TEMPLATE = (
    "https://www.facebook.com/ads/library/"
    "?active_status=all&ad_type=all&country={geo}"
    "&q={query}&search_type=keyword_unordered&media_type=all"
)

DEFAULT_QUERIES_GH = [
    "aviator",
    "aviator ghana",
    "1xbet",
    "betway ghana",
    "spribe",
    "crash game",
    "MTN MoMo bet",
]

# Шум — B2B, predictor-скам, технические страницы
NOISE_PATTERNS = [
    "b2b",
    "igaming provider",
    "predictor app",
    "predictor hack",
    "signal group",
    "hack algorithm",
    "predictor signal",
    "aviator predictor",
    "crash predictor",
]

DEFAULT_LIMIT_PER_QUERY = 8


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


@dataclass
class AdCard:
    query: str
    advertiser: str
    primary_text: str
    headline: str
    description: str
    cta: str
    card_index: int
    url: str = ""

    def is_noise(self) -> bool:
        combined = (self.primary_text + " " + self.advertiser + " " + self.headline).lower()
        return any(n in combined for n in NOISE_PATTERNS)


# JS-сниппет для извлечения карточек — универсальный для структуры 2025-2026
EXTRACT_JS = """
(limit) => {
    const cards = [];

    // Контейнеры карточек объявлений в Ad Library (несколько возможных структур)
    const articles = document.querySelectorAll('[role="article"]');

    for (let i = 0; i < Math.min(articles.length, limit); i++) {
        const el = articles[i];
        const rawText = el.innerText || '';
        const lines = rawText.split('\\n').map(l => l.trim()).filter(l => l.length > 2);

        // Рекламодатель — жирный текст в шапке карточки
        let advertiser = '';
        const bolds = el.querySelectorAll('strong, b');
        if (bolds.length > 0) advertiser = bolds[0].innerText.trim().substring(0, 80);

        // Альтернативный поиск рекламодателя через ссылку на профиль
        if (!advertiser) {
            const profileLink = el.querySelector('a[href*="facebook.com/"]');
            if (profileLink) advertiser = profileLink.innerText.trim().substring(0, 80);
        }

        // Primary text — самый длинный блок (обычно это тело объявления)
        let primaryText = '';
        let maxLen = 0;

        // Ищем div/span с длинным текстом (не ссылка, не кнопка)
        const textCandidates = el.querySelectorAll(
            'div[style*="white-space"], div[class*="text"], span[class*="text"], ' +
            '[data-ad-preview="message"], ._7jyr, [data-testid*="ad-message"]'
        );
        for (const tc of textCandidates) {
            const t = tc.innerText.trim();
            if (t.length > maxLen && t.length < 2000 && !t.startsWith('http')) {
                maxLen = t.length;
                primaryText = t;
            }
        }

        // Если специфичные селекторы не сработали — берём самую длинную строку из lines
        if (!primaryText) {
            for (const line of lines) {
                if (line.length > maxLen && line.length < 2000 && !line.startsWith('http')) {
                    maxLen = line.length;
                    primaryText = line;
                }
            }
        }

        // Headline — специфичный элемент или второй заметный текстовый блок
        let headline = '';
        const headlineEl = el.querySelector(
            '[data-ad-preview="headline"], [class*="headline"], ' +
            'h2, h3, [data-testid*="headline"]'
        );
        if (headlineEl) headline = headlineEl.innerText.trim().substring(0, 200);

        // Description
        let description = '';
        const descEl = el.querySelector(
            '[data-ad-preview="description"], [class*="description"], ' +
            '[data-testid*="description"]'
        );
        if (descEl) description = descEl.innerText.trim().substring(0, 300);

        // CTA button
        let cta = '';
        const ctaEl = el.querySelector(
            '[data-ad-preview="call_to_action_button"], ' +
            'a[role="button"], [class*="cta"], [class*="call-to-action"]'
        );
        if (ctaEl) cta = ctaEl.innerText.trim().substring(0, 50);

        // Все строки карточки — для ручного анализа при падении основных селекторов
        const allLines = lines.slice(0, 25);

        if (primaryText.length > 10 || headline.length > 5) {
            cards.push({
                advertiser,
                primary_text: primaryText.substring(0, 1200),
                headline,
                description,
                cta,
                all_lines: allLines,
            });
        }
    }
    return cards;
}
"""


async def scrape_query(page, geo: str, query: str, limit: int) -> list[AdCard]:
    """Скрапит одну поисковую выдачу Ad Library, возвращает список карточек."""
    url = AD_LIBRARY_TEMPLATE.format(geo=geo, query=query.replace(" ", "%20"))
    print(f"  → {url}", flush=True)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=35_000)
    except Exception as e:
        print(f"  [WARN] goto: {e}", flush=True)

    # Ждём появления карточек или «no results»
    try:
        await page.wait_for_selector(
            '[role="article"], [data-testid="no-results"], [aria-label*="No ads"]',
            timeout=15_000,
        )
    except Exception:
        print("  [WARN] карточки не появились за 15s", flush=True)

    # Lazy-load пауза
    await asyncio.sleep(2.5)

    # Попытка раскрыть «See more» для полного текста
    try:
        see_more = await page.query_selector_all(
            '[role="button"][aria-label*="See more"], [aria-label*="see more"]'
        )
        for btn in see_more[: limit * 2]:
            try:
                await btn.click(timeout=1500)
                await asyncio.sleep(0.2)
            except Exception:
                pass
    except Exception:
        pass

    cards: list[AdCard] = []

    # Основной парсинг через JS evaluate
    try:
        raw_list = await page.evaluate(EXTRACT_JS, limit)
        for idx, raw in enumerate(raw_list):
            card = AdCard(
                query=query,
                advertiser=raw.get("advertiser", "").strip() or "(unknown)",
                primary_text=raw.get("primary_text", "").strip(),
                headline=raw.get("headline", "").strip(),
                description=raw.get("description", "").strip(),
                cta=raw.get("cta", "").strip(),
                card_index=idx,
                url=url,
            )
            cards.append(card)
    except Exception as e:
        print(f"  [ERROR] evaluate: {e}", flush=True)

    # Fallback: если карточек нет — читаем весь body.innerText и разбиваем на блоки
    if not cards:
        try:
            body = await page.evaluate("() => document.body.innerText || ''")
            chunks = [c.strip() for c in re.split(r"\n{3,}", body) if len(c.strip()) > 40]
            if chunks:
                combined = "\n\n---\n\n".join(chunks[:20])
                cards.append(
                    AdCard(
                        query=query,
                        advertiser="(page_text_fallback)",
                        primary_text=combined[:2500],
                        headline="",
                        description="",
                        cta="",
                        card_index=0,
                        url=url,
                    )
                )
                print("  [INFO] используем page_text_fallback", flush=True)
        except Exception:
            pass

    print(f"  [OK] '{query}': {len(cards)} карточек", flush=True)
    return cards


async def run_all(geo: str, queries: list[str], limit: int, profile: Path) -> list[AdCard]:
    """Запускает playwright и прогоняет все запросы."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ImportError("Установи: pip install playwright && playwright install chromium")

    all_cards: list[AdCard] = []

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(profile),
            headless=True,
            viewport={"width": 1280, "height": 900},
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

        # Проверяем FB-сессию
        try:
            await page.goto(
                "https://www.facebook.com/", wait_until="domcontentloaded", timeout=20_000
            )
            await asyncio.sleep(2)
            body_text = await page.evaluate("() => document.body.innerText || ''")
            if re.search(r"log in to facebook|create new account", body_text[:1500], re.I):
                print("[WARN] FB не залогинен — Ad Library может давать меньше данных.", flush=True)
            else:
                print("[INFO] FB сессия активна.", flush=True)
        except Exception as e:
            print(f"[WARN] FB-check: {e}", flush=True)

        for query in queries:
            print(f"\n[QUERY] '{query}'", flush=True)
            try:
                cards = await scrape_query(page, geo, query, limit)
                all_cards.extend(cards)
            except Exception as e:
                print(f"  [ERROR] {query}: {e}", flush=True)
            await asyncio.sleep(1.5)

        await ctx.close()

    return all_cards


def dedup_and_filter(cards: list[AdCard]) -> list[AdCard]:
    """Убирает шум и дублей по первым 100 символам primary_text."""
    seen: set[str] = set()
    result: list[AdCard] = []
    for c in cards:
        if c.is_noise():
            print(f"  [NOISE] {c.advertiser[:40]}", flush=True)
            continue
        key = c.primary_text[:100].lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(c)
    return result


def print_report(cards: list[AdCard]) -> None:
    sep = "─" * 68
    print(f"\n{'=' * 68}")
    print(f"  FB Ad Library — GH/Aviator — {len(cards)} карточек (дедуп, фильтр шума)")
    print(f"{'=' * 68}")
    for i, c in enumerate(cards, 1):
        print(f"\n{sep}")
        print(f"  [{i:02d}] {c.advertiser}  (query: {c.query!r})")
        print(sep)
        if c.primary_text:
            print(f"  TEXT:\n    {c.primary_text[:500].replace(chr(10), chr(10) + '    ')}")
        if c.headline:
            print(f"  HEADLINE: {c.headline}")
        if c.description:
            print(f"  DESC:     {c.description}")
        if c.cta:
            print(f"  CTA:      {c.cta}")
    print(f"\n{'=' * 68}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Recon FB Ad Library (Playwright, headless)")
    parser.add_argument("--geo", default="GH", help="код страны (GH, KE, ...)")
    parser.add_argument(
        "--query",
        "--queries",
        default="",
        help="Запросы через запятую (пусто = дефолт для GH)",
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT_PER_QUERY, help="Карточек на запрос"
    )
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE, help="Путь к профилю")
    args = parser.parse_args()

    queries = [q.strip() for q in args.query.split(",") if q.strip()] or DEFAULT_QUERIES_GH

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    args.profile.mkdir(parents=True, exist_ok=True)

    print(f"[START] geo={args.geo}, запросов={len(queries)}, лимит={args.limit}/запрос")
    print(f"        профиль: {args.profile}")

    cards = asyncio.run(run_all(args.geo, queries, args.limit, args.profile))
    print(f"\n[DEDUP] до={len(cards)}", flush=True)
    cards = dedup_and_filter(cards)
    print(f"[DEDUP] после={len(cards)}", flush=True)

    print_report(cards)

    ts = _ts()
    out_json = OUT_DIR / f"gh_avi_adlib_{ts}.json"
    out_json.write_text(
        json.dumps([asdict(c) for c in cards], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[SAVED] {out_json}")


if __name__ == "__main__":
    main()
