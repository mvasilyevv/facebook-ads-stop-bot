# -*- coding: utf-8 -*-
"""Observer Worker: основной цикл — refresh → scroll → parse → evaluate → notify.

Единственный worker, который взаимодействует с Playwright.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import random
import time as _time
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from core.browser.humanizer import human_move, human_wheel_scroll
from core.browser.vision_client import VisionClient
from core.db import get_session_factory
from core.diagnostics import (
    build_ad_quality_diagnostics,
    build_diagnostics_context_text,
    compute_cpm_baselines_by_offer,
)
from core.disable_tasks import is_delivery_disabled
from core.domain import AlertStage, AlertState
from core.models import AdSnapshot, AlertEvent, FbAd
from core.observer.db_queries import (
    check_scan_requested_flag,
    check_scanning_enabled,
    check_vision_reconnect_flag,
    collect_reminder_alerts,
    get_disable_queue_pause_reason,
    load_ad_states_from_db,
    load_fake_deposits,
    load_observer_settings_from_db,
    load_offers_from_db,
    load_telegram_settings_from_db,
    load_vision_settings_for_runtime,
    refresh_runtime_ad_states,
    set_observer_scanning_enabled,
)
from core.observer.disable_reconciler import (
    auto_create_disable_tasks,
    reconcile_disable_incidents_after_scan,
    reconcile_disable_tasks_in_db,
)
from core.observer.runtime_status import (
    format_observer_runtime_message,
    update_observer_runtime_status,
)
from core.observer.scan_guard import ZeroScanGuard
from core.observer.service import (
    AlertCandidate,
    _compose_reason_text,
    build_metrics_json,
    evaluate_row,
    resolve_offer_code,
)
from core.observer.snapshot_writer import batch_save_snapshots
from core.observer.state_machine import (
    _state_for_emitted_stage,
    reopen_reactivated_alert_state,
    resolve_off_alert_state,
    resolve_transition,
)
from core.observer.thresholds import (
    DEFAULT_STOP_PERCENT_OF_BASE,
    DEFAULT_WARNING_PERCENT_OF_STOP,
    extract_observer_threshold_values,
)
from core.scanner.models import ScannedAdRow
from core.scanner.parser import refresh_table
from core.scanner.recovery import ScanDataUnavailableError, scan_ads_with_page_recovery
from core.telegram.client import TelegramBotClient
from core.telegram.delivery import broadcast_observer_runtime_message
from core.telegram.message_refs import (
    load_message_refs_by_chat,
    stream_for_alert_stage,
    upsert_message_ref,
)
from core.telegram.messaging import safe_edit_or_send_message
from core.telegram.renderer import TelegramAlertItem, render_alert_message

try:
    from patchright.async_api import Error as PatchrightError
except ModuleNotFoundError:  # pragma: no cover - зависит от окружения

    class PatchrightError(RuntimeError):
        """Фолбэк-тип ошибки, когда patchright недоступен в окружении."""


logger = logging.getLogger(__name__)

# Максимальное количество попыток переподключения к браузеру
MAX_RECONNECT_ATTEMPTS = 5
# Таймаут подключения к CDP браузеру (сек)
BROWSER_CONNECT_TIMEOUT_SECONDS = 60
# Базовая задержка для экспоненциального backoff (сек)
BASE_RECONNECT_DELAY = 10
# Пока очередь отключения не опустеет, observer не должен трогать общий браузер.
DISABLE_QUEUE_SCAN_PAUSE_SECONDS = 5.0

_BROWSER_RUNTIME_ERROR_MARKERS = (
    "target page, context or browser has been closed",
    "browser has been closed",
    "browser has disconnected",
    "session closed",
    "connection closed",
    "cdp",
    "websocket",
    "pipe closed",
    "broken pipe",
)

# Инкапсулированный zero-scan guard вместо трёх глобальных переменных
_scan_guard = ZeroScanGuard()


def _is_browser_connection_error(exc: Exception) -> bool:
    """Определяет, относится ли ошибка к обрыву соединения с браузером."""
    if isinstance(exc, (ConnectionError, OSError, PatchrightError)):
        return True
    if not isinstance(exc, RuntimeError):
        return False
    message = str(exc).casefold()
    return any(marker in message for marker in _BROWSER_RUNTIME_ERROR_MARKERS)


def _get_reconnect_delay(attempt_number: int) -> int:
    """Возвращает задержку перед очередной попыткой переподключения."""
    return min(BASE_RECONNECT_DELAY * (2 ** (attempt_number - 1)), 30)


async def _handle_browser_connection_error(
    *,
    exc: Exception,
    attempt_number: int,
    browser_manager,
    page,
):
    """Пытается восстановить соединение с браузером после сетевой ошибки."""
    logger.error(
        "Ошибка связи с браузером (попытка %s/%s): %s",
        attempt_number,
        MAX_RECONNECT_ATTEMPTS,
        exc,
    )

    if attempt_number >= MAX_RECONNECT_ATTEMPTS:
        logger.critical(
            "Превышено максимальное число попыток переподключения (%s). Завершение работы.",
            MAX_RECONNECT_ATTEMPTS,
        )
        raise exc

    delay = _get_reconnect_delay(attempt_number)
    if browser_manager is None:
        await asyncio.sleep(delay)
        return page

    logger.info("Пауза %s сек перед переподключением к браузеру", delay)
    await asyncio.sleep(delay)

    try:
        await browser_manager.disconnect()
        await asyncio.wait_for(
            browser_manager.connect(),
            timeout=BROWSER_CONNECT_TIMEOUT_SECONDS,
        )
        new_page = await browser_manager.get_page()
        logger.info("Успешное переподключение к браузеру")
        return new_page
    except Exception:
        logger.warning("Не удалось переподключиться к браузеру", exc_info=True)
        return page


async def reconnect_browser_manager_with_vision_settings(
    browser_manager,
) -> object | None:
    """Переподключает browser_manager с актуальными Vision-настройками."""
    current_vision = getattr(browser_manager, "_vision", None)
    fallback_x_token = ""
    fallback_api_url = "http://127.0.0.1:3030"
    if current_vision is not None:
        fallback_x_token = getattr(current_vision, "_headers", {}).get("X-Token", "")
        fallback_api_url = getattr(current_vision, "_base", fallback_api_url)
    fallback_profile_id = getattr(browser_manager, "_profile_id", "")

    x_token, api_url, profile_id = await load_vision_settings_for_runtime(
        fallback_x_token=fallback_x_token,
        fallback_api_url=fallback_api_url,
        fallback_profile_id=fallback_profile_id,
    )
    if not x_token or not profile_id:
        logger.warning("Vision-настройки для переподключения не найдены")
        return None

    if current_vision is not None:
        try:
            await browser_manager.disconnect()
        except Exception:
            logger.warning("Не удалось отключить старое Vision-соединение", exc_info=True)
        try:
            await current_vision.close()
        except Exception:
            logger.debug("Не удалось закрыть старый Vision-клиент", exc_info=True)

    # Обновляем настройки через публичный метод вместо прямого доступа к полям
    browser_manager.reconfigure(
        vision_client=VisionClient(x_token=x_token, base_url=api_url),
        profile_id=profile_id,
        folder_id=None,
    )
    await asyncio.wait_for(
        browser_manager.connect(),
        timeout=BROWSER_CONNECT_TIMEOUT_SECONDS,
    )
    logger.info("Vision браузер переподключён с актуальными настройками")
    return await browser_manager.get_page()


def _build_scan_recovery_alert_text(exc: ScanDataUnavailableError) -> str:
    """Формирует Telegram-алерт о фатальной недоступности данных скана."""
    return (
        "🚨 <b>Observer отключён</b>\n\n"
        "Причина: Ads Manager не вернул данные сканирования после "
        f"{exc.attempts} попыток перезагрузки страницы.\n"
        f"Интервал между попытками: {int(exc.retry_interval_seconds)} сек.\n"
        "Сканирование автоматически выключено.\n"
        "Проверьте открытую страницу кабинета и затем включите воркер снова."
    )


async def _update_scan_recovery_status(attempt: int, max_attempts: int) -> None:
    """Пишет в runtime-статус, что observer пытается восстановить данные страницы."""
    await update_observer_runtime_status(
        status="RECOVERING",
        message=(
            "Данные сканирования недоступны. "
            f"Перезагружаем страницу и повторяем попытку {attempt}/{max_attempts}."
        ),
    )


def compute_jitter(interval_seconds: int, jitter_seconds: int) -> float:
    """Вычисляет интервал сна: interval_seconds ± случайный jitter_seconds.

    При interval=45, jitter=4 → результат от 41 до 49 сек.
    Минимум 5 секунд (защита от слишком частого скана).
    """
    offset = random.uniform(-jitter_seconds, jitter_seconds)
    return max(5.0, interval_seconds + offset)


async def _human_micro_pause() -> None:
    """Случайная микропауза 0.5-2 сек между действиями (имитация человека)."""
    await asyncio.sleep(random.uniform(0.5, 2.0))


async def _maybe_macro_pause() -> None:
    """С вероятностью ~15% — макропауза 5-15 сек (имитация отвлечения)."""
    if random.random() < 0.15:
        pause = random.uniform(5.0, 15.0)
        logger.info("Макропауза %.1f сек (имитация отвлечения)", pause)
        await asyncio.sleep(pause)


async def _reset_ads_table_scroll(page) -> None:
    """Возвращает таблицу Ads Manager в начало перед новым циклом сканирования."""
    viewport = page.viewport_size or {"width": 1280, "height": 800}
    table_x = viewport["width"] * 0.5
    table_y = viewport["height"] * 0.5

    await human_move(page, table_x, table_y)

    reset_count = await page.evaluate("""() => {
        const seen = new Set();
        const scrollables = [];

        const addScrollable = (node) => {
            if (!(node instanceof HTMLElement)) {
                return;
            }
            if (seen.has(node)) {
                return;
            }
            seen.add(node);
            if (node.scrollHeight - node.clientHeight > 40) {
                scrollables.push(node);
            }
        };

        const docScroller = document.scrollingElement;
        if (docScroller instanceof HTMLElement) {
            addScrollable(docScroller);
        }

        const firstRowCell = document.querySelector('[data-surface*="table_row:"]');
        for (let node = firstRowCell; node; node = node.parentElement) {
            addScrollable(node);
        }

        for (const selector of ['[role="grid"]', '[role="table"]', '[aria-rowcount]']) {
            for (const node of document.querySelectorAll(selector)) {
                addScrollable(node);
            }
        }

        let changed = 0;
        for (const node of scrollables) {
            if (typeof node.scrollTo === 'function') {
                node.scrollTo({top: 0, left: 0, behavior: 'auto'});
            }
            if (node.scrollTop > 0) {
                node.scrollTop = 0;
                changed += 1;
            }
        }

        window.scrollTo(0, 0);
        return changed;
    }""")

    for _ in range(4):
        await human_wheel_scroll(
            page,
            -1200,
            anchor=(table_x, table_y),
            move_before=False,
            settle_range=(0.12, 0.25),
            drift_x_range=(-6, 6),
            drift_y_range=(-4, 4),
        )

    if isinstance(reset_count, int) and reset_count > 0:
        logger.info(
            "Observer: перед сканированием позиция таблицы сброшена к началу (%s контейнеров)",
            reset_count,
        )


async def _get_ads_table_scroll_metrics(page) -> dict[str, float | bool]:
    """Возвращает текущее положение прокрутки таблицы Ads Manager."""
    try:
        metrics = await page.evaluate("""() => {
            const seen = new Set();
            const scrollables = [];

            const addScrollable = (node) => {
                if (!(node instanceof HTMLElement)) {
                    return;
                }
                if (seen.has(node)) {
                    return;
                }
                seen.add(node);
                const maxScrollTop = node.scrollHeight - node.clientHeight;
                if (maxScrollTop > 40) {
                    scrollables.push(node);
                }
            };

            const firstRowCell = document.querySelector('[data-surface*="table_row:"]');
            for (let node = firstRowCell; node; node = node.parentElement) {
                addScrollable(node);
            }

            for (const selector of ['[role="grid"]', '[role="table"]', '[aria-rowcount]']) {
                for (const node of document.querySelectorAll(selector)) {
                    addScrollable(node);
                }
            }

            const docScroller = document.scrollingElement;
            if (docScroller instanceof HTMLElement) {
                addScrollable(docScroller);
            }

            if (!scrollables.length) {
                return {
                    found: false,
                    scroll_top: 0,
                    max_scroll_top: 0,
                    at_bottom: false,
                };
            }

            scrollables.sort((left, right) => {
                const leftMax = left.scrollHeight - left.clientHeight;
                const rightMax = right.scrollHeight - right.clientHeight;
                return rightMax - leftMax;
            });

            const node = scrollables[0];
            const maxScrollTop = Math.max(node.scrollHeight - node.clientHeight, 0);
            const scrollTop = Math.max(node.scrollTop, 0);
            return {
                found: true,
                scroll_top: scrollTop,
                max_scroll_top: maxScrollTop,
                at_bottom: maxScrollTop <= 0 ? true : scrollTop >= maxScrollTop - 4,
            };
        }""")
    except Exception:
        logger.debug("Observer: не удалось прочитать позицию прокрутки таблицы", exc_info=True)
        return {
            "found": False,
            "scroll_top": 0.0,
            "max_scroll_top": 0.0,
            "at_bottom": False,
        }

    if not isinstance(metrics, dict):
        return {
            "found": False,
            "scroll_top": 0.0,
            "max_scroll_top": 0.0,
            "at_bottom": False,
        }

    return {
        "found": bool(metrics.get("found")),
        "scroll_top": float(metrics.get("scroll_top") or 0.0),
        "max_scroll_top": float(metrics.get("max_scroll_top") or 0.0),
        "at_bottom": bool(metrics.get("at_bottom")),
    }


async def _wait_for_dom_stable(
    page, *, timeout_seconds: float = 2.0, poll_interval: float = 0.1
) -> None:
    """Ждёт стабилизации числа DOM-строк таблицы перед парсингом.

    Facebook Ads Manager использует виртуальную прокрутку — после wheel-скролла
    новые строки появляются в DOM с небольшой задержкой рендеринга. Ждём, пока
    количество [data-surface*="table_row:"] перестанет меняться.
    """
    async with asyncio.timeout(timeout_seconds):
        prev = -1
        while True:
            count: int = await page.evaluate(
                "() => document.querySelectorAll('[data-surface*=\"table_row:\"]').length"
            )
            if count == prev:
                return
            prev = count
            await asyncio.sleep(poll_interval)


async def _scroll_and_parse(page, parse_fn) -> list[ScannedAdRow]:
    """Плавный скролл с рандомными паузами, имитирующий человека.

    Прокручивает таблицу Ads Manager, парсит видимые строки после
    каждого скролла, мерджит результаты. Останавливается когда
    новых строк больше нет и таблица фактически перестаёт двигаться вниз.
    """
    all_rows: dict[str, ScannedAdRow] = {}
    max_scroll_passes = 50  # Защита от бесконечного цикла
    prev_count = -1
    stalled_without_metrics = 0
    max_stalled_without_metrics = 2
    min_scroll_delta = 8.0

    # Антидетект: перемещаем мышь в область таблицы перед скроллом
    viewport = page.viewport_size or {"width": 1280, "height": 800}
    table_x = viewport["width"] * random.uniform(0.3, 0.7)
    table_y = viewport["height"] * random.uniform(0.4, 0.6)
    await human_move(page, table_x, table_y)

    for pass_num in range(max_scroll_passes):
        # После скролла ждём стабилизации виртуального DOM перед парсингом.
        # Первый проход пропускаем — страница ещё не прокручивалась.
        if pass_num > 0:
            await _wait_for_dom_stable(page)

        # Парсим текущий view; таймаут защищает от зависания при заморозке Ads Manager
        try:
            visible_rows = await asyncio.wait_for(parse_fn(page), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning(
                "Observer: parse_fn превысил таймаут 30 сек на проходе %s — пропускаю проход",
                pass_num + 1,
            )
            visible_rows = []
        for row in visible_rows:
            all_rows[row.fb_ad_id] = row

        current_count = len(all_rows)
        got_new_rows = current_count != prev_count
        prev_count = current_count
        scroll_before = await _get_ads_table_scroll_metrics(page)

        # Если уже стоим внизу и новых строк нет — дальше скроллить бессмысленно.
        if not got_new_rows and scroll_before["found"] and scroll_before["at_bottom"]:
            logger.info(
                "Observer: скролл завершён, проход %s, всего строк %s, таблица уже внизу",
                pass_num + 1,
                current_count,
            )
            break

        # Антидетект: mouse.wheel от текущей позиции мыши в области таблицы
        # Уменьшаем шаг, чтобы не пролетать мимо середины виртуализированного списка.
        scroll_amount = random.randint(160, 260)
        await human_wheel_scroll(
            page,
            scroll_amount,
            anchor=(table_x, table_y),
            move_before=False,
            settle_range=(0.4, 0.8),
            drift_x_range=(-15, 15),
            drift_y_range=(-10, 10),
        )

        if got_new_rows:
            stalled_without_metrics = 0
            continue

        scroll_after = await _get_ads_table_scroll_metrics(page)
        if scroll_before["found"] and scroll_after["found"]:
            scroll_delta = scroll_after["scroll_top"] - scroll_before["scroll_top"]
            if scroll_delta <= min_scroll_delta:
                logger.info(
                    "Observer: скролл завершён, проход %s, всего строк %s, новых ID нет и таблица вниз уже не двигается (delta=%.1f)",
                    pass_num + 1,
                    current_count,
                    scroll_delta,
                )
                break

            logger.debug(
                "Observer: новых ID нет, но таблица ещё движется вниз (delta=%.1f) — продолжаю скролл",
                scroll_delta,
            )
            stalled_without_metrics = 0
            continue

        # Фолбэк, если позицию таблицы не удалось прочитать.
        stalled_without_metrics += 1
        if stalled_without_metrics >= max_stalled_without_metrics:
            logger.info(
                "Observer: скролл завершён, проход %s, всего строк %s, новых ID нет и позиция таблицы недоступна",
                pass_num + 1,
                current_count,
            )
            break

    return list(all_rows.values())


async def _send_alerts_to_telegram(
    client: TelegramBotClient,
    destination,
    alerts: list[AlertCandidate],
) -> None:
    """Отправляет или обновляет алерты одному получателю по stream+incident.

    Сначала отправляет все TG-сообщения, затем сохраняет все AlertEvent
    в одной DB-сессии (один checkout из пула вместо N).
    """
    # Фаза 1: отправка TG-сообщений, сбор результатов
    _DeliveryResult = tuple[AlertCandidate, AlertState, str, int | None, int | None, str]
    delivered: list[_DeliveryResult] = []

    for a in alerts:
        alert_state = _state_for_emitted_stage(a.stage)
        stream_kind = stream_for_alert_stage(a.stage)
        message_thread_id = destination.thread_id_for_stream(stream_kind)
        item = TelegramAlertItem(
            snapshot_id=a.snapshot_id,
            fb_ad_id=a.fb_ad_id,
            ad_name=a.ad_name,
            campaign_name=a.campaign_name,
            adset_name=a.adset_name,
            offer_code=a.offer_code,
            stage=a.stage,
            alert_state=alert_state,
            matched_rule_codes=a.matched_rule_codes,
            reason_title=a.reason_title,
            reason_text=a.reason_text,
            metrics_json=a.metrics_json,
        )
        message = render_alert_message(stage=a.stage, items=[item])
        existing_message_id = None
        try:
            refs_by_chat = await load_message_refs_by_chat(
                fb_ad_id=a.fb_ad_id,
                incident_key=a.snapshot_id,
                stream_kind=stream_kind,
            )
            existing_message_id = refs_by_chat.get(destination.chat_id)
        except Exception:
            logger.exception("Не удалось загрузить delivery-ref для %s", a.fb_ad_id)
            continue

        try:
            delivery_action, delivered_message_id = await safe_edit_or_send_message(
                client,
                chat_id=destination.chat_id,
                message_id=existing_message_id,
                message_thread_id=message_thread_id,
                text=message.text,
                reply_markup=message.reply_markup,
            )
            logger.info(
                "TG-алерт %s для %s, стадия=%s",
                "обновлён" if delivery_action == "edited" else "отправлен",
                a.ad_name,
                a.stage,
            )
        except Exception:
            logger.exception("Не удалось отправить TG-сообщение для %s", a.ad_name)
            continue

        if delivered_message_id is not None:
            try:
                await upsert_message_ref(
                    chat_id=destination.chat_id,
                    message_id=delivered_message_id,
                    fb_ad_id=a.fb_ad_id,
                    incident_key=a.snapshot_id,
                    stream_kind=stream_kind,
                )
            except Exception:
                logger.exception("Не удалось сохранить delivery-ref для %s", a.fb_ad_id)

        delivered.append(
            (a, alert_state, message.text, delivered_message_id, existing_message_id, stream_kind)
        )

    # Фаза 2: batch-сохранение AlertEvent в одной DB-сессии
    if not delivered:
        return

    factory = get_session_factory()
    try:
        async with factory() as session:
            # Загрузим все нужные snapshot и fb_ad одним batch-запросом
            fb_ad_ids = list({a.fb_ad_id for a, *_ in delivered})
            snap_result = await session.execute(
                select(AdSnapshot).where(AdSnapshot.fb_ad_id.in_(fb_ad_ids))
            )
            snapshots_map = {s.fb_ad_id: s for s in snap_result.scalars().all()}

            fb_ad_result = await session.execute(select(FbAd).where(FbAd.fb_ad_id.in_(fb_ad_ids)))
            fb_ads_map = {f.fb_ad_id: f for f in fb_ad_result.scalars().all()}

            for (
                a,
                alert_state,
                message_text,
                delivered_message_id,
                existing_message_id,
                _stream,
            ) in delivered:
                snapshot = snapshots_map.get(a.fb_ad_id)
                fb_ad = fb_ads_map.get(a.fb_ad_id)
                ad_id = fb_ad.id if fb_ad else None

                existing_stage_event = await session.scalar(
                    select(AlertEvent)
                    .where(
                        AlertEvent.ad_id == ad_id,
                        AlertEvent.telegram_chat_id == destination.chat_id,
                        AlertEvent.telegram_group_key == a.snapshot_id,
                        AlertEvent.stage == a.stage,
                        AlertEvent.state == alert_state,
                    )
                    .order_by(AlertEvent.updated_at.desc(), AlertEvent.created_at.desc())
                )

                if snapshot is not None:
                    snapshot.telegram_group_key = a.snapshot_id
                    snapshot.telegram_chat_id = destination.chat_id
                    snapshot.telegram_message_id = delivered_message_id

                if existing_stage_event is not None:
                    existing_stage_event.ad_id = ad_id
                    existing_stage_event.snapshot_id = snapshot.id if snapshot else None
                    existing_stage_event.offer_id = a.offer_id
                    existing_stage_event.matched_rule_codes = a.matched_rule_codes
                    existing_stage_event.reason_title = a.reason_title
                    existing_stage_event.reason_text = a.reason_text
                    existing_stage_event.metrics_json = a.metrics_json
                    existing_stage_event.message_text = message_text
                    existing_stage_event.telegram_message_id = delivered_message_id
                elif a.persist_event or existing_message_id is None:
                    session.add(
                        AlertEvent(
                            ad_id=ad_id,
                            snapshot_id=(snapshot.id if snapshot else None),
                            offer_id=a.offer_id,
                            stage=a.stage,
                            state=alert_state,
                            matched_rule_codes=a.matched_rule_codes,
                            reason_title=a.reason_title,
                            reason_text=a.reason_text,
                            metrics_json=a.metrics_json,
                            message_text=message_text,
                            telegram_chat_id=destination.chat_id,
                            telegram_message_id=delivered_message_id,
                            telegram_group_key=a.snapshot_id,
                        )
                    )

            await session.commit()
            logger.info("Batch AlertEvent сохранён: %d алертов", len(delivered))
    except Exception:
        logger.exception("Не удалось batch-сохранить AlertEvent (%d алертов)", len(delivered))


async def _run_scan_cycle(
    *,
    page,
    offers: dict,
    rows: list[ScannedAdRow],
    ad_states: dict,
    fake_deposits_map: dict[str, int],
    observer_thresholds: dict,
) -> tuple[list[AlertCandidate], list[AlertCandidate], list[dict]]:
    """Один полный цикл оценки правил: resolve офферов, evaluate, FSM-переходы.

    Возвращает (alerts_to_send, stop_alerts, snapshot_batch).
    """
    resolved_rows = []
    for row in rows:
        offer_code = resolve_offer_code(row.ad_name, row.campaign_name, offers)
        offer_data = offers.get(offer_code) if offer_code else None
        resolved_rows.append((row, offer_code, offer_data))

    cpm_baselines = compute_cpm_baselines_by_offer(
        [item for item in resolved_rows if item[1]],
        offer_code_getter=lambda item: item[1],
        cpm_getter=lambda item: item[0].cpm,
    )

    alerts_to_send: list[AlertCandidate] = []
    stop_alerts: list[AlertCandidate] = []
    snapshot_batch: list[dict] = []
    now = datetime.now(UTC)

    for row, offer_code, offer_data in resolved_rows:
        diagnostics = None
        if offer_code and offer_data and offer_data.get("rule_config") is not None:
            diagnostics = build_ad_quality_diagnostics(
                cpm_value=row.cpm,
                cpm_baseline=cpm_baselines.get(offer_code),
                frequency_value=row.frequency,
                frequency_elevated_threshold=offer_data["rule_config"].frequency_elevated_threshold,
                frequency_critical_threshold=offer_data["rule_config"].frequency_critical_threshold,
            )

        # Выключенные объявления не оцениваем — сбрасываем FSM и идём дальше
        if is_delivery_disabled(row.delivery_status):
            current_state, _ = ad_states.get(row.fb_ad_id, (AlertState.NORMAL, None))
            off_state = resolve_off_alert_state(current_state)
            ad_states[row.fb_ad_id] = (off_state, None)
            offer_id = None
            if offer_code and offer_code in offers:
                offer_id = offers[offer_code]["offer"].id
            snapshot_batch.append(
                {
                    "fb_ad_id": row.fb_ad_id,
                    "campaign_name": row.campaign_name,
                    "adset_name": row.adset_name,
                    "ad_name": row.ad_name,
                    "delivery_status": row.delivery_status,
                    "offer_id": offer_id,
                    "resolved_offer_code": offer_code,
                    "spend": row.spend,
                    "budget": row.budget,
                    "reach": row.reach,
                    "impressions": row.impressions,
                    "clicks": row.clicks,
                    "cpc": row.cpc,
                    "ctr": row.ctr,
                    "outbound_clicks": row.outbound_clicks,
                    "outbound_ctr": row.outbound_ctr,
                    "landing_page_views": row.landing_page_views,
                    "cost_per_result": row.cost_per_result,
                    "cost_per_landing_page_view": row.cost_per_landing_page_view,
                    "cpm": row.cpm,
                    "frequency": row.frequency,
                    "leads": row.leads,
                    "cost_per_lead": row.cost_per_lead,
                    "registrations": row.registrations,
                    "cost_per_registration": row.cost_per_registration,
                    "deposits": row.deposits,
                    "alert_state": off_state,
                    "current_stage": None,
                    "early_signal_rule_codes": [],
                    "warning_rule_codes": [],
                    "stop_rule_codes": [],
                    "open_state_token": None,
                    "telegram_group_key": None,
                    "last_observed_at": now,
                }
            )
            continue

        if offer_code is None:
            logger.debug("Observer: %s — оффер не найден, пропуск", row.ad_name)
        elif offer_data is None or offer_data.get("rule_config") is None:
            logger.warning(
                "Observer: %s — оффер '%s' найден, но правила не настроены",
                row.ad_name,
                offer_code,
            )

        # Корректировка ложных депозитов перед оценкой правил
        fake_count = fake_deposits_map.get(row.fb_ad_id, 0)
        eval_row = row
        if fake_count > 0 and row.deposits > 0:
            effective_deps = max(0, row.deposits - fake_count)
            eval_row = dataclasses.replace(row, deposits=effective_deps)

        evaluation = evaluate_row(
            row=eval_row,
            offer_cpa=(Decimal(offer_data["offer"].cpa_amount) if offer_data else None),
            rule_config=(offer_data.get("rule_config") if offer_data else None),
            warning_percent_of_stop=observer_thresholds["warning_percent_of_stop"],
            stop_percent_of_base=observer_thresholds["stop_percent_of_base"],
            observer_thresholds=observer_thresholds,
        )

        # FSM-переход
        current_state, current_token = ad_states.get(row.fb_ad_id, (AlertState.NORMAL, None))
        normalized_state, normalized_token = reopen_reactivated_alert_state(
            current_state,
            current_token,
            row.delivery_status,
        )
        if normalized_state != current_state:
            logger.info(
                "Observer: %s — объявление снова активно, сбрасываю состояние %s → NORMAL",
                row.fb_ad_id,
                current_state,
            )
        current_state, current_token = normalized_state, normalized_token
        next_state, token, should_emit = resolve_transition(
            current_state=current_state,
            current_token=current_token,
            next_stage=evaluation.stage,
        )

        # Авто-стоп: при STOP-алерте сразу переводим в CLAIMED
        is_auto_stop = should_emit and evaluation.stage == AlertStage.STOP
        if is_auto_stop:
            next_state = AlertState.CLAIMED

        ad_states[row.fb_ad_id] = (next_state, token)

        # Лог для диагностики: FSM заблокировал повторный алерт
        if evaluation.stage is not None and not should_emit:
            logger.info(
                "Observer: %s — стадия=%s, FSM блокирует (состояние=%s)",
                row.ad_name,
                evaluation.stage,
                current_state,
            )

        # Определяем offer_id
        offer_id = None
        if offer_code and offer_code in offers:
            offer_id = offers[offer_code]["offer"].id

        # Добавляем в батч снэпшотов
        snapshot_batch.append(
            {
                "fb_ad_id": row.fb_ad_id,
                "campaign_name": row.campaign_name,
                "adset_name": row.adset_name,
                "ad_name": row.ad_name,
                "delivery_status": row.delivery_status,
                "offer_id": offer_id,
                "resolved_offer_code": offer_code,
                "spend": row.spend,
                "budget": row.budget,
                "reach": row.reach,
                "impressions": row.impressions,
                "clicks": row.clicks,
                "cpc": row.cpc,
                "ctr": row.ctr,
                "outbound_clicks": row.outbound_clicks,
                "outbound_ctr": row.outbound_ctr,
                "landing_page_views": row.landing_page_views,
                "cost_per_result": row.cost_per_result,
                "cost_per_landing_page_view": row.cost_per_landing_page_view,
                "cpm": row.cpm,
                "frequency": row.frequency,
                "leads": row.leads,
                "cost_per_lead": row.cost_per_lead,
                "registrations": row.registrations,
                "cost_per_registration": row.cost_per_registration,
                "deposits": row.deposits,
                "alert_state": next_state,
                "current_stage": evaluation.stage,
                "early_signal_rule_codes": evaluation.early_signal_rule_codes,
                "warning_rule_codes": evaluation.warning_rule_codes,
                "stop_rule_codes": evaluation.stop_rule_codes,
                "open_state_token": token,
                "telegram_group_key": token,
                "last_observed_at": now,
            }
        )

        # Собираем алерты для отправки
        if should_emit and evaluation.stage is not None:
            diagnostics_text = (
                build_diagnostics_context_text(diagnostics) if diagnostics is not None else None
            )
            candidate = AlertCandidate(
                snapshot_id=token or uuid.uuid4().hex,
                offer_id=offer_id,
                fb_ad_id=row.fb_ad_id,
                ad_name=row.ad_name,
                campaign_name=row.campaign_name,
                adset_name=row.adset_name,
                offer_code=offer_code,
                offer_name=offer_data["offer"].code if offer_data else None,
                offer_cpa=str(offer_data["offer"].cpa_amount) if offer_data else None,
                stage=evaluation.stage,
                matched_rule_codes=evaluation.matched_rule_codes,
                reason_title=evaluation.reason_title,
                reason_text=_compose_reason_text(evaluation.reason_text, diagnostics_text),
                metrics_json=build_metrics_json(
                    row,
                    rule_summaries=[hit.summary for hit in evaluation.matched_hits],
                    traffic_diagnostics=(
                        diagnostics.as_dict() if diagnostics is not None else None
                    ),
                ),
            )
            alerts_to_send.append(candidate)
            if is_auto_stop:
                stop_alerts.append(candidate)
            logger.info(
                "AlertCandidate: %s | стадия=%s | правила=%s | fsm_было=%s",
                row.ad_name,
                evaluation.stage,
                evaluation.matched_rule_codes,
                current_state,
            )

    return alerts_to_send, stop_alerts, snapshot_batch


async def _process_scan_results(
    *,
    alerts_to_send: list[AlertCandidate],
    stop_alerts: list[AlertCandidate],
    snapshot_batch: list[dict],
    tg_client: TelegramBotClient | None,
    tg_destinations: list,
    interval_seconds: int,
) -> None:
    """Обработка результатов скана: сохранение снэпшотов, алерты, disable tasks."""
    # Батчевый upsert снэпшотов
    try:
        await batch_save_snapshots(snapshot_batch, _scan_guard)
        logger.info("Батч-сохранение: %s снэпшотов", len(snapshot_batch))
    except Exception:
        logger.warning(
            "Не удалось выполнить батч-сохранение снэпшотов",
            exc_info=True,
        )
    else:
        try:
            await reconcile_disable_tasks_in_db()
        except Exception:
            logger.warning(
                "Не удалось обновить очередь отключения после сохранения снэпшотов",
                exc_info=True,
            )

    # Авто-стоп: создаём DisableTask для STOP-алертов
    if stop_alerts:
        await auto_create_disable_tasks(stop_alerts)

    try:
        manual_attention_alerts = await reconcile_disable_incidents_after_scan()
        if manual_attention_alerts:
            alerts_to_send.extend(manual_attention_alerts)
            logger.warning(
                "Observer: %s инцидентов переведены в режим ручного разбора без нового STOP-спама",
                len(manual_attention_alerts),
            )
    except Exception:
        logger.warning(
            "Не удалось согласовать disable-инциденты после скана",
            exc_info=True,
        )

    # Напоминания: повторно отправляем алерты, на которые не отреагировали
    try:
        reminders = await collect_reminder_alerts(interval_seconds)
        if reminders:
            alerts_to_send.extend(reminders)
            logger.info("Observer: добавлено %s напоминаний в очередь отправки", len(reminders))
    except Exception:
        logger.warning("Не удалось собрать напоминания", exc_info=True)

    # Диагностика: логируем статус алертов и TG перед отправкой
    logger.info(
        "Observer: алертов к отправке: %s (STOP авто-стоп: %s), tg_client: %s, получателей: %s",
        len(alerts_to_send),
        len(stop_alerts),
        "есть" if tg_client else "НЕТ",
        len(tg_destinations),
    )

    # Микропауза перед отправкой алертов
    await _human_micro_pause()

    # Отправка в Telegram всем активным получателям
    if alerts_to_send and tg_client:
        if not tg_destinations:
            logger.warning(
                "Observer: есть алерты, но список получателей TG пуст — "
                "подготовьте forum-cutover в UI и активируйте owner через /start в CONTROL"
            )
        for destination in tg_destinations:
            await _send_alerts_to_telegram(tg_client, destination, alerts_to_send)
    elif alerts_to_send and not tg_client:
        logger.warning(
            "Observer: есть %s алертов, но tg_client=None — "
            "Telegram не авторизован или не настроен",
            len(alerts_to_send),
        )


async def _wait_for_next_cycle(
    *,
    interval_seconds: int,
    jitter_seconds: int,
    shutdown_event: asyncio.Event | None,
    cycle_completed: bool,
) -> bool:
    """Прерываемый сон между циклами с поллингом флагов.

    Возвращает True если нужно продолжить (не получен сигнал остановки).
    """
    sleep_time = compute_jitter(interval_seconds, jitter_seconds)
    logger.info("Observer: следующий цикл через %.0f сек", sleep_time)

    end_at = _time.monotonic() + sleep_time
    poll_interval = 5.0  # проверяем флаги каждые 5 секунд

    while True:
        remaining = end_at - _time.monotonic()
        if remaining <= 0:
            break

        # Завершаемся при shutdown
        if shutdown_event is not None and shutdown_event.is_set():
            logger.info("Observer: получен сигнал остановки, завершаем цикл")
            return False

        # Проверяем флаг немедленного скана
        if await check_scan_requested_flag():
            logger.info("Observer: прерываем сон — запрошен немедленный скан")
            break

        chunk = min(poll_interval, remaining)
        if cycle_completed:
            await update_observer_runtime_status(
                status="RUNNING",
                message="Ожидаем следующий цикл сканирования.",
                clear_last_error=True,
            )
        if shutdown_event is not None:
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=chunk)
                logger.info("Observer: получен сигнал остановки, завершаем цикл")
                return False
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(chunk)

    return True


async def observer_loop(
    *,
    page,
    offers: dict,
    telegram_bot_token: str,
    telegram_chat_id: str,
    interval_seconds: int = 90,
    jitter_seconds: int = 10,
    warning_percent_of_stop: Decimal = DEFAULT_WARNING_PERCENT_OF_STOP,
    stop_percent_of_base: Decimal = DEFAULT_STOP_PERCENT_OF_BASE,
    parse_fn,
    on_snapshot_update=None,
    browser_manager=None,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Основной бесконечный цикл observer.

    Args:
        page: Playwright Page (уже открыта на Ads Manager)
        offers: dict[offer_code -> {offer, rule_config}]
        telegram_bot_token: токен TG-бота
        telegram_chat_id: ID чата для уведомлений
        interval_seconds: интервал между обновлениями (дефолт, перезаписывается из БД)
        jitter_seconds: случайный jitter в секундах (дефолт, перезаписывается из БД)
        warning_percent_of_stop: legacy warning для обратной совместимости
        stop_percent_of_base: legacy stop для обратной совместимости
        parse_fn: функция парсинга DOM → list[ScannedAdRow]
        on_snapshot_update: callback для сохранения snapshot в БД
        browser_manager: VisionBrowserManager для переподключения при сбое
        shutdown_event: asyncio.Event для graceful shutdown по Ctrl+C
    """
    # Загружаем FSM-состояния из БД при старте (задача 2.3)
    try:
        ad_states = await load_ad_states_from_db()
        logger.info("FSM-состояния восстановлены из БД: %s записей", len(ad_states))
    except Exception:
        logger.warning(
            "Не удалось загрузить FSM-состояния из БД, стартуем с чистого листа",
            exc_info=True,
        )
        ad_states = {}

    # Загружаем TG настройки из БД (с fallback на .env)
    tg_token, tg_destinations = await load_telegram_settings_from_db(
        fallback_token=telegram_bot_token,
        fallback_chat_id=telegram_chat_id,
    )
    tg_client = None
    if tg_token and tg_destinations:
        tg_client = TelegramBotClient(tg_token)
    else:
        logger.warning(
            "Telegram не настроен (token=%s, получателей=%s) — алерты не будут отправляться",
            "есть" if tg_token else "пусто",
            len(tg_destinations),
        )

    observer_thresholds = extract_observer_threshold_values(
        {
            "warning_percent_of_stop": warning_percent_of_stop,
            "stop_percent_of_base": stop_percent_of_base,
        }
    )

    # Загружаем настройки observer из БД при старте
    try:
        (
            interval_seconds,
            jitter_seconds,
            observer_thresholds,
        ) = await load_observer_settings_from_db()
        logger.info(
            "Настройки observer из БД: интервал=%sс, jitter=%sс, "
            "warning(CPC/CPL/CPR)=%.0f/%.0f/%.0f%%, stop(CPC/CPL/CPR)=%.0f/%.0f/%.0f%%",
            interval_seconds,
            jitter_seconds,
            observer_thresholds["cpc_warning_percent_of_stop"],
            observer_thresholds["cpl_warning_percent_of_stop"],
            observer_thresholds["cpr_warning_percent_of_stop"],
            observer_thresholds["cpc_stop_percent_of_base"],
            observer_thresholds["cpl_stop_percent_of_base"],
            observer_thresholds["cpr_stop_percent_of_base"],
        )
    except Exception:
        logger.warning("Не удалось загрузить настройки observer из БД", exc_info=True)

    await update_observer_runtime_status(
        status="RUNNING",
        message="Observer подключён к браузеру и готовит первый цикл сканирования.",
        clear_last_error=True,
    )

    # Счётчик циклов для периодической перезагрузки офферов и TG настроек
    cycle_count = 0
    fake_deposits_map: dict[str, int] = {}
    RELOAD_EVERY = 10  # Перечитываем офферы, TG настройки и интервал каждые 10 циклов

    # Счётчик последовательных ошибок браузера (задача 2.4)
    consecutive_browser_errors = 0
    disable_pause_logged = False

    def _should_stop() -> bool:
        """Проверяет, нужно ли завершить работу."""
        return shutdown_event is not None and shutdown_event.is_set()

    while not _should_stop():
        cycle_completed = False
        try:
            # Перезагружаем офферы и TG настройки каждые N циклов
            if cycle_count % RELOAD_EVERY == 0:
                try:
                    offers = await load_offers_from_db()
                except Exception:
                    logger.warning(
                        "Не удалось обновить офферы из БД, используем предыдущие",
                        exc_info=True,
                    )
                try:
                    fake_deposits_map = await load_fake_deposits()
                except Exception:
                    logger.warning(
                        "Не удалось загрузить корректировки ложных депозитов",
                        exc_info=True,
                    )
                # Перечитываем TG настройки — пользователь мог обновить через UI
                try:
                    new_token, new_destinations = await load_telegram_settings_from_db(
                        fallback_token=telegram_bot_token,
                        fallback_chat_id=telegram_chat_id,
                    )
                    if new_token and new_destinations:
                        if new_token != tg_token:
                            if tg_client is not None:
                                await tg_client.close()
                            tg_token = new_token
                            tg_client = TelegramBotClient(tg_token)
                            logger.info("Telegram настройки обновлены из БД")
                        elif tg_client is None:
                            tg_client = TelegramBotClient(tg_token)
                        if new_destinations != tg_destinations:
                            tg_destinations = new_destinations
                            logger.info(
                                "Список получателей Telegram обновлён: %s",
                                len(tg_destinations),
                            )
                    elif not new_token or not new_destinations:
                        if tg_client is not None:
                            await tg_client.close()
                        tg_client = None
                        tg_token = ""
                        tg_destinations = []
                except Exception:
                    logger.debug("Не удалось обновить TG настройки", exc_info=True)

                # Перечитываем интервал и jitter из БД
                try:
                    (
                        new_interval,
                        new_jitter,
                        new_thresholds,
                    ) = await load_observer_settings_from_db()
                    if new_interval != interval_seconds or new_jitter != jitter_seconds:
                        logger.info(
                            "Настройки интервала обновлены: %sс→%sс, jitter %sс→%sс",
                            interval_seconds,
                            new_interval,
                            jitter_seconds,
                            new_jitter,
                        )
                    interval_seconds = new_interval
                    jitter_seconds = new_jitter
                    observer_thresholds = new_thresholds
                except Exception:
                    logger.debug("Не удалось обновить настройки observer из БД", exc_info=True)

                # Проверяем флаг переподключения к браузеру
                try:
                    if await check_vision_reconnect_flag() and browser_manager is not None:
                        logger.info("Переподключение к Vision браузеру по запросу из UI")
                        reconnected_page = await reconnect_browser_manager_with_vision_settings(
                            browser_manager
                        )
                        if reconnected_page is not None:
                            page = reconnected_page
                except Exception:
                    logger.warning("Не удалось выполнить переподключение к браузеру", exc_info=True)

            cycle_count += 1

            # Сначала приводим очередь отключения в консистентное состояние.
            try:
                await reconcile_disable_tasks_in_db()
            except Exception:
                logger.warning(
                    "Не удалось согласовать очередь отключения с текущими снэпшотами",
                    exc_info=True,
                )

            # Telegram, UI и фоновые задачи могут менять alert_state вне observer.
            # Перед новым сканом подтягиваем БД, чтобы не слать повторный алерт поверх CLAIMED.
            try:
                ad_states = await refresh_runtime_ad_states(ad_states)
            except Exception:
                logger.debug("Не удалось синхронизировать FSM-состояния из БД", exc_info=True)

            # Проверяем флаг is_scanning_enabled перед каждым сканом
            if not await check_scanning_enabled():
                await update_observer_runtime_status(
                    status="PAUSED",
                    message="Сканирование выключено в настройках.",
                )
                logger.info("Observer: сканирование отключено, пропускаем цикл")
                # Короткий сон перед следующей проверкой
                await asyncio.sleep(10.0)
                continue

            disable_queue_pause_reason = await get_disable_queue_pause_reason()
            if disable_queue_pause_reason:
                await update_observer_runtime_status(
                    status="WAITING_BROWSER",
                    message=(
                        f"Браузер занят задачами отключения. Причина: {disable_queue_pause_reason}"
                    ),
                )
                if not disable_pause_logged:
                    logger.info(
                        "Observer: ставлю скан на паузу, пока disable worker освобождает браузер: %s",
                        disable_queue_pause_reason,
                    )
                    disable_pause_logged = True
                await asyncio.sleep(DISABLE_QUEUE_SCAN_PAUSE_SECONDS)
                continue

            if disable_pause_logged:
                logger.info("Observer: очередь отключения освободила браузер — возобновляю скан")
                disable_pause_logged = False

            await update_observer_runtime_status(
                status="RUNNING",
                message="Выполняем цикл сканирования объявлений.",
                clear_last_error=True,
            )

            # 1-2. Обновляем таблицу и, если строки не появились,
            # пробуем восстановить страницу через page reload.
            rows = await scan_ads_with_page_recovery(
                page=page,
                parse_fn=parse_fn,
                refresh_table_fn=refresh_table,
                reset_scroll_fn=_reset_ads_table_scroll,
                scroll_and_parse_fn=_scroll_and_parse,
                sleep_fn=asyncio.sleep,
                settle_delay_seconds=random.uniform(2.0, 4.0),
                on_recovery_attempt=_update_scan_recovery_status,
            )
            logger.info("Observer: получено %s объявлений", len(rows))

            # 3. Оценка правил, FSM-переходы, сбор алертов
            alerts_to_send, stop_alerts, snapshot_batch = await _run_scan_cycle(
                page=page,
                offers=offers,
                rows=rows,
                ad_states=ad_states,
                fake_deposits_map=fake_deposits_map,
                observer_thresholds=observer_thresholds,
            )

            # 4. Сохранение снэпшотов, disable tasks, отправка алертов в TG
            await _process_scan_results(
                alerts_to_send=alerts_to_send,
                stop_alerts=stop_alerts,
                snapshot_batch=snapshot_batch,
                tg_client=tg_client,
                tg_destinations=tg_destinations,
                interval_seconds=interval_seconds,
            )

            # Успешный цикл — сбрасываем счётчик ошибок браузера
            consecutive_browser_errors = 0
            cycle_completed = True

        except ScanDataUnavailableError as exc:
            runtime_message = str(exc)
            await set_observer_scanning_enabled(False)
            await update_observer_runtime_status(
                status="PAUSED",
                message=runtime_message,
                last_error=runtime_message,
            )
            logger.error("Observer: %s", runtime_message)
            try:
                await broadcast_observer_runtime_message(
                    text=_build_scan_recovery_alert_text(exc),
                    fallback_token=tg_token or telegram_bot_token,
                    fallback_chat_id=telegram_chat_id,
                )
            except Exception:
                logger.exception("Не удалось отправить Telegram-алерт о недоступности данных скана")

            continue

        except TimeoutError:
            # asyncio.timeout бросает TimeoutError (BaseException в Python 3.11+).
            # Ловим отдельно, чтобы таймаут DOM-стабилизации не крашил весь loop.
            logger.warning("Observer: таймаут ожидания DOM-стабилизации, пропускаем цикл")
            await update_observer_runtime_status(
                status="WARNING",
                message="Таймаут ожидания данных Ads Manager. Следующий цикл запустится по расписанию.",
                last_error="TimeoutError при ожидании DOM",
            )
            continue

        except Exception as exc:
            if _is_browser_connection_error(exc):
                runtime_message = format_observer_runtime_message(exc)
                await update_observer_runtime_status(
                    status="ERROR",
                    message=runtime_message,
                    last_error=runtime_message,
                )
                consecutive_browser_errors += 1
                page = await _handle_browser_connection_error(
                    exc=exc,
                    attempt_number=consecutive_browser_errors,
                    browser_manager=browser_manager,
                    page=page,
                )
                continue

            runtime_message = (
                format_observer_runtime_message(exc) or "Внутренняя ошибка в цикле observer."
            )
            await update_observer_runtime_status(
                status="ERROR",
                message=runtime_message,
                last_error=runtime_message,
            )
            logger.exception("Observer: ошибка в цикле")

        if cycle_completed:
            await update_observer_runtime_status(
                status="RUNNING",
                message="Цикл завершён. Ожидаем следующий запуск.",
                clear_last_error=True,
            )

        # 5. Прерываемый сон с jitter + поллинг scan_requested
        should_continue = await _wait_for_next_cycle(
            interval_seconds=interval_seconds,
            jitter_seconds=jitter_seconds,
            shutdown_event=shutdown_event,
            cycle_completed=cycle_completed,
        )
        if not should_continue:
            return
