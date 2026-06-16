# -*- coding: utf-8 -*-
"""Обновить SYNTX_AUTH_TOKEN из залогиненного recon_profile → записать в .env.

syntx авторизует только заголовком `Authorization: Bearer <JWT>`. JWT лежит в
`localStorage.auth_token` на https://syntx.ai (профиль `data/recon_profile`,
залогинен) и живёт ~30 дней. Этот скрипт headless-Playwright'ом открывает syntx
в recon_profile, читает свежий токен и переписывает строку SYNTX_AUTH_TOKEN в .env.

Запуск (из корня репо, в .venv с установленным playwright):
    .venv/bin/python scripts/syntx_refresh_token.py

Печатает только маску токена + срок жизни — полный JWT в stdout не светим.
Профиль должен быть СВОБОДЕН (не открыт в другом браузере), иначе persistent
context не стартует (SingletonLock). Секрет идёт прямо в .env (он в .gitignore),
в коммиты НЕ попадает.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from core.syntx.auth import token_days_left  # noqa: E402

PROFILE = str(_ROOT / "data" / "recon_profile")
ENV = _ROOT / ".env"
# localStorage.auth_token живёт на web-origin'е приложения (не на api.syntx.ai)
URLS = ["https://syntx.ai", "https://app.syntx.ai", "https://syntx.ai/app"]


def save_to_env(token: str) -> None:
    """Заменить (или добавить) строку SYNTX_AUTH_TOKEN=… в .env."""
    line = f"SYNTX_AUTH_TOKEN={token}"
    if ENV.exists():
        txt = ENV.read_text(encoding="utf-8")
        if re.search(r"(?m)^SYNTX_AUTH_TOKEN=.*$", txt):
            txt = re.sub(r"(?m)^SYNTX_AUTH_TOKEN=.*$", line, txt)
        else:
            txt = txt.rstrip("\n") + "\n" + line + "\n"
    else:
        txt = line + "\n"
    ENV.write_text(txt, encoding="utf-8")


async def _grab() -> int:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        ctx = None
        for channel in ("chrome", None):
            try:
                ctx = await p.chromium.launch_persistent_context(
                    PROFILE,
                    headless=True,
                    channel=channel,
                    args=["--no-first-run", "--no-default-browser-check"],
                )
                break
            except Exception as e:  # noqa: BLE001 — пробуем следующий канал
                print(f"[launch channel={channel}] {type(e).__name__}: {e}", file=sys.stderr)
        if ctx is None:
            print("RESULT=CANNOT_LAUNCH (профиль занят/локнут — закрой recon_profile)")
            return 2

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        token = None
        hit_url = None
        for url in URLS:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2500)
                token = await page.evaluate("() => localStorage.getItem('auth_token')")
                if token:
                    hit_url = url
                    break
            except Exception as e:  # noqa: BLE001 — пробуем следующий URL
                print(f"[goto {url}] {type(e).__name__}: {e}", file=sys.stderr)
        await ctx.close()

        if not token:
            print("RESULT=NO_TOKEN (recon_profile не залогинен в syntx?)")
            return 3

        save_to_env(token)
        days = token_days_left(token)
        days_s = f"{days:.1f}" if days is not None else "?"
        masked = token[:14] + "…" + token[-6:]
        print(f"RESULT=OK url={hit_url}")
        print(f"TOKEN={masked} (len={len(token)})")
        print(f"DAYS_LEFT={days_s}  → записан в .env")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_grab()))
