# -*- coding: utf-8 -*-
"""Диагностика: что видит headless-браузер на FB Ad Library."""

import asyncio
from pathlib import Path

PROFILE = Path("data/recon_adlib_profile")
OUT_DIR = Path("docs/creatives/geo/GH/reports/media_video")

TEST_URL = (
    "https://www.facebook.com/ads/library/"
    "?active_status=all&ad_type=all&country=GH"
    "&q=aviator&search_type=keyword_unordered&media_type=video"
)


async def main():
    from playwright.async_api import async_playwright

    OUT_DIR.mkdir(parents=True, exist_ok=True)

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
            ],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        print("[1] Идём на FB главную...")
        await page.goto("https://www.facebook.com/", wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(3)

        # Скриншот главной
        ss1 = OUT_DIR / "diag_fb_home.png"
        await page.screenshot(path=str(ss1), full_page=False)
        print(f"[SCREENSHOT] {ss1}")

        body1 = await page.evaluate("() => document.body.innerText.substring(0, 500)")
        print(f"[BODY HEAD] {body1[:300]}")

        # Проверяем куки
        cookies = await ctx.cookies()
        fb_cookies = [c for c in cookies if "facebook.com" in c.get("domain", "")]
        print(f"[COOKIES] FB куков: {len(fb_cookies)}")
        for c in fb_cookies[:5]:
            print(f"  {c.get('name')} = {c.get('value', '')[:30]}...")

        # Переходим на Ad Library
        print(f"\n[2] Идём на Ad Library: {TEST_URL}")
        try:
            await page.goto(TEST_URL, wait_until="domcontentloaded", timeout=40_000)
        except Exception as e:
            print(f"  [WARN] goto error: {e}")

        await asyncio.sleep(5)

        ss2 = OUT_DIR / "diag_adlib_aviator.png"
        await page.screenshot(path=str(ss2), full_page=False)
        print(f"[SCREENSHOT] {ss2}")

        body2 = await page.evaluate("() => document.body.innerText.substring(0, 1000)")
        print(f"\n[PAGE BODY]\n{body2[:800]}")

        # Считаем article-элементы
        art_count = await page.evaluate(
            "() => document.querySelectorAll('[role=\"article\"]').length"
        )
        print(f"\n[ARTICLES] role=article: {art_count}")

        # Смотрим URL после редиректов
        current_url = page.url
        print(f"[URL] текущий: {current_url}")

        await ctx.close()

    print(f"\n[DONE] скриншоты в {OUT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
