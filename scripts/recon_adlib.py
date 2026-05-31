# -*- coding: utf-8 -*-
"""Разведка Facebook Ad Library — переиспользуемый Playwright-скрипт (Webwright-подход).

Идея: не поштучные клики, а один re-runnable скрипт. Артефакты (JSON + скриншот + лог)
ложатся в data/recon_out/ — повторяемо и debuggable. Дальше результат идёт в Creative
Registry (references). См. docs/creatives/SOP.md (Фаза 0).

Изолированный профиль (НЕ боевой Vision, НЕ recon_profile с syntx) — свой под FB-расходник.

Запуск:
    # первый раз — войти в FB-расходник вручную (headed), сессия сохранится в профиль:
    python scripts/recon_adlib.py --geo KE --query "chicken road 2" --headed --login
    # дальше — автоматический сбор (headless):
    python scripts/recon_adlib.py --geo KE --query "chicken road"

v0: диагностика (login-state + сколько карточек видно + скриншот). Парсинг карточек —
следующая итерация.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

DEFAULT_PROFILE = Path("data/recon_adlib_profile")
OUT_DIR = Path("data/recon_out")

# active объявления, страна, keyword-поиск (как в creative_kb §2.1)
AD_LIBRARY_URL = (
    "https://www.facebook.com/ads/library/"
    "?active_status=active&ad_type=all&country={geo}"
    "&q={query}&search_type=keyword_unordered&media_type=all"
)


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


async def recon(geo: str, query: str, headed: bool, login: bool, profile: Path) -> dict:
    """Открывает Ad Library и снимает диагностику. Возвращает dict-результат.

    Каталоги (OUT_DIR/profile) создаёт вызывающий (main) — async-функция не делает
    блокирующего файлового I/O (ruff ASYNC240). JSON пишется в main по возврату.
    """
    url = AD_LIBRARY_URL.format(geo=geo, query=query.replace(" ", "%20"))

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(profile),
            headless=not headed,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(6000)  # дать SPA дорисовать выдачу

        diag = await page.evaluate(
            r"""() => {
              const txt = document.body.innerText || '';
              // эвристики FB Ad Library
              const resultMatch = txt.match(/~?\s*([\d,]+)\s+result/i);
              return {
                title: document.title,
                body_len: txt.length,
                results_label: resultMatch ? resultMatch[0] : null,
                login_wall: /log in to facebook|log into facebook|create new account/i.test(txt.slice(0, 1500)),
                head_text: txt.slice(0, 280).replace(/\s+/g, ' '),
              };
            }"""
        )

        if login:
            # Режим логина: ждём, пока пользователь войдёт вручную (headed)
            print("\n[login] Войди в FB-расходник в открытом окне. Жду до 180с...")
            try:
                await page.wait_for_function(
                    "() => !/log in to facebook|create new account/i.test(document.body.innerText.slice(0,1500))",
                    timeout=180000,
                )
                print("[login] похоже, вход выполнен — сохраняю сессию в профиль.")
            except Exception:
                print("[login] не дождался входа (таймаут) — сессия не подтверждена.")
            diag = await page.evaluate(
                "() => ({title: document.title, login_wall: /log in to facebook|create new account/i.test(document.body.innerText.slice(0,1500))})"
            )

        ts = _ts()
        safe_q = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")
        shot = OUT_DIR / f"adlib_{geo}_{safe_q}_{ts}.png"
        await page.screenshot(path=str(shot), full_page=False)

        result = {
            "geo": geo,
            "query": query,
            "url": url,
            "ts_utc": ts,
            "safe_q": safe_q,
            "diagnostic": diag,
            "screenshot": str(shot),
            "logged_in": not diag.get("login_wall", False),
        }
        await ctx.close()
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Разведка FB Ad Library (Playwright)")
    parser.add_argument("--geo", required=True, help="код страны, напр. KE")
    parser.add_argument("--query", required=True, help="поисковый запрос (вариация названия)")
    parser.add_argument("--headed", action="store_true", help="показать окно браузера")
    parser.add_argument("--login", action="store_true", help="режим ручного входа (с --headed)")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE, help="каталог профиля")
    args = parser.parse_args()

    # Каталоги и запись JSON — синхронно в main (async-функция без блокирующего I/O).
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    args.profile.mkdir(parents=True, exist_ok=True)

    res = asyncio.run(recon(args.geo, args.query, args.headed, args.login, args.profile))

    out_json = OUT_DIR / f"adlib_{res['geo']}_{res['safe_q']}_{res['ts_utc']}.json"
    out_json.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== РЕЗУЛЬТАТ ===")
    print(f"logged_in: {res['logged_in']}")
    print(f"title: {res['diagnostic'].get('title')}")
    print(f"results_label: {res['diagnostic'].get('results_label')}")
    print(f"head: {res['diagnostic'].get('head_text', '')[:160]}")
    print(f"скриншот: {res['screenshot']}")
    print(f"json: {out_json}")


if __name__ == "__main__":
    main()
