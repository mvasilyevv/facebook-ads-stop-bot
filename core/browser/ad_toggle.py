# -*- coding: utf-8 -*-
"""Общие утилиты для поиска строки и переключения статуса объявления в Ads Manager."""

from __future__ import annotations

import asyncio
import random
from typing import Any, Awaitable, Callable, Sequence


async def reset_ads_table_to_top(
    page,
    *,
    get_scroll_anchor: Callable[[Any], Awaitable[tuple[float, float] | None]],
    reset_scroll: Callable[[Any], Awaitable[int]],
    move_mouse: Callable[..., Awaitable[None]],
    wheel_scroll: Callable[..., Awaitable[None]],
    logger=None,
) -> None:
    """Сбрасывает таблицу к началу так же, как это делает человек колесом мыши."""
    anchor = await get_scroll_anchor(page)
    if anchor is None:
        viewport = page.viewport_size or {"width": 1280, "height": 800}
        anchor = (viewport["width"] * 0.5, viewport["height"] * 0.5)

    await move_mouse(page, anchor[0], anchor[1])
    reset_count = await reset_scroll(page)
    for _ in range(4):
        await wheel_scroll(
            page,
            -1200,
            anchor=anchor,
            move_before=False,
            settle_range=(0.12, 0.25),
            drift_x_range=(-6, 6),
            drift_y_range=(-4, 4),
        )

    if logger and isinstance(reset_count, int) and reset_count > 0:
        logger.info(
            "Сбросил прокрутку таблицы Ads Manager к началу (%s контейнеров)",
            reset_count,
        )


async def scan_for_toggle_cell(
    page,
    fb_ad_id: str,
    *,
    selector_builder: Callable[[str], str],
    find_in_dom: Callable[[Any, str], Awaitable[Any]],
    reset_to_top_fn: Callable[[Any], Awaitable[None]] | None,
    get_scroll_metrics: Callable[[Any], Awaitable[dict[str, float | bool]]],
    scroll_down: Callable[..., Awaitable[dict[str, float | bool]]],
    legacy_scroll_to_find: Callable[..., Awaitable[Any]],
    reset_to_top: bool,
    max_scroll_passes: int,
    step_px: int,
    fallback_max_steps: int,
    logger=None,
    reached_bottom_log: str | None = None,
    found_log: str | None = None,
    stalled_log: str | None = None,
    fallback_log: str | None = None,
) -> Any | None:
    """Ищет строку объявления проходом по внутренней прокрутке таблицы."""
    selector = selector_builder(fb_ad_id)

    if reset_to_top and reset_to_top_fn is not None:
        await reset_to_top_fn(page)
        await asyncio.sleep(random.uniform(0.3, 0.6))

    found_cell = await find_in_dom(page, fb_ad_id)
    if found_cell is not None:
        return found_cell

    metrics_seen = False
    stalled_passes = 0
    for pass_num in range(1, max_scroll_passes + 1):
        scroll_before = await get_scroll_metrics(page)
        metrics_seen = metrics_seen or bool(scroll_before.get("found"))
        if scroll_before.get("found") and scroll_before.get("at_bottom"):
            if logger and reached_bottom_log:
                logger.info(reached_bottom_log, fb_ad_id, pass_num)
            return None

        scroll_after = await scroll_down(page, step_px=step_px)
        metrics_seen = metrics_seen or bool(scroll_after.get("found"))

        found_cell = await find_in_dom(page, fb_ad_id)
        if found_cell is not None:
            if logger and found_log:
                logger.info(found_log, fb_ad_id, pass_num + 1)
            return found_cell

        if scroll_after.get("moved"):
            stalled_passes = 0
            continue

        stalled_passes += 1
        if stalled_passes >= 2:
            if logger and stalled_log:
                logger.info(stalled_log, fb_ad_id, pass_num)
            break

    if metrics_seen:
        return None

    if logger and fallback_log:
        logger.info(fallback_log, fb_ad_id)
    return await legacy_scroll_to_find(
        page,
        selector,
        max_steps=fallback_max_steps,
        step_px=step_px,
    )


async def confirm_dialog_if_present(
    page,
    *,
    confirm_words: set[str],
    click_fn: Callable[..., Awaitable[None]],
    logger=None,
    sleep_range: tuple[float, float] = (0.5, 1.0),
    log_message: str = "Подтверждаю диалог: '%s'",
) -> bool:
    """Подтверждает модальный диалог Meta, если после клика он появился."""
    try:
        buttons = await page.query_selector_all(
            '[role="dialog"] button, [role="dialog"] [role="button"], '
            '[role="alertdialog"] button, [role="alertdialog"] [role="button"]'
        )
        for button in buttons:
            text = (await button.inner_text()).lower().strip()
            if any(word in text for word in confirm_words):
                if logger:
                    logger.info(log_message, text[:40])
                await click_fn(page, button, double_check_pause=False)
                min_sleep, max_sleep = sleep_range
                await asyncio.sleep(random.uniform(min_sleep, max_sleep))
                return True
    except Exception:
        if logger:
            logger.debug("Ошибка при проверке диалога", exc_info=True)
    return False


async def get_toggle_aria_checked_via_js(
    page,
    fb_ad_id: str,
    *,
    selector_builder: Callable[[str], str],
) -> str:
    """Читает текущее значение aria-checked у точного switch-переключателя."""
    try:
        selector = selector_builder(fb_ad_id)
        result = await page.evaluate(
            f"""() => {{
                const cell = document.querySelector('{selector}');
                if (!cell) return 'not_found';
                const toggle = cell.querySelector('[role="switch"][aria-checked]');
                if (!toggle) return 'no_toggle';
                return toggle.getAttribute('aria-checked') || 'null';
            }}"""
        )
        return str(result)
    except Exception:
        return "error"


def normalize_aria_checked(value: str | None) -> str:
    """Нормализует значение aria-checked для строгого сравнения."""
    return (value or "").strip().lower()


async def restore_toggle_row_visibility(
    page,
    fb_ad_id: str,
    *,
    find_in_dom: Callable[[Any, str], Awaitable[Any]],
    scan_in_table: Callable[..., Awaitable[Any]],
    logger=None,
    log_message: str | None = None,
    max_scroll_passes: int,
    step_px: int,
    fallback_max_steps: int,
) -> None:
    """Возвращает строку объявления в область видимости после перестройки DOM."""
    try:
        if await find_in_dom(page, fb_ad_id) is not None:
            return
        if logger and log_message:
            logger.info(log_message, fb_ad_id)
        await scan_in_table(
            page,
            fb_ad_id,
            reset_to_top=True,
            max_scroll_passes=max_scroll_passes,
            step_px=step_px,
            fallback_max_steps=fallback_max_steps,
        )
    except Exception:
        if logger:
            logger.debug(
                "Не удалось вернуть строку %s в область видимости", fb_ad_id, exc_info=True
            )


async def wait_for_toggle_confirmation(
    page,
    fb_ad_id: str,
    *,
    poll_delays_seconds: Sequence[float],
    required_reads: int,
    expected_checked: str,
    read_state: Callable[[Any, str], Awaitable[str]],
    restore_visibility: Callable[[Any, str], Awaitable[None]],
    logger=None,
    progress_log_message: str,
    success_message: str,
    failure_message: str,
    recoverable_states: set[str] | None = None,
) -> tuple[bool, str]:
    """Ждёт подтверждение нужного состояния по нескольким чтениям aria-checked."""
    consecutive_reads = 0
    target_checked = normalize_aria_checked(expected_checked)
    total_attempts = len(poll_delays_seconds)
    recoverable = recoverable_states or {"not_found", "no_toggle", "error", "null"}

    for attempt, delay_seconds in enumerate(poll_delays_seconds, start=1):
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        checked_after = normalize_aria_checked(await read_state(page, fb_ad_id))
        if logger:
            logger.info(
                progress_log_message,
                fb_ad_id,
                attempt,
                total_attempts,
                checked_after or "null",
            )

        if checked_after == target_checked:
            consecutive_reads += 1
            if consecutive_reads >= required_reads:
                return True, success_message
            continue

        consecutive_reads = 0

        if checked_after in recoverable:
            await restore_visibility(page, fb_ad_id)

    return False, failure_message
