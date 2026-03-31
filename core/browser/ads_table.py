# -*- coding: utf-8 -*-
"""Утилиты для работы с таблицей Ads Manager через DOM и humanized-скролл."""

from __future__ import annotations

import random

from core.browser.humanizer import human_scroll_to_find, human_wheel_scroll


def _default_scroll_metrics(*, moved: bool = False) -> dict[str, float | bool]:
    """Возвращает пустые метрики прокрутки таблицы."""
    return {
        "found": False,
        "scroll_top": 0.0,
        "max_scroll_top": 0.0,
        "at_bottom": False,
        "moved": moved,
    }


async def get_ads_table_scroll_anchor(page) -> tuple[float, float] | None:
    """Находит точку внутри видимой области таблицы Ads Manager."""
    try:
        anchor = await page.evaluate("""() => {
            const selectors = [
                '[role="grid"]',
                '[role="table"]',
                '[aria-rowcount]',
                '[data-surface*="table_row:"]',
            ];

            for (const selector of selectors) {
                const node = document.querySelector(selector);
                if (!(node instanceof Element)) {
                    continue;
                }

                const rect = node.getBoundingClientRect();
                if (rect.width < 40 || rect.height < 40) {
                    continue;
                }

                return {
                    x: rect.left + rect.width * 0.5,
                    y: rect.top + rect.height * 0.5,
                };
            }

            return null;
        }""")
    except Exception:
        return None

    if not isinstance(anchor, dict):
        return None

    try:
        return float(anchor["x"]), float(anchor["y"])
    except (KeyError, TypeError, ValueError):
        return None


async def reset_ads_table_scroll(page) -> int:
    """Сбрасывает таблицу Ads Manager к началу и возвращает число сдвинутых контейнеров."""
    try:
        result = await page.evaluate("""() => {
            const seen = new Set();
            const scrollables = [];

            const addScrollable = (node) => {
                if (!(node instanceof HTMLElement)) {
                    return;
                }
                if (seen.has(node)) {
                    return;
                }

                seen.add(node);
                if (node.scrollHeight - node.clientHeight > 40) {
                    scrollables.push(node);
                }
            };

            const firstRowCell = document.querySelector('[data-surface*="table_row:"]');
            for (let node = firstRowCell; node; node = node.parentElement) {
                addScrollable(node);
            }

            for (const selector of ['[role="grid"]', '[role="table"]', '[aria-rowcount]']) {
                for (const node of document.querySelectorAll(selector)) {
                    addScrollable(node);
                }
            }

            const docScroller = document.scrollingElement;
            if (docScroller instanceof HTMLElement) {
                addScrollable(docScroller);
            }

            let changed = 0;
            for (const node of scrollables) {
                const hadOffset = node.scrollTop > 0;
                if (typeof node.scrollTo === 'function') {
                    node.scrollTo({top: 0, left: 0, behavior: 'auto'});
                }
                node.scrollTop = 0;
                if (hadOffset) {
                    changed += 1;
                }
            }

            window.scrollTo(0, 0);
            return changed;
        }""")
    except Exception:
        return 0

    if isinstance(result, int):
        return result
    return 0


async def get_ads_table_scroll_metrics(page) -> dict[str, float | bool]:
    """Читает положение прокрутки основной таблицы Ads Manager."""
    try:
        metrics = await page.evaluate("""() => {
            const seen = new Set();
            const scrollables = [];

            const addScrollable = (node) => {
                if (!(node instanceof HTMLElement)) {
                    return;
                }
                if (seen.has(node)) {
                    return;
                }

                seen.add(node);
                const maxScrollTop = node.scrollHeight - node.clientHeight;
                if (maxScrollTop > 40) {
                    scrollables.push(node);
                }
            };

            const firstRowCell = document.querySelector('[data-surface*="table_row:"]');
            for (let node = firstRowCell; node; node = node.parentElement) {
                addScrollable(node);
            }

            for (const selector of ['[role="grid"]', '[role="table"]', '[aria-rowcount]']) {
                for (const node of document.querySelectorAll(selector)) {
                    addScrollable(node);
                }
            }

            const docScroller = document.scrollingElement;
            if (docScroller instanceof HTMLElement) {
                addScrollable(docScroller);
            }

            if (!scrollables.length) {
                return null;
            }

            scrollables.sort((left, right) => {
                const leftMax = left.scrollHeight - left.clientHeight;
                const rightMax = right.scrollHeight - right.clientHeight;
                return rightMax - leftMax;
            });

            const node = scrollables[0];
            const maxScrollTop = Math.max(node.scrollHeight - node.clientHeight, 0);
            const scrollTop = Math.max(node.scrollTop, 0);
            return {
                found: true,
                scroll_top: scrollTop,
                max_scroll_top: maxScrollTop,
                at_bottom: maxScrollTop <= 0 ? true : scrollTop >= maxScrollTop - 4,
            };
        }""")
    except Exception:
        return _default_scroll_metrics()

    if not isinstance(metrics, dict):
        return _default_scroll_metrics()

    return {
        "found": bool(metrics.get("found", False)),
        "scroll_top": float(metrics.get("scroll_top", 0.0) or 0.0),
        "max_scroll_top": float(metrics.get("max_scroll_top", 0.0) or 0.0),
        "at_bottom": bool(metrics.get("at_bottom", False)),
        "moved": False,
    }


async def get_visible_ads_table_row_ids(page) -> list[str]:
    """Возвращает список row id, которые сейчас присутствуют в DOM таблицы."""
    try:
        row_ids = await page.evaluate("""() => {
            const ids = new Set();
            const rowRe = /table_row:(\\d+)/;

            for (const node of document.querySelectorAll('[data-surface*="table_row:"]')) {
                const surface = node.getAttribute('data-surface') || '';
                const match = rowRe.exec(surface);
                if (match) {
                    ids.add(match[1]);
                }
            }

            return Array.from(ids);
        }""")
    except Exception:
        return []

    if not isinstance(row_ids, list):
        return []

    return [str(row_id) for row_id in row_ids if str(row_id)]


def toggle_cell_selector(fb_ad_id: str) -> str:
    """Возвращает селектор toggle-ячейки для строки объявления."""
    return f'[data-surface*="table_row:{fb_ad_id}"][data-surface*="forObjectType(toggle"]'


async def find_toggle_cell_in_dom(page, fb_ad_id: str):
    """Ищет toggle-ячейку среди уже видимых строк таблицы."""
    return await page.query_selector(toggle_cell_selector(fb_ad_id))


async def find_toggle_cell_with_table_scan(
    page,
    fb_ad_id: str,
    *,
    reset_to_top: bool = True,
    max_scroll_passes: int = 60,
    step_px: int = 220,
    fallback_max_steps: int = 12,
):
    """Ищет toggle-ячейку проходом по внутренней прокрутке таблицы."""
    selector = toggle_cell_selector(fb_ad_id)

    if reset_to_top:
        await reset_ads_table_scroll(page)

    found_cell = await find_toggle_cell_in_dom(page, fb_ad_id)
    if found_cell is not None:
        return found_cell

    metrics_seen = False
    stalled_passes = 0
    for _ in range(max_scroll_passes):
        scroll_before = await get_ads_table_scroll_metrics(page)
        metrics_seen = metrics_seen or bool(scroll_before.get("found"))
        if scroll_before["found"] and scroll_before["at_bottom"]:
            return None

        scroll_after = await scroll_ads_table_down(page, step_px=step_px)
        metrics_seen = metrics_seen or bool(scroll_after.get("found"))

        found_cell = await find_toggle_cell_in_dom(page, fb_ad_id)
        if found_cell is not None:
            return found_cell

        if scroll_after.get("moved"):
            stalled_passes = 0
            continue

        stalled_passes += 1
        if stalled_passes >= 2:
            break

    if metrics_seen:
        return None

    return await human_scroll_to_find(
        page,
        selector,
        max_steps=fallback_max_steps,
        step_px=step_px,
    )


async def scroll_ads_table_down(page, *, step_px: int | None = None) -> dict[str, float | bool]:
    """Прокручивает таблицу вниз на один шаг и возвращает свежие метрики."""
    delta_y = step_px or random.randint(160, 260)
    before = await get_ads_table_scroll_metrics(page)
    if before["found"] and before["at_bottom"]:
        return _default_scroll_metrics(moved=False) | before

    anchor = await get_ads_table_scroll_anchor(page)
    if anchor is not None:
        try:
            await human_wheel_scroll(
                page,
                delta_y,
                anchor=anchor,
                move_before=False,
                settle_range=(0.18, 0.35),
                drift_x_range=(-8, 8),
                drift_y_range=(-6, 6),
            )
        except Exception:
            pass

    after = await get_ads_table_scroll_metrics(page)
    if (
        before["found"]
        and after["found"]
        and float(after["scroll_top"]) - float(before["scroll_top"]) > 4
    ):
        after["moved"] = True
        return after

    try:
        fallback = await page.evaluate(
            f"""() => {{
                const seen = new Set();
                const scrollables = [];

                const addScrollable = (node) => {{
                    if (!(node instanceof HTMLElement)) {{
                        return;
                    }}
                    if (seen.has(node)) {{
                        return;
                    }}

                    seen.add(node);
                    const maxScrollTop = node.scrollHeight - node.clientHeight;
                    if (maxScrollTop > 40) {{
                        scrollables.push(node);
                    }}
                }};

                const firstRowCell = document.querySelector('[data-surface*="table_row:"]');
                for (let node = firstRowCell; node; node = node.parentElement) {{
                    addScrollable(node);
                }}

                for (const selector of ['[role="grid"]', '[role="table"]', '[aria-rowcount]']) {{
                    for (const node of document.querySelectorAll(selector)) {{
                        addScrollable(node);
                    }}
                }}

                const docScroller = document.scrollingElement;
                if (docScroller instanceof HTMLElement) {{
                    addScrollable(docScroller);
                }}

                if (!scrollables.length) {{
                    return null;
                }}

                scrollables.sort((left, right) => {{
                    const leftMax = left.scrollHeight - left.clientHeight;
                    const rightMax = right.scrollHeight - right.clientHeight;
                    return rightMax - leftMax;
                }});

                const node = scrollables[0];
                const maxScrollTop = Math.max(node.scrollHeight - node.clientHeight, 0);
                const prevTop = Math.max(node.scrollTop, 0);

                if (typeof node.scrollBy === 'function') {{
                    node.scrollBy({{top: {delta_y}, left: 0, behavior: 'auto'}});
                }}
                node.scrollTop = Math.min(prevTop + {delta_y}, maxScrollTop);

                const nextTop = Math.max(node.scrollTop, 0);
                return {{
                    found: true,
                    scroll_top: nextTop,
                    max_scroll_top: maxScrollTop,
                    at_bottom: maxScrollTop <= 0 ? true : nextTop >= maxScrollTop - 4,
                    moved: Math.abs(nextTop - prevTop) > 4,
                }};
            }}"""
        )
    except Exception:
        fallback = None

    if not isinstance(fallback, dict):
        return _default_scroll_metrics(moved=False)

    return {
        "found": bool(fallback.get("found", False)),
        "scroll_top": float(fallback.get("scroll_top", 0.0) or 0.0),
        "max_scroll_top": float(fallback.get("max_scroll_top", 0.0) or 0.0),
        "at_bottom": bool(fallback.get("at_bottom", False)),
        "moved": bool(fallback.get("moved", False)),
    }
