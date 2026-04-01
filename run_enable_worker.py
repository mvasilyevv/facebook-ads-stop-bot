# -*- coding: utf-8 -*-
"""Точка входа: запускает enable worker с подключением к Vision и БД."""

from __future__ import annotations

import asyncio
import logging
import pathlib
import signal
import sys
from datetime import UTC, datetime, timedelta

try:
    from patchright._impl._errors import Error as PatchrightError
except ModuleNotFoundError:  # pragma: no cover - зависит от окружения

    class PatchrightError(Exception):
        """Фолбэк-тип ошибок patchright для окружений без библиотеки."""


from sqlalchemy import select

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
    reset_ads_table_scroll,
    scroll_ads_table_down,
    toggle_cell_selector,
)
from core.browser.humanizer import human_click, human_move, human_scroll_to_find, human_wheel_scroll
from core.browser.manager import VisionBrowserManager
from core.browser.vision_client import VisionClient
from core.config import get_settings
from core.crypto import decrypt
from core.db import get_session_factory
from core.domain import EnableTaskStatus
from core.enable_tasks import reconcile_enable_tasks
from core.models import EnableTask, VisionSettings
from core.telegram.client import TelegramBotClient
from core.telegram.delivery import (
    broadcast_enable_task_runtime_message,
    render_enable_task_runtime_message,
)
from core.worker_utils import PidFileLock, calculate_retry_delay, wait_for_shutdown_or_timeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
VISION_SETTINGS_POLL_INTERVAL_SECONDS = 5
ENABLE_CONFIRMATION_POLL_DELAYS_SECONDS = (0.0, 3.0, 3.0, 3.0, 4.0, 4.0)
ENABLE_CONFIRMATION_TRUE_READS_REQUIRED = 2
ENABLE_CONFIRMATION_WINDOW_SECONDS = int(sum(ENABLE_CONFIRMATION_POLL_DELAYS_SECONDS))
ENABLE_BROWSER_TASK_TIMEOUT_SECONDS = 60
ENABLE_SINGLE_SEARCH_MAX_SCROLL_PASSES = 120
ENABLE_SINGLE_SEARCH_FALLBACK_MAX_STEPS = 60
ENABLE_TABLE_SCROLL_STEP_PX = 220
_BROWSER_RUNTIME_ERROR_MARKERS = (
    "target page, context or browser has been closed",
    "browser has disconnected",
    "session closed",
    "connection closed",
    "cdp",
    "websocket",
    "pipe closed",
    "broken pipe",
)


class EnableBrowserOperationTimeoutError(RuntimeError):
    """Браузерная операция enable worker превысила допустимый таймаут."""


def _build_enable_timeout_message(timeout_seconds: int) -> str:
    """Формирует текст ошибки таймаута браузерной операции включения."""
    return f"Браузерная операция включения превысила таймаут {timeout_seconds} сек"


def _is_browser_connection_error(exc: Exception) -> bool:
    """Определяет, относится ли ошибка к потере соединения с браузером."""
    if isinstance(exc, (ConnectionError, OSError, PatchrightError)):
        return True
    if not isinstance(exc, RuntimeError):
        return False
    message = str(exc).casefold()
    return any(marker in message for marker in _BROWSER_RUNTIME_ERROR_MARKERS)


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
    """Берёт следующую задачу включения из очереди (PENDING или RETRYING с наступившим retry)."""
    factory = get_session_factory()
    async with factory() as session:
        now = datetime.now(UTC)
        recovery_summary = await reconcile_enable_tasks(session, now=now)
        if any(recovery_summary.values()):
            await session.commit()

        result = await session.execute(
            select(EnableTask)
            .where(
                (EnableTask.status == EnableTaskStatus.PENDING)
                | (
                    (EnableTask.status == EnableTaskStatus.RETRYING)
                    & (EnableTask.next_retry_at <= now)
                )
            )
            .order_by(EnableTask.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        task = result.scalar_one_or_none()
        if task is None:
            return None

        task.status = EnableTaskStatus.RUNNING
        task.attempt_count += 1
        await session.commit()
        await session.refresh(task)
        return task


def _find_ads_manager_page(manager) -> object | None:
    """Ищет вкладку Ads Manager среди открытых страниц браузера."""
    browser = manager._browser
    if browser is None:
        return None
    for context in browser.contexts:
        for page in context.pages:
            url = page.url or ""
            if "adsmanager" in url or "facebook.com/ads" in url:
                return page
    for context in browser.contexts:
        if context.pages:
            return context.pages[0]
    return None


async def _resolve_ads_manager_page(manager) -> tuple[object | None, str | None]:
    """Находит рабочую вкладку Ads Manager и при необходимости переподключается."""
    page = _find_ads_manager_page(manager)
    if page is not None:
        return page, None

    logger.warning("Enable worker: вкладка Ads Manager не найдена, переподключаюсь к браузеру")
    try:
        await manager.disconnect()
        await manager.connect()
    except Exception as reconnect_err:
        return None, f"Не удалось переподключиться к браузеру: {reconnect_err}"

    page = _find_ads_manager_page(manager)
    if page is None:
        return None, "Не найдена страница Ads Manager после переподключения"
    return page, None


async def _confirm_dialog_if_present(page) -> bool:
    """Подтверждает модальный диалог Meta, если он появился после включения."""
    confirm_words = {
        "подтвердить",
        "ok",
        "да",
        "продолжить",
        "включить",
        "confirm",
        "yes",
        "publish",
        "опубликовать",
    }
    try:
        return await shared_confirm_dialog_if_present(
            page,
            confirm_words=confirm_words,
            click_fn=human_click,
            logger=logger,
            sleep_range=(0.7, 0.7),
            log_message="Enable worker: подтверждаю диалог Meta: '%s'",
        )
    except Exception:
        logger.debug("Enable worker: не удалось проверить диалог подтверждения", exc_info=True)
        return False


async def _get_enable_aria_checked_via_js(page, fb_ad_id: str) -> str:
    """Читает текущее состояние реального switch-переключателя через свежий DOM-запрос."""
    return await shared_get_toggle_aria_checked_via_js(
        page,
        fb_ad_id,
        selector_builder=toggle_cell_selector,
    )


def _normalize_aria_checked(value: str | None) -> str:
    """Нормализует aria-checked для строгого сравнения."""
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


async def find_toggle_cell_in_dom(page, fb_ad_id: str):
    """Ищет toggle-ячейку объявления среди видимых строк."""
    return await _find_toggle_cell_in_dom_raw(page, fb_ad_id)


async def find_toggle_cell_with_table_scan(
    page,
    fb_ad_id: str,
    *,
    reset_to_top: bool = True,
    max_scroll_passes: int = ENABLE_SINGLE_SEARCH_MAX_SCROLL_PASSES,
    step_px: int = ENABLE_TABLE_SCROLL_STEP_PX,
    fallback_max_steps: int = ENABLE_SINGLE_SEARCH_FALLBACK_MAX_STEPS,
):
    """Ищет строку объявления проходом сверху вниз по таблице Ads Manager."""
    return await scan_for_toggle_cell(
        page,
        fb_ad_id,
        selector_builder=toggle_cell_selector,
        find_in_dom=find_toggle_cell_in_dom,
        reset_to_top_fn=_reset_ads_table_scroll,
        get_scroll_metrics=get_ads_table_scroll_metrics,
        scroll_down=scroll_ads_table_down,
        legacy_scroll_to_find=human_scroll_to_find,
        reset_to_top=reset_to_top,
        max_scroll_passes=max_scroll_passes,
        step_px=step_px,
        fallback_max_steps=fallback_max_steps,
    )


async def _restore_toggle_row_visibility(page, fb_ad_id: str) -> None:
    """Возвращает строку объявления в DOM, если Meta временно перестроила таблицу."""
    await shared_restore_toggle_row_visibility(
        page,
        fb_ad_id,
        find_in_dom=find_toggle_cell_in_dom,
        scan_in_table=find_toggle_cell_with_table_scan,
        logger=logger,
        log_message="Enable worker: строка %s временно пропала из DOM — возвращаю объявление в область видимости",
        max_scroll_passes=ENABLE_SINGLE_SEARCH_MAX_SCROLL_PASSES,
        step_px=ENABLE_TABLE_SCROLL_STEP_PX,
        fallback_max_steps=ENABLE_SINGLE_SEARCH_FALLBACK_MAX_STEPS,
    )


async def _wait_for_enable_confirmation(page, fb_ad_id: str) -> tuple[bool, str]:
    """Ждёт подтверждения ON через повторные чтения aria-checked."""
    return await shared_wait_for_toggle_confirmation(
        page,
        fb_ad_id,
        poll_delays_seconds=ENABLE_CONFIRMATION_POLL_DELAYS_SECONDS,
        required_reads=ENABLE_CONFIRMATION_TRUE_READS_REQUIRED,
        expected_checked="true",
        read_state=_get_enable_aria_checked_via_js,
        restore_visibility=_restore_toggle_row_visibility,
        logger=logger,
        progress_log_message="Проверка включения %s: попытка %s/%s, aria-checked=%s",
        success_message="Объявление включено: переключатель дважды подтвердил состояние ON",
        failure_message="Переключатель нажат, но интерфейс Meta не подтвердил состояние ON",
    )


async def execute_enable_via_playwright(page, fb_ad_id: str) -> tuple[bool, str]:
    """Выполняет клик для включения объявления через Playwright.

    Находит переключатель в ячейке toggle и кликает если он выключен.
    """
    try:
        toggle_cell = await find_toggle_cell_in_dom(page, fb_ad_id)

        if toggle_cell is None:
            logger.info(
                "Объявление %s не в DOM, прохожу таблицу Ads Manager сверху вниз",
                fb_ad_id,
            )
            toggle_cell = await find_toggle_cell_with_table_scan(
                page,
                fb_ad_id,
                reset_to_top=True,
                max_scroll_passes=ENABLE_SINGLE_SEARCH_MAX_SCROLL_PASSES,
                step_px=ENABLE_TABLE_SCROLL_STEP_PX,
                fallback_max_steps=ENABLE_SINGLE_SEARCH_FALLBACK_MAX_STEPS,
            )

        if toggle_cell is None:
            try:
                screenshot_path = f"/tmp/enable_fail_{fb_ad_id}.png"
                await page.screenshot(path=screenshot_path)
                logger.error(
                    "Строка объявления не найдена — скриншот сохранён: %s", screenshot_path
                )
            except Exception:
                pass
            return False, f"Строка с Ad ID {fb_ad_id} не найдена в таблице после прокрутки"

        toggle = await toggle_cell.query_selector('[role="switch"][aria-checked]')
        if toggle is None:
            return (
                False,
                "Не найден точный switch-переключатель объявления; batch-checkbox и fallback-контролы отключены",
            )

        # Проверяем текущее состояние: включать только если выключено
        aria_checked = _normalize_aria_checked(await toggle.get_attribute("aria-checked"))
        if aria_checked == "true":
            logger.info("Объявление %s уже включено (aria-checked=true), пропускаем", fb_ad_id)
            return True, "Объявление уже включено"
        if aria_checked != "false":
            return (
                False,
                f"Не удалось однозначно определить состояние переключателя: aria-checked={aria_checked or 'null'}",
            )

        logger.info(
            "Найден переключатель: aria-checked=%s для %s, выполняю клик",
            aria_checked,
            fb_ad_id,
        )

        try:
            await human_click(page, toggle, double_check_pause=True)
        except Exception:
            logger.debug(
                "Enable worker: первый human_click не сработал, пробую повторный клик",
                exc_info=True,
            )
            await asyncio.sleep(0.3)
            try:
                await human_click(page, toggle, double_check_pause=False)
            except Exception as second_click_error:
                return (
                    False,
                    f"Не удалось нажать переключатель через humanizer: {second_click_error}",
                )

        await asyncio.sleep(2.0)

        dialog_confirmed = await _confirm_dialog_if_present(page)
        if dialog_confirmed:
            await asyncio.sleep(1.0)

        success, confirmation_message = await _wait_for_enable_confirmation(page, fb_ad_id)
        if success:
            return True, confirmation_message

        try:
            screenshot_path = f"/tmp/enable_fail_{fb_ad_id}.png"
            await page.screenshot(path=screenshot_path)
            logger.error("Включение не подтверждено — скриншот: %s", screenshot_path)
        except Exception:
            pass

        return (
            False,
            f"{confirmation_message} (около {ENABLE_CONFIRMATION_WINDOW_SECONDS} сек)",
        )

    except Exception as e:
        if _is_browser_connection_error(e):
            raise
        logger.exception("Ошибка Playwright при включении %s", fb_ad_id)
        try:
            screenshot_path = f"/tmp/enable_error_{fb_ad_id}.png"
            await page.screenshot(path=screenshot_path)
            logger.error("Скриншот ошибки: %s", screenshot_path)
        except Exception:
            pass
        return False, f"Ошибка Playwright: {e}"


async def mark_succeeded(task_id) -> None:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(EnableTask).where(EnableTask.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.status = EnableTaskStatus.SUCCEEDED
            task.next_retry_at = None
            task.last_error = None
            task.completed_at = datetime.now(UTC)
            await session.commit()


async def mark_retrying(task_id, error: str, next_retry_at: datetime) -> None:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(EnableTask).where(EnableTask.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.status = EnableTaskStatus.RETRYING
            task.completed_at = None
            task.last_error = error[:500]
            task.next_retry_at = next_retry_at
            await session.commit()


async def mark_failed(task_id, error: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(EnableTask).where(EnableTask.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.status = EnableTaskStatus.FAILED
            task.next_retry_at = None
            task.last_error = error[:500]
            task.completed_at = datetime.now(UTC)
            await session.commit()


async def _send_enable_task_runtime_update(
    task,
    *,
    status: str,
    detail: str = "",
    next_retry_at: datetime | None = None,
) -> None:
    """Рассылает runtime-обновление по задаче включения всем активным получателям."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(EnableTask).where(EnableTask.id == task.id))
        persisted_task = result.scalar_one_or_none()

    task_row = persisted_task or task
    await broadcast_enable_task_runtime_message(
        ad_name=task_row.ad_name,
        fb_ad_id=task_row.fb_ad_id,
        requested_by_username=task_row.requested_by_username or "",
        status=status,
        incident_key=(
            str(task_row.recommendation_event_id) if task_row.recommendation_event_id else ""
        ),
        detail=detail,
        next_retry_at=next_retry_at,
    )


async def _process_enable_task_result(
    *,
    task,
    success: bool,
    message: str,
    tg_client,
    tg_chat_id: str,
    send_completion_callback,
) -> None:
    """Фиксирует итог обработки enable-задачи и рассылает runtime-обновление."""
    next_retry_at = None
    if success:
        await mark_succeeded(task.id)
        status = EnableTaskStatus.SUCCEEDED
        logger.info("Объявление %s успешно включено", task.fb_ad_id)
    else:
        attempt = task.attempt_count
        max_attempts = task.max_attempts
        if attempt >= max_attempts:
            await mark_failed(task.id, message)
            status = EnableTaskStatus.FAILED
            logger.error(
                "Задача %s для %s провалена: исчерпаны все %s попыток",
                task.id,
                task.fb_ad_id,
                max_attempts,
            )
        else:
            delay = calculate_retry_delay(attempt)
            next_retry_at = datetime.now(tz=UTC) + timedelta(seconds=delay)
            await mark_retrying(task.id, message, next_retry_at)
            status = EnableTaskStatus.RETRYING
            logger.warning(
                "Не удалось включить %s: %s. Retry через %s сек",
                task.fb_ad_id,
                message,
                delay,
            )

    if send_completion_callback:
        await send_completion_callback(task, status.value, message, next_retry_at)
        return

    if tg_client and tg_chat_id:
        try:
            await tg_client.send_message(
                chat_id=tg_chat_id,
                text=render_enable_task_runtime_message(
                    ad_name=task.ad_name,
                    fb_ad_id=task.fb_ad_id,
                    requested_by_username=task.requested_by_username or "",
                    status=status.value,
                    detail=message,
                    next_retry_at=next_retry_at,
                ),
            )
        except Exception:
            logger.exception("Не удалось отправить уведомление в TG")


async def enable_worker_loop(
    manager,
    tg_client,
    tg_chat_id: str,
    poll_interval: int = 5,
    shutdown_event: asyncio.Event | None = None,
    send_completion_callback=None,
) -> None:
    """Бесконечный цикл обработки задач на включение."""
    while not (shutdown_event and shutdown_event.is_set()):
        try:
            task = await claim_next_task()
            if task is None:
                try:
                    if shutdown_event:
                        await asyncio.wait_for(shutdown_event.wait(), timeout=poll_interval)
                        break
                except asyncio.TimeoutError:
                    pass
                continue

            logger.info(
                "Enable worker: выполняю задачу %s для объявления %s",
                task.id,
                task.fb_ad_id,
            )

            page, page_error = await _resolve_ads_manager_page(manager)
            if page is None:
                success, message = False, page_error or "Не найдена рабочая вкладка Ads Manager"
            else:
                try:
                    success, message = await asyncio.wait_for(
                        execute_enable_via_playwright(page, task.fb_ad_id),
                        timeout=ENABLE_BROWSER_TASK_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError as exc:
                    timeout_message = _build_enable_timeout_message(
                        ENABLE_BROWSER_TASK_TIMEOUT_SECONDS,
                    )
                    logger.error(
                        "Enable worker: задача %s для %s зависла дольше %s сек, переподключаю браузер",
                        task.id,
                        task.fb_ad_id,
                        ENABLE_BROWSER_TASK_TIMEOUT_SECONDS,
                    )
                    await _process_enable_task_result(
                        task=task,
                        success=False,
                        message=timeout_message,
                        tg_client=tg_client,
                        tg_chat_id=tg_chat_id,
                        send_completion_callback=send_completion_callback,
                    )
                    raise EnableBrowserOperationTimeoutError(timeout_message) from exc

            await _process_enable_task_result(
                task=task,
                success=success,
                message=message,
                tg_client=tg_client,
                tg_chat_id=tg_chat_id,
                send_completion_callback=send_completion_callback,
            )

        except EnableBrowserOperationTimeoutError:
            raise
        except Exception as exc:
            if _is_browser_connection_error(exc):
                logger.error(
                    "Enable worker: потеряно соединение с браузером, нужен reconnect: %s",
                    exc,
                )
                raise
            logger.exception("Enable worker: ошибка в цикле")
            await asyncio.sleep(poll_interval)


async def main() -> None:
    """Запуск enable worker."""
    settings = get_settings()
    tg_client = None
    if settings.telegram_bot_token and settings.telegram_chat_id:
        tg_client = TelegramBotClient(settings.telegram_bot_token)

    shutdown_event = asyncio.Event()
    waiting_for_vision_logged = False

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, shutdown_event.set)
    loop.add_signal_handler(signal.SIGINT, shutdown_event.set)

    try:
        while not shutdown_event.is_set():
            vision_x_token, vision_api_url, vision_profile_id = await _load_vision_settings()
            if not vision_x_token or not vision_profile_id:
                if not waiting_for_vision_logged:
                    logger.info(
                        "Enable worker ждёт Vision-настройки из UI или .env и продолжает работать в фоне"
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
                logger.info("Enable worker подключён к Vision")

                await enable_worker_loop(
                    manager=manager,
                    tg_client=tg_client,
                    tg_chat_id=settings.telegram_chat_id,
                    shutdown_event=shutdown_event,
                    send_completion_callback=lambda task, status, detail, next_retry_at: (
                        _send_enable_task_runtime_update(
                            task,
                            status=status,
                            detail=detail,
                            next_retry_at=next_retry_at,
                        )
                    ),
                )
            except KeyboardInterrupt:
                logger.info("Enable worker остановлен по Ctrl+C")
                break
            except Exception:
                if shutdown_event.is_set():
                    break
                logger.exception("Enable worker: ошибка запуска или подключения к Vision")
                if await wait_for_shutdown_or_timeout(
                    shutdown_event,
                    VISION_SETTINGS_POLL_INTERVAL_SECONDS,
                ):
                    break
            finally:
                try:
                    await manager.disconnect()
                except Exception:
                    logger.debug(
                        "Enable worker: не удалось корректно отключить браузер",
                        exc_info=True,
                    )
                try:
                    await vision.close()
                except Exception:
                    logger.debug(
                        "Enable worker: не удалось закрыть Vision клиент",
                        exc_info=True,
                    )
    except KeyboardInterrupt:
        logger.info("Enable worker остановлен по Ctrl+C")
    finally:
        if tg_client is not None:
            await tg_client.close()
        logger.info("Enable worker: ресурсы освобождены")


if __name__ == "__main__":
    _PID_FILE = pathlib.Path("/tmp/fb_enable_worker.pid")
    try:
        with PidFileLock(_PID_FILE):
            asyncio.run(main())
    except RuntimeError as e:
        logger.error("%s", e)
        sys.exit(1)
