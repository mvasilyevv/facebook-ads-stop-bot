# -*- coding: utf-8 -*-
"""Точка входа: запускает disable worker с подключением к Vision и БД."""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import random
import signal
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from core.browser.humanizer import human_click, human_scroll_to_find
from core.browser.manager import VisionBrowserManager
from core.browser.vision_client import VisionClient
from core.config import get_settings
from core.crypto import decrypt
from core.db import get_session_factory
from core.disable_tasks import is_delivery_disabled, reconcile_disable_tasks
from core.domain import AlertState, DisableTaskStatus
from core.models import AdSnapshot, DisableTask, VisionSettings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Расширенное окно подтверждения нужно, потому что Meta может обновлять aria-checked
# заметно позже самого клика по переключателю.
DISABLE_CONFIRMATION_POLL_DELAYS_SECONDS = (0.0, 3.0, 3.0, 3.0, 3.0, 4.0, 4.0, 5.0, 5.0)
DISABLE_CONFIRMATION_FALSE_READS_REQUIRED = 2
DISABLE_CONFIRMATION_WINDOW_SECONDS = int(sum(DISABLE_CONFIRMATION_POLL_DELAYS_SECONDS))


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
        await session.refresh(task)
        return task


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
        buttons = await page.query_selector_all(
            '[role="dialog"] button, [role="dialog"] [role="button"], '
            '[role="alertdialog"] button, [role="alertdialog"] [role="button"]'
        )
        for btn in buttons:
            text = (await btn.inner_text()).lower().strip()
            if any(w in text for w in confirm_words):
                logger.info("Подтверждаю диалог: '%s'", text[:40])
                await btn.click()
                await asyncio.sleep(random.uniform(0.5, 1.0))
                return True
    except Exception:
        logger.debug("Ошибка при проверке диалога", exc_info=True)
    return False


async def _get_aria_checked_via_js(page, fb_ad_id: str) -> str:
    """Получает текущее значение aria-checked у реального переключателя объявления."""
    try:
        result = await page.evaluate(
            f"""() => {{
                const cell = document.querySelector(
                    '[data-surface*="table_row:{fb_ad_id}"][data-surface*="forObjectType(toggle"]'
                );
                if (!cell) return 'not_found';
                const toggle = cell.querySelector('[role="switch"][aria-checked]');
                if (!toggle) return 'no_toggle';
                return toggle.getAttribute('aria-checked') || 'null';
            }}"""
        )
        return str(result)
    except Exception:
        return "error"


def _normalize_aria_checked(value: str | None) -> str:
    """Нормализует значение aria-checked для строгого сравнения."""
    return (value or "").strip().lower()


async def _restore_toggle_row_visibility(page, fb_ad_id: str) -> None:
    """Возвращает строку объявления в DOM, если она пропала после обновления таблицы."""
    toggle_cell_selector = (
        f'[data-surface*="table_row:{fb_ad_id}"][data-surface*="forObjectType(toggle"]'
    )
    try:
        existing = await page.query_selector(toggle_cell_selector)
        if existing is not None:
            return

        logger.info(
            "Строка %s временно пропала из DOM — возвращаю объявление в область видимости",
            fb_ad_id,
        )
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(random.uniform(0.3, 0.6))
        await human_scroll_to_find(page, toggle_cell_selector, max_steps=12)
    except Exception:
        logger.debug("Не удалось вернуть строку %s в область видимости", fb_ad_id, exc_info=True)


async def _wait_for_disable_confirmation(page, fb_ad_id: str) -> tuple[bool, str]:
    """Ждёт подтверждения OFF в интерфейсе Meta без снижения критерия надёжности."""
    false_reads = 0
    total_attempts = len(DISABLE_CONFIRMATION_POLL_DELAYS_SECONDS)

    for attempt, delay_seconds in enumerate(DISABLE_CONFIRMATION_POLL_DELAYS_SECONDS, start=1):
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        checked_after = _normalize_aria_checked(await _get_aria_checked_via_js(page, fb_ad_id))
        logger.info(
            "Проверка отключения %s: попытка %s/%s, aria-checked=%s",
            fb_ad_id,
            attempt,
            total_attempts,
            checked_after or "null",
        )

        if checked_after == "false":
            false_reads += 1
            if false_reads >= DISABLE_CONFIRMATION_FALSE_READS_REQUIRED:
                return (
                    True,
                    "Объявление выключено: переключатель дважды подтвердил состояние OFF",
                )
            continue

        false_reads = 0

        if checked_after in {"not_found", "no_toggle", "no_input", "error", "null"}:
            await _restore_toggle_row_visibility(page, fb_ad_id)

    return (
        False,
        "Переключатель нажат, но интерфейс не подтвердил OFF даже после расширенной проверки",
    )


async def execute_disable_via_playwright(manager, fb_ad_id: str) -> tuple[bool, str]:
    """Выполняет клик для отключения объявления через Playwright.

    Ищет строку по data-surface атрибуту (Ad ID) и нажимает переключатель.
    После клика проверяет результат через свежий JS-запрос (избегает stale element).
    """
    page = None
    try:
        page = _find_ads_manager_page(manager)
        if page is None:
            logger.warning("Страница не найдена, переподключаюсь к браузеру...")
            try:
                await manager.disconnect()
                await manager.connect()
                page = _find_ads_manager_page(manager)
            except Exception as reconnect_err:
                return False, f"Не удалось переподключиться к браузеру: {reconnect_err}"
        if page is None:
            return False, "Не найдена страница браузера после переподключения"
        logger.info("Disable: используем страницу %s", (page.url or "")[:80])

        toggle_cell_selector = (
            f'[data-surface*="table_row:{fb_ad_id}"][data-surface*="forObjectType(toggle"]'
        )

        # Шаг 1: ищем ячейку с тогглом (с прокруткой если нет в DOM)
        found_cell = await page.query_selector(toggle_cell_selector)
        if found_cell is None:
            logger.info("Объявление %s не в DOM, прокручиваю таблицу...", fb_ad_id)
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(random.uniform(0.3, 0.6))
            found_cell = await human_scroll_to_find(page, toggle_cell_selector, max_steps=30)

        if found_cell is None:
            return False, f"Строка с Ad ID {fb_ad_id} не найдена в таблице после прокрутки"

        # Шаг 2: находим реальный переключатель объявления.
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

        # Шаг 3: уже выключено — не кликаем.
        if initial_checked == "false":
            return True, f"Объявление уже отключено (aria-checked={initial_checked})"
        if initial_checked != "true":
            return (
                False,
                f"Не удалось однозначно определить состояние переключателя: aria-checked={initial_checked or 'null'}",
            )

        # Шаг 4: один надёжный клик по переключателю.
        try:
            await human_click(page, toggle, double_check_pause=True)
        except Exception:
            logger.debug(
                "human_click не сработал, пробую обычный клик по переключателю",
                exc_info=True,
            )
            await toggle.click(timeout=3000)

        await asyncio.sleep(random.uniform(1.5, 2.0))

        # Шаг 5: проверяем диалог подтверждения (кнопка «Опубликовать»)
        dialog_confirmed = await _confirm_dialog_if_present(page)
        if dialog_confirmed:
            await asyncio.sleep(random.uniform(0.5, 1.0))

        # Шаг 6: проверяем результат через расширенное окно подтверждения.
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

    except Exception as e:
        logger.exception("Ошибка Playwright при отключении %s", fb_ad_id)
        try:
            screenshot_path = f"/tmp/disable_error_{fb_ad_id}.png"
            await page.screenshot(path=screenshot_path)
            logger.error("Скриншот ошибки: %s", screenshot_path)
        except Exception:
            pass
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
            task.next_retry_at = None
            task.last_error = None

            snap_result = await session.execute(
                select(AdSnapshot).where(AdSnapshot.fb_ad_id == task.fb_ad_id)
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
            task.status = DisableTaskStatus.FAILED
            task.last_error = error[:500]
            task.completed_at = datetime.now(UTC)
            await session.commit()


async def main() -> None:
    """Запуск disable worker."""
    vision_x_token, vision_api_url, vision_profile_id = await _load_vision_settings()
    settings = get_settings()

    if not vision_x_token or not vision_profile_id:
        logger.error("Не заданы Vision-настройки ни в БД, ни в .env")
        sys.exit(1)

    vision = VisionClient(
        x_token=vision_x_token,
        base_url=vision_api_url,
    )
    manager = VisionBrowserManager(
        vision_client=vision,
        profile_id=vision_profile_id,
    )

    shutdown_event = asyncio.Event()

    def _handle_signal(signum, frame):
        logger.info("Получен сигнал %s — завершаем disable worker", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        await manager.connect()
        logger.info("Disable worker подключён к Vision")

        from apps.disable_worker.main import disable_worker_loop

        await disable_worker_loop(
            poll_interval_seconds=5,
            claim_next_task=claim_next_task,
            execute_disable=lambda fb_ad_id: execute_disable_via_playwright(manager, fb_ad_id),
            mark_succeeded=mark_succeeded,
            mark_retrying=mark_retrying,
            mark_failed=mark_failed,
            telegram_bot_token=settings.telegram_bot_token,
            telegram_chat_id=settings.telegram_chat_id,
            shutdown_event=shutdown_event,
        )
    except KeyboardInterrupt:
        logger.info("Disable worker остановлен по Ctrl+C")
    finally:
        await manager.disconnect()
        await vision.close()
        logger.info("Disable worker: ресурсы освобождены")


if __name__ == "__main__":
    _PID_FILE = pathlib.Path("/tmp/fb_disable_worker.pid")
    if _PID_FILE.exists():
        _old_pid = int(_PID_FILE.read_text().strip())
        try:
            os.kill(_old_pid, 0)
            logger.error(
                "Disable worker уже запущен (PID %s). Запуск второго экземпляра запрещён.", _old_pid
            )
            sys.exit(1)
        except ProcessLookupError:
            pass
    _PID_FILE.write_text(str(os.getpid()))
    try:
        asyncio.run(main())
    finally:
        _PID_FILE.unlink(missing_ok=True)
