# -*- coding: utf-8 -*-
"""Точка входа: запускает enable worker с подключением к Vision и БД."""

from __future__ import annotations

import asyncio
import html
import logging
import signal
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from core.browser.manager import VisionBrowserManager
from core.browser.vision_client import VisionClient
from core.config import get_settings
from core.db import get_session_factory
from core.domain import EnableTaskStatus
from core.models import EnableTask
from core.telegram.client import TelegramBotClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def claim_next_task():
    """Берёт следующую задачу включения из очереди (PENDING или RETRYING с наступившим retry)."""
    factory = get_session_factory()
    async with factory() as session:
        now = datetime.now(UTC)
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


async def execute_enable_via_playwright(page, fb_ad_id: str) -> tuple[bool, str]:
    """Выполняет клик для включения объявления через Playwright.

    Находит переключатель в ячейке toggle и кликает если он выключен.
    """
    try:
        # Ищем ячейку toggle для конкретного объявления
        toggle_cell_selector = (
            f'[data-surface*="table_row:{fb_ad_id}"][data-surface*="forObjectType(toggle"]'
        )
        toggle_cell = await page.query_selector(toggle_cell_selector)
        toggle = None

        if toggle_cell is not None:
            toggle = await toggle_cell.query_selector('input[role="switch"]')
            if toggle is None:
                toggle = await toggle_cell.query_selector('input[type="checkbox"]')

        # Fallback: ищем по строке
        if toggle is None:
            row_selector = f'[data-surface*="table_row:{fb_ad_id}"]'
            target_row = await page.query_selector(row_selector)
            if target_row is not None:
                toggle = await target_row.query_selector('[role="switch"]')

        if toggle is None:
            try:
                screenshot_path = f"/tmp/enable_fail_{fb_ad_id}.png"
                await page.screenshot(path=screenshot_path)
                logger.error("Переключатель не найден — скриншот сохранён: %s", screenshot_path)
            except Exception:
                pass
            return False, f"Переключатель для объявления {fb_ad_id} не найден в таблице"

        # Проверяем текущее состояние: включать только если выключено
        aria_checked = await toggle.get_attribute("aria-checked") or "false"
        if aria_checked.lower() == "true":
            logger.info("Объявление %s уже включено (aria-checked=true), пропускаем", fb_ad_id)
            return True, "Объявление уже включено"

        logger.info(
            "Найден переключатель: aria-checked=%s для %s, выполняю клик",
            aria_checked, fb_ad_id,
        )

        await toggle.scroll_into_view_if_needed()
        await asyncio.sleep(0.5)
        await toggle.click()
        await asyncio.sleep(2.0)

        # Проверяем результат (до 3 попыток × 3 сек)
        for attempt in range(3):
            aria_checked_after = await toggle.get_attribute("aria-checked") or "false"
            if aria_checked_after.lower() == "true":
                return True, "Объявление включено через переключатель"
            logger.info(
                "Проверка результата включения %s: попытка %s/3", fb_ad_id, attempt + 1
            )
            await asyncio.sleep(3.0)

        return True, "Переключатель нажат (статус не изменился за 9 сек, требуется проверка)"

    except Exception as e:
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
            task.completed_at = datetime.now(UTC)
            await session.commit()


async def mark_retrying(task_id, error: str, next_retry_at: datetime) -> None:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(EnableTask).where(EnableTask.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.status = EnableTaskStatus.RETRYING
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
            task.last_error = error[:500]
            task.completed_at = datetime.now(UTC)
            await session.commit()


async def enable_worker_loop(
    page, tg_client, tg_chat_id: str, poll_interval: int = 5,
    shutdown_event: asyncio.Event | None = None,
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
                else:
                    await asyncio.sleep(poll_interval)
                continue

            logger.info(
                "Enable worker: выполняю задачу %s для объявления %s",
                task.id,
                task.fb_ad_id,
            )

            success, message = await execute_enable_via_playwright(page, task.fb_ad_id)

            if success:
                await mark_succeeded(task.id)
                logger.info("Объявление %s успешно включено", task.fb_ad_id)

                if tg_client and tg_chat_id:
                    try:
                        await tg_client.send_message(
                            chat_id=tg_chat_id,
                            text=(
                                f"✅ <b>Объявление включено</b>\n\n"
                                f"📢 {html.escape(task.ad_name)}\n"
                                f"🆔 <code>{task.fb_ad_id}</code>\n"
                                f"👤 Запросил: @{task.requested_by_username or 'неизвестно'}"
                            ),
                        )
                    except Exception:
                        logger.exception("Не удалось отправить уведомление в TG")
            else:
                attempt = task.attempt_count
                max_att = task.max_attempts
                if attempt >= max_att:
                    await mark_failed(task.id, message)
                    logger.error(
                        "Задача %s для %s провалена: исчерпаны все %s попыток",
                        task.id, task.fb_ad_id, max_att,
                    )
                else:
                    from datetime import timedelta
                    delay = min(30 * (2 ** max(attempt - 1, 0)), 300)
                    next_retry = datetime.now(tz=UTC) + timedelta(seconds=delay)
                    await mark_retrying(task.id, message, next_retry)
                    logger.warning(
                        "Не удалось включить %s: %s. Retry через %s сек",
                        task.fb_ad_id, message, delay,
                    )

        except Exception:
            logger.exception("Enable worker: ошибка в цикле")
            await asyncio.sleep(poll_interval)


async def main() -> None:
    """Запуск enable worker."""
    settings = get_settings()

    if not settings.vision_x_token or not settings.vision_profile_id:
        logger.error("Не заданы VISION_X_TOKEN или VISION_PROFILE_ID")
        sys.exit(1)

    vision = VisionClient(
        x_token=settings.vision_x_token,
        base_url=settings.vision_api_url,
    )
    manager = VisionBrowserManager(
        vision_client=vision,
        profile_id=settings.vision_profile_id,
    )

    tg_client = None
    if settings.telegram_bot_token and settings.telegram_chat_id:
        tg_client = TelegramBotClient(settings.telegram_bot_token)

    shutdown_event = asyncio.Event()

    def _handle_signal(signum, frame):
        logger.info("Получен сигнал %s — завершаем enable worker", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        await manager.connect()
        page = await manager.get_page()
        logger.info("Enable worker подключён к Vision")

        await enable_worker_loop(
            page=page,
            tg_client=tg_client,
            tg_chat_id=settings.telegram_chat_id,
            shutdown_event=shutdown_event,
        )
    except KeyboardInterrupt:
        logger.info("Enable worker остановлен по Ctrl+C")
    finally:
        await manager.disconnect()
        await vision.close()
        logger.info("Enable worker: ресурсы освобождены")


if __name__ == "__main__":
    import os, pathlib
    _PID_FILE = pathlib.Path("/tmp/fb_enable_worker.pid")
    if _PID_FILE.exists():
        _old_pid = int(_PID_FILE.read_text().strip())
        try:
            os.kill(_old_pid, 0)
            logger.error("Enable worker уже запущен (PID %s). Запуск второго экземпляра запрещён.", _old_pid)
            sys.exit(1)
        except ProcessLookupError:
            pass
    _PID_FILE.write_text(str(os.getpid()))
    try:
        asyncio.run(main())
    finally:
        _PID_FILE.unlink(missing_ok=True)
