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

from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig, ScanResult
from core.config import get_settings
from core.crypto import decrypt
from core.db import get_session_factory
from core.disable_tasks import is_delivery_disabled, reconcile_disable_tasks
from core.domain import AlertState, DisableTaskStatus
from core.models import AdSnapshot, DisableTask, VisionSettings
from core.sentry import setup_sentry
from core.task_queue import PostgresTaskQueue
from core.telegram.delivery import broadcast_disable_task_runtime_message
from core.worker_utils import PidFileLock, wait_for_shutdown_or_timeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
VISION_SETTINGS_POLL_INTERVAL_SECONDS = 5

# Параметры поиска, клика и повторной проверки для disable
DISABLE_BATCH_SIZE = 10
DISABLE_BATCH_MAX_SCROLL_PASSES = 50
DISABLE_SINGLE_SEARCH_MAX_SCROLL_PASSES = 120
DISABLE_MANAGER_DISCONNECT_TIMEOUT_SECONDS = 15
DISABLE_VISION_CLOSE_TIMEOUT_SECONDS = 10
DISABLE_APPLY_DELAY_SECONDS = 3.0
DISABLE_VERIFY_SCAN_MAX_SCROLL_PASSES = 80
DISABLE_VERIFY_TOGGLE_POLL_DELAYS_SECONDS = (0.0, 1.0, 1.0, 2.0)
DISABLE_CONFIRMED_DELIVERY_STATUS = "OFF"
DISABLE_ALREADY_OFF_MESSAGE_PREFIX = "Объявление уже отключено"

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
) -> tuple[bool, str]:
    """Отключает одно объявление через gRPC.

    Шаги:
    1. Найти toggle-ячейку (со скроллом при необходимости).
    2. Проверить aria-checked=true (уже включено).
    3. Вызвать toggle_ad(target_state=False).
    4. Подождать применения toggle без нажатия publish/confirm.
    5. Быстро подтвердить OFF через aria-checked.
    """
    # Шаг 1: Поиск toggle-ячейки
    find_result = await client.find_toggle_cell(
        fb_ad_id,
        reset_to_top=reset_table_before_search,
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

    # Шаг 4: Ads Manager применяет toggle через несколько секунд, отдельное подтверждение не нужно.
    await asyncio.sleep(DISABLE_APPLY_DELAY_SECONDS)

    # Шаг 5: Быстро проверяем aria-checked, а финальный статус подтвердит повторный скан пачки.
    confirm_result = await client.wait_for_toggle_confirmation(
        fb_ad_id,
        expected_checked="false",
        required_reads=1,
        poll_delays_seconds=[0.0, 1.0, 1.0],
        max_scroll_passes_restore=DISABLE_SINGLE_SEARCH_MAX_SCROLL_PASSES,
    )

    if confirm_result["success"]:
        return True, "Клик по выключению выполнен, toggle показал OFF"

    return (
        False,
        confirm_result.get("message", "Интерфейс не подтвердил OFF после клика"),
    )


async def _scan_rows_for_disable_verification(client: BrowserAgentClient) -> dict[str, object]:
    """Запускает повторный scan после кликов и возвращает строки по fb_ad_id."""
    rows_by_ad_id: dict[str, object] = {}
    async for event in client.run_scan_cycle(
        max_scroll_passes=DISABLE_VERIFY_SCAN_MAX_SCROLL_PASSES,
        do_refresh=True,
        reset_scroll_first=True,
        settle_delay_seconds=2.0,
    ):
        if isinstance(event, ScanResult):
            rows_by_ad_id = {row.fb_ad_id: row for row in event.rows}
    return rows_by_ad_id


async def _mark_snapshot_disabled_from_verification(fb_ad_id: str, delivery_status: str) -> None:
    """Обновляет UI-снимок после повторного скана, подтвердившего отключение."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(AdSnapshot).where(AdSnapshot.fb_ad_id == fb_ad_id))
        snapshot = result.scalar_one_or_none()
        if snapshot:
            snapshot.delivery_status = delivery_status
            snapshot.alert_state = AlertState.DISABLED
            snapshot.last_observed_at = datetime.now(UTC)
            await session.commit()


async def _execute_disable_batch(
    client: BrowserAgentClient,
    tasks: list[DisableTask],
) -> dict[str, tuple[bool, str]]:
    """Проходит таблицу сверху вниз и отключает все найденные объявления.

    Для каждой задачи:
    1. Ищем toggle через find_toggle_cell (со скроллом).
    2. Если найден и aria-checked=true — переключаем.
    3. Без подтверждений идём дальше по целям.
    4. После пачки запускаем повторный скан и подтверждаем OFF по toggle,
       а delivery_status используем как дополнительную синхронизацию снимка.
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
    pending_verify_ad_ids: set[str] = set()
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
            )

            if success:
                if _is_already_disabled_message(message):
                    await _mark_snapshot_disabled_from_verification(
                        fb_ad_id,
                        DISABLE_CONFIRMED_DELIVERY_STATUS,
                    )
                    for task in tasks_by_ad_id[fb_ad_id]:
                        results[task.id] = (True, message)
                    logger.info(
                        "Disable batch: %s уже OFF, повторный скан для задачи не нужен",
                        fb_ad_id,
                    )
                else:
                    pending_verify_ad_ids.add(fb_ad_id)
                    logger.info(
                        "Disable batch: %s выключен в UI, финально проверим повторным сканом",
                        fb_ad_id,
                    )
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

    if pending_verify_ad_ids:
        logger.info(
            "Disable batch: запускаю повторный скан для проверки %s отключённых объявлений",
            len(pending_verify_ad_ids),
        )
        rows_by_ad_id = await _scan_rows_for_disable_verification(client)
        for fb_ad_id in pending_verify_ad_ids:
            row = rows_by_ad_id.get(fb_ad_id)
            toggle_result = await client.wait_for_toggle_confirmation(
                fb_ad_id,
                expected_checked="false",
                required_reads=1,
                poll_delays_seconds=list(DISABLE_VERIFY_TOGGLE_POLL_DELAYS_SECONDS),
                max_scroll_passes_restore=DISABLE_SINGLE_SEARCH_MAX_SCROLL_PASSES,
            )
            delivery_status = getattr(row, "delivery_status", "") if row is not None else ""

            if row is None:
                result = (False, "После клика объявление не найдено при повторном скане")
            elif not toggle_result.get("success", False):
                result = (
                    False,
                    "Повторная проверка не подтвердила OFF по toggle: "
                    f"aria-checked={toggle_result.get('final_aria_checked', 'unknown')}",
                )
            elif is_delivery_disabled(delivery_status):
                await _mark_snapshot_disabled_from_verification(
                    fb_ad_id,
                    delivery_status,
                )
                result = (True, "Объявление отключено: toggle OFF и delivery_status подтверждены")
            else:
                # Meta иногда держит промежуточный delivery_status дольше, чем сам toggle,
                # поэтому считаем задачу успешно выключенной, но явно логируем лаг статуса.
                logger.info(
                    "Disable batch: %s подтверждён OFF по toggle, но delivery_status пока=%s",
                    fb_ad_id,
                    delivery_status or "пусто",
                )
                result = (
                    True,
                    "Toggle подтверждён в OFF, но delivery_status ещё не обновился: "
                    f"{delivery_status or 'пусто'}",
                )
            for task in tasks_by_ad_id[fb_ad_id]:
                results[task.id] = result

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
                    poll_interval_seconds=5,
                    claim_next_task=claim_next_task,
                    claim_task_batch=claim_task_batch,
                    execute_disable=lambda fb_ad_id, _client=grpc_client: _execute_disable_single(
                        _client,  # type: ignore[arg-type]
                        fb_ad_id,
                        reset_table_before_search=True,
                    ),
                    execute_disable_batch=lambda tasks, _client=grpc_client: _execute_disable_batch(
                        _client,  # type: ignore[arg-type]
                        tasks,
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
                logger.exception("Disable worker: ошибка запуска или подключения к browser-agent")
                if await wait_for_shutdown_or_timeout(
                    shutdown_event,
                    VISION_SETTINGS_POLL_INTERVAL_SECONDS,
                ):
                    break
            finally:
                await _close_disable_runtime_resources(grpc_client)
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
