# -*- coding: utf-8 -*-
"""Подключиться по CDP к открытой Vision-сессии и заинспектировать чипы гео."""

from __future__ import annotations

import asyncio
import json

from playwright.async_api import async_playwright

CDP_URL = "http://127.0.0.1:65059"


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = None
        for p in ctx.pages:
            if "adsmanager.facebook.com" in p.url:
                page = p
                break
        if page is None:
            raise SystemExit("Не нашёл страницу Ads Manager")
        print(f"URL: {page.url}\n")

        remove_labels = await page.evaluate(
            """
            () => Array.from(document.querySelectorAll('[aria-label^="Удалить"]'))
                .map(el => ({
                    aria: el.getAttribute('aria-label'),
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role'),
                }))
            """
        )
        print('=== [aria-label^="Удалить"] ===')
        print(json.dumps(remove_labels, ensure_ascii=False, indent=2))

        hk_info = await page.evaluate(
            """
            () => {
                const out = [];
                const all = Array.from(document.querySelectorAll('*'));
                for (const el of all) {
                    const t = (el.innerText || '').trim();
                    if (t === 'Гонконг' || t === 'Hong Kong') {
                        let p = el;
                        for (let i = 0; i < 6 && p; i++) {
                            out.push({
                                depth: i,
                                tag: p.tagName.toLowerCase(),
                                aria: p.getAttribute('aria-label'),
                                role: p.getAttribute('role'),
                                text: (p.innerText || '').slice(0, 80),
                                outerHTML: p.outerHTML.slice(0, 400),
                            });
                            p = p.parentElement;
                        }
                        break;
                    }
                }
                return out;
            }
            """
        )
        print("\n=== Контекст вокруг «Гонконг» ===")
        print(json.dumps(hk_info, ensure_ascii=False, indent=2))

        hk_aria = await page.evaluate(
            """
            () => Array.from(document.querySelectorAll('[aria-label]'))
                .filter(el => /Гонконг|Hong Kong|香港/i.test(el.getAttribute('aria-label') || ''))
                .map(el => ({
                    aria: el.getAttribute('aria-label'),
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role'),
                }))
            """
        )
        print("\n=== aria-label содержит «Гонконг/Hong Kong/香港» ===")
        print(json.dumps(hk_aria, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
