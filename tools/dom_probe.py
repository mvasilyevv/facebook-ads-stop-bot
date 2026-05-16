# -*- coding: utf-8 -*-
"""DOM-зонд для FB Ads Manager через Vision CDP.

Подключается к уже открытому профилю Vision и снимает фрагменты DOM,
чтобы переписать селекторы шагов campaign_creator под фактическую разметку.

Подкоманды:
  snapshot   — outerHTML одного элемента по CSS-селектору
  drawer     — outerHTML открытого drawer (role=dialog или [role="complementary"])
  roles      — сводка по интерактивным элементам в drawer/scope
              (role / aria-label / placeholder / innerText[:80])
  find-text  — найти все элементы, содержащие текст, и распечатать их XPath/atts
  raw-eval   — выполнить произвольный JS, вернуть JSON (для разовых проверок)

Все результаты пишет в recordings/dom_probes/<ts>_<name>.{html,md}.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import Page

from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
from core.campaign_creator.step_executor import open_page
from core.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dom_probe")

OUT_DIR = Path(__file__).resolve().parents[1] / "recordings" / "dom_probes"


# ---------- JS helpers ----------

_JS_ROLES = """
(scope) => {
    const root = scope || document;
    const sel = '[role], button, input, textarea, select, a[href], [aria-label], [placeholder]';
    const nodes = Array.from(root.querySelectorAll(sel));
    return nodes.slice(0, 400).map(n => {
        const text = (n.innerText || n.value || '').trim().replace(/\\s+/g, ' ').slice(0, 100);
        return {
            tag: n.tagName.toLowerCase(),
            role: n.getAttribute('role') || '',
            ariaLabel: n.getAttribute('aria-label') || '',
            placeholder: n.getAttribute('placeholder') || '',
            type: n.getAttribute('type') || '',
            id: n.id || '',
            cls: (n.getAttribute('class') || '').slice(0, 80),
            text,
            visible: !!(n.offsetParent || n.getClientRects().length),
        };
    });
}
"""

_JS_FIND_TEXT = """
(needle) => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
    const results = [];
    let node;
    while ((node = walker.nextNode())) {
        const own = (node.childNodes.length === 1 && node.firstChild.nodeType === 3)
            ? node.firstChild.nodeValue
            : '';
        if (own && own.includes(needle)) {
            results.push({
                tag: node.tagName.toLowerCase(),
                role: node.getAttribute('role') || '',
                ariaLabel: node.getAttribute('aria-label') || '',
                id: node.id || '',
                cls: (node.getAttribute('class') || '').slice(0, 80),
                text: own.trim().slice(0, 120),
                xpath: (function getXPath(el) {
                    if (el.id) return `//*[@id="${el.id}"]`;
                    const segs = [];
                    while (el && el.nodeType === 1) {
                        let i = 1, sib = el.previousElementSibling;
                        while (sib) { if (sib.tagName === el.tagName) i++; sib = sib.previousElementSibling; }
                        segs.unshift(`${el.tagName.toLowerCase()}[${i}]`);
                        el = el.parentNode;
                        if (el === document.body) { segs.unshift('body'); break; }
                    }
                    return '/' + segs.join('/');
                })(node),
            });
        }
        if (results.length >= 50) break;
    }
    return results;
}
"""


# ---------- snapshots ----------


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write(name: str, html: str | None, md: str | None) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = OUT_DIR / f"{_stamp()}_{name}"
    if html is not None:
        base.with_suffix(".html").write_text(html, encoding="utf-8")
        logger.info("HTML: %s", base.with_suffix(".html"))
    if md is not None:
        base.with_suffix(".md").write_text(md, encoding="utf-8")
        logger.info("MD:   %s", base.with_suffix(".md"))


async def cmd_snapshot(page: Page, name: str, selector: str) -> None:
    loc = page.locator(selector).first
    count = await loc.count()
    if not count:
        raise SystemExit(f"Селектор не найден: {selector}")
    html = await loc.evaluate("el => el.outerHTML")
    md = f"# snapshot `{name}`\n\nselector: `{selector}`\n\nfound: {count}\n"
    _write(name, html, md)


async def cmd_drawer(page: Page, name: str) -> None:
    # Drawer FB — это обычно role=dialog или большой div со скроллом справа.
    selectors = [
        '[role="dialog"]',
        '[role="complementary"]',
        'div[data-testid*="drawer"]',
        'div[data-testid*="Drawer"]',
    ]
    for sel in selectors:
        loc = page.locator(sel).first
        if await loc.count():
            html = await loc.evaluate("el => el.outerHTML")
            md = f"# drawer `{name}`\n\nmatched: `{sel}`\n"
            _write(name, html, md)
            return
    raise SystemExit("Drawer не найден (нет role=dialog/complementary)")


async def cmd_roles(page: Page, name: str, scope: str | None) -> None:
    scope_handle = None
    if scope:
        loc = page.locator(scope).first
        if not await loc.count():
            raise SystemExit(f"scope не найден: {scope}")
        scope_handle = await loc.element_handle()
    items = await page.evaluate(_JS_ROLES, scope_handle)
    md_lines = [
        f"# roles `{name}`",
        "",
        f"scope: `{scope or 'document'}`",
        f"count: {len(items)}",
        "",
    ]
    md_lines.append("| # | tag | role | aria-label | placeholder | type | id | text | vis |")
    md_lines.append("|---|-----|------|------------|-------------|------|----|------|-----|")
    for i, it in enumerate(items):
        md_lines.append(
            f"| {i} | {it['tag']} | {it['role']} | {it['ariaLabel']} | {it['placeholder']} | "
            f"{it['type']} | {it['id'][:40]} | {it['text'][:80]} | {'Y' if it['visible'] else 'n'} |"
        )
    _write(name, None, "\n".join(md_lines))


async def cmd_find_text(page: Page, name: str, needle: str) -> None:
    items = await page.evaluate(_JS_FIND_TEXT, needle)
    md = [f"# find-text `{needle}`", f"matches: {len(items)}", ""]
    for it in items:
        md.append(
            f"- **{it['tag']}** role=`{it['role']}` aria=`{it['ariaLabel']}` "
            f"id=`{it['id']}` cls=`{it['cls']}` text=`{it['text']}`"
        )
        md.append(f"  xpath: `{it['xpath']}`")
    _write(name, None, "\n".join(md))


async def cmd_raw_eval(page: Page, name: str, js: str) -> None:
    value = await page.evaluate(js)
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    _write(name, None, f"# raw-eval `{name}`\n\n```js\n{js}\n```\n\n```json\n{payload}\n```\n")


# ---------- entry ----------


def _make_client() -> BrowserAgentClient:
    s = get_settings()
    return BrowserAgentClient(
        BrowserAgentConfig(
            vision_x_token=s.vision_x_token,
            vision_api_url=s.vision_api_url,
            vision_profile_id=s.vision_profile_id,
        )
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_snap = sub.add_parser("snapshot", help="outerHTML одного элемента")
    p_snap.add_argument("name")
    p_snap.add_argument("selector")

    p_drw = sub.add_parser("drawer", help="outerHTML открытого drawer (dialog/complementary)")
    p_drw.add_argument("name")

    p_roles = sub.add_parser("roles", help="сводка по интерактивным элементам")
    p_roles.add_argument("name")
    p_roles.add_argument("--scope", default=None, help="CSS-селектор корня (по умолчанию document)")

    p_find = sub.add_parser("find-text", help="поиск элементов по тексту")
    p_find.add_argument("name")
    p_find.add_argument("needle")

    p_raw = sub.add_parser("raw-eval", help="произвольный JS → JSON")
    p_raw.add_argument("name")
    p_raw.add_argument("js")

    args = ap.parse_args()

    client = _make_client()
    async with open_page(client) as page:
        if args.cmd == "snapshot":
            await cmd_snapshot(page, args.name, args.selector)
        elif args.cmd == "drawer":
            await cmd_drawer(page, args.name)
        elif args.cmd == "roles":
            await cmd_roles(page, args.name, args.scope)
        elif args.cmd == "find-text":
            await cmd_find_text(page, args.name, args.needle)
        elif args.cmd == "raw-eval":
            await cmd_raw_eval(page, args.name, args.js)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
