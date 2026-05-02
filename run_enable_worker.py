# -*- coding: utf-8 -*-
"""Точка входа: запускает enable worker с подключением к browser-agent через gRPC."""

from __future__ import annotations

import asyncio
import logging
import pathlib
import signal
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
from core.browser.lock import acquire_browser_lock
from core.config import get_settings
from core.crypto import decrypt
from core.db import get_session_factory
from core.domain import AlertStage, AlertState, DisableTaskStatus, EnableTaskStatus
from core.enable_tasks import reconcile_enable_tasks
from core.models import AdSnapshot, DisableTask, EnableTask, VisionSettings
from core.observer.runtime_status import update_worker_heartbeat
from core.sentry import setup_sentry
from core.task_queue import PostgresTaskQueue
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
HEARTBEAT_INTERVAL_SECONDS = 30

# Параметры подтверждения для enable
ENABLE_CONFIRMATION_POLL_DELAYS_SECONDS = (0.0, 3.0, 3.0, 3.0, 4.0, 4.0)
ENABLE_CONFIRMATION_TRUE_READS_REQUIRED = 2
ENABLE_CONFIRMATION_WINDOW_SECONDS = int(sum(ENABLE_CONFIRMATION_POLL_DELAYS_SECONDS))
ENABLE_BROWSER_TASK_TIMEOUT_SECONDS = 60
ENABLE_SINGLE_SEARCH_MAX_SCROLL_PASSES = 120
ENABLE_BROWSER_LOCK_TIMEOUT_SECONDS = 180.0

# Ошибки gRPC, указывающие на потерю соединения с браузером
_GRPC_CONNECTION_ERROR_MARKERS = (
    "unavailable",
    "connection refused",
    "connection closed",
    "connection reset",
    "transport closed",
    "goaway",
    "stream closed",
    "deadline exceeded",
)


async def _heartbeat_loop(status_ref: list[str], message_ref: list[str | None]) -> None:
    """Фоновая задача: отправляет heartbeat enable worker каждые 30 секунд."""
    while True:
        await update_worker_heartbeat(
            "enable",
            status=status_ref[0],
            message=message_ref[0],
        )
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


# Общая очередь задач на включение
_enable_queue = PostgresTaskQueue(
    model_class=EnableTask,
    status_enum=EnableTaskStatus,
    eager_loads=[EnableTask.fb_ad],
)


def _is_grpc_connection_error(exc: Exception) -> bool:
    """Определяет, относится ли ошибка к потере соединения с browser-agent."""
    if isinstance(exc, (ConnectionError, OSError)):
        return True
    message = str(exc).casefold()
    return any(marker in message for marker in _GRPC_CONNECTION_ERROR_MARKERS)


def _build_browser_runtime_error_message(exc: Exception) -> str:
    """Формирует текст ошибки браузерной операции для retry-задачи."""
    detail = str(exc).strip()
    if not detail:
        return "Браузерная операция включения завершилась ошибкой"
    return f"Браузерная операция включения завершилась ошибкой: {detail}"


def _build_client_config(
    vision_token: str, vision_url: str, vision_profile: str
) -> BrowserAgentConfig:
    """Создаёт конфигурацию gRPC клиента из Vision настроек."""
    settings = get_settings()
    return BrowserAgentConfig(
        vision_x_token=vision_token,
        vision_api_url=vision_url,
        vision_profile_id=vision_profile,
        vision_folder_id=settings.vision_folder_id
        if hasattr(settings, "vision_folder_id")
        else None,
    )


async def _init_grpc_client(
    vision_token: str,
    vision_url: str,
    vision_profile: str,
) -> BrowserAgentClient:
    """Создаёт, запускает и подключает gRPC клиент к browser-agent."""
    config = _build_client_config(vision_token, vision_url, vision_profile)
    client = BrowserAgentClient(config)
    await client.start()
    await client.start_browser()
    logger.info("gRPC клиент подключён, session_id=%s", client.session_id)
    return client


async def _close_runtime_resources(grpc_client: BrowserAgentClient | None) -> None:
    """Закрывает gRPC клиент с таймаутами."""
    if grpc_client is not None:
        try:
            await asyncio.wait_for(grpc_client.disconnect_browser(), timeout=15)
        except (asyncio.TimeoutError, Exception):
            logger.debug("Enable worker: не удалось отключиться от browser-agent", exc_info=True)
        try:
            await asyncio.wait_for(grpc_client.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            logger.debug("Enable worker: не удалось закрыть gRPC канал", exc_info=True)


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


async def _reconnect_browser(client: BrowserAgentClient) -> str:
    """Переподключает gRPC клиент к браузеру."""
    logger.warning("Enable worker: переподключаюсь к browser-agent...")
    new_session = await client.reconnect_browser()
    logger.info("Enable worker: переподключён, session_id=%s", new_session)
    return new_session


async def claim_next_task():
    """Берёт следующую задачу включения из очереди (PENDING или RETRYING с наступившим retry)."""
    factory = get_session_factory()
    async with factory() as session:
        now = datetime.now(UTC)
        disable_count = await session.scalar(
            select(func.count())
            .select_from(DisableTask)
            .where(
                or_(
                    DisableTask.status.in_(
                        (
                            DisableTaskStatus.PENDING,
                            DisableTaskStatus.RUNNING,
                        )
                    ),
                    and_(
                        DisableTask.status == DisableTaskStatus.RETRYING,
                        DisableTask.next_retry_at <= now,
                    ),
                )
            )
        )
        if disable_count:
            logger.info(
                "Enable worker: задачи отключения имеют приоритет, включение ждёт освобождения браузера"
            )
            return None

        recovery_summary = await reconcile_enable_tasks(session, now=now)
        if any(recovery_summary.values()):
            await session.commit()

        task = await _enable_queue.claim_next(session)
        if task is None:
            return None

        await session.commit()
        await session.refresh(task)
        return task


async def has_claimable_enable_tasks() -> bool:
    """Проверяет, есть ли задачи включения, ради которых нужно занимать браузер."""
    factory = get_session_factory()
    async with factory() as session:
        now = datetime.now(UTC)
        disable_count = await session.scalar(
            select(func.count())
            .select_from(DisableTask)
            .where(
                or_(
                    DisableTask.status.in_(
                        (
                            DisableTaskStatus.PENDING,
                            DisableTaskStatus.RUNNING,
                        )
                    ),
                    and_(
                        DisableTask.status == DisableTaskStatus.RETRYING,
                        DisableTask.next_retry_at <= now,
                    ),
                )
            )
        )
        if disable_count:
            return False

        recovery_summary = await reconcile_enable_tasks(session, now=now)
        if any(recovery_summary.values()):
            await session.commit()

        count = await session.scalar(
            select(func.count())
            .select_from(EnableTask)
            .where(
                or_(
                    EnableTask.status == EnableTaskStatus.PENDING,
                    and_(
                        EnableTask.status == EnableTaskStatus.RETRYING,
                        EnableTask.next_retry_at <= now,
                    ),
                )
            )
        )
    return bool(count)


async def _execute_enable_single(
    client: BrowserAgentClient,
    fb_ad_id: str,
) -> tuple[bool, str]:
    """Включает одно объявление через gRPC.

    Шаги:
    1. Найти toggle-ячейку (со скроллом при необходимости).
    2. Проверить aria-checked=false (уже выключено).
    3. Вызвать toggle_ad(target_state=True).
    4. Подождать применения toggle.
    5. Подождать подтверждения ON через polling aria-checked.
    """
    # Шаг 1: Поиск toggle-ячейки
    find_result = await client.find_toggle_cell(
        fb_ad_id,
        reset_to_top=True,
        max_scroll_passes=ENABLE_SINGLE_SEARCH_MAX_SCROLL_PASSES,
    )

    if not find_result["found"]:
        return False, f"Строка с Ad ID {fb_ad_id} не найдена в таблице"

    # Шаг 2: Проверка текущего состояния
    initial_checked = find_result.get("aria_checked", "")
    if initial_checked not in {"true", "false"}:
        initial_checked = await client.read_toggle_state(fb_ad_id)

    logger.info(
        "Enable: toggle найден x=%.0f y=%.0f, aria-checked=%s для %s",
        find_result["cell_x"],
        find_result["cell_y"],
        initial_checked or "null",
        fb_ad_id,
    )

    if initial_checked == "true":
        return True, "Объявление уже включено"

    if initial_checked != "false":
        return (
            False,
            f"Не удалось определить состояние переключателя: aria-checked={initial_checked or 'null'}",
        )

    # Шаг 3: Клик по toggle (включение)
    toggle_result = await client.toggle_ad(fb_ad_id, target_state=True)

    if not toggle_result["success"]:
        return (
            False,
            f"Не удалось переключить toggle: final_state={toggle_result.get('final_state', 'unknown')}",
        )

    # Шаг 4: Пауза после клика. Ads Manager применяет toggle без отдельного подтверждения.
    await asyncio.sleep(3.0)

    # Шаг 5: Подтверждение ON
    confirm_result = await client.wait_for_toggle_confirmation(
        fb_ad_id,
        expected_checked="true",
        required_reads=ENABLE_CONFIRMATION_TRUE_READS_REQUIRED,
        poll_delays_seconds=list(ENABLE_CONFIRMATION_POLL_DELAYS_SECONDS),
        max_scroll_passes_restore=ENABLE_SINGLE_SEARCH_MAX_SCROLL_PASSES,
    )

    if confirm_result["success"]:
        return True, "Объявление включено: переключатель подтвердил состояние ON"

    return (
        False,
        f"{confirm_result.get('message', 'Интерфейс не подтвердил ON')} "
        f"(около {ENABLE_CONFIRMATION_WINDOW_SECONDS} сек)",
    )


async def _execute_enable_single_locked(
    client: BrowserAgentClient,
    fb_ad_id: str,
) -> tuple[bool, str]:
    """Включает объявление, удерживая общий lock браузера на весь flow."""
    async with acquire_browser_lock(
        owner="enable-worker",
        timeout_seconds=ENABLE_BROWSER_LOCK_TIMEOUT_SECONDS,
    ):
        return await _execute_enable_single(client, fb_ad_id)


async def _cancel_enable_task_if_alert_blocked(task_id) -> str | None:
    """Отменяет включение, если свежий snapshot снова показывает предупреждение или стоп."""
    factory = get_session_factory()
    async with factory() as session:
        task = await session.scalar(select(EnableTask).where(EnableTask.id == task_id))
        if task is None:
            return None

        snapshot = await session.scalar(
            select(AdSnapshot)
            .where(AdSnapshot.ad_id == task.ad_id)
            .order_by(AdSnapshot.last_observed_at.desc(), AdSnapshot.created_at.desc())
            .limit(1)
        )
        if snapshot is None:
            return None

        blocked_by_stage = snapshot.current_stage in (AlertStage.WARNING, AlertStage.STOP)
        blocked_by_state = snapshot.alert_state in (
            AlertState.WARNING_SENT,
            AlertState.STOP_SENT,
            AlertState.CLAIMED,
        )
        if not blocked_by_stage and not blocked_by_state:
            return None

        message = "Задача отменена: у объявления снова активен предупреждающий или стоп-сигнал."
        task.status = EnableTaskStatus.CANCELLED
        task.completed_at = datetime.now(UTC)
        task.next_retry_at = None
        task.last_error = message
        await session.commit()
        return message


async def mark_succeeded(task_id) -> None:
    """Помечает задачу включения как успешно выполненную."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(EnableTask).where(EnableTask.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            await _enable_queue.mark_succeeded(session, task)
            await session.commit()


async def mark_retrying(task_id, error: str, next_retry_at: datetime) -> None:
    """Помечает задачу включения для повторной попытки."""
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
    """Помечает задачу включения как окончательно проваленную."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(EnableTask).where(EnableTask.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            await _enable_queue.mark_failed(session, task)
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
        result = await session.execute(
            select(EnableTask)
            .options(selectinload(EnableTask.fb_ad))
            .where(EnableTask.id == task.id)
        )
        persisted_task = result.scalar_one_or_none()

    task_row = persisted_task or task
    fb_ad = task_row.fb_ad
    await broadcast_enable_task_runtime_message(
        ad_name=fb_ad.ad_name if fb_ad else "",
        fb_ad_id=fb_ad.fb_ad_id if fb_ad else "",
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
        logger.info("Объявление %s успешно включено", task.fb_ad.fb_ad_id)
    else:
        attempt = task.attempt_count
        max_attempts = task.max_attempts
        if attempt >= max_attempts:
            await mark_failed(task.id, message)
            status = EnableTaskStatus.FAILED
            logger.error(
                "Задача %s для %s провалена: исчерпаны все %s попыток",
                task.id,
                task.fb_ad.fb_ad_id,
                max_attempts,
            )
        else:
            delay = calculate_retry_delay(attempt)
            next_retry_at = datetime.now(tz=UTC) + timedelta(seconds=delay)
            await mark_retrying(task.id, message, next_retry_at)
            status = EnableTaskStatus.RETRYING
            logger.warning(
                "Не удалось включить %s: %s. Retry через %s сек",
                task.fb_ad.fb_ad_id,
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
                    ad_name=task.fb_ad.ad_name,
                    fb_ad_id=task.fb_ad.fb_ad_id,
                    requested_by_username=task.requested_by_username or "",
                    status=status.value,
                    detail=message,
                    next_retry_at=next_retry_at,
                ),
            )
        except Exception:
            logger.exception("Не удалось отправить уведомление в TG")


async def enable_worker_loop(
    client: BrowserAgentClient,
    tg_client,
    tg_chat_id: str,
    poll_interval: int = 5,
    shutdown_event: asyncio.Event | None = None,
    send_completion_callback=None,
    status_ref: list[str] | None = None,
    message_ref: list[str | None] | None = None,
) -> None:
    """Бесконечный цикл обработки задач на включение."""
    while not (shutdown_event and shutdown_event.is_set()):
        try:
            task = await claim_next_task()
            if task is None:
                if status_ref is not None:
                    status_ref[0] = "idle"
                if message_ref is not None:
                    message_ref[0] = None
                try:
                    if shutdown_event:
                        await asyncio.wait_for(shutdown_event.wait(), timeout=poll_interval)
                        break
                except asyncio.TimeoutError:
                    pass
                continue

            if status_ref is not None:
                status_ref[0] = "busy"
            if message_ref is not None:
                message_ref[0] = f"Задача {task.id} для {task.fb_ad.fb_ad_id}"

            logger.info(
                "Enable worker: выполняю задачу %s для объявления %s",
                task.id,
                task.fb_ad.fb_ad_id,
            )

            blocked_message = await _cancel_enable_task_if_alert_blocked(task.id)
            if blocked_message:
                logger.warning(
                    "Enable worker: задача %s для %s отменена перед включением: %s",
                    task.id,
                    task.fb_ad.fb_ad_id,
                    blocked_message,
                )
                await _send_enable_task_runtime_update(
                    task,
                    status=EnableTaskStatus.CANCELLED.value,
                    detail=blocked_message,
                )
                continue

            try:
                success, message = await asyncio.wait_for(
                    _execute_enable_single_locked(client, task.fb_ad.fb_ad_id),
                    timeout=ENABLE_BROWSER_TASK_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                timeout_message = (
                    f"Браузерная операция включения превысила таймаут "
                    f"{ENABLE_BROWSER_TASK_TIMEOUT_SECONDS} сек"
                )
                logger.error(
                    "Enable worker: задача %s для %s зависла дольше %s сек, переподключаю браузер",
                    task.id,
                    task.fb_ad.fb_ad_id,
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
                # Переподключаем браузер при таймауте
                await _reconnect_browser(client)
                continue
            except Exception as exc:
                runtime_message = _build_browser_runtime_error_message(exc)
                logger.error(
                    "Enable worker: задача %s для %s завершилась ошибкой, переподключаю браузер",
                    task.id,
                    task.fb_ad.fb_ad_id,
                    exc_info=True,
                )
                await _process_enable_task_result(
                    task=task,
                    success=False,
                    message=runtime_message,
                    tg_client=tg_client,
                    tg_chat_id=tg_chat_id,
                    send_completion_callback=send_completion_callback,
                )
                await _reconnect_browser(client)
                continue

            await _process_enable_task_result(
                task=task,
                success=success,
                message=message,
                tg_client=tg_client,
                tg_chat_id=tg_chat_id,
                send_completion_callback=send_completion_callback,
            )

        except Exception as exc:
            if _is_grpc_connection_error(exc):
                logger.error(
                    "Enable worker: потеряно соединение с browser-agent, нужен reconnect: %s",
                    exc,
                )
                try:
                    await _reconnect_browser(client)
                except Exception:
                    logger.exception("Enable worker: не удалось переподключить browser-agent")
                    await asyncio.sleep(poll_interval)
                continue
            logger.exception("Enable worker: ошибка в цикле")
            await asyncio.sleep(poll_interval)


async def main() -> None:
    """Запуск enable worker."""
    settings = get_settings()
    setup_sentry(dsn=settings.sentry_dsn, environment=settings.sentry_environment)
    tg_client = None
    if settings.telegram_bot_token and settings.telegram_chat_id:
        tg_client = TelegramBotClient(settings.telegram_bot_token)

    shutdown_event = asyncio.Event()
    waiting_for_vision_logged = False

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, shutdown_event.set)
    loop.add_signal_handler(signal.SIGINT, shutdown_event.set)

    grpc_client: BrowserAgentClient | None = None

    # Разделяемый статус для heartbeat-задачи
    status_ref: list[str] = ["idle"]
    message_ref: list[str | None] = [None]
    heartbeat_task = asyncio.create_task(_heartbeat_loop(status_ref, message_ref))

    try:
        while not shutdown_event.is_set():
            if not await has_claimable_enable_tasks():
                status_ref[0] = "idle"
                message_ref[0] = None
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
                        "Enable worker ждёт Vision-настройки из UI или .env и продолжает работать в фоне"
                    )
                    waiting_for_vision_logged = True
                status_ref[0] = "idle"
                if await wait_for_shutdown_or_timeout(
                    shutdown_event,
                    VISION_SETTINGS_POLL_INTERVAL_SECONDS,
                ):
                    break
                continue

            waiting_for_vision_logged = False

            try:
                grpc_client = await _init_grpc_client(
                    vision_x_token,
                    vision_api_url,
                    vision_profile_id,
                )

                await enable_worker_loop(
                    client=grpc_client,
                    tg_client=tg_client,
                    tg_chat_id=settings.telegram_chat_id,
                    shutdown_event=shutdown_event,
                    status_ref=status_ref,
                    message_ref=message_ref,
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
                logger.exception("Enable worker: ошибка запуска или подключения к browser-agent")
                if await wait_for_shutdown_or_timeout(
                    shutdown_event,
                    VISION_SETTINGS_POLL_INTERVAL_SECONDS,
                ):
                    break
            finally:
                await _close_runtime_resources(grpc_client)
                grpc_client = None
    except KeyboardInterrupt:
        logger.info("Enable worker остановлен по Ctrl+C")
    finally:
        heartbeat_task.cancel()
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
