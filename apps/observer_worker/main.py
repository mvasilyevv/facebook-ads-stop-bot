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
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import grpc
from core.ai_assistant.explain import explain_alert
from core.logging import bind_contextvars, unbind_contextvars
from core.pubsub import CHANNEL_ALERT_CREATED, CHANNEL_SCAN_FINISHED, RedisPubSub
from sqlalchemy import select

from clients.python_grpc.client import (
    BrowserAgentClient,
    ScanDataUnavailableError,
    ScanProgress,
    ScanResult,
)
from core.browser.lock import BrowserLockTimeoutError, acquire_browser_lock
from core.db import get_session_factory
from core.diagnostics import (
    build_ad_quality_diagnostics,
    build_diagnostics_context_text,
    compute_cpm_baselines_by_offer,
)
from core.disable_tasks import is_delivery_disabled
from core.domain import AlertStage, AlertState
from core.models import AdSnapshot, AlertEvent, FbAd
from core.observer.browser_recovery import BrowserRecoveryEscalator
from core.observer.db_queries import (
    check_scanning_enabled,
    collect_reminder_alerts,
    compute_adaptive_cpa_by_offer,
    compute_cpl_cpr_baselines_by_offer,
    consume_scan_flags_combined,
    get_disable_queue_pause_reason,
    get_enable_queue_pause_reason,
    get_frequency_baselines_for_ads,
    load_active_snooze_ad_ids,
    load_ad_states_from_db,
    load_fake_deposits,
    load_history_ad_ids_with_metrics,
    load_offers_from_db,
    load_rule_confidence_by_offer,
    load_telegram_settings_from_db,
    load_vision_settings_for_runtime,
    peek_scan_requested_flag,
    refresh_runtime_ad_states,
)
from core.observer.disable_reconciler import (
    auto_create_disable_tasks,
    reconcile_disable_incidents_after_scan,
    reconcile_disable_tasks_in_db,
    reconcile_enable_tasks_in_db,
)
from core.observer.outcome_classifier import (
    ScanOutcome,
    classify_scan_outcome,
)
from core.observer.regression_guard import RegressionGuard
from core.observer.runtime_status import (
    format_observer_runtime_message,
    record_successful_scan,
    set_observer_phase,
    update_observer_runtime_status,
)
from core.observer.scan_guard import ZeroScanGuard
from core.observer.scan_run_writer import begin_scan_run, finish_scan_run
from core.observer.self_healing import SelfHealingEscalator
from core.observer.service import (
    AlertCandidate,
    _compose_reason_text,
    build_metrics_json,
    evaluate_row,
    resolve_offer_code,
)
from core.observer.snapshot_writer import batch_save_snapshots
from core.observer.stale_data_handler import StaleAction, StaleDataEscalator
from core.observer.state_machine import (
    _state_for_emitted_stage,
    reopen_reactivated_alert_state,
    resolve_off_alert_state,
    resolve_transition,
)
from core.scanner.models import ScannedAdRow
from core.settings_queries import get_or_create_observer_settings
from core.telegram.client import TelegramBotClient
from core.telegram.delivery import broadcast_observer_runtime_message
from core.telegram.message_refs import (
    load_message_refs_by_chat,
    stream_for_alert_stage,
    upsert_message_ref,
)
from core.telegram.messaging import safe_edit_or_send_message
from core.telegram.renderer import TelegramAlertItem, render_alert_message
from core.telegram.service import load_web_app_url

logger = logging.getLogger(__name__)
_TELEGRAM_DELIVERY_ACTION_LABELS = {
    "edited": "обновлён",
    "sent": "отправлен",
    "unchanged": "без изменений",
}

# Пока очередь действий не опустеет, observer не должен трогать общий браузер.
BROWSER_QUEUE_SCAN_PAUSE_SECONDS = 5.0
# Максимальное ожидание общего браузера перед переносом scan-цикла.
BROWSER_SCAN_LOCK_TIMEOUT_SECONDS = 60.0
# Проверку колонок подтверждаем повторной попыткой, чтобы не останавливать скан на перерисовке Ads Manager.
COLUMN_VALIDATION_FAILURE_LIMIT = 2
# Парсер ячеек может временно не дочитать правые колонки (CPM, Частота) из-за горизонтальной
# виртуализации Ads Manager — даём 3 подряд провала, прежде чем считать это фатальным.
PARSER_MISSING_COLUMNS_FAILURE_LIMIT = 3
PARSER_MISSING_COLUMNS_RETRY_DELAY_SECONDS = 10.0
# Пустой scan ещё не считаем фатальным: сначала даём странице несколько шансов вернуть строки таблицы.
EMPTY_SCAN_FAILURE_LIMIT = 3
EMPTY_SCAN_RETRY_DELAY_SECONDS = 10.0
# Интервал фонового heartbeat observer (обновляет worker_heartbeat_at независимо от длины скана)
OBSERVER_HEARTBEAT_INTERVAL_SECONDS = 20

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
# Гард от ложных откатов накопительных метрик (N подряд → принять новые значения)
_regression_guard = RegressionGuard()

# Глобальные переменные для хранения текущего статуса воркера
_observer_status: str = "RUNNING"
_observer_message: str | None = "Запущен."


async def update_observer_status(
    *,
    status: str,
    message: str | None = None,
    **kwargs,
) -> None:
    """Обновляет статус observer как локально (для фонового heartbeat), так и в БД.

    Это гарантирует, что фоновый поток heartbeat не перезапишет актуальный статус
    устаревшими значениями при долгой работе воркера.
    """
    global _observer_status, _observer_message
    _observer_status = status
    _observer_message = message
    await update_observer_runtime_status(status=status, message=message, **kwargs)


def _is_browser_connection_error(exc: Exception) -> bool:
    """Определяет, относится ли ошибка к обрыву соединения с browser-agent."""
    if isinstance(exc, grpc.RpcError):
        return True
    if isinstance(exc, (ConnectionError, OSError)):
        return True
    if not isinstance(exc, RuntimeError):
        return False
    message = str(exc).casefold()
    return any(marker in message for marker in _BROWSER_RUNTIME_ERROR_MARKERS)


def _is_transient_column_validation_failure(validation: dict) -> bool:
    """Отличает временную проблему браузера от реального отсутствия колонок."""
    missing_columns = list(validation.get("missing_columns") or [])
    if missing_columns:
        return False

    error_message = str(validation.get("error_message") or "").casefold()
    found_columns = list(validation.get("found_columns") or [])
    if not found_columns:
        return True

    return any(marker in error_message for marker in _BROWSER_RUNTIME_ERROR_MARKERS)


async def reconnect_browser_manager_with_vision_settings(
    grpc_client: BrowserAgentClient,
) -> None:
    """Переподключает browser-agent с актуальными Vision-настройками."""
    x_token, api_url, profile_id = await load_vision_settings_for_runtime(
        fallback_x_token=grpc_client.config.vision_x_token,
        fallback_api_url=grpc_client.config.vision_api_url,
        fallback_profile_id=grpc_client.config.vision_profile_id,
    )
    if not x_token or not profile_id:
        logger.warning("Vision-настройки для переподключения не найдены")
        return

    grpc_client.config.vision_x_token = x_token
    grpc_client.config.vision_api_url = api_url
    grpc_client.config.vision_profile_id = profile_id
    await grpc_client.reconnect_browser()
    logger.info("Browser-agent переподключён с актуальными Vision-настройками")


def _build_scan_recovery_alert_text(exc: ScanDataUnavailableError) -> str:
    """Формирует Telegram-алерт о фатальной недоступности данных скана."""
    return (
        "🚨 <b>Observer отключён</b>\n\n"
        f"Причина: {exc.reason}.\n"
        f"Подряд циклов без данных: {exc.attempts}.\n"
        f"Интервал между повторными циклами: {int(exc.retry_interval_seconds)} сек.\n"
        "Сканирование автоматически выключено.\n"
        "Проверьте, что в открытом профиле Vision видна таблица объявлений Ads Manager, "
        "а не loader, диалог или другая страница. Затем включите воркер снова."
    )


def _build_missing_columns_alert_text(missing_columns: list[str]) -> str:
    """Формирует Telegram-алерт о фатальной проблеме с колонками Ads Manager."""
    columns_text = "\n".join(f"• {column}" for column in missing_columns)
    return (
        "🚨 <b>Observer отключён</b>\n\n"
        "Причина: в таблице Ads Manager не хватает обязательных колонок "
        "или изменён их порядок.\n\n"
        f"<b>Проблемы:</b>\n{columns_text}\n\n"
        "Сканирование автоматически выключено, чтобы не записывать некорректные данные. "
        "Верните нужный набор колонок в Ads Manager и включите сканирование снова."
    )


_PARSER_MISSING_COLUMNS_MARKER = "Не удалось распарсить колонки"


def _extract_parser_missing_columns(message: str) -> list[str]:
    """Парсит список колонок из текста ошибки парсера Ads Manager."""
    if _PARSER_MISSING_COLUMNS_MARKER not in message:
        return []
    after_marker = message.split(_PARSER_MISSING_COLUMNS_MARKER, 1)[1]
    after_colon = after_marker.split(":", 1)[1] if ":" in after_marker else after_marker
    head = after_colon.split(". Примеры", 1)[0]
    head = head.strip().rstrip(".")
    return [item.strip() for item in head.split(",") if item.strip()]


def _build_empty_scan_reason() -> str:
    """Возвращает точную причину пустого scan-цикла для UI и Telegram."""
    return "Ads Manager вернул 0 строк таблицы объявлений"


async def _update_scan_recovery_status(
    *,
    reason: str,
    attempt: int,
    max_attempts: int,
    retry_delay_seconds: float,
) -> None:
    """Пишет в runtime-статус, что observer повторяет пустой scan-цикл."""
    await update_observer_status(
        status="RECOVERING",
        message=(
            f"{reason}. "
            f"Повторяем цикл сканирования {attempt}/{max_attempts} "
            f"через {int(retry_delay_seconds)} сек."
        ),
        last_error=f"{reason}. Пустой цикл {attempt}/{max_attempts}.",
    )


async def _get_browser_queue_pause() -> tuple[str | None, str | None]:
    """Возвращает тип очереди и причину, если браузер занят задачами действий."""
    reason: str | None = await get_disable_queue_pause_reason()
    if reason:
        return "disable", reason

    reason = await get_enable_queue_pause_reason()
    if reason:
        return "enable", reason

    return None, None


def compute_jitter(interval_seconds: int, jitter_seconds: int) -> float:
    """Вычисляет интервал сна: interval_seconds ± случайный jitter_seconds.

    При interval=45, jitter=4 → результат от 41 до 49 сек.
    Минимум 5 секунд (защита от слишком частого скана).
    """
    offset = random.uniform(-jitter_seconds, jitter_seconds)
    return max(5.0, interval_seconds + offset)


# ---------------------------------------------------------------------------
# Адаптивный интервал: уровень угрозы определяет частоту сканирования
# ---------------------------------------------------------------------------

# Интервалы (секунды) для каждого уровня угрозы
_ADAPTIVE_INTERVAL_CRITICAL = 10  # STOP в текущем срезе
_ADAPTIVE_INTERVAL_ELEVATED = 13  # WARNING в текущем срезе
_ADAPTIVE_INTERVAL_ACTIVE = 15  # Активный залив без сигналов
_ADAPTIVE_INTERVAL_CALM = 30  # Есть мониторинг, но активного залива нет
_ADAPTIVE_INTERVAL_IDLE = 55  # Нет объявлений с офферами
_FALLBACK_INTERVAL = 55  # Fallback при ошибках цикла


def _snapshot_delivery_is_disabled(snapshot: dict) -> bool:
    """Проверяет, что snapshot относится к выключенному объявлению."""
    status = str(snapshot.get("delivery_status") or "").casefold()
    return status == "off" or "off" in status


def _snapshot_has_traffic(snapshot: dict) -> bool:
    """Проверяет, что snapshot содержит ненулевой трафик или расход."""
    metric_names = (
        "spend",
        "reach",
        "impressions",
        "clicks",
        "outbound_clicks",
        "landing_page_views",
        "leads",
        "registrations",
        "deposits",
    )
    for metric_name in metric_names:
        value = snapshot.get(metric_name)
        if value is None:
            continue
        try:
            if Decimal(str(value)) > 0:
                return True
        except Exception:
            continue
    return False


def compute_adaptive_interval(
    snapshot_batch: list[dict],
    *,
    has_stop_alerts: bool = False,
) -> tuple[int, str]:
    """Вычисляет интервал до следующего скана по уровню угрозы.

    Анализирует current_stage всех объявлений в батче и выбирает
    минимальный интервал, соответствующий максимальной угрозе.

    Уровни: IMMEDIATE(0) → CRITICAL(10) → ELEVATED(13) → ACTIVE(15)
    → CALM(30) → IDLE(55).

    Returns:
        (interval_seconds, threat_level_name) — интервал и название уровня для логов.
    """
    # Немедленный ре-скан после STOP: интервал 0 (вызывающий код пропустит сон)
    if has_stop_alerts:
        return 0, "IMMEDIATE"

    has_stop = False
    has_warning = False
    has_active_monitored_ads = False
    has_monitored_ads = False

    for snap in snapshot_batch:
        stage = snap.get("current_stage")
        if stage is not None:
            has_monitored_ads = True
            stage_name = stage.value if hasattr(stage, "value") else str(stage)
            if stage_name == "STOP":
                has_stop = True
            elif stage_name == "WARNING":
                has_warning = True
        # Объявление с оффером тоже считается мониторимым
        if snap.get("resolved_offer_code"):
            has_monitored_ads = True
            if not _snapshot_delivery_is_disabled(snap) and _snapshot_has_traffic(snap):
                has_active_monitored_ads = True

    if has_stop:
        return _ADAPTIVE_INTERVAL_CRITICAL, "CRITICAL"
    if has_warning:
        return _ADAPTIVE_INTERVAL_ELEVATED, "ELEVATED"
    if has_active_monitored_ads:
        return _ADAPTIVE_INTERVAL_ACTIVE, "ACTIVE"
    if has_monitored_ads:
        return _ADAPTIVE_INTERVAL_CALM, "CALM"
    return _ADAPTIVE_INTERVAL_IDLE, "IDLE"


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
        # Генерируем LLM-объяснение для STOP/WARNING алертов (не блокирует отправку)
        rule_name = a.matched_rule_codes[0] if a.matched_rule_codes else ""
        explanation = await explain_alert(
            rule_name=rule_name,
            stage=a.stage.value,
            metrics=dict(a.metrics_json or {}),
            thresholds={},
            offer_context={"offer_code": a.offer_code} if a.offer_code else None,
        )
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
            explanation=explanation,
        )
        web_app_url = await load_web_app_url()
        message = render_alert_message(stage=a.stage, items=[item], web_app_url=web_app_url)
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
                _TELEGRAM_DELIVERY_ACTION_LABELS.get(delivery_action, delivery_action),
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

            try:
                from core.telegram.delivery import _maybe_post_general_link

                await _maybe_post_general_link(
                    client,
                    destination=destination,
                    stream_kind=stream_kind,
                    ad_name=a.ad_name,
                    topic_thread_id=message_thread_id,
                    topic_message_id=delivered_message_id,
                )
            except Exception:
                logger.exception("Не удалось отправить cross-link в General для %s", a.fb_ad_id)

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


async def _increment_scan_id() -> int:
    """Атомарно инкрементирует current_scan_id и возвращает новое значение.

    Используется в начале каждого реального scan-цикла observer'а, чтобы
    помечать AdSnapshot этого батча. Идёт отдельной короткой транзакцией,
    чтобы не зависеть от длинного цикла сканирования.
    """
    from core.settings_queries import get_or_create_observer_settings

    factory = get_session_factory()
    async with factory() as session:
        settings = await get_or_create_observer_settings(session)
        settings.current_scan_id = (settings.current_scan_id or 0) + 1
        new_id = settings.current_scan_id
        await session.commit()
        return new_id


async def _run_scan_cycle(
    *,
    offers: dict,
    rows: list[ScannedAdRow],
    ad_states: dict,
    fake_deposits_map: dict[str, int],
    current_scan_id: int,
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

    # Медианы CPL/CPR по офферу для Bayesian-сглаживания при малой выборке
    cpl_cpr_baselines = await compute_cpl_cpr_baselines_by_offer()

    # Rolling median CPA по DISABLED-объявлениям оффера за последние N дней (adaptive baseline)
    adaptive_cpa_by_offer = await compute_adaptive_cpa_by_offer()

    # Frequency ~час назад для правила выгорания аудитории (правило 7)
    all_ad_ids = [row.fb_ad_id for row in rows]
    frequency_baselines = await get_frequency_baselines_for_ads(all_ad_ids, hours_ago=1.0)

    # ML-confidence: загружаем статистику confidence по (оффер, правило)
    confidence_by_offer = await load_rule_confidence_by_offer()

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

        # Получаем медианы CPL/CPR для данного оффера (None → сглаживание не применяется)
        _baseline_cpl, _baseline_cpr = (
            cpl_cpr_baselines.get(offer_code, (None, None)) if offer_code else (None, None)
        )
        _freq_1h_ago = frequency_baselines.get(row.fb_ad_id)
        # Adaptive CPA baseline: rolling median по офферу (если включён)
        _rule_cfg = offer_data.get("rule_config") if offer_data else None
        _use_adaptive = bool(getattr(_rule_cfg, "use_adaptive_cpa", False)) if _rule_cfg else False
        _adaptive_cpa = adaptive_cpa_by_offer.get(offer_code) if offer_code else None
        # ML-confidence: словарь rule_name → confidence для данного оффера
        _rule_confidence = confidence_by_offer.get(offer_code) if offer_code else None
        evaluation = evaluate_row(
            row=eval_row,
            offer_cpa=(Decimal(offer_data["offer"].cpa_amount) if offer_data else None),
            rule_config=_rule_cfg,
            offer_median_cpl=_baseline_cpl,
            offer_median_cpr=_baseline_cpr,
            frequency_1h_ago=_freq_1h_ago,
            adaptive_cpa=_adaptive_cpa,
            use_adaptive_cpa=_use_adaptive,
            observed_at=now,
            rule_confidence_map=_rule_confidence,
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
    current_scan_id: int | None = None,
) -> None:
    """Обработка результатов скана: сохранение снэпшотов, алерты, disable tasks."""
    # Батчевый upsert снэпшотов
    snapshots_saved = False
    try:
        snapshots_saved = await batch_save_snapshots(
            snapshot_batch,
            _scan_guard,
            regression_guard=_regression_guard,
            current_scan_id=current_scan_id,
        )
        if snapshots_saved:
            logger.info("Батч-сохранение: %s снэпшотов", len(snapshot_batch))
        else:
            logger.info("Батч-сохранение пропущено guard-ом: %s снэпшотов", len(snapshot_batch))
    except Exception:
        logger.warning(
            "Не удалось выполнить батч-сохранение снэпшотов",
            exc_info=True,
        )
    else:
        if snapshots_saved:
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
        reminders = await collect_reminder_alerts()
        if reminders:
            alerts_to_send.extend(reminders)
            logger.info("Observer: добавлено %s напоминаний в очередь отправки", len(reminders))
    except Exception:
        logger.warning("Не удалось собрать напоминания", exc_info=True)

    # Фильтрация снузов: пропускаем алерты для ad_id с активным снузом.
    # FSM-состояние не меняем — алерт вернётся после истечения снуза.
    if alerts_to_send:
        try:
            snoozed_ids = await load_active_snooze_ad_ids()
            if snoozed_ids:
                before_count = len(alerts_to_send)
                alerts_to_send = [a for a in alerts_to_send if a.fb_ad_id not in snoozed_ids]
                skipped = before_count - len(alerts_to_send)
                if skipped:
                    logger.info("Observer: %s алертов пропущено из-за активного снуза", skipped)
        except Exception:
            logger.warning("Не удалось загрузить активные снузы", exc_info=True)

    # Диагностика: логируем статус алертов и TG перед отправкой
    logger.info(
        "Observer: алертов к отправке: %s (STOP авто-стоп: %s), tg_client: %s, получателей: %s",
        len(alerts_to_send),
        len(stop_alerts),
        "есть" if tg_client else "НЕТ",
        len(tg_destinations),
    )

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


async def _process_fast_stop_results(
    *,
    stop_alerts: list[AlertCandidate],
    snapshot_batch: list[dict],
    current_scan_id: int | None = None,
) -> None:
    """Сохраняет STOP-строки и ставит задачи отключения до завершения полного сканирования."""
    if not stop_alerts:
        return

    stop_ids = {alert.fb_ad_id for alert in stop_alerts}
    stop_snapshots = [snap for snap in snapshot_batch if snap.get("fb_ad_id") in stop_ids]
    if stop_snapshots:
        saved = await batch_save_snapshots(
            stop_snapshots,
            _scan_guard,
            allow_cabinet_rollover=False,
            bypass_scan_guard=True,
            current_scan_id=current_scan_id,
        )
        if saved:
            logger.info(
                "Быстрый стоп: сохранено %s STOP-снэпшотов до конца сканирования",
                len(stop_snapshots),
            )

    await auto_create_disable_tasks(stop_alerts)
    logger.info("Быстрый стоп: создано или проверено задач отключения: %s", len(stop_alerts))


def _merge_progress_into_fast_stop(
    *,
    progress_alerts: list,
    progress_stop_alerts: list,
    progress_snapshot_batch: list[dict],
    progress_ad_states: dict,
    ad_states: dict,
    alerts_to_send: list,
    stop_alerts: list,
    snapshot_batch: list[dict],
) -> set[str]:
    """Сливает результаты progress-прохода в общие списки fast-stop ветки.

    Возвращает set fb_ad_id, по которым нашёлся STOP. WARNING-алерты и non-STOP
    снэпшоты из того же progress-прохода тоже сохраняются: иначе observer
    теряет их до конца fast-stop и ломает baseline regression_guard для
    следующего полного цикла.
    """
    stop_ids = {alert.fb_ad_id for alert in progress_stop_alerts}
    for fb_ad_id in stop_ids:
        if fb_ad_id in progress_ad_states:
            ad_states[fb_ad_id] = progress_ad_states[fb_ad_id]
    alerts_to_send.extend(progress_alerts)
    stop_alerts.extend(progress_stop_alerts)
    snapshot_batch.extend(progress_snapshot_batch)
    return stop_ids


async def _wait_for_next_cycle(
    *,
    shutdown_event: asyncio.Event | None,
    cycle_completed: bool,
    adaptive_interval: int | None = None,
    threat_level: str | None = None,
) -> bool:
    """Прерываемый сон между циклами с поллингом флагов.

    Интервал определяется адаптивно по уровню угрозы.
    poll_interval (частота проверки флагов) тоже адаптивен:
    - IMMEDIATE/CRITICAL/ELEVATED: 0.2 сек
    - CALM/IDLE и прочие: 5.0 сек
    Возвращает True если нужно продолжить (не получен сигнал остановки).
    Флаг scan_requested здесь НЕ сбрасывается — мы только просыпаемся при его
    появлении, а потребляет его уже основной цикл через consume_scan_requested_flag.
    """
    interval = (
        adaptive_interval if adaptive_interval and adaptive_interval > 0 else _FALLBACK_INTERVAL
    )
    adaptive_jitter = max(1, interval // 10)
    sleep_time = compute_jitter(interval, adaptive_jitter)
    next_scan_at = datetime.now(UTC) + timedelta(seconds=sleep_time)
    logger.info(
        "Observer: интервал %sс (угроза=%s), следующий цикл через %.0f сек",
        interval,
        threat_level or "FALLBACK",
        sleep_time,
    )

    end_at = _time.monotonic() + sleep_time
    # Адаптивный poll_interval: в режимах высокой угрозы проверяем флаги чаще
    if threat_level in ("IMMEDIATE", "CRITICAL", "ELEVATED"):
        poll_interval = 0.2
    else:
        poll_interval = 5.0

    while True:
        remaining = end_at - _time.monotonic()
        if remaining <= 0:
            break

        # Завершаемся при shutdown
        if shutdown_event is not None and shutdown_event.is_set():
            logger.info("Observer: получен сигнал остановки, завершаем цикл")
            return False

        # Просыпаемся при запросе немедленного скана. Флаг НЕ потребляем —
        # это сделает основной цикл, чтобы решение принималось в одном месте.
        if await peek_scan_requested_flag():
            logger.info("Observer: прерываем сон — запрошен немедленный скан")
            return True

        chunk = min(poll_interval, remaining)
        if cycle_completed:
            await update_observer_status(
                status="RUNNING",
                message="Ожидаем следующий цикл сканирования.",
                current_scan_interval_seconds=interval,
                current_scan_jitter_seconds=adaptive_jitter,
                current_scan_threat_level=threat_level or "FALLBACK",
                next_scan_at=next_scan_at,
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


async def _maybe_auto_resume_scanning() -> None:
    """Авто-resume: если pause_until истёк — включаем сканирование обратно."""
    factory = get_session_factory()
    async with factory() as session:
        from core.settings_queries import get_or_create_observer_settings

        settings = await get_or_create_observer_settings(session)
        if (
            settings.pause_until is not None
            and not settings.is_scanning_enabled
            and settings.pause_until <= datetime.now(UTC)
        ):
            settings.is_scanning_enabled = True
            settings.pause_until = None
            await session.commit()
            logger.info("Авто-возобновление сканирования по истечении паузы")


async def _observer_heartbeat_loop() -> None:
    """Фоновый цикл: обновляет worker_heartbeat_at каждые OBSERVER_HEARTBEAT_INTERVAL_SECONDS.

    Благодаря этому watchdog видит свежий heartbeat независимо от длины scan-цикла,
    что устраняет ложные перезапуски observer при сканах длиннее порога watchdog.
    """
    while True:
        try:
            await update_observer_runtime_status(status=_observer_status, message=_observer_message)
        except Exception:
            logger.debug("Observer: фоновый heartbeat не удалось записать", exc_info=True)
        await asyncio.sleep(OBSERVER_HEARTBEAT_INTERVAL_SECONDS)


async def observer_loop(
    *,
    grpc_client: BrowserAgentClient,
    offers: dict,
    telegram_bot_token: str,
    telegram_chat_id: str,
    on_snapshot_update=None,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Основной бесконечный цикл observer."""
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

    # Self-healing эскалатор: счётчик последовательных провалов цикла
    _self_healing = SelfHealingEscalator()

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

    await update_observer_status(
        status="RUNNING",
        message="Observer подключён к браузеру и готовит первый цикл сканирования.",
        clear_last_error=True,
    )

    # Шина событий: один экземпляр на весь жизненный цикл воркера
    from core.config import get_settings as _get_settings

    _pubsub = RedisPubSub(_get_settings().redis_url)

    # Счётчик циклов для периодической перезагрузки офферов и TG настроек
    cycle_count = 0
    fake_deposits_map: dict[str, int] = {}
    RELOAD_EVERY = 10  # Перечитываем офферы, TG настройки и интервал каждые 10 циклов

    # Счётчик последовательных ошибок браузера (задача 2.4)
    consecutive_browser_errors = 0
    consecutive_column_validation_errors = 0
    consecutive_parser_missing_columns_errors = 0
    consecutive_empty_scan_cycles = 0
    browser_pause_kind: str | None = None

    # Локальные list-обёртки для совместимости с heartbeat-замыканием — будут
    # удалены полностью в T13b при переходе на outcome-driven цикл.
    status_ref: list[str] = ["RUNNING"]
    message_ref: list[str | None] = ["Запущен."]

    # Эскалаторы для STALE_DATA и BROWSER_LOST. Живут между итерациями.
    stale_escalator = StaleDataEscalator()
    recovery_escalator = BrowserRecoveryEscalator()

    def _should_stop() -> bool:
        """Проверяет, нужно ли завершить работу."""
        return shutdown_event is not None and shutdown_event.is_set()

    # run_id живёт между фазами одной итерации цикла; при ранних выходах
    # (fast-stop, empty-retry, timeout, user-disable) закрывается через
    # _ensure_scan_run_finished_with. nonlocal требует объявления в enclosing scope.
    run_id: int | None = None

    async def _ensure_scan_run_finished_with(
        outcome: str,
        error_kind: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Безопасно закрывает run_id если он ещё открыт.

        Используется в early-exit путях (fast-stop, empty-scan retry, user disable,
        timeout) чтобы не оставлять RUNNING-черновик в scan_runs.
        """
        nonlocal run_id
        if run_id is None:
            return
        factory_local = get_session_factory()
        try:
            async with factory_local() as session_local:
                await finish_scan_run(
                    session_local,
                    run_id=run_id,
                    outcome=outcome,
                    error_kind=error_kind,
                    error_message=(error_message or "")[:500] or None,
                )
                await session_local.commit()
        except Exception:
            logger.exception("Не удалось закрыть scan_run (%s)", outcome)
        finally:
            run_id = None

    # Запускаем фоновую задачу heartbeat, которая читает глобальные переменные
    # и пишет heartbeat каждые OBSERVER_HEARTBEAT_INTERVAL_SECONDS секунд.
    heartbeat_task = asyncio.create_task(
        _observer_heartbeat_loop(),
        name="observer-heartbeat",
    )

    try:
        while not _should_stop():
            cycle_completed = False
            alerts_to_send: list[AlertCandidate] = []
            stop_alerts: list[AlertCandidate] = []
            snapshot_batch: list[dict] = []
            fast_stop_triggered = False
            scan_result_obj: ScanResult | None = None
            run_id = None
            # Timing-инструментация: замеряем фазы цикла для baseline/после-сравнения.
            timing: dict[str, float] = {"cycle_start": _time.monotonic()}
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

                    # Флаг vision reconnect теперь читается в объединённом префлайт-запросе ниже
                    # (consume_scan_flags_combined).

                cycle_count += 1

                # Сначала приводим очереди отключения и включения в консистентное состояние.
                try:
                    await reconcile_disable_tasks_in_db()
                except Exception:
                    logger.warning(
                        "Не удалось согласовать очередь отключения с текущими снэпшотами",
                        exc_info=True,
                    )
                try:
                    await reconcile_enable_tasks_in_db()
                except Exception:
                    logger.warning(
                        "Не удалось согласовать очередь включения с текущими снэпшотами",
                        exc_info=True,
                    )

                # Telegram, UI и фоновые задачи могут менять alert_state вне observer.
                # Перед новым сканом подтягиваем БД, чтобы не слать повторный алерт поверх CLAIMED.
                try:
                    ad_states = await refresh_runtime_ad_states(ad_states)
                except Exception:
                    logger.debug("Не удалось синхронизировать FSM-состояния из БД", exc_info=True)

                # Авто-resume: если pause_until истёк — возобновляем сканирование
                try:
                    await _maybe_auto_resume_scanning()
                except Exception:
                    logger.debug("Не удалось проверить авто-resume", exc_info=True)

                # Принудительный скан + проверка is_scanning_enabled + флаг vision reconnect —
                # одним запросом к БД (вместо трёх раздельных round-trip).
                (
                    scanning_enabled,
                    forced_scan,
                    vision_reconnect_requested,
                ) = await consume_scan_flags_combined()
                if vision_reconnect_requested:
                    try:
                        await grpc_client.reconnect_browser()
                    except Exception:
                        logger.warning(
                            "Не удалось выполнить переподключение к браузеру", exc_info=True
                        )
                if forced_scan:
                    logger.info("Observer: запрошен немедленный скан — пробиваем паузу мониторинга")

                # Проверяем флаг is_scanning_enabled перед каждым сканом
                if not forced_scan and not scanning_enabled:
                    consecutive_empty_scan_cycles = 0
                    status_ref[0] = "PAUSED"
                    message_ref[0] = "Сканирование выключено в настройках."
                    await update_observer_runtime_status(
                        status="PAUSED",
                        message="Сканирование выключено в настройках.",
                        clear_scan_schedule=True,
                    )
                    # Короткий сон перед следующей проверкой
                    await asyncio.sleep(10.0)
                    continue

                browser_pause_kind_next, browser_pause_reason = await _get_browser_queue_pause()
                # Кэшируем результат на короткий интервал: второй вызов внутри лока (ниже)
                # переиспользует это значение, если прошло < 5 с.
                _queue_pause_cached_at = _time.monotonic()
                _queue_pause_cached_value = (browser_pause_kind_next, browser_pause_reason)

                if browser_pause_reason and browser_pause_kind_next == "disable":
                    consecutive_empty_scan_cycles = 0
                    status_ref[0] = "WAITING_BROWSER"
                    await update_observer_runtime_status(
                        status="WAITING_BROWSER",
                        message=(
                            f"Браузер занят задачами отключения. Причина: {browser_pause_reason}"
                        ),
                    )
                    if browser_pause_kind != "disable":
                        logger.info(
                            "Observer: ставлю скан на паузу, пока очередь отключения освобождает браузер: %s",
                            browser_pause_reason,
                        )
                        browser_pause_kind = "disable"
                    await asyncio.sleep(BROWSER_QUEUE_SCAN_PAUSE_SECONDS)
                    continue

                if browser_pause_reason and browser_pause_kind_next == "enable":
                    consecutive_empty_scan_cycles = 0
                    status_ref[0] = "WAITING_BROWSER"
                    await update_observer_runtime_status(
                        status="WAITING_BROWSER",
                        message=(
                            f"Браузер занят задачами включения. Причина: {browser_pause_reason}"
                        ),
                    )
                    if browser_pause_kind != "enable":
                        logger.info(
                            "Observer: ставлю скан на паузу, пока очередь включения освобождает браузер: %s",
                            browser_pause_reason,
                        )
                        browser_pause_kind = "enable"
                    await asyncio.sleep(BROWSER_QUEUE_SCAN_PAUSE_SECONDS)
                    continue

                if browser_pause_kind == "disable":
                    logger.info(
                        "Observer: очередь отключения освободила браузер — возобновляю скан"
                    )
                    browser_pause_kind = None
                elif browser_pause_kind == "enable":
                    logger.info("Observer: очередь включения освободила браузер — возобновляю скан")
                    browser_pause_kind = None

                rows: list[ScannedAdRow] = []
                scanned_rows_by_id: dict[str, ScannedAdRow] = {}
                timing["preflight_end"] = _time.monotonic()
                try:
                    async with acquire_browser_lock(
                        owner="observer-scan",
                        timeout_seconds=BROWSER_SCAN_LOCK_TIMEOUT_SECONDS,
                    ):
                        timing["lock_acquired"] = _time.monotonic()
                        # Если первый вызов был < 5 с назад — переиспользуем результат,
                        # не делая повторный SELECT внутри лока.
                        if _time.monotonic() - _queue_pause_cached_at < 5.0:
                            browser_pause_kind_next, browser_pause_reason = (
                                _queue_pause_cached_value
                            )
                        else:
                            (
                                browser_pause_kind_next,
                                browser_pause_reason,
                            ) = await _get_browser_queue_pause()
                        if browser_pause_reason:
                            consecutive_empty_scan_cycles = 0
                            status_ref[0] = "WAITING_BROWSER"
                            await update_observer_runtime_status(
                                status="WAITING_BROWSER",
                                message=(
                                    "Браузер занят задачами "
                                    f"{'отключения' if browser_pause_kind_next == 'disable' else 'включения'}. "
                                    f"Причина: {browser_pause_reason}"
                                ),
                            )
                            browser_pause_kind = browser_pause_kind_next
                            raise BrowserLockTimeoutError(
                                f"Браузер занят задачами: {browser_pause_reason}"
                            )

                        validation = await grpc_client.validate_columns()
                        if not validation.get("valid", False):
                            consecutive_empty_scan_cycles = 0
                            missing_columns = list(validation.get("missing_columns") or [])
                            validation_error = str(validation.get("error_message") or "").strip()

                            if _is_transient_column_validation_failure(validation):
                                consecutive_column_validation_errors = 0
                                transient_message = (
                                    "Проверка колонок Ads Manager не получила данные от страницы. "
                                    "Считаю это временной проблемой браузера/CDP, а не изменением колонок."
                                )
                                await update_observer_runtime_status(
                                    status="RECOVERING",
                                    message=transient_message,
                                    last_error=validation_error
                                    or "Проверка колонок не вернула детали",
                                )
                                logger.warning(
                                    "Observer: временный сбой проверки колонок, пробую переподключиться: %s",
                                    validation_error or "детали не вернулись",
                                )
                                try:
                                    await grpc_client.reconnect_browser()
                                except Exception:
                                    logger.warning(
                                        "Observer: не удалось переподключиться после сбоя проверки колонок",
                                        exc_info=True,
                                    )
                                await asyncio.sleep(10.0)
                                continue

                            consecutive_column_validation_errors += 1
                            columns_message = (
                                "Проверка колонок Ads Manager не пройдена "
                                f"({consecutive_column_validation_errors}/{COLUMN_VALIDATION_FAILURE_LIMIT}): "
                                f"{', '.join(missing_columns) or 'детали не вернулись'}"
                            )

                            if (
                                consecutive_column_validation_errors
                                < COLUMN_VALIDATION_FAILURE_LIMIT
                            ):
                                await update_observer_runtime_status(
                                    status="WARNING",
                                    message=(
                                        "Колонки Ads Manager временно не совпали с ожидаемыми. "
                                        "Скан этого цикла пропущен, повторим проверку перед следующим сканом."
                                    ),
                                    last_error=columns_message,
                                )
                                logger.warning("Observer: %s", columns_message)
                                await asyncio.sleep(10.0)
                                continue

                            await update_observer_runtime_status(
                                status="PAUSED",
                                message=(
                                    "Сканирование остановлено: обязательные колонки Ads Manager "
                                    "отсутствуют или идут в другом порядке."
                                ),
                                last_error=columns_message,
                            )
                            logger.error("Observer: %s", columns_message)
                            try:
                                await broadcast_observer_runtime_message(
                                    text=_build_missing_columns_alert_text(missing_columns),
                                    fallback_token=tg_token or telegram_bot_token,
                                    fallback_chat_id=telegram_chat_id,
                                )
                            except Exception:
                                logger.exception(
                                    "Не удалось отправить Telegram-алерт о колонках Ads Manager"
                                )
                            continue

                        consecutive_column_validation_errors = 0

                        status_ref[0] = "RUNNING"
                        message_ref[0] = "Выполняем цикл сканирования объявлений."
                        await update_observer_runtime_status(
                            status="RUNNING",
                            message="Выполняем цикл сканирования объявлений.",
                            clear_last_error=True,
                        )

                        # Инкрементируем scan_id один раз на полный цикл скана — все
                        # снэпшоты этого батча получат одинаковый last_scan_id.
                        current_scan_id = await _increment_scan_id()
                        # Инжектируем scan_id в structlog contextvars — появится в каждом лог-событии цикла
                        bind_contextvars(scan_id=current_scan_id)

                        # Создаём черновик в scan_runs: outcome='RUNNING', finished_at=NULL
                        factory_for_run = get_session_factory()
                        async with factory_for_run() as run_session:
                            run_id = await begin_scan_run(run_session, scan_id=current_scan_id)
                            await run_session.commit()

                        # 1-2. Сканирование через gRPC browser-agent: refresh + первый проход
                        # settle_delay_seconds=0.0 — фиксированный sleep после refresh убран.
                        # Ожидание реальных строк/стабильности DOM на TS-стороне делает его лишним.
                        await set_observer_phase("scrolling")
                        scan_events = grpc_client.run_scan_cycle(
                            max_scroll_passes=50,
                            do_refresh=True,
                            reset_scroll_first=True,
                            settle_delay_seconds=0.0,
                        )
                        timing["validate_columns_done"] = _time.monotonic()
                        async for event in scan_events:
                            # Проверяем, не отключил ли пользователь сканирование в настройках
                            if not await check_scanning_enabled():
                                logger.info(
                                    "Observer: сканирование отключено пользователем, экстренно закрываем поток"
                                )
                                close_stream = getattr(scan_events, "aclose", None)
                                if close_stream is not None:
                                    await close_stream()
                                await _ensure_scan_run_finished_with(
                                    outcome="INTERRUPTED",
                                    error_kind="user_disabled",
                                    error_message="Сканирование отключено пользователем посреди стрима",
                                )
                                break

                            if isinstance(event, ScanResult):
                                scan_result_obj = event
                                await set_observer_phase("parsing")
                                timing["scan_result"] = _time.monotonic()
                                rows = event.rows
                                logger.info(
                                    "Observer: сканирование завершено — %d строк за %.1fs (%d проходов)",
                                    len(rows),
                                    event.duration_seconds,
                                    event.total_passes,
                                )
                                # Проверяем неизвестные модальные окна (Wave 1 modal-dismisser)
                                unknown_modals = getattr(event, "unknown_modal_artifacts", []) or []
                                if unknown_modals:
                                    _tg_chat_for_healing = (
                                        tg_destinations[0].chat_id
                                        if tg_destinations
                                        else telegram_chat_id
                                    )
                                    await _self_healing.handle_unknown_modal_artifacts(
                                        unknown_modals,
                                        tg_client=tg_client,
                                        tg_chat_id=_tg_chat_for_healing,
                                    )
                            elif isinstance(event, ScanProgress):
                                if "first_progress" not in timing:
                                    timing["first_progress"] = _time.monotonic()
                                for row in event.new_rows:
                                    scanned_rows_by_id[row.fb_ad_id] = row
                                logger.debug(
                                    "Observer: проход %d — %d строк пока, at_bottom=%s",
                                    event.pass_number,
                                    event.rows_so_far,
                                    event.at_bottom,
                                )
                                if not event.new_rows:
                                    continue

                                progress_ad_states = dict(ad_states)
                                (
                                    progress_alerts,
                                    progress_stop_alerts,
                                    progress_snapshot_batch,
                                ) = await _run_scan_cycle(
                                    offers=offers,
                                    rows=event.new_rows,
                                    ad_states=progress_ad_states,
                                    fake_deposits_map=fake_deposits_map,
                                    current_scan_id=current_scan_id,
                                )
                                if not progress_stop_alerts:
                                    continue

                                _merge_progress_into_fast_stop(
                                    progress_alerts=progress_alerts,
                                    progress_stop_alerts=progress_stop_alerts,
                                    progress_snapshot_batch=progress_snapshot_batch,
                                    progress_ad_states=progress_ad_states,
                                    ad_states=ad_states,
                                    alerts_to_send=alerts_to_send,
                                    stop_alerts=stop_alerts,
                                    snapshot_batch=snapshot_batch,
                                )
                                fast_stop_triggered = True
                                rows = list(scanned_rows_by_id.values())
                                logger.info(
                                    "Быстрый стоп: STOP найден на проходе %d, завершаю сканирование досрочно",
                                    event.pass_number,
                                )
                                close_stream = getattr(scan_events, "aclose", None)
                                if close_stream is not None:
                                    await close_stream()
                                break

                except BrowserLockTimeoutError as exc:
                    consecutive_empty_scan_cycles = 0
                    await _ensure_scan_run_finished_with(
                        outcome="INTERRUPTED",
                        error_kind="browser_lock_timeout",
                        error_message=str(exc),
                    )
                    status_ref[0] = "WAITING_BROWSER"
                    message_ref[0] = (
                        "Браузер занят другой операцией. Сканирование будет повторено позже."
                    )
                    await update_observer_runtime_status(
                        status="WAITING_BROWSER",
                        message="Браузер занят другой операцией. Сканирование будет повторено позже.",
                        last_error=str(exc),
                    )
                    logger.warning("Observer: %s", exc)
                    await asyncio.sleep(BROWSER_QUEUE_SCAN_PAUSE_SECONDS)
                    continue

                if fast_stop_triggered:
                    consecutive_empty_scan_cycles = 0
                    await _process_fast_stop_results(
                        stop_alerts=stop_alerts,
                        snapshot_batch=snapshot_batch,
                        current_scan_id=current_scan_id,
                    )
                    # WARNING-алерты и non-STOP снэпшоты обрабатываем здесь же,
                    # без полного _process_scan_results: тяжёлые reconcile_* и
                    # сбор напоминаний — задача следующего полного цикла.
                    stop_ad_ids = {a.fb_ad_id for a in stop_alerts}
                    non_stop_snapshots = [
                        s for s in snapshot_batch if s.get("fb_ad_id") not in stop_ad_ids
                    ]
                    if non_stop_snapshots:
                        try:
                            await batch_save_snapshots(
                                non_stop_snapshots,
                                _scan_guard,
                                regression_guard=_regression_guard,
                                current_scan_id=current_scan_id,
                            )
                        except Exception:
                            logger.warning(
                                "Не удалось сохранить non-STOP снэпшоты в fast-stop пути",
                                exc_info=True,
                            )
                    warning_alerts = [a for a in alerts_to_send if a.stage != AlertStage.STOP]
                    if warning_alerts and tg_client:
                        for destination in tg_destinations:
                            try:
                                await _send_alerts_to_telegram(
                                    tg_client, destination, warning_alerts
                                )
                            except Exception:
                                logger.warning(
                                    "Не удалось отправить WARNING-алерты в fast-stop",
                                    exc_info=True,
                                )
                    await _ensure_scan_run_finished_with(outcome="OK")
                    # Фиксируем успешный scan для «пульса» (fast-stop путь)
                    await record_successful_scan()
                else:
                    if not rows:
                        consecutive_empty_scan_cycles += 1
                        empty_scan_reason = _build_empty_scan_reason()
                        logger.warning(
                            "Observer: пустой scan-цикл %s/%s — %s",
                            consecutive_empty_scan_cycles,
                            EMPTY_SCAN_FAILURE_LIMIT,
                            empty_scan_reason,
                        )

                        if consecutive_empty_scan_cycles < EMPTY_SCAN_FAILURE_LIMIT:
                            await _update_scan_recovery_status(
                                reason=empty_scan_reason,
                                attempt=consecutive_empty_scan_cycles,
                                max_attempts=EMPTY_SCAN_FAILURE_LIMIT,
                                retry_delay_seconds=EMPTY_SCAN_RETRY_DELAY_SECONDS,
                            )
                            await _ensure_scan_run_finished_with(
                                outcome="EMPTY_BAD",
                                error_kind="empty_scan",
                                error_message=empty_scan_reason,
                            )
                            await asyncio.sleep(EMPTY_SCAN_RETRY_DELAY_SECONDS)
                            continue

                        await _ensure_scan_run_finished_with(
                            outcome="EMPTY_BAD",
                            error_kind="empty_scan",
                            error_message=empty_scan_reason,
                        )
                        raise ScanDataUnavailableError(
                            attempts=consecutive_empty_scan_cycles,
                            retry_interval_seconds=EMPTY_SCAN_RETRY_DELAY_SECONDS,
                            reason=empty_scan_reason,
                        )

                    consecutive_empty_scan_cycles = 0

                    # 3. Оценка правил, FSM-переходы, сбор алертов
                    await set_observer_phase("evaluating")
                    alerts_to_send, stop_alerts, snapshot_batch = await _run_scan_cycle(
                        offers=offers,
                        rows=rows,
                        ad_states=ad_states,
                        fake_deposits_map=fake_deposits_map,
                        current_scan_id=current_scan_id,
                    )

                    # 4. Сохранение снэпшотов, disable tasks, отправка алертов в TG
                    await _process_scan_results(
                        alerts_to_send=alerts_to_send,
                        stop_alerts=stop_alerts,
                        snapshot_batch=snapshot_batch,
                        tg_client=tg_client,
                        tg_destinations=tg_destinations,
                        current_scan_id=current_scan_id,
                    )

                    # Классификация outcome для записи в scan_runs
                    stale_threshold = 0.9
                    try:
                        factory_for_threshold = get_session_factory()
                        async with factory_for_threshold() as settings_session:
                            observer_settings_for_threshold = await get_or_create_observer_settings(
                                settings_session
                            )
                            stale_threshold = float(
                                getattr(
                                    observer_settings_for_threshold,
                                    "stale_data_threshold",
                                    0.9,
                                )
                                or 0.9
                            )
                    except Exception:
                        logger.warning(
                            "Не удалось прочитать stale_data_threshold, использую 0.9",
                            exc_info=True,
                        )

                    ad_ids_in_scan = [
                        getattr(r, "fb_ad_id", "") for r in rows if getattr(r, "fb_ad_id", "")
                    ]
                    try:
                        history_ids = await load_history_ad_ids_with_metrics(
                            ad_ids_in_scan, lookback_hours=24
                        )
                    except Exception:
                        history_ids = set()
                        logger.warning("history-предикат не доступен", exc_info=True)

                    if scan_result_obj is not None:
                        outcome_details = classify_scan_outcome(
                            scan_result_obj,
                            stale_threshold=stale_threshold,
                            has_history_for_ids=lambda ids, _h=history_ids: bool(
                                _h.intersection(ids)
                            ),
                        )
                    else:
                        # Fallback (fast-stop путь): считаем OK
                        outcome_details = None

                    # STALE_DATA: эскалируем hard reload
                    if (
                        outcome_details is not None
                        and outcome_details.kind == ScanOutcome.STALE_DATA
                    ):
                        step = stale_escalator.next_action()
                        logger.warning(
                            "Observer: STALE_DATA попытка %d, action=%s, stale_ratio=%.2f",
                            step.attempt,
                            step.kind.value,
                            outcome_details.stale_ratio,
                        )
                        if step.kind == StaleAction.HARD_RELOAD:
                            try:
                                await grpc_client.hard_reload(bypass_cache=True)
                            except Exception:
                                logger.warning("hard_reload упал", exc_info=True)
                        if step.should_send_alert:
                            try:
                                await broadcast_observer_runtime_message(
                                    text=(
                                        "🚨 Ads Manager не отдаёт метрики уже 5 циклов подряд. "
                                        "Перезагружаю с очисткой кеша. Проверь сеть/прокси."
                                    ),
                                    fallback_token=tg_token or telegram_bot_token,
                                    fallback_chat_id=telegram_chat_id,
                                )
                            except Exception:
                                logger.exception("Не удалось отправить TG-алерт STALE_DATA")
                    else:
                        # Любой не-STALE_DATA исход — сбрасываем счётчик
                        stale_escalator.reset()

                    # BROWSER_LOST счётчик сбрасываем — мы дошли до здесь, значит браузер жив
                    recovery_escalator.reset()

                    # Финиш scan_runs записи
                    if run_id is not None and outcome_details is not None:
                        rows_with_data = max(
                            0,
                            len(rows)
                            - (
                                scan_result_obj.rows_with_all_metrics_empty
                                if scan_result_obj
                                else 0
                            ),
                        )
                        try:
                            async with factory_for_run() as run_session:
                                await finish_scan_run(
                                    run_session,
                                    run_id=run_id,
                                    outcome=outcome_details.kind.value,
                                    rows_total=len(rows),
                                    rows_partial=outcome_details.partial_count,
                                    rows_with_data=rows_with_data,
                                    alerts_warning=len(
                                        [a for a in alerts_to_send if a.stage == AlertStage.WARNING]
                                    ),
                                    alerts_stop=len(stop_alerts),
                                    phase_timings=scan_result_obj.phase_timings
                                    if scan_result_obj
                                    else None,
                                    warnings=scan_result_obj.warnings if scan_result_obj else None,
                                    empty_reason=outcome_details.empty_reason,
                                    error_kind=None,
                                    error_message=outcome_details.note or None,
                                    threat_level=None,
                                    next_interval_s=None,
                                )
                                await run_session.commit()
                                run_id = None
                        except Exception:
                            logger.exception("Не удалось записать scan_run finish")

                # Успешный цикл — сбрасываем счётчик ошибок браузера и self-healing
                consecutive_browser_errors = 0
                consecutive_parser_missing_columns_errors = 0
                _self_healing.record_success()
                cycle_completed = True
                # Фиксируем успешный scan для «пульса» (обычный путь)
                await record_successful_scan()
                # Очищаем scan_id из structlog contextvars по окончании цикла
                unbind_contextvars("scan_id")

                # Публикуем событие scan_finished для подписчиков WS-дашборда
                try:
                    _cycle_duration = _time.monotonic() - timing.get(
                        "cycle_start", _time.monotonic()
                    )
                    await _pubsub.publish(
                        CHANNEL_SCAN_FINISHED,
                        {
                            "type": "scan_finished",
                            "scan_id": current_scan_id,
                            "ads_count": len(rows),
                            "alerts_count": len(alerts_to_send),
                            "duration_sec": round(_cycle_duration, 1),
                        },
                    )
                    # Публикуем отдельное событие для каждого нового алерта
                    for _alert in alerts_to_send:
                        await _pubsub.publish(
                            CHANNEL_ALERT_CREATED,
                            {
                                "type": "alert_created",
                                "ad_id": _alert.fb_ad_id,
                                "ad_name": _alert.ad_name,
                                "rule_name": _alert.matched_rule_codes[0]
                                if _alert.matched_rule_codes
                                else "",
                                "stage": _alert.stage.value if _alert.stage else "",
                            },
                        )
                except Exception:
                    logger.debug("Observer: не удалось опубликовать scan_finished", exc_info=True)

                # Timing-лог: одна строка в формате OBSERVER_TIMING cycle=… preflight_ms=…
                # для пост-фактум агрегации через tools/timing_percentiles.py.
                try:
                    _ts0 = timing.get("cycle_start", 0.0)
                    _now = _time.monotonic()
                    _parts = [f"cycle={cycle_count}"]
                    _preflight = timing.get("preflight_end")
                    _lock = timing.get("lock_acquired")
                    _validate = timing.get("validate_columns_done")
                    _first_prog = timing.get("first_progress")
                    _scan_res = timing.get("scan_result")
                    if _preflight is not None:
                        _parts.append(f"preflight_ms={int((_preflight - _ts0) * 1000)}")
                    if _lock is not None and _preflight is not None:
                        _parts.append(f"lock_wait_ms={int((_lock - _preflight) * 1000)}")
                    if _validate is not None and _lock is not None:
                        _parts.append(f"validate_ms={int((_validate - _lock) * 1000)}")
                    if _first_prog is not None and _validate is not None:
                        _parts.append(
                            f"refresh_to_first_row_ms={int((_first_prog - _validate) * 1000)}"
                        )
                    if _scan_res is not None and _validate is not None:
                        _parts.append(f"scan_total_ms={int((_scan_res - _validate) * 1000)}")
                    _parts.append(f"rows={len(rows)}")
                    _parts.append(f"cycle_ms={int((_now - _ts0) * 1000)}")
                    logger.info("OBSERVER_TIMING %s", " ".join(_parts))
                except Exception:
                    pass

            except ScanDataUnavailableError as exc:
                consecutive_empty_scan_cycles = 0
                runtime_message = str(exc)
                status_ref[0] = "PAUSED"
                message_ref[0] = runtime_message
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
                    logger.exception(
                        "Не удалось отправить Telegram-алерт о недоступности данных скана"
                    )

                # Считаем ScanDataUnavailableError провалом цикла для self-healing
                _tg_chat_for_healing = (
                    tg_destinations[0].chat_id if tg_destinations else telegram_chat_id
                )
                await _self_healing.record_failure(
                    grpc_client=grpc_client,
                    tg_client=tg_client,
                    tg_chat_id=_tg_chat_for_healing,
                )
                continue

            except TimeoutError:
                await _ensure_scan_run_finished_with(
                    outcome="INTERRUPTED",
                    error_kind="timeout",
                    error_message="TimeoutError при ожидании DOM",
                )
                consecutive_empty_scan_cycles = 0
                # asyncio.timeout бросает TimeoutError (BaseException в Python 3.11+).
                # Ловим отдельно, чтобы таймаут DOM-стабилизации не крашил весь loop.
                logger.warning("Observer: таймаут ожидания DOM-стабилизации, пропускаем цикл")
                status_ref[0] = "WARNING"
                message_ref[0] = (
                    "Таймаут ожидания данных Ads Manager. Следующий цикл запустится по расписанию."
                )
                await update_observer_runtime_status(
                    status="WARNING",
                    message="Таймаут ожидания данных Ads Manager. Следующий цикл запустится по расписанию.",
                    last_error="TimeoutError при ожидании DOM",
                )
                _tg_chat_for_healing = (
                    tg_destinations[0].chat_id if tg_destinations else telegram_chat_id
                )
                await _self_healing.record_failure(
                    grpc_client=grpc_client,
                    tg_client=tg_client,
                    tg_chat_id=_tg_chat_for_healing,
                )
                continue

            except Exception as exc:
                # Если был открыт run_id и ещё не закрыт — фиксируем как INTERRUPTED/BROWSER_LOST
                if run_id is not None:
                    factory_for_run = get_session_factory()
                    try:
                        async with factory_for_run() as run_session:
                            await finish_scan_run(
                                run_session,
                                run_id=run_id,
                                outcome="BROWSER_LOST"
                                if _is_browser_connection_error(exc)
                                else "INTERRUPTED",
                                error_kind="browser_disconnect"
                                if _is_browser_connection_error(exc)
                                else "internal",
                                error_message=str(exc)[:500],
                            )
                            await run_session.commit()
                            run_id = None
                    except Exception:
                        logger.exception("Не удалось записать scan_run при ошибке")

                consecutive_empty_scan_cycles = 0
                exc_message_text = str(exc)
                if _PARSER_MISSING_COLUMNS_MARKER in exc_message_text:
                    parser_missing_columns = _extract_parser_missing_columns(exc_message_text)
                    consecutive_parser_missing_columns_errors += 1
                    columns_message = (
                        "Парсер Ads Manager не смог прочитать колонки "
                        f"({consecutive_parser_missing_columns_errors}/"
                        f"{PARSER_MISSING_COLUMNS_FAILURE_LIMIT}): "
                        f"{', '.join(parser_missing_columns) or 'детали не вернулись'}"
                    )

                    if (
                        consecutive_parser_missing_columns_errors
                        < PARSER_MISSING_COLUMNS_FAILURE_LIMIT
                    ):
                        status_ref[0] = "WARNING"
                        message_ref[0] = columns_message
                        await update_observer_runtime_status(
                            status="WARNING",
                            message=(
                                "Парсер Ads Manager временно не дочитал колонки в строках таблицы "
                                "(вероятно, виртуализация скрыла правые ячейки). "
                                "Пробую переподключиться и повторить скан."
                            ),
                            last_error=columns_message,
                        )
                        logger.warning("Observer: %s", columns_message)
                        try:
                            await grpc_client.reconnect_browser()
                        except Exception:
                            logger.warning(
                                "Observer: не удалось переподключиться после сбоя парсера колонок",
                                exc_info=True,
                            )
                        await asyncio.sleep(PARSER_MISSING_COLUMNS_RETRY_DELAY_SECONDS)
                        continue

                    consecutive_parser_missing_columns_errors = 0
                    status_ref[0] = "PAUSED"
                    message_ref[0] = columns_message
                    await update_observer_runtime_status(
                        status="PAUSED",
                        message=(
                            "Сканирование остановлено: парсер не смог прочитать обязательные "
                            "колонки Ads Manager в строках таблицы "
                            f"{PARSER_MISSING_COLUMNS_FAILURE_LIMIT} раза подряд."
                        ),
                        last_error=columns_message,
                    )
                    logger.error("Observer: %s", columns_message)
                    try:
                        await broadcast_observer_runtime_message(
                            text=_build_missing_columns_alert_text(
                                parser_missing_columns or ["детали не вернулись"]
                            ),
                            fallback_token=tg_token or telegram_bot_token,
                            fallback_chat_id=telegram_chat_id,
                        )
                    except Exception:
                        logger.exception(
                            "Не удалось отправить Telegram-алерт о парсере колонок Ads Manager"
                        )
                    continue

                if _is_browser_connection_error(exc) or isinstance(exc, grpc.RpcError):
                    runtime_message = format_observer_runtime_message(exc)
                    status_ref[0] = "ERROR"
                    message_ref[0] = runtime_message
                    await update_observer_runtime_status(
                        status="ERROR",
                        message=runtime_message,
                        last_error=runtime_message,
                    )
                    consecutive_browser_errors += 1
                    _tg_chat_for_healing = (
                        tg_destinations[0].chat_id if tg_destinations else telegram_chat_id
                    )
                    await _self_healing.record_failure(
                        grpc_client=grpc_client,
                        tg_client=tg_client,
                        tg_chat_id=_tg_chat_for_healing,
                    )
                    step = recovery_escalator.next_step()
                    logger.warning(
                        "Observer: BROWSER_LOST попытка %d, sleep %ds",
                        step.attempt,
                        step.sleep_seconds,
                    )
                    if step.should_send_alert:
                        try:
                            await broadcast_observer_runtime_message(
                                text=(
                                    "🚨 Observer не может подключиться к браузеру 5 циклов подряд. "
                                    "Проверь Vision."
                                ),
                                fallback_token=tg_token or telegram_bot_token,
                                fallback_chat_id=telegram_chat_id,
                            )
                        except Exception:
                            logger.exception("Не удалось отправить TG-алерт BROWSER_LOST")
                    await asyncio.sleep(step.sleep_seconds)
                    continue

                runtime_message = (
                    format_observer_runtime_message(exc) or "Внутренняя ошибка в цикле observer."
                )
                status_ref[0] = "ERROR"
                message_ref[0] = runtime_message
                await update_observer_runtime_status(
                    status="ERROR",
                    message=runtime_message,
                    last_error=runtime_message,
                )
                logger.exception("Observer: ошибка в цикле")

            # 5. Адаптивный интервал: вычисляем по уровню угрозы
            adaptive_interval_secs = 0
            threat_level_name = "IDLE"
            if cycle_completed:
                adaptive_interval_secs, threat_level_name = compute_adaptive_interval(
                    snapshot_batch,
                    has_stop_alerts=bool(stop_alerts),
                )
                # Немедленный ре-скан после STOP: пропускаем ожидание
                if adaptive_interval_secs == 0:
                    logger.info(
                        "Observer: обнаружен STOP-алерт — немедленный ре-скан (пропускаем ожидание)"
                    )
                    status_ref[0] = "RUNNING"
                    message_ref[0] = "STOP обнаружен — немедленный ре-скан."
                    await update_observer_runtime_status(
                        status="RUNNING",
                        message="STOP обнаружен — немедленный ре-скан.",
                        clear_scan_schedule=True,
                        clear_last_error=True,
                    )
                    continue

                status_ref[0] = "RUNNING"
                message_ref[0] = (
                    f"Цикл завершён. Угроза: {threat_level_name}, "
                    f"интервал: {adaptive_interval_secs}с."
                )
                await update_observer_runtime_status(
                    status="RUNNING",
                    message=(
                        f"Цикл завершён. Угроза: {threat_level_name}, "
                        f"интервал: {adaptive_interval_secs}с."
                    ),
                    current_scan_interval_seconds=adaptive_interval_secs,
                    current_scan_jitter_seconds=max(1, adaptive_interval_secs // 10),
                    current_scan_threat_level=threat_level_name,
                    clear_last_error=True,
                )

            # 6. Прерываемый сон с адаптивным интервалом + поллинг scan_requested
            await set_observer_phase("sleeping")
            should_continue = await _wait_for_next_cycle(
                shutdown_event=shutdown_event,
                cycle_completed=cycle_completed,
                adaptive_interval=adaptive_interval_secs if cycle_completed else None,
                threat_level=threat_level_name if cycle_completed else None,
            )
            if not should_continue:
                return
    finally:
        try:
            await set_observer_phase(None)
        except Exception:
            pass
        # Закрываем publish-соединение шины событий
        try:
            await _pubsub.close()
        except Exception:
            pass
        # Отменяем фоновый heartbeat при выходе из цикла
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
