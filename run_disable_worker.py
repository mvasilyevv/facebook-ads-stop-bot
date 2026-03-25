# -*- coding: utf-8 -*-
"""Точка входа: запускает disable worker с подключением к Vision и БД."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from core.browser.manager import VisionBrowserManager
from core.browser.vision_client import VisionClient
from core.config import get_settings
from core.db import get_session_factory
from core.domain import AlertState, DisableTaskStatus
from core.models import AdSnapshot, DisableTask

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def claim_next_task():
    """Берёт следующую задачу из очереди (PENDING или RETRYING с наступившим retry)."""
    factory = get_session_factory()
    async with factory() as session:
        now = datetime.now(UTC)
        result = await session.execute(
            select(DisableTask)
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

        # Помечаем как RUNNING
        task.status = DisableTaskStatus.RUNNING
        task.attempt_count += 1
        await session.commit()
        # Возвращаем данные задачи (detached от сессии)
        await session.refresh(task)
        return task


async def execute_disable_via_playwright(page, fb_ad_id: str) -> tuple[bool, str]:
    """Выполняет клик для отключения объявления через Playwright.

    Ищет строку с Ad ID в таблице Ads Manager и нажимает toggle/кнопку выключения.
    """
    try:
        # Ищем строку с нужным Ad ID
        rows = await page.query_selector_all('[data-surface*="table_row:"]')
        target_row = None
        for row in rows:
            text = await row.inner_text()
            if fb_ad_id in text:
                target_row = row
                break

        if target_row is None:
            return False, f"Строка с Ad ID {fb_ad_id} не найдена в таблице"

        # Ищем toggle/switch для отключения
        toggle = await target_row.query_selector(
            'input[type="checkbox"], [role="switch"], [aria-label*="toggle"]'
        )
        if toggle:
            await toggle.click()
            await asyncio.sleep(1.0)
            return True, "Объявление отключено через toggle"

        # Альтернатива: ищем кнопку с текстом выключения
        buttons = await target_row.query_selector_all('[role="button"]')
        for btn in buttons:
            text = (await btn.inner_text()).lower()
            if any(w in text for w in ("выключить", "off", "pause", "пауза", "disable")):
                await btn.click()
                await asyncio.sleep(1.0)
                return True, "Объявление отключено через кнопку"

        return False, "Не найден элемент для отключения объявления"
    except Exception as e:
        return False, f"Ошибка Playwright: {e}"


async def mark_succeeded(task_id) -> None:
    """Помечает задачу как успешно выполненную."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(DisableTask).where(DisableTask.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.status = DisableTaskStatus.SUCCEEDED
            task.completed_at = datetime.now(UTC)

            # Обновляем состояние снэпшота
            snap_result = await session.execute(
                select(AdSnapshot).where(AdSnapshot.fb_ad_id == task.fb_ad_id)
            )
            snapshot = snap_result.scalar_one_or_none()
            if snapshot:
                snapshot.alert_state = AlertState.DISABLED

            await session.commit()


async def mark_retrying(task_id, error: str, next_retry_at: datetime) -> None:
    """Помечает задачу для повторной попытки."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(DisableTask).where(DisableTask.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.status = DisableTaskStatus.RETRYING
            task.last_error = error[:500]
            task.next_retry_at = next_retry_at
            await session.commit()


async def main() -> None:
    """Запуск disable worker."""
    settings = get_settings()

    if not settings.vision_x_token or not settings.vision_profile_id:
        logger.error("Не заданы VISION_X_TOKEN или VISION_PROFILE_ID")
        sys.exit(1)

    # Инициализация Vision
    vision = VisionClient(
        x_token=settings.vision_x_token,
        base_url=settings.vision_api_url,
    )
    manager = VisionBrowserManager(
        vision_client=vision,
        profile_id=settings.vision_profile_id,
    )

    # Graceful shutdown
    shutdown_event = asyncio.Event()

    def _handle_signal(signum, frame):
        logger.info("Получен сигнал %s — завершаем disable worker", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        await manager.connect()
        page = await manager.get_page()
        logger.info("Disable worker подключён к Vision")

        from apps.disable_worker.main import disable_worker_loop

        await disable_worker_loop(
            poll_interval_seconds=5,
            claim_next_task=claim_next_task,
            execute_disable=lambda fb_ad_id: execute_disable_via_playwright(page, fb_ad_id),
            mark_succeeded=mark_succeeded,
            mark_retrying=mark_retrying,
            telegram_bot_token=settings.telegram_bot_token,
            telegram_chat_id=settings.telegram_chat_id,
        )
    except KeyboardInterrupt:
        logger.info("Disable worker остановлен по Ctrl+C")
    finally:
        await manager.disconnect()
        await vision.close()
        logger.info("Disable worker: ресурсы освобождены")


if __name__ == "__main__":
    asyncio.run(main())
