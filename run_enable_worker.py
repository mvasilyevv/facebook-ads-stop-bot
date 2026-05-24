# -*- coding: utf-8 -*-
"""Точка входа: запускает enable worker с подключением к browser-agent через gRPC."""

from __future__ import annotations

import asyncio
import logging
import pathlib
import signal
import sys
from datetime import UTC, datetime

from core.logging import setup_logging
from core.pubsub import CHANNEL_TASK_CHANGED, RedisPubSub
from core.task_queue.grpc_worker_mixin import (
    close_grpc_client,
    init_grpc_client,
    load_vision_settings,
)
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from clients.python_grpc.client import BrowserAgentClient
from core.browser.lock import acquire_browser_lock
from core.config import get_settings
from core.db import get_session_factory
from core.domain import AlertStage, AlertState, DisableTaskStatus, EnableTaskStatus
from core.enable_tasks import reconcile_enable_tasks
from core.models import AdSnapshot, DisableTask, EnableTask
from core.observer.runtime_status import update_worker_heartbeat  # noqa: F401 (тесты патчат)
from core.sentry import setup_sentry
from core.task_queue import PostgresTaskQueue
from core.telegram.client import TelegramBotClient
from core.telegram.delivery import broadcast_enable_task_runtime_message
from core.worker_utils import PidFileLock, wait_for_shutdown_or_timeout

setup_logging("enable_worker")
logger = logging.getLogger(__name__)
VISION_SETTINGS_POLL_INTERVAL_SECONDS = 5

# Параметры подтверждения для enable
ENABLE_CONFIRMATION_POLL_DELAYS_SECONDS = (0.0, 3.0, 3.0, 3.0, 4.0, 4.0)
ENABLE_CONFIRMATION_TRUE_READS_REQUIRED = 2
ENABLE_CONFIRMATION_WINDOW_SECONDS = int(sum(ENABLE_CONFIRMATION_POLL_DELAYS_SECONDS))
ENABLE_BROWSER_TASK_TIMEOUT_SECONDS = 60
ENABLE_SINGLE_SEARCH_MAX_SCROLL_PASSES = 120
ENABLE_BROWSER_LOCK_TIMEOUT_SECONDS = 180.0

# Общая очередь задач на включение
_enable_queue = PostgresTaskQueue(
    model_class=EnableTask,
    status_enum=EnableTaskStatus,
    eager_loads=[EnableTask.fb_ad],
)


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
        # Disable-задачи имеют приоритет — enable ждёт освобождения браузера
        disable_count = await session.scalar(
            select(func.count())
            .select_from(DisableTask)
            .where(
                or_(
                    DisableTask.status.in_((DisableTaskStatus.PENDING, DisableTaskStatus.RUNNING)),
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
                    DisableTask.status.in_((DisableTaskStatus.PENDING, DisableTaskStatus.RUNNING)),
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
            if task.status == EnableTaskStatus.CANCELLED:
                logger.info(
                    "Задача %s уже отменена как неактуальная — пропускаю mark_succeeded",
                    task.id,
                )
                return
            await _enable_queue.mark_succeeded(session, task)

            # После успешного включения сбрасываем alert_state снэпшота: объявление снова крутится.
            # FSM формально не предусматривает переход из DISABLED обратно, но включение — это
            # естественный сброс терминального состояния, поэтому пишем поле напрямую.
            snap_result = await session.execute(
                select(AdSnapshot).where(AdSnapshot.ad_id == task.ad_id)
            )
            snapshot = snap_result.scalar_one_or_none()
            if snapshot and snapshot.alert_state in (
                AlertState.DISABLED,
                AlertState.CLAIMED,
                AlertState.STOP_SENT,
                AlertState.WARNING_SENT,
            ):
                snapshot.alert_state = AlertState.NORMAL
                snapshot.open_state_token = None

            await session.commit()


async def mark_retrying(task_id, error: str, next_retry_at: datetime) -> None:
    """Помечает задачу включения для повторной попытки."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(EnableTask).where(EnableTask.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            if task.status == EnableTaskStatus.CANCELLED:
                logger.info(
                    "Задача %s уже отменена как неактуальная — пропускаю mark_retrying",
                    task.id,
                )
                return
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
            if task.status == EnableTaskStatus.CANCELLED:
                logger.info(
                    "Задача %s уже отменена как неактуальная — пропускаю mark_failed",
                    task.id,
                )
                return
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

    # Публикуем событие в шину для WS-дашборда
    try:
        _pubsub = RedisPubSub(get_settings().redis_url)
        await _pubsub.publish(
            CHANNEL_TASK_CHANGED,
            {
                "type": "task_changed",
                "task_kind": "enable",
                "task_id": str(task_row.id),
                "fb_ad_id": fb_ad.fb_ad_id if fb_ad else "",
                "status": status,
            },
        )
        await _pubsub.close()
    except Exception:
        logger.debug("Enable worker: не удалось опубликовать task_changed", exc_info=True)


async def _process_enable_task_result(
    *,
    task,
    success: bool,
    message: str,
    tg_client,
    tg_chat_id: str,
    send_completion_callback=None,
) -> None:
    """Фиксирует итог enable-задачи (обёртка для обратной совместимости с тестами).

    Берёт mark_* из текущего модульного пространства, чтобы monkeypatch работал.
    """
    from apps.enable_worker.main import _process_enable_task_result as _inner

    await _inner(
        task=task,
        success=success,
        message=message,
        tg_client=tg_client,
        tg_chat_id=tg_chat_id,
        send_completion_callback=send_completion_callback,
        mark_succeeded=mark_succeeded,
        mark_retrying=mark_retrying,
        mark_failed=mark_failed,
    )


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


async def _heartbeat_loop(status_ref: list[str], message_ref: list[str | None]) -> None:
    """Фоновая задача: отправляет heartbeat enable worker каждые 30 секунд.

    Использует update_worker_heartbeat из пространства текущего модуля,
    чтобы тесты могли патчить run_enable_worker.update_worker_heartbeat.
    """
    _HEARTBEAT_INTERVAL = 30
    while True:
        await update_worker_heartbeat(
            "enable",
            status=status_ref[0],
            message=message_ref[0],
        )
        await asyncio.sleep(_HEARTBEAT_INTERVAL)


async def enable_worker_loop(
    client,
    tg_client,
    tg_chat_id: str,
    poll_interval: int = 5,
    shutdown_event: asyncio.Event | None = None,
    send_completion_callback=None,
    status_ref: list[str] | None = None,
    message_ref: list[str | None] | None = None,
) -> None:
    """Бесконечный цикл обработки задач на включение (обратная совместимость)."""
    from apps.enable_worker.main import enable_worker_loop as _inner_loop

    await _inner_loop(
        client,
        tg_client,
        tg_chat_id,
        poll_interval=poll_interval,
        shutdown_event=shutdown_event,
        send_completion_callback=send_completion_callback,
        status_ref=status_ref,
        message_ref=message_ref,
        claim_next_task=claim_next_task,
        execute_enable=_execute_enable_single_locked,
        mark_succeeded=mark_succeeded,
        mark_retrying=mark_retrying,
        mark_failed=mark_failed,
        cancel_if_alert_blocked=_cancel_enable_task_if_alert_blocked,
        reconnect_browser=_reconnect_browser,
    )


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

    status_ref: list[str] = ["idle"]
    message_ref: list[str | None] = [None]
    heartbeat_task = asyncio.create_task(_heartbeat_loop(status_ref, message_ref))

    try:
        while not shutdown_event.is_set():
            if not await has_claimable_enable_tasks():
                status_ref[0] = "idle"
                message_ref[0] = None
                if await wait_for_shutdown_or_timeout(
                    shutdown_event, VISION_SETTINGS_POLL_INTERVAL_SECONDS
                ):
                    break
                continue

            vision_x_token, vision_api_url, vision_profile_id = await load_vision_settings()
            if not vision_x_token or not vision_profile_id:
                if not waiting_for_vision_logged:
                    logger.info(
                        "Enable worker ждёт Vision-настройки из UI или .env и продолжает работать в фоне"
                    )
                    waiting_for_vision_logged = True
                status_ref[0] = "idle"
                if await wait_for_shutdown_or_timeout(
                    shutdown_event, VISION_SETTINGS_POLL_INTERVAL_SECONDS
                ):
                    break
                continue

            waiting_for_vision_logged = False

            try:
                grpc_client = await init_grpc_client(
                    vision_x_token,
                    vision_api_url,
                    vision_profile_id,
                    worker_name="enable_worker",
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
                    shutdown_event, VISION_SETTINGS_POLL_INTERVAL_SECONDS
                ):
                    break
            finally:
                await close_grpc_client(grpc_client, worker_name="enable_worker")
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
