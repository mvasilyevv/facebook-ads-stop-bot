# -*- coding: utf-8 -*-
"""Точка входа: запускает disable worker с подключением к Node.js browser-agent через gRPC."""

from __future__ import annotations

import asyncio
import logging
import pathlib
import signal
import sys
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from clients.python_grpc.client import BrowserAgentClient
from core.browser.lock import acquire_browser_lock
from core.config import get_settings
from core.db import get_session_factory
from core.disable_tasks import is_delivery_disabled, reconcile_disable_tasks
from core.domain import AlertState, DisableTaskStatus
from core.logging import setup_logging
from core.models import AdSnapshot, DisableTask
from core.pubsub import CHANNEL_TASK_CHANGED, RedisPubSub
from core.sentry import setup_sentry
from core.task_queue import PostgresTaskQueue
from core.task_queue.grpc_worker_mixin import (
    close_grpc_client,
    init_grpc_client,
    load_vision_settings,
)
from core.telegram.delivery import broadcast_disable_task_runtime_message
from core.worker_utils import PidFileLock, wait_for_shutdown_or_timeout

setup_logging("disable_worker")
logger = logging.getLogger(__name__)
VISION_SETTINGS_POLL_INTERVAL_SECONDS = 1

# Параметры поиска, клика и повторной проверки для disable
DISABLE_BATCH_SIZE = 10
DISABLE_BATCH_MAX_SCROLL_PASSES = 50
DISABLE_SINGLE_SEARCH_MAX_SCROLL_PASSES = 120
DISABLE_VISIBLE_ROW_TOGGLE_SEARCH_PASSES = 8
DISABLE_MANAGER_DISCONNECT_TIMEOUT_SECONDS = 15
DISABLE_VISION_CLOSE_TIMEOUT_SECONDS = 10
DISABLE_CONFIRMED_DELIVERY_STATUS = "OFF"
DISABLE_ALREADY_OFF_MESSAGE_PREFIX = "Объявление уже отключено"
DISABLE_BROWSER_LOCK_TIMEOUT_SECONDS = 180.0

# Параметры подтверждения для disable: симметрично enable, но реже опрашиваем,
# чтобы быстро ловить откат тумблера (диалоги FB, баги UI).
DISABLE_CONFIRMATION_POLL_DELAYS_SECONDS = (0.0, 2.0, 2.0, 3.0, 3.0)
DISABLE_CONFIRMATION_FALSE_READS_REQUIRED = 2
DISABLE_CONFIRMATION_WINDOW_SECONDS = int(sum(DISABLE_CONFIRMATION_POLL_DELAYS_SECONDS))

# Общая очередь задач на отключение
_disable_queue = PostgresTaskQueue(
    model_class=DisableTask,
    status_enum=DisableTaskStatus,
    eager_loads=[DisableTask.fb_ad],
)


def _is_already_disabled_message(message: str) -> bool:
    """Проверяет, что результат пришёл от уже выключенного тумблера."""
    return message.startswith(DISABLE_ALREADY_OFF_MESSAGE_PREFIX)


async def _close_disable_runtime_resources(grpc_client: BrowserAgentClient | None) -> None:
    """Закрывает gRPC канал и browser-agent сессию с таймаутами."""
    if grpc_client is not None:
        try:
            await asyncio.wait_for(
                grpc_client.disconnect_browser(),
                timeout=DISABLE_MANAGER_DISCONNECT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Disable worker: таймаут %s сек при отключении от browser-agent — продолжаю восстановление",
                DISABLE_MANAGER_DISCONNECT_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.debug(
                "Disable worker: не удалось отключиться от browser-agent",
                exc_info=True,
            )
        try:
            await asyncio.wait_for(
                grpc_client.close(),
                timeout=DISABLE_VISION_CLOSE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Disable worker: таймаут %s сек при закрытии gRPC канала",
                DISABLE_VISION_CLOSE_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.debug(
                "Disable worker: не удалось закрыть gRPC канал",
                exc_info=True,
            )


# Псевдонимы для обратной совместимости с тестами
async def _load_vision_settings() -> tuple[str, str, str]:
    """Загружает Vision-настройки из БД с fallback на .env."""
    return await load_vision_settings()


def _build_client_config(vision_token: str, vision_url: str, vision_profile: str):
    """Создаёт конфигурацию gRPC клиента из Vision настроек.

    Использует get_settings() из текущего модуля, чтобы тесты могли патчить его.
    """
    from clients.python_grpc.client import BrowserAgentConfig

    settings = get_settings()
    return BrowserAgentConfig(
        vision_x_token=vision_token,
        vision_api_url=vision_url,
        vision_profile_id=vision_profile,
        vision_folder_id=getattr(settings, "vision_folder_id", None),
    )


async def _init_grpc_client(
    vision_token: str,
    vision_url: str,
    vision_profile: str,
) -> BrowserAgentClient:
    """Создаёт, запускает и подключает gRPC клиент к browser-agent."""
    return await init_grpc_client(
        vision_token, vision_url, vision_profile, worker_name="disable_worker"
    )


async def claim_next_task():
    """Берёт следующую задачу из очереди (PENDING или RETRYING с наступившим retry)."""
    factory = get_session_factory()
    async with factory() as session:
        now = datetime.now(UTC)
        recovery_summary = await reconcile_disable_tasks(session, now=now)
        if any(recovery_summary.values()):
            await session.commit()

        task = await _disable_queue.claim_next(session)
        if task is None:
            return None

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

        tasks = await _disable_queue.claim_batch(session, limit)
        if not tasks:
            return []

        await session.commit()
        for task in tasks:
            await session.refresh(task)
        return tasks


async def has_claimable_disable_tasks() -> bool:
    """Проверяет, есть ли задачи отключения, ради которых нужно занимать браузер."""
    factory = get_session_factory()
    async with factory() as session:
        now = datetime.now(UTC)
        recovery_summary = await reconcile_disable_tasks(session, now=now)
        if any(recovery_summary.values()):
            await session.commit()

        count = await session.scalar(
            select(func.count())
            .select_from(DisableTask)
            .where(
                or_(
                    DisableTask.status == DisableTaskStatus.PENDING,
                    and_(
                        DisableTask.status == DisableTaskStatus.RETRYING,
                        DisableTask.next_retry_at <= now,
                    ),
                )
            )
        )
    return bool(count)


async def _execute_disable_single(
    client: BrowserAgentClient,
    fb_ad_id: str,
    *,
    reset_table_before_search: bool = True,
    search_max_scroll_passes: int = DISABLE_SINGLE_SEARCH_MAX_SCROLL_PASSES,
    verify_after_click: bool = True,
) -> tuple[bool, str]:
    """Отключает одно объявление через gRPC.

    Шаги:
    1. Найти toggle-ячейку (со скроллом при необходимости).
    2. Проверить aria-checked=true (уже включено).
    3. Вызвать toggle_ad(target_state=False).
    4. Если verify_after_click=True — повторно убедиться через wait_for_toggle_confirmation,
       что тумблер не откатился (диалоги FB, баги UI).
    """
    # Шаг 1: Поиск toggle-ячейки
    find_result = await client.find_toggle_cell(
        fb_ad_id,
        reset_to_top=reset_table_before_search,
        max_scroll_passes=search_max_scroll_passes,
    )

    if not find_result["found"]:
        return False, f"Строка с Ad ID {fb_ad_id} не найдена в таблице"

    # Шаг 2: Проверка текущего состояния
    initial_checked = find_result.get("aria_checked", "")
    if initial_checked not in {"true", "false"}:
        # Делаем отдельное чтение toggle, если быстрый поиск ячейки не смог вернуть aria-checked.
        initial_checked = await client.read_toggle_state(fb_ad_id)

    logger.info(
        "Disable: toggle найден x=%.0f y=%.0f, aria-checked=%s для %s",
        find_result["cell_x"],
        find_result["cell_y"],
        initial_checked or "null",
        fb_ad_id,
    )

    if initial_checked == "false":
        return True, f"{DISABLE_ALREADY_OFF_MESSAGE_PREFIX} (aria-checked={initial_checked})"

    if initial_checked != "true":
        return (
            False,
            f"Не удалось определить состояние переключателя: aria-checked={initial_checked or 'null'}",
        )

    # Шаг 3: Клик по toggle (выключение)
    toggle_result = await client.toggle_ad(fb_ad_id, target_state=False)

    if not toggle_result["success"]:
        return (
            False,
            f"Не удалось переключить toggle: final_state={toggle_result.get('final_state', 'unknown')}",
        )

    final_state = toggle_result.get("final_state", "unknown")
    if final_state != "false":
        return False, f"Интерфейс не подтвердил OFF после клика: aria-checked={final_state}"

    if not verify_after_click:
        # Batch-flow подтверждение оставляет следующему сканеру observer'а — здесь
        # лишний wait тормозил бы пачку.
        return True, "Клик по выключению выполнен, toggle показал OFF"

    # Шаг 4: Post-click verification — повторно читаем aria-checked, чтобы поймать
    # откат тумблера (диалоги FB, баги UI) до следующего скана observer'а.
    confirm_result = await client.wait_for_toggle_confirmation(
        fb_ad_id,
        expected_checked="false",
        required_reads=DISABLE_CONFIRMATION_FALSE_READS_REQUIRED,
        poll_delays_seconds=list(DISABLE_CONFIRMATION_POLL_DELAYS_SECONDS),
        max_scroll_passes_restore=DISABLE_VISIBLE_ROW_TOGGLE_SEARCH_PASSES,
    )

    if confirm_result["success"]:
        return True, "Клик по выключению выполнен, toggle показал OFF"

    return (
        False,
        f"{confirm_result.get('message', 'Интерфейс не подтвердил OFF')} "
        f"(около {DISABLE_CONFIRMATION_WINDOW_SECONDS} сек)",
    )


async def _execute_disable_single_locked(
    client: BrowserAgentClient,
    fb_ad_id: str,
    *,
    reset_table_before_search: bool = True,
) -> tuple[bool, str]:
    """Отключает одно объявление, удерживая общий lock браузера на весь flow."""
    async with acquire_browser_lock(
        owner="disable-worker",
        timeout_seconds=DISABLE_BROWSER_LOCK_TIMEOUT_SECONDS,
    ):
        return await _execute_disable_single(
            client,
            fb_ad_id,
            reset_table_before_search=reset_table_before_search,
        )


async def _execute_disable_batch(
    client: BrowserAgentClient,
    tasks: list[DisableTask],
) -> dict[str, tuple[bool, str]]:
    """Проходит таблицу сверху вниз и отключает все найденные объявления.

    Для каждой задачи:
    1. Ищем toggle через find_toggle_cell (со скроллом).
    2. Если найден и aria-checked=true — переключаем.
    3. Без подтверждений идём дальше по целям.
    4. Финальный delivery_status подтвердит следующий скан наблюдателя.
    """
    results: dict[str, tuple[bool, str]] = {}
    if not tasks:
        return results

    # Группируем задачи по ad_id (могут быть дубликаты)
    tasks_by_ad_id: dict[str, list[DisableTask]] = {}
    for task in tasks:
        ad_id = task.fb_ad.fb_ad_id
        if ad_id not in tasks_by_ad_id:
            tasks_by_ad_id[ad_id] = []
        tasks_by_ad_id[ad_id].append(task)

    remaining_ad_ids = set(tasks_by_ad_id.keys())
    stalled_scroll_passes = 0
    logger.info(
        "Disable batch: начинаю отключение %s задач (%s уникальных объявлений)",
        len(tasks),
        len(remaining_ad_ids),
    )

    # Сброс таблицы перед проходом
    await client.reset_scroll()
    await asyncio.sleep(0.5)

    for pass_num in range(1, DISABLE_BATCH_MAX_SCROLL_PASSES + 1):
        visible_ids = set(await client.get_visible_row_ids())
        visible_targets = [aid for aid in remaining_ad_ids if aid in visible_ids]

        if visible_targets:
            logger.info(
                "Disable batch: проход %s, видно %s целевых объявлений",
                pass_num,
                len(visible_targets),
            )

        for fb_ad_id in visible_targets:
            if fb_ad_id not in remaining_ad_ids:
                continue

            success, message = await _execute_disable_single(
                client,
                fb_ad_id,
                reset_table_before_search=False,
                search_max_scroll_passes=DISABLE_VISIBLE_ROW_TOGGLE_SEARCH_PASSES,
                verify_after_click=False,
            )

            if success:
                result_message = message
                if _is_already_disabled_message(message):
                    result_message = (
                        f"{message}. Финальный delivery_status проверит следующий скан."
                    )
                    logger.info(
                        "Пачка отключения: %s уже OFF по тумблеру, финальную сверку оставляю следующему скану",
                        fb_ad_id,
                    )
                else:
                    logger.info(
                        "Пачка отключения: %s выключен в интерфейсе, финальную сверку оставляю следующему скану",
                        fb_ad_id,
                    )
                for task in tasks_by_ad_id[fb_ad_id]:
                    results[task.id] = (True, result_message)
            else:
                for task in tasks_by_ad_id[fb_ad_id]:
                    results[task.id] = (success, message)
            remaining_ad_ids.discard(fb_ad_id)
            await asyncio.sleep(0.3)

        if not remaining_ad_ids:
            logger.info("Disable batch: все объявления обработаны за %s проходов", pass_num)
            break

        before_scroll_ids = list(visible_ids)

        # Проверяем достигнут ли низ таблицы
        metrics = await client.get_scroll_metrics()
        if metrics.get("at_bottom", False):
            logger.info(
                "Disable batch: достигнут низ, не найдено %s объявлений",
                len(remaining_ad_ids),
            )
            break

        # Скролл вниз
        await client.scroll_and_parse(scroll_amount=320, wait_for_stable=True)
        await asyncio.sleep(1.0)
        after_scroll_ids = await client.get_visible_row_ids()
        if set(after_scroll_ids) == set(before_scroll_ids):
            stalled_scroll_passes += 1
            if stalled_scroll_passes >= 3:
                logger.info("Disable batch: видимые строки перестали меняться, завершаю проход")
                break
        else:
            stalled_scroll_passes = 0

    # Не найденные объявления
    for fb_ad_id in remaining_ad_ids:
        for task in tasks_by_ad_id[fb_ad_id]:
            results[task.id] = (
                False,
                "Объявление не найдено в таблице за проход сверху вниз",
            )

    return results


async def _execute_disable_batch_locked(
    client: BrowserAgentClient,
    tasks: list[DisableTask],
) -> dict[str, tuple[bool, str]]:
    """Отключает пачку объявлений, удерживая общий lock браузера на весь batch."""
    async with acquire_browser_lock(
        owner="disable-worker",
        timeout_seconds=DISABLE_BROWSER_LOCK_TIMEOUT_SECONDS,
    ):
        return await _execute_disable_batch(client, tasks)


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
            await _disable_queue.mark_succeeded(session, task)

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
            await _disable_queue.mark_failed(session, task)
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

    # Публикуем событие в шину для WS-дашборда
    try:
        _pubsub = RedisPubSub(get_settings().redis_url)
        await _pubsub.publish(
            CHANNEL_TASK_CHANGED,
            {
                "type": "task_changed",
                "task_kind": "disable",
                "task_id": str(persisted_task.id),
                "fb_ad_id": fb_ad.fb_ad_id if fb_ad else "",
                "success": success,
                "status": str(persisted_task.status),
            },
        )
        await _pubsub.close()
    except Exception:
        logger.debug("Disable worker: не удалось опубликовать task_changed", exc_info=True)


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
        from apps.disable_worker.main import _heartbeat_loop, disable_worker_loop

        # Heartbeat запускается немедленно — до любой проверки очереди.
        # Это гарантирует, что watchdog не перезапустит воркер при пустой очереди.
        status_ref: list[str] = ["idle"]
        message_ref: list[str | None] = [None]
        heartbeat_task = asyncio.create_task(_heartbeat_loop(status_ref, message_ref))

        while not shutdown_event.is_set():
            if not await has_claimable_disable_tasks():
                if await wait_for_shutdown_or_timeout(
                    shutdown_event,
                    VISION_SETTINGS_POLL_INTERVAL_SECONDS,
                ):
                    break
                continue

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
            grpc_client: BrowserAgentClient | None = None

            try:
                grpc_client = await _init_grpc_client(
                    vision_x_token,
                    vision_api_url,
                    vision_profile_id,
                )

                await disable_worker_loop(
                    poll_interval_seconds=1,
                    claim_next_task=claim_next_task,
                    claim_task_batch=claim_task_batch,
                    execute_disable=lambda fb_ad_id, _client=grpc_client: (
                        _execute_disable_single_locked(
                            _client,  # type: ignore[arg-type]
                            fb_ad_id,
                            reset_table_before_search=True,
                        )
                    ),
                    execute_disable_batch=lambda tasks, _client=grpc_client: (
                        _execute_disable_batch_locked(
                            _client,  # type: ignore[arg-type]
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
                    status_ref=status_ref,
                    message_ref=message_ref,
                )
            except KeyboardInterrupt:
                logger.info("Disable worker остановлен по Ctrl+C")
                break
            except Exception:
                if shutdown_event.is_set():
                    break
                logger.exception("Disable worker: ошибка запуска или подключения к browser-agent")
                if await wait_for_shutdown_or_timeout(
                    shutdown_event,
                    VISION_SETTINGS_POLL_INTERVAL_SECONDS,
                ):
                    break
            finally:
                await close_grpc_client(
                    grpc_client,
                    worker_name="disable_worker",
                    disconnect_timeout=DISABLE_MANAGER_DISCONNECT_TIMEOUT_SECONDS,
                    close_timeout=DISABLE_VISION_CLOSE_TIMEOUT_SECONDS,
                )
    except KeyboardInterrupt:
        logger.info("Disable worker остановлен по Ctrl+C")
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        logger.info("Disable worker: ресурсы освобождены")


if __name__ == "__main__":
    _PID_FILE = pathlib.Path("/tmp/fb_disable_worker.pid")
    try:
        with PidFileLock(_PID_FILE):
            asyncio.run(main())
    except RuntimeError as e:
        logger.error("%s", e)
        sys.exit(1)
