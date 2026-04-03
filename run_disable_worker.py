# -*- coding: utf-8 -*-
"""Точка входа: запускает disable worker с подключением к Vision и БД."""

from __future__ import annotations

import asyncio
import logging
import pathlib
import random
import signal
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.browser.ad_toggle import (
    confirm_dialog_if_present as shared_confirm_dialog_if_present,
)
from core.browser.ad_toggle import (
    get_toggle_aria_checked_via_js as shared_get_toggle_aria_checked_via_js,
)
from core.browser.ad_toggle import (
    normalize_aria_checked as shared_normalize_aria_checked,
)
from core.browser.ad_toggle import (
    reset_ads_table_to_top as shared_reset_ads_table_to_top,
)
from core.browser.ad_toggle import (
    restore_toggle_row_visibility as shared_restore_toggle_row_visibility,
)
from core.browser.ad_toggle import (
    scan_for_toggle_cell,
)
from core.browser.ad_toggle import (
    wait_for_toggle_confirmation as shared_wait_for_toggle_confirmation,
)
from core.browser.ads_table import (
    find_toggle_cell_in_dom as _find_toggle_cell_in_dom_raw,
)
from core.browser.ads_table import (
    get_ads_table_scroll_anchor,
    get_ads_table_scroll_metrics,
    get_visible_ads_table_row_ids,
    reset_ads_table_scroll,
    scroll_ads_table_down,
)
from core.browser.ads_table import (
    toggle_cell_selector as _toggle_cell_selector_raw,
)
from core.browser.humanizer import human_click, human_move, human_scroll_to_find, human_wheel_scroll
from core.browser.manager import VisionBrowserManager
from core.browser.vision_client import VisionClient
from core.config import get_settings
from core.crypto import decrypt
from core.db import get_session_factory
from core.disable_tasks import is_delivery_disabled, reconcile_disable_tasks
from core.domain import AlertState, DisableTaskStatus
from core.models import AdSnapshot, DisableTask, VisionSettings
from core.sentry import setup_sentry
from core.telegram.delivery import broadcast_disable_task_runtime_message
from core.worker_utils import PidFileLock, wait_for_shutdown_or_timeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
VISION_SETTINGS_POLL_INTERVAL_SECONDS = 5

# Расширенное окно подтверждения нужно, потому что Meta может обновлять aria-checked
# заметно позже самого клика по переключателю.
DISABLE_CONFIRMATION_POLL_DELAYS_SECONDS = (0.0, 3.0, 3.0, 3.0, 3.0, 4.0, 4.0, 5.0, 5.0)
DISABLE_CONFIRMATION_FALSE_READS_REQUIRED = 2
DISABLE_CONFIRMATION_WINDOW_SECONDS = int(sum(DISABLE_CONFIRMATION_POLL_DELAYS_SECONDS))
DISABLE_BATCH_SIZE = 10
DISABLE_BATCH_SCROLL_STEP_PX = 220
DISABLE_BATCH_MAX_SCROLL_PASSES = 50
DISABLE_SINGLE_SEARCH_MAX_SCROLL_PASSES = 120
DISABLE_SINGLE_SEARCH_FALLBACK_MAX_STEPS = 60
DISABLE_MANAGER_DISCONNECT_TIMEOUT_SECONDS = 15
DISABLE_VISION_CLOSE_TIMEOUT_SECONDS = 10


async def _close_disable_runtime_resources(manager, vision) -> None:
    """Закрывает browser/Vision ресурсы с таймаутами, чтобы worker не зависал в cleanup."""
    if manager is not None:
        try:
            await asyncio.wait_for(
                manager.disconnect(),
                timeout=DISABLE_MANAGER_DISCONNECT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Disable worker: таймаут %s сек при отключении браузера — продолжаю восстановление",
                DISABLE_MANAGER_DISCONNECT_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.debug(
                "Disable worker: не удалось корректно отключить браузер",
                exc_info=True,
            )

    if vision is not None:
        try:
            await asyncio.wait_for(
                vision.close(),
                timeout=DISABLE_VISION_CLOSE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Disable worker: таймаут %s сек при закрытии Vision клиента — продолжаю восстановление",
                DISABLE_VISION_CLOSE_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.debug(
                "Disable worker: не удалось закрыть Vision клиент",
                exc_info=True,
            )


async def _load_vision_settings() -> tuple[str, str, str]:
    """Загружает Vision-настройки из БД с fallback на .env."""
    settings = get_settings()
    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await session.execute(
                select(VisionSettings).where(VisionSettings.singleton_key == "default")
            )
            row = result.scalar_one_or_none()
            if row and row.x_token_encrypted and row.profile_id:
                token = decrypt(row.x_token_encrypted)
                if token:
                    logger.info("Vision-настройки загружены из БД")
                    return token, row.api_url or settings.vision_api_url, row.profile_id
    except Exception:
        logger.debug("Не удалось загрузить Vision-настройки из БД", exc_info=True)

    return settings.vision_x_token, settings.vision_api_url, settings.vision_profile_id


async def claim_next_task():
    """Берёт следующую задачу из очереди (PENDING или RETRYING с наступившим retry)."""
    factory = get_session_factory()
    async with factory() as session:
        now = datetime.now(UTC)
        recovery_summary = await reconcile_disable_tasks(session, now=now)
        if any(recovery_summary.values()):
            await session.commit()

        result = await session.execute(
            select(DisableTask)
            .options(selectinload(DisableTask.fb_ad))
            .where(
                (DisableTask.status == DisableTaskStatus.PENDING)
                | (
                    (DisableTask.status == DisableTaskStatus.RETRYING)
                    & (DisableTask.next_retry_at <= now)
                )
            )
            .order_by(DisableTask.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        task = result.scalar_one_or_none()
        if task is None:
            return None

        task.status = DisableTaskStatus.RUNNING
        task.attempt_count += 1
        await session.commit()
        await session.refresh(task, attribute_names=["fb_ad"])
        return task


async def claim_task_batch(limit: int = DISABLE_BATCH_SIZE) -> list[DisableTask]:
    """Берёт пачку задач на отключение в порядке очереди."""
    factory = get_session_factory()
    async with factory() as session:
        now = datetime.now(UTC)
        recovery_summary = await reconcile_disable_tasks(session, now=now)
        if any(recovery_summary.values()):
            await session.commit()

        result = await session.execute(
            select(DisableTask)
            .options(selectinload(DisableTask.fb_ad))
            .where(
                (DisableTask.status == DisableTaskStatus.PENDING)
                | (
                    (DisableTask.status == DisableTaskStatus.RETRYING)
                    & (DisableTask.next_retry_at <= now)
                )
            )
            .order_by(DisableTask.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        tasks = list(result.scalars())
        if not tasks:
            return []

        for task in tasks:
            task.status = DisableTaskStatus.RUNNING
            task.attempt_count += 1

        await session.commit()
        for task in tasks:
            await session.refresh(task)
        return tasks


def _find_ads_manager_page(manager) -> object | None:
    """Ищет вкладку Ads Manager среди всех открытых страниц браузера."""
    browser = manager._browser
    if browser is None:
        return None
    for context in browser.contexts:
        for p in context.pages:
            url = p.url or ""
            if "adsmanager" in url or "facebook.com/ads" in url:
                return p
    # Fallback: первая страница
    for context in browser.contexts:
        if context.pages:
            return context.pages[0]
    return None


async def _confirm_dialog_if_present(page) -> bool:
    """Проверяет появился ли диалог подтверждения и нажимает кнопку подтвердить.

    Кнопки в диалоге Facebook — это div[role="button"], не <button>.
    """
    confirm_words = {
        "подтвердить",
        "ok",
        "да",
        "продолжить",
        "отключить",
        "confirm",
        "yes",
        "pause",
        "приостановить",
        "опубликовать",
        "publish",
    }
    try:
        return await shared_confirm_dialog_if_present(
            page,
            confirm_words=confirm_words,
            click_fn=human_click,
            logger=logger,
            sleep_range=(0.5, 1.0),
            log_message="Подтверждаю диалог: '%s'",
        )
    except Exception:
        logger.debug("Ошибка при проверке диалога", exc_info=True)
    return False


async def _get_aria_checked_via_js(page, fb_ad_id: str) -> str:
    """Получает текущее значение aria-checked у реального переключателя объявления."""
    return await shared_get_toggle_aria_checked_via_js(
        page,
        fb_ad_id,
        selector_builder=_toggle_cell_selector,
    )


def _normalize_aria_checked(value: str | None) -> str:
    """Нормализует значение aria-checked для строгого сравнения."""
    return shared_normalize_aria_checked(value)


async def _reset_ads_table_scroll(page) -> None:
    """Сбрасывает внутреннюю прокрутку таблицы Ads Manager к началу."""
    await shared_reset_ads_table_to_top(
        page,
        get_scroll_anchor=get_ads_table_scroll_anchor,
        reset_scroll=reset_ads_table_scroll,
        move_mouse=human_move,
        wheel_scroll=human_wheel_scroll,
        logger=logger,
    )


async def _restore_toggle_row_visibility(page, fb_ad_id: str) -> None:
    """Возвращает строку объявления в DOM, если она пропала после обновления таблицы."""
    await shared_restore_toggle_row_visibility(
        page,
        fb_ad_id,
        find_in_dom=_find_toggle_cell_in_dom,
        scan_in_table=_find_toggle_cell_with_table_scan,
        logger=logger,
        log_message="Строка %s временно пропала из DOM — возвращаю объявление в область видимости",
        max_scroll_passes=DISABLE_SINGLE_SEARCH_MAX_SCROLL_PASSES,
        step_px=DISABLE_BATCH_SCROLL_STEP_PX,
        fallback_max_steps=DISABLE_SINGLE_SEARCH_FALLBACK_MAX_STEPS,
    )


async def _wait_for_disable_confirmation(page, fb_ad_id: str) -> tuple[bool, str]:
    """Ждёт подтверждения OFF в интерфейсе Meta без снижения критерия надёжности."""
    return await shared_wait_for_toggle_confirmation(
        page,
        fb_ad_id,
        poll_delays_seconds=DISABLE_CONFIRMATION_POLL_DELAYS_SECONDS,
        required_reads=DISABLE_CONFIRMATION_FALSE_READS_REQUIRED,
        expected_checked="false",
        read_state=_get_aria_checked_via_js,
        restore_visibility=_restore_toggle_row_visibility,
        logger=logger,
        progress_log_message="Проверка отключения %s: попытка %s/%s, aria-checked=%s",
        success_message="Объявление выключено: переключатель дважды подтвердил состояние OFF",
        failure_message="Переключатель нажат, но интерфейс не подтвердил OFF даже после расширенной проверки",
        recoverable_states={"not_found", "no_toggle", "no_input", "error", "null"},
    )


def _toggle_cell_selector(fb_ad_id: str) -> str:
    """Возвращает селектор ячейки toggle для конкретного объявления."""
    return _toggle_cell_selector_raw(fb_ad_id)


async def _resolve_ads_manager_page(manager) -> tuple[object | None, str | None]:
    """Находит рабочую вкладку Ads Manager и при необходимости переподключается."""
    page = _find_ads_manager_page(manager)
    if page is not None:
        return page, None

    logger.warning("Страница не найдена, переподключаюсь к браузеру...")
    try:
        await manager.disconnect()
        await manager.connect()
    except Exception as reconnect_err:
        return None, f"Не удалось переподключиться к браузеру: {reconnect_err}"

    page = _find_ads_manager_page(manager)
    if page is None:
        return None, "Не найдена страница браузера после переподключения"
    return page, None


async def _find_toggle_cell_in_dom(page, fb_ad_id: str):
    """Ищет ячейку toggle для объявления среди уже видимых строк."""
    return await _find_toggle_cell_in_dom_raw(page, fb_ad_id)


async def _find_toggle_cell_with_table_scan(
    page,
    fb_ad_id: str,
    *,
    reset_to_top: bool,
):
    """Ищет строку объявления проходом сверху вниз до реального низа таблицы."""
    logger.info(
        "Объявление %s не в DOM, прохожу таблицу Ads Manager сверху вниз до конца",
        fb_ad_id,
    )

    return await scan_for_toggle_cell(
        page,
        fb_ad_id,
        selector_builder=_toggle_cell_selector,
        find_in_dom=_find_toggle_cell_in_dom,
        reset_to_top_fn=_reset_ads_table_scroll,
        get_scroll_metrics=get_ads_table_scroll_metrics,
        scroll_down=scroll_ads_table_down,
        legacy_scroll_to_find=human_scroll_to_find,
        reset_to_top=reset_to_top,
        max_scroll_passes=DISABLE_SINGLE_SEARCH_MAX_SCROLL_PASSES,
        step_px=DISABLE_BATCH_SCROLL_STEP_PX,
        fallback_max_steps=DISABLE_SINGLE_SEARCH_FALLBACK_MAX_STEPS,
        logger=logger,
        reached_bottom_log="Поиск %s дошёл до низа таблицы на проходе %s",
        found_log="Объявление %s найдено в таблице на проходе %s",
        stalled_log="Поиск %s остановлен: таблица перестала двигаться на проходе %s",
        fallback_log="Поиск %s не получил метрик таблицы, пробую резервный humanized-поиск",
    )


async def _execute_disable_on_page(
    page,
    fb_ad_id: str,
    *,
    reset_table_before_search: bool,
    allow_scroll_search: bool,
) -> tuple[bool, str]:
    """Отключает объявление на уже найденной странице Ads Manager."""
    found_cell = await _find_toggle_cell_in_dom(page, fb_ad_id)
    if found_cell is None and allow_scroll_search:
        found_cell = await _find_toggle_cell_with_table_scan(
            page,
            fb_ad_id,
            reset_to_top=reset_table_before_search,
        )

    if found_cell is None:
        return False, f"Строка с Ad ID {fb_ad_id} не найдена в таблице после прокрутки"

    # Работаем только с точным switch-контролом объявления.
    # Любые другие aria-checked элементы считаем недостоверными.
    toggle = await found_cell.query_selector('[role="switch"][aria-checked]')

    if toggle is None:
        return (
            False,
            "Не найден точный switch-переключатель объявления; batch-checkbox и fallback-контролы отключены",
        )

    toggle_label = await toggle.get_attribute("aria-label") or "switch"
    initial_checked = _normalize_aria_checked(await toggle.get_attribute("aria-checked"))
    logger.info(
        "Переключатель найден: '%s' aria-checked=%s для %s",
        toggle_label,
        initial_checked or "null",
        fb_ad_id,
    )

    if initial_checked == "false":
        return True, f"Объявление уже отключено (aria-checked={initial_checked})"
    if initial_checked != "true":
        return (
            False,
            f"Не удалось однозначно определить состояние переключателя: aria-checked={initial_checked or 'null'}",
        )

    try:
        await human_click(page, toggle, double_check_pause=True)
    except Exception:
        logger.debug(
            "human_click не сработал, повторная попытка через humanizer",
            exc_info=True,
        )
        await asyncio.sleep(random.uniform(0.2, 0.4))
        try:
            await human_click(page, toggle, double_check_pause=False)
        except Exception as second_click_error:
            return False, f"Не удалось нажать переключатель через humanizer: {second_click_error}"

    await asyncio.sleep(random.uniform(1.5, 2.0))

    dialog_confirmed = await _confirm_dialog_if_present(page)
    if dialog_confirmed:
        await asyncio.sleep(random.uniform(0.5, 1.0))

    success, confirmation_message = await _wait_for_disable_confirmation(page, fb_ad_id)
    if success:
        return True, confirmation_message

    try:
        screenshot_path = f"/tmp/disable_fail_{fb_ad_id}.png"
        await page.screenshot(path=screenshot_path)
        logger.error("Отключение не подтверждено — скриншот: %s", screenshot_path)
    except Exception:
        pass

    return (
        False,
        f"{confirmation_message} (около {DISABLE_CONFIRMATION_WINDOW_SECONDS} сек)",
    )


async def execute_disable_via_playwright(manager, fb_ad_id: str) -> tuple[bool, str]:
    """Выполняет клик для отключения объявления через Playwright.

    Ищет строку по data-surface атрибуту (Ad ID) и нажимает переключатель.
    После клика проверяет результат через свежий JS-запрос (избегает stale element).
    """
    page = None
    try:
        page, page_error = await _resolve_ads_manager_page(manager)
        if page is None:
            return False, page_error or "Не найдена страница браузера после переподключения"
        logger.info("Disable: используем страницу %s", (page.url or "")[:80])
        return await _execute_disable_on_page(
            page,
            fb_ad_id,
            reset_table_before_search=True,
            allow_scroll_search=True,
        )
    except Exception as e:
        logger.exception("Ошибка Playwright при отключении %s", fb_ad_id)
        try:
            screenshot_path = f"/tmp/disable_error_{fb_ad_id}.png"
            await page.screenshot(path=screenshot_path)
            logger.error("Скриншот ошибки: %s", screenshot_path)
        except Exception:
            pass
        return False, f"Ошибка Playwright: {e}"


async def execute_disable_batch_via_playwright(
    manager, tasks: list[DisableTask]
) -> dict[str, tuple[bool, str]]:
    """Проходит таблицу сверху вниз и пытается отключить сразу несколько объявлений."""
    results: dict[str, tuple[bool, str]] = {}
    if not tasks:
        return results

    page = None
    try:
        page, page_error = await _resolve_ads_manager_page(manager)
        if page is None:
            error_message = page_error or "Не найдена страница браузера после переподключения"
            return {task.id: (False, error_message) for task in tasks}

        logger.info(
            "Disable: начинаю проход сверху вниз по таблице для %s задач",
            len(tasks),
        )

        await _reset_ads_table_scroll(page)
        await asyncio.sleep(random.uniform(0.3, 0.6))

        tasks_by_ad_id: dict[str, list[DisableTask]] = {}
        ordered_ad_ids: list[str] = []
        for task in tasks:
            task_list = tasks_by_ad_id.setdefault(task.fb_ad.fb_ad_id, [])
            if not task_list:
                ordered_ad_ids.append(task.fb_ad.fb_ad_id)
            task_list.append(task)

        remaining_ad_ids = set(ordered_ad_ids)
        stalled_passes = 0
        should_fallback_to_legacy_search = False

        for pass_num in range(1, DISABLE_BATCH_MAX_SCROLL_PASSES + 1):
            visible_row_ids = set(await get_visible_ads_table_row_ids(page))
            visible_target_ids = [
                fb_ad_id
                for fb_ad_id in ordered_ad_ids
                if fb_ad_id in remaining_ad_ids and fb_ad_id in visible_row_ids
            ]

            if visible_target_ids:
                logger.info(
                    "Disable: проход %s, в видимой части таблицы найдено %s целевых объявлений",
                    pass_num,
                    len(visible_target_ids),
                )

            for fb_ad_id in visible_target_ids:
                if fb_ad_id not in remaining_ad_ids:
                    continue

                found_cell = await _find_toggle_cell_in_dom(page, fb_ad_id)
                if found_cell is None:
                    continue

                success, message = await _execute_disable_on_page(
                    page,
                    fb_ad_id,
                    reset_table_before_search=False,
                    allow_scroll_search=False,
                )
                for task in tasks_by_ad_id[fb_ad_id]:
                    results[task.id] = (success, message)
                remaining_ad_ids.discard(fb_ad_id)

                await asyncio.sleep(random.uniform(0.2, 0.5))

            if not remaining_ad_ids:
                logger.info("Disable: все объявления из пачки найдены за один проход таблицы")
                break

            scroll_before = await get_ads_table_scroll_metrics(page)
            if scroll_before["found"] and scroll_before["at_bottom"]:
                logger.info(
                    "Disable: достигнут низ таблицы, не найдено ещё %s объявлений, возвращаюсь к точечному поиску",
                    len(remaining_ad_ids),
                )
                should_fallback_to_legacy_search = True
                break

            scroll_after = await scroll_ads_table_down(page, step_px=DISABLE_BATCH_SCROLL_STEP_PX)
            if scroll_after.get("moved"):
                stalled_passes = 0
                continue

            stalled_passes += 1
            if stalled_passes >= 2:
                logger.info(
                    "Disable: таблица перестала двигаться вниз, возвращаюсь к поиску сверху для %s объявлений",
                    len(remaining_ad_ids),
                )
                should_fallback_to_legacy_search = True
                break

        if remaining_ad_ids and should_fallback_to_legacy_search:
            logger.info(
                "Disable: запускаю fallback-поиск сверху для %s объявлений",
                len(remaining_ad_ids),
            )
            await _reset_ads_table_scroll(page)
            await asyncio.sleep(random.uniform(0.3, 0.6))

            for fb_ad_id in ordered_ad_ids:
                if fb_ad_id not in remaining_ad_ids:
                    continue

                success, message = await _execute_disable_on_page(
                    page,
                    fb_ad_id,
                    reset_table_before_search=True,
                    allow_scroll_search=True,
                )
                for task in tasks_by_ad_id[fb_ad_id]:
                    results[task.id] = (success, message)
                remaining_ad_ids.discard(fb_ad_id)
                await asyncio.sleep(random.uniform(0.2, 0.5))

        for fb_ad_id in remaining_ad_ids:
            for task in tasks_by_ad_id[fb_ad_id]:
                results[task.id] = (
                    False,
                    "Объявление не найдено в таблице за проход сверху вниз",
                )

        return results
    except Exception as e:
        logger.exception("Ошибка пакетного обхода таблицы при отключении")
        error_message = f"Ошибка Playwright: {e}"
        for task in tasks:
            results.setdefault(task.id, (False, error_message))
        return results


async def mark_succeeded(task_id) -> None:
    """Помечает задачу как успешно выполненную."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(DisableTask).where(DisableTask.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            if task.status == DisableTaskStatus.CANCELLED:
                logger.info(
                    "Задача %s уже отменена как неактуальная — пропускаю mark_succeeded",
                    task.id,
                )
                return
            task.status = DisableTaskStatus.SUCCEEDED
            task.completed_at = datetime.now(UTC)
            task.next_retry_at = None
            task.last_error = None

            snap_result = await session.execute(
                select(AdSnapshot).where(AdSnapshot.ad_id == task.ad_id)
            )
            snapshot = snap_result.scalar_one_or_none()
            if snapshot:
                snapshot.alert_state = (
                    AlertState.DISABLED
                    if is_delivery_disabled(snapshot.delivery_status)
                    else AlertState.CLAIMED
                )

            await session.commit()


async def mark_retrying(task_id, error: str, next_retry_at: datetime) -> None:
    """Помечает задачу для повторной попытки."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(DisableTask).where(DisableTask.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            if task.status == DisableTaskStatus.CANCELLED:
                logger.info(
                    "Задача %s уже отменена как неактуальная — пропускаю mark_retrying",
                    task.id,
                )
                return
            task.status = DisableTaskStatus.RETRYING
            task.last_error = error[:500]
            task.next_retry_at = next_retry_at
            await session.commit()


async def mark_failed(task_id, error: str) -> None:
    """Помечает задачу как окончательно проваленную (исчерпаны попытки)."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(DisableTask).where(DisableTask.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            if task.status == DisableTaskStatus.CANCELLED:
                logger.info(
                    "Задача %s уже отменена как неактуальная — пропускаю mark_failed",
                    task.id,
                )
                return
            task.status = DisableTaskStatus.FAILED
            task.last_error = error[:500]
            task.completed_at = datetime.now(UTC)
            await session.commit()


async def _send_disable_task_completion_update(
    task,
    *,
    success: bool,
    message: str,
    fallback_token: str,
    fallback_chat_id: str,
) -> None:
    """Рассылает lifecycle-обновление по disable task всем активным получателям."""
    factory = get_session_factory()
    async with factory() as session:
        persisted_task = await session.scalar(
            select(DisableTask)
            .options(selectinload(DisableTask.fb_ad))
            .where(DisableTask.id == task.id)
        )
        if persisted_task is None:
            return

    fb_ad = persisted_task.fb_ad
    await broadcast_disable_task_runtime_message(
        ad_name=fb_ad.ad_name if fb_ad else "",
        fb_ad_id=fb_ad.fb_ad_id if fb_ad else "",
        requested_by_username=persisted_task.requested_by_username or "",
        status=str(persisted_task.status),
        incident_key=persisted_task.open_state_token,
        detail=message,
        next_retry_at=persisted_task.next_retry_at,
        fallback_token=fallback_token,
        fallback_chat_id=fallback_chat_id,
    )


async def main() -> None:
    """Запуск disable worker."""
    settings = get_settings()
    setup_sentry(dsn=settings.sentry_dsn, environment=settings.sentry_environment)
    shutdown_event = asyncio.Event()
    waiting_for_vision_logged = False

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, shutdown_event.set)
    loop.add_signal_handler(signal.SIGINT, shutdown_event.set)

    try:
        from apps.disable_worker.main import disable_worker_loop

        while not shutdown_event.is_set():
            vision_x_token, vision_api_url, vision_profile_id = await _load_vision_settings()
            if not vision_x_token or not vision_profile_id:
                if not waiting_for_vision_logged:
                    logger.info(
                        "Disable worker ждёт Vision-настройки из UI или .env и продолжает работать в фоне"
                    )
                    waiting_for_vision_logged = True
                if await wait_for_shutdown_or_timeout(
                    shutdown_event,
                    VISION_SETTINGS_POLL_INTERVAL_SECONDS,
                ):
                    break
                continue

            waiting_for_vision_logged = False
            vision = VisionClient(
                x_token=vision_x_token,
                base_url=vision_api_url,
            )
            manager = VisionBrowserManager(
                vision_client=vision,
                profile_id=vision_profile_id,
            )

            try:
                await manager.connect()
                logger.info("Disable worker подключён к Vision")

                await disable_worker_loop(
                    poll_interval_seconds=5,
                    claim_next_task=claim_next_task,
                    claim_task_batch=claim_task_batch,
                    execute_disable=lambda fb_ad_id, manager=manager: (
                        execute_disable_via_playwright(
                            manager,
                            fb_ad_id,
                        )
                    ),
                    execute_disable_batch=lambda tasks, manager=manager: (
                        execute_disable_batch_via_playwright(
                            manager,
                            tasks,
                        )
                    ),
                    batch_size=DISABLE_BATCH_SIZE,
                    mark_succeeded=mark_succeeded,
                    mark_retrying=mark_retrying,
                    mark_failed=mark_failed,
                    send_completion_callback=lambda task, success, message: (
                        _send_disable_task_completion_update(
                            task,
                            success=success,
                            message=message,
                            fallback_token=settings.telegram_bot_token,
                            fallback_chat_id=settings.telegram_chat_id,
                        )
                    ),
                    telegram_bot_token="",
                    telegram_chat_id="",
                    shutdown_event=shutdown_event,
                )
            except KeyboardInterrupt:
                logger.info("Disable worker остановлен по Ctrl+C")
                break
            except Exception:
                if shutdown_event.is_set():
                    break
                logger.exception("Disable worker: ошибка запуска или подключения к Vision")
                if await wait_for_shutdown_or_timeout(
                    shutdown_event,
                    VISION_SETTINGS_POLL_INTERVAL_SECONDS,
                ):
                    break
            finally:
                await _close_disable_runtime_resources(manager, vision)
    except KeyboardInterrupt:
        logger.info("Disable worker остановлен по Ctrl+C")
    finally:
        logger.info("Disable worker: ресурсы освобождены")


if __name__ == "__main__":
    _PID_FILE = pathlib.Path("/tmp/fb_disable_worker.pid")
    try:
        with PidFileLock(_PID_FILE):
            asyncio.run(main())
    except RuntimeError as e:
        logger.error("%s", e)
        sys.exit(1)
