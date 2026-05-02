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
from core.observer.db_queries import (
    check_scan_requested_flag,
    check_scanning_enabled,
    check_vision_reconnect_flag,
    collect_reminder_alerts,
    get_disable_queue_pause_reason,
    get_enable_queue_pause_reason,
    load_active_snooze_ad_ids,
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
    reconcile_enable_tasks_in_db,
)
from core.observer.regression_guard import RegressionGuard
from core.observer.runtime_status import (
    format_observer_runtime_message,
    update_observer_runtime_status,
)
from core.observer.scan_guard import ZeroScanGuard
from core.observer.self_healing import SelfHealingEscalator
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
from core.telegram.client import TelegramBotClient
from core.telegram.delivery import broadcast_observer_runtime_message
from core.telegram.message_refs import (
    load_message_refs_by_chat,
    stream_for_alert_stage,
    upsert_message_ref,
)
from core.telegram.messaging import safe_edit_or_send_message
from core.telegram.renderer import TelegramAlertItem, render_alert_message

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
# Пустой scan ещё не считаем фатальным: сначала даём странице несколько шансов вернуть строки таблицы.
EMPTY_SCAN_FAILURE_LIMIT = 3
EMPTY_SCAN_RETRY_DELAY_SECONDS = 10.0
# Адаптивное ожидание загрузки данных после refresh
DATA_LOAD_POLL_INTERVAL_SECONDS = 2.0
DATA_LOAD_MAX_WAIT_SECONDS = 30.0
DATA_LOAD_LOG_INTERVAL_SECONDS = 10.0

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
    await update_observer_runtime_status(
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
) -> None:
    """Обработка результатов скана: сохранение снэпшотов, алерты, disable tasks."""
    # Батчевый upsert снэпшотов
    snapshots_saved = False
    try:
        snapshots_saved = await batch_save_snapshots(
            snapshot_batch, _scan_guard, regression_guard=_regression_guard
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
        )
        if saved:
            logger.info(
                "Быстрый стоп: сохранено %s STOP-снэпшотов до конца сканирования",
                len(stop_snapshots),
            )

    await auto_create_disable_tasks(stop_alerts)
    logger.info("Быстрый стоп: создано или проверено задач отключения: %s", len(stop_alerts))


async def _wait_for_data_load(
    grpc_client: BrowserAgentClient,
    *,
    prev_had_spend: bool,
    initial_rows: list[ScannedAdRow],
) -> list[ScannedAdRow]:
    """Адаптивное ожидание загрузки данных после refresh.

    Если в предыдущем скане был spend > 0 — поллим до появления spend.
    Если предыдущий скан тоже был нулевым (начало дня) — сразу возвращаем текущие строки.

    Возвращает полный список строк, не заменяя его подозрительно частичным чтением.
    """
    if any(r.spend and r.spend > 0 for r in initial_rows):
        return initial_rows

    if not prev_had_spend:
        # Начало дня или первый скан — не ждём, данные нулевые по природе.
        return initial_rows

    elapsed = 0.0
    last_log_at = 0.0
    best_rows = initial_rows

    while elapsed < DATA_LOAD_MAX_WAIT_SECONDS:
        # Читаем текущее состояние таблицы без refresh, но всегда с верхней строки,
        # чтобы не заменить полный scan нижним фрагментом виртуальной таблицы.
        current_rows: list[ScannedAdRow] = []
        async for event in grpc_client.run_scan_cycle(
            max_scroll_passes=50,
            do_refresh=False,
            reset_scroll_first=True,
            settle_delay_seconds=0.0,
        ):
            if isinstance(event, ScanResult):
                current_rows = event.rows

        merged_rows = _merge_scan_rows(best_rows, current_rows)
        if len(merged_rows) >= len(best_rows):
            best_rows = merged_rows

        # Проверяем: есть ли хоть одно объявление со spend > 0
        if any(r.spend and r.spend > 0 for r in best_rows) and len(best_rows) >= len(initial_rows):
            logger.info(
                "Observer: данные загружены за %.0f сек (spend > 0 обнаружен)",
                elapsed,
            )
            return best_rows

        elapsed += DATA_LOAD_POLL_INTERVAL_SECONDS
        if elapsed - last_log_at >= DATA_LOAD_LOG_INTERVAL_SECONDS:
            logger.info(
                "Ожидание загрузки данных: %.0fс из %.0fс",
                elapsed,
                DATA_LOAD_MAX_WAIT_SECONDS,
            )
            last_log_at = elapsed

        if elapsed < DATA_LOAD_MAX_WAIT_SECONDS:
            await asyncio.sleep(DATA_LOAD_POLL_INTERVAL_SECONDS)

    logger.warning(
        "Observer: данные не появились за %.0f сек — продолжаем с последним полным срезом",
        DATA_LOAD_MAX_WAIT_SECONDS,
    )
    return best_rows


def _merge_scan_rows(
    base_rows: list[ScannedAdRow],
    update_rows: list[ScannedAdRow],
) -> list[ScannedAdRow]:
    """Обновляет строки по fb_ad_id, не теряя объявления из полного базового скана."""
    if not update_rows:
        return base_rows

    updates_by_id = {row.fb_ad_id: row for row in update_rows if getattr(row, "fb_ad_id", None)}
    seen_ids: set[str] = set()
    merged: list[ScannedAdRow] = []

    for row in base_rows:
        fb_ad_id = getattr(row, "fb_ad_id", None)
        if fb_ad_id and fb_ad_id in updates_by_id:
            merged.append(updates_by_id[fb_ad_id])
            seen_ids.add(fb_ad_id)
        else:
            merged.append(row)
            if fb_ad_id:
                seen_ids.add(fb_ad_id)

    for row in update_rows:
        fb_ad_id = getattr(row, "fb_ad_id", None)
        if not fb_ad_id or fb_ad_id in seen_ids:
            continue
        merged.append(row)
        seen_ids.add(fb_ad_id)

    return merged


async def _wait_for_next_cycle(
    *,
    shutdown_event: asyncio.Event | None,
    cycle_completed: bool,
    adaptive_interval: int | None = None,
    threat_level: str | None = None,
) -> bool:
    """Прерываемый сон между циклами с поллингом флагов.

    Интервал определяется адаптивно по уровню угрозы.
    Возвращает True если нужно продолжить (не получен сигнал остановки).
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


async def observer_loop(
    *,
    grpc_client: BrowserAgentClient,
    offers: dict,
    telegram_bot_token: str,
    telegram_chat_id: str,
    warning_percent_of_stop: Decimal = DEFAULT_WARNING_PERCENT_OF_STOP,
    stop_percent_of_base: Decimal = DEFAULT_STOP_PERCENT_OF_BASE,
    on_snapshot_update=None,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Основной бесконечный цикл observer.

    Интервал между сканами определяется адаптивно по уровню угрозы:
    IMMEDIATE (0с) → CRITICAL (10с) → ELEVATED (13с) → ACTIVE (15с)
    → CALM (30с) → IDLE (55с).
    После обнаружения STOP — немедленный ре-скан.

    Args:
        grpc_client: BrowserAgentClient для связи с Node.js browser-agent
        offers: dict[offer_code -> {offer, rule_config}]
        telegram_bot_token: токен TG-бота
        telegram_chat_id: ID чата для уведомлений
        warning_percent_of_stop: legacy warning для обратной совместимости
        stop_percent_of_base: legacy stop для обратной совместимости
        on_snapshot_update: callback для сохранения snapshot в БД
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

    observer_thresholds = extract_observer_threshold_values(
        {
            "warning_percent_of_stop": warning_percent_of_stop,
            "stop_percent_of_base": stop_percent_of_base,
        }
    )

    # Загружаем пороги observer из БД при старте
    try:
        observer_thresholds = await load_observer_settings_from_db()
        logger.info(
            "Пороги observer из БД: "
            "warning(CPC/CPL/CPR)=%.0f/%.0f/%.0f%%, stop(CPC/CPL/CPR)=%.0f/%.0f/%.0f%%",
            observer_thresholds["cpc_warning_percent_of_stop"],
            observer_thresholds["cpl_warning_percent_of_stop"],
            observer_thresholds["cpr_warning_percent_of_stop"],
            observer_thresholds["cpc_stop_percent_of_base"],
            observer_thresholds["cpl_stop_percent_of_base"],
            observer_thresholds["cpr_stop_percent_of_base"],
        )
    except Exception:
        logger.warning("Не удалось загрузить пороги observer из БД", exc_info=True)

    await update_observer_runtime_status(
        status="RUNNING",
        message="Observer подключён к браузеру и готовит первый цикл сканирования.",
        clear_last_error=True,
    )

    # Счётчик циклов для периодической перезагрузки офферов и TG настроек
    cycle_count = 0
    fake_deposits_map: dict[str, int] = {}
    RELOAD_EVERY = 10  # Перечитываем офферы, TG настройки и интервал каждые 10 циклов
    # Флаг: был ли spend > 0 в предыдущем успешном скане (для адаптивного ожидания)
    prev_scan_had_spend = False

    # Счётчик последовательных ошибок браузера (задача 2.4)
    consecutive_browser_errors = 0
    consecutive_column_validation_errors = 0
    consecutive_empty_scan_cycles = 0
    browser_pause_kind: str | None = None

    def _should_stop() -> bool:
        """Проверяет, нужно ли завершить работу."""
        return shutdown_event is not None and shutdown_event.is_set()

    while not _should_stop():
        cycle_completed = False
        alerts_to_send: list[AlertCandidate] = []
        stop_alerts: list[AlertCandidate] = []
        snapshot_batch: list[dict] = []
        fast_stop_triggered = False
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

                # Перечитываем пороги из БД
                try:
                    observer_thresholds = await load_observer_settings_from_db()
                except Exception:
                    logger.debug("Не удалось обновить пороги observer из БД", exc_info=True)

                # Проверяем флаг переподключения к браузеру
                try:
                    if await check_vision_reconnect_flag():
                        logger.info("Переподключение к Vision браузеру по запросу из UI")
                        await grpc_client.reconnect_browser()
                except Exception:
                    logger.warning("Не удалось выполнить переподключение к браузеру", exc_info=True)

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

            # Проверяем флаг is_scanning_enabled перед каждым сканом
            if not await check_scanning_enabled():
                consecutive_empty_scan_cycles = 0
                await update_observer_runtime_status(
                    status="PAUSED",
                    message="Сканирование выключено в настройках.",
                    clear_scan_schedule=True,
                )
                logger.info("Observer: сканирование отключено, пропускаем цикл")
                # Короткий сон перед следующей проверкой
                await asyncio.sleep(10.0)
                continue

            browser_pause_kind_next, browser_pause_reason = await _get_browser_queue_pause()

            if browser_pause_reason and browser_pause_kind_next == "disable":
                consecutive_empty_scan_cycles = 0
                await update_observer_runtime_status(
                    status="WAITING_BROWSER",
                    message=(f"Браузер занят задачами отключения. Причина: {browser_pause_reason}"),
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
                await update_observer_runtime_status(
                    status="WAITING_BROWSER",
                    message=(f"Браузер занят задачами включения. Причина: {browser_pause_reason}"),
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
                logger.info("Observer: очередь отключения освободила браузер — возобновляю скан")
                browser_pause_kind = None
            elif browser_pause_kind == "enable":
                logger.info("Observer: очередь включения освободила браузер — возобновляю скан")
                browser_pause_kind = None

            rows: list[ScannedAdRow] = []
            scanned_rows_by_id: dict[str, ScannedAdRow] = {}
            try:
                async with acquire_browser_lock(
                    owner="observer-scan",
                    timeout_seconds=BROWSER_SCAN_LOCK_TIMEOUT_SECONDS,
                ):
                    browser_pause_kind_next, browser_pause_reason = await _get_browser_queue_pause()
                    if browser_pause_reason:
                        consecutive_empty_scan_cycles = 0
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
                                last_error=validation_error or "Проверка колонок не вернула детали",
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

                        if consecutive_column_validation_errors < COLUMN_VALIDATION_FAILURE_LIMIT:
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

                        await set_observer_scanning_enabled(False)
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

                    await update_observer_runtime_status(
                        status="RUNNING",
                        message="Выполняем цикл сканирования объявлений.",
                        clear_last_error=True,
                    )

                    # 1-2. Сканирование через gRPC browser-agent: refresh + первый проход
                    scan_events = grpc_client.run_scan_cycle(
                        max_scroll_passes=50,
                        do_refresh=True,
                        reset_scroll_first=True,
                        settle_delay_seconds=random.uniform(2.0, 4.0),
                    )
                    async for event in scan_events:
                        if isinstance(event, ScanResult):
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
                                observer_thresholds=observer_thresholds,
                            )
                            if not progress_stop_alerts:
                                continue

                            stop_ids = {alert.fb_ad_id for alert in progress_stop_alerts}
                            for fb_ad_id in stop_ids:
                                if fb_ad_id in progress_ad_states:
                                    ad_states[fb_ad_id] = progress_ad_states[fb_ad_id]
                            fast_snapshot_batch = [
                                snap
                                for snap in progress_snapshot_batch
                                if snap.get("fb_ad_id") in stop_ids
                            ]
                            alerts_to_send.extend(
                                alert
                                for alert in progress_alerts
                                if alert.fb_ad_id in stop_ids and alert.stage == AlertStage.STOP
                            )
                            stop_alerts.extend(progress_stop_alerts)
                            snapshot_batch.extend(fast_snapshot_batch)
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

                    # 1b. Адаптивное ожидание загрузки данных после refresh.
                    if rows and not fast_stop_triggered:
                        rows = await _wait_for_data_load(
                            grpc_client,
                            prev_had_spend=prev_scan_had_spend,
                            initial_rows=rows,
                        )
            except BrowserLockTimeoutError as exc:
                consecutive_empty_scan_cycles = 0
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
                prev_scan_had_spend = prev_scan_had_spend or any(
                    r.spend and r.spend > 0 for r in rows
                )
                await _process_fast_stop_results(
                    stop_alerts=stop_alerts,
                    snapshot_batch=snapshot_batch,
                )
                await _process_scan_results(
                    alerts_to_send=alerts_to_send,
                    stop_alerts=[],
                    snapshot_batch=[],
                    tg_client=tg_client,
                    tg_destinations=tg_destinations,
                )
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
                        await asyncio.sleep(EMPTY_SCAN_RETRY_DELAY_SECONDS)
                        continue

                    raise ScanDataUnavailableError(
                        attempts=consecutive_empty_scan_cycles,
                        retry_interval_seconds=EMPTY_SCAN_RETRY_DELAY_SECONDS,
                        reason=empty_scan_reason,
                    )

                consecutive_empty_scan_cycles = 0

                # Обновляем флаг spend для следующего цикла адаптивного ожидания
                prev_scan_had_spend = any(r.spend and r.spend > 0 for r in rows)

                # 3. Оценка правил, FSM-переходы, сбор алертов
                alerts_to_send, stop_alerts, snapshot_batch = await _run_scan_cycle(
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
                )

            # Успешный цикл — сбрасываем счётчик ошибок браузера и self-healing
            consecutive_browser_errors = 0
            _self_healing.record_success()
            cycle_completed = True

        except ScanDataUnavailableError as exc:
            consecutive_empty_scan_cycles = 0
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
            consecutive_empty_scan_cycles = 0
            # asyncio.timeout бросает TimeoutError (BaseException в Python 3.11+).
            # Ловим отдельно, чтобы таймаут DOM-стабилизации не крашил весь loop.
            logger.warning("Observer: таймаут ожидания DOM-стабилизации, пропускаем цикл")
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
            consecutive_empty_scan_cycles = 0
            if _is_browser_connection_error(exc) or isinstance(exc, grpc.RpcError):
                runtime_message = format_observer_runtime_message(exc)
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
                await update_observer_runtime_status(
                    status="RUNNING",
                    message="STOP обнаружен — немедленный ре-скан.",
                    clear_scan_schedule=True,
                    clear_last_error=True,
                )
                continue

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
        should_continue = await _wait_for_next_cycle(
            shutdown_event=shutdown_event,
            cycle_completed=cycle_completed,
            adaptive_interval=adaptive_interval_secs if cycle_completed else None,
            threat_level=threat_level_name if cycle_completed else None,
        )
        if not should_continue:
            return
