# -*- coding: utf-8 -*-
"""Observer Worker: основной цикл — refresh → scroll → parse → evaluate → notify.

Единственный worker, который взаимодействует с Playwright.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time as _time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

from core.browser.humanizer import human_move, human_wheel_scroll
from core.browser.vision_client import VisionClient
from core.cabinet_day import (
    build_cabinet_day_archive_payload,
    has_any_metric_value,
    is_cabinet_day_reset_scan,
)
from core.db import get_session_factory
from core.diagnostics import (
    build_ad_quality_diagnostics,
    build_diagnostics_context_text,
    compute_cpm_baselines_by_offer,
)
from core.disable_tasks import (
    ACTIVE_DISABLE_TASK_STATUSES,
    SILENT_DISABLE_INCIDENT_RETRY_LIMIT,
    is_delivery_disabled,
    reconcile_disable_tasks,
)
from core.domain import AlertStage, AlertState, DisableTaskStatus
from core.models import (
    AdSnapshot,
    AlertEvent,
    CabinetDayArchive,
    DisableTask,
    ObserverSettings,  # noqa: F401 — используется в check_scan_requested_flag
    Offer,
    VisionSettings,
)
from core.observer.thresholds import (
    DEFAULT_STOP_PERCENT_OF_BASE,
    DEFAULT_WARNING_PERCENT_OF_STOP,
    extract_observer_threshold_values,
)

try:
    from patchright.async_api import Error as PatchrightError
except ModuleNotFoundError:  # pragma: no cover - зависит от окружения

    class PatchrightError(RuntimeError):
        """Фолбэк-тип ошибки, когда patchright недоступен в окружении."""


from core.observer.runtime_status import (
    format_observer_runtime_message,
    update_observer_runtime_status,
)
from core.observer.service import AlertCandidate, build_metrics_json, evaluate_row
from core.observer.state_machine import resolve_transition
from core.scanner.models import ScannedAdRow
from core.scanner.parser import refresh_table
from core.telegram.client import TelegramBotClient
from core.telegram.message_refs import (
    load_message_refs_by_chat,
    stream_for_alert_stage,
    upsert_message_ref,
)
from core.telegram.messaging import safe_edit_or_send_message
from core.telegram.renderer import TelegramAlertItem, render_alert_message
from core.telegram.service import TelegramDestination, load_telegram_runtime_config

logger = logging.getLogger(__name__)

# Максимальное количество попыток переподключения к браузеру
MAX_RECONNECT_ATTEMPTS = 5
# Базовая задержка для экспоненциального backoff (сек)
BASE_RECONNECT_DELAY = 10
# Пока очередь отключения не опустеет, observer не должен трогать общий браузер.
DISABLE_QUEUE_SCAN_PAUSE_SECONDS = 5.0
# После успешного клика Meta может ещё долго отдавать UNKNOWN/ACTIVE, поэтому
# не сбрасываем CLAIMED слишком рано и ждём подтверждение следующими сканами.
DISABLE_SUCCESS_CONFIRMATION_GRACE_TIMEOUT = timedelta(minutes=30)
# Напоминания в Telegram шлём только по объявлениям из актуальной скан-сессии.
ACTIVE_ALERT_WINDOW = timedelta(minutes=30)
MANUAL_ATTENTION_REASON_TITLE = "Нужна ручная проверка отключения"
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
_PENDING_ZERO_SCAN_CONFIRMATION_AT: datetime | None = None
_PENDING_PARTIAL_BATCH_CONFIRMATION_AT: datetime | None = None
_LAST_ACCEPTED_SNAPSHOT_BATCH_SIZE: int | None = None
_SUSPICIOUS_PARTIAL_BATCH_DROP_RATIO = 0.85
_SUSPICIOUS_PARTIAL_BATCH_MIN_DROP = 5


def _state_for_emitted_stage(stage: AlertStage) -> AlertState:
    """Возвращает состояние объявления для отправленного алерта."""
    if stage == AlertStage.STOP:
        return AlertState.CLAIMED
    if stage == AlertStage.WARNING:
        return AlertState.WARNING_SENT
    return AlertState.EARLY_SIGNAL_SENT


def _should_skip_snapshot_batch_persist(snapshot_data: list[dict]) -> bool:
    """Пропускает первый подозрительный zero-scan до повторного подтверждения."""
    global _LAST_ACCEPTED_SNAPSHOT_BATCH_SIZE
    global _PENDING_PARTIAL_BATCH_CONFIRMATION_AT
    global _PENDING_ZERO_SCAN_CONFIRMATION_AT

    if not snapshot_data:
        _PENDING_ZERO_SCAN_CONFIRMATION_AT = None
        _PENDING_PARTIAL_BATCH_CONFIRMATION_AT = None
        return False

    scan_started_at = max(
        (item.get("last_observed_at") for item in snapshot_data if item.get("last_observed_at")),
        default=datetime.now(UTC),
    )
    snapshot_count = len(snapshot_data)

    if not is_cabinet_day_reset_scan(snapshot_data):
        if _PENDING_ZERO_SCAN_CONFIRMATION_AT is not None:
            logger.warning(
                "Observer: zero-scan не подтвердился на следующем цикле, продолжаю работать по прежнему живому срезу"
            )
        _PENDING_ZERO_SCAN_CONFIRMATION_AT = None

        previous_snapshot_count = _LAST_ACCEPTED_SNAPSHOT_BATCH_SIZE
        suspicious_partial_batch = (
            previous_snapshot_count is not None
            and previous_snapshot_count - snapshot_count >= _SUSPICIOUS_PARTIAL_BATCH_MIN_DROP
            and snapshot_count < previous_snapshot_count * _SUSPICIOUS_PARTIAL_BATCH_DROP_RATIO
        )
        if suspicious_partial_batch:
            if _PENDING_PARTIAL_BATCH_CONFIRMATION_AT is None:
                _PENDING_PARTIAL_BATCH_CONFIRMATION_AT = scan_started_at
                logger.warning(
                    "Observer: получен подозрительно неполный батч (%s вместо %s) — пропускаю сохранение до повторного подтверждения",
                    snapshot_count,
                    previous_snapshot_count,
                )
                return True

            logger.warning(
                "Observer: повторный неполный батч подтверждён (%s вместо %s) — принимаю новый урезанный срез",
                snapshot_count,
                previous_snapshot_count,
            )
            _PENDING_PARTIAL_BATCH_CONFIRMATION_AT = None
            _LAST_ACCEPTED_SNAPSHOT_BATCH_SIZE = snapshot_count
            return False

        if _PENDING_PARTIAL_BATCH_CONFIRMATION_AT is not None:
            logger.warning(
                "Observer: неполный батч не подтвердился на следующем цикле, сохраняю только восстановленный полный срез"
            )
        _PENDING_PARTIAL_BATCH_CONFIRMATION_AT = None
        _LAST_ACCEPTED_SNAPSHOT_BATCH_SIZE = snapshot_count
        return False

    _PENDING_PARTIAL_BATCH_CONFIRMATION_AT = None
    if _PENDING_ZERO_SCAN_CONFIRMATION_AT is None:
        _PENDING_ZERO_SCAN_CONFIRMATION_AT = scan_started_at
        logger.warning(
            "Observer: получен полный zero-scan без подтверждения — пропускаю сохранение текущего батча до следующего цикла"
        )
        return True

    logger.warning(
        "Observer: повторный zero-scan подтверждён — принимаю новый нулевой срез кабинета"
    )
    _PENDING_ZERO_SCAN_CONFIRMATION_AT = None
    _LAST_ACCEPTED_SNAPSHOT_BATCH_SIZE = snapshot_count
    return False


def _compose_reason_text(base_reason: str | None, diagnostics_text: str | None) -> str | None:
    """Склеивает основную причину и диагностический контекст."""
    if base_reason and diagnostics_text:
        return f"{base_reason} {diagnostics_text}"
    return base_reason or diagnostics_text


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
        await browser_manager.connect()
        new_page = await browser_manager.get_page()
        logger.info("Успешное переподключение к браузеру")
        return new_page
    except Exception:
        logger.warning("Не удалось переподключиться к браузеру", exc_info=True)
        return page


async def load_offers_from_db() -> dict:
    """Загружает активные офферы с правилами из БД.

    Returns:
        dict[offer_code_lower -> {"offer": Offer, "rule_config": OfferRuleConfig}]
    """
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Offer).where(Offer.is_active.is_(True)).options(selectinload(Offer.rule_config))
        )
        offers = result.scalars().all()

        offers_map = {}
        for offer in offers:
            if offer.rule_config:
                offers_map[offer.code.casefold()] = {
                    "offer": offer,
                    "rule_config": offer.rule_config,
                }
        logger.info("Загружено %s активных офферов из БД", len(offers_map))
        return offers_map


def resolve_offer_code(
    ad_name: str,
    campaign_name: str,
    offers: dict,
) -> str | None:
    """Сопоставляет объявление с оффером по вхождению кода в название.

    Оффер содержит часть названия объявления/кампании.
    Например, оффер "DRC_CR2" → объявление "DRC_CR2_CR002".
    """
    # Проверяем ad_name и campaign_name
    text_lower = f"{campaign_name} {ad_name}".casefold()
    best_match: str | None = None
    best_len = 0

    for code in offers:
        if code in text_lower and len(code) > best_len:
            best_match = code
            best_len = len(code)

    return best_match


async def load_telegram_settings_from_db(
    fallback_token: str = "",
    fallback_chat_id: str = "",
) -> tuple[str, list[TelegramDestination]]:
    """Обёртка для загрузки токена и активных получателей Telegram."""
    return await load_telegram_runtime_config(
        fallback_token=fallback_token,
        fallback_chat_id=fallback_chat_id,
    )


async def load_vision_settings_from_db() -> tuple[str, str, str]:
    """Загружает настройки Vision из БД.

    Returns:
        (x_token, api_url, profile_id) — или ("", "", "") если не настроено
    """
    from core.crypto import decrypt

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
                    logger.info("Vision настройки загружены из БД")
                    return token, row.api_url, row.profile_id
    except Exception:
        logger.debug("Не удалось загрузить Vision настройки из БД", exc_info=True)
    return "", "", ""


async def load_vision_settings_for_runtime(
    *,
    fallback_x_token: str = "",
    fallback_api_url: str = "http://127.0.0.1:3030",
    fallback_profile_id: str = "",
) -> tuple[str, str, str]:
    """Возвращает Vision-настройки для запуска или переподключения."""
    db_x_token, db_api_url, db_profile_id = await load_vision_settings_from_db()
    if db_x_token and db_profile_id:
        return db_x_token, db_api_url or fallback_api_url, db_profile_id
    return fallback_x_token, fallback_api_url, fallback_profile_id


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

    browser_manager._vision = VisionClient(x_token=x_token, base_url=api_url)
    browser_manager._profile_id = profile_id
    browser_manager._folder_id = None
    await browser_manager.connect()
    logger.info("Vision браузер переподключён с актуальными настройками")
    return await browser_manager.get_page()


async def check_vision_reconnect_flag() -> bool:
    """Проверяет и сбрасывает флаг reconnect_requested в VisionSettings.

    Returns:
        True если observer должен переподключиться к браузеру.
    """
    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await session.execute(
                select(VisionSettings).where(VisionSettings.singleton_key == "default")
            )
            row = result.scalar_one_or_none()
            if row and row.reconnect_requested:
                row.reconnect_requested = False
                await session.commit()
                logger.info(
                    "Флаг reconnect_requested сброшен — выполняем переподключение к браузеру"
                )
                return True
    except Exception:
        logger.debug("Не удалось проверить флаг reconnect_requested", exc_info=True)
    return False


async def check_scanning_enabled() -> bool:
    """Проверяет флаг is_scanning_enabled из ObserverSettings.

    Returns:
        True если сканирование включено (или настройки не найдены).
    """
    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await session.execute(
                select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
            )
            row = result.scalar_one_or_none()
            if row is not None:
                return bool(row.is_scanning_enabled)
    except Exception:
        logger.debug("Не удалось проверить флаг is_scanning_enabled", exc_info=True)
    return True


async def check_scan_requested_flag() -> bool:
    """Проверяет и сбрасывает флаг scan_requested в ObserverSettings.

    Returns:
        True если нужно немедленно запустить скан.
    """
    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await session.execute(
                select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
            )
            row = result.scalar_one_or_none()
            if row and row.scan_requested:
                row.scan_requested = False
                await session.commit()
                logger.info("Флаг scan_requested сброшен — выполняем немедленный скан")
                return True
    except Exception:
        logger.debug("Не удалось проверить флаг scan_requested", exc_info=True)
    return False


async def get_disable_queue_pause_reason() -> str | None:
    """Возвращает причину паузы сканирования, если очередь отключения блокирует браузер."""
    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await session.execute(
                select(DisableTask.status, DisableTask.next_retry_at).where(
                    DisableTask.status.in_(
                        (
                            DisableTaskStatus.PENDING,
                            DisableTaskStatus.RUNNING,
                            DisableTaskStatus.RETRYING,
                        )
                    )
                )
            )
            rows = result.all()
    except Exception:
        logger.debug("Не удалось проверить активную очередь отключения", exc_info=True)
        return None

    if not rows:
        return None

    now = datetime.now(UTC)
    pending_count = sum(1 for status, _ in rows if status == DisableTaskStatus.PENDING)
    running_count = sum(1 for status, _ in rows if status == DisableTaskStatus.RUNNING)
    ready_retry_times = [
        next_retry_at
        for status, next_retry_at in rows
        if (
            status == DisableTaskStatus.RETRYING
            and next_retry_at is not None
            and next_retry_at <= now
        )
    ]
    retry_count = sum(
        1
        for status, next_retry_at in rows
        if status == DisableTaskStatus.RETRYING and (next_retry_at is None or next_retry_at <= now)
    )

    parts: list[str] = []
    if pending_count:
        parts.append(f"ожидают: {pending_count}")
    if running_count:
        parts.append(f"выполняются: {running_count}")
    if retry_count:
        nearest_retry_at = min(ready_retry_times) if ready_retry_times else None
        if nearest_retry_at is not None:
            retry_in_seconds = max(int((nearest_retry_at - now).total_seconds()), 0)
            parts.append(f"повтор: {retry_count} (ближайший через {retry_in_seconds} сек)")
        else:
            parts.append(f"повтор: {retry_count}")

    if not parts:
        return None

    return ", ".join(parts)


async def load_telegram_recipients_from_db() -> list[TelegramDestination]:
    """Сохраняет совместимость старого имени helper-а."""
    _, destinations = await load_telegram_runtime_config()
    return destinations


async def load_ad_states_from_db() -> dict[str, tuple[AlertState, str | None]]:
    """Загружает FSM-состояния из БД при старте воркера.

    Читает alert_state и open_state_token из всех активных снэпшотов,
    чтобы восстановить состояние после перезапуска.
    """
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(
                AdSnapshot.fb_ad_id,
                AdSnapshot.alert_state,
                AdSnapshot.open_state_token,
            )
        )
        rows = result.all()

    states: dict[str, tuple[AlertState, str | None]] = {}
    for fb_ad_id, alert_state, token in rows:
        states[fb_ad_id] = (alert_state, token)

    logger.info("Загружено %s FSM-состояний из БД", len(states))
    return states


async def refresh_runtime_ad_states(
    current_states: dict[str, tuple[AlertState, str | None]],
) -> dict[str, tuple[AlertState, str | None]]:
    """Синхронизирует in-memory FSM с БД, чтобы внешние действия сразу были видны observer."""
    persisted_states = await load_ad_states_from_db()
    if persisted_states != current_states:
        logger.info(
            "Observer: FSM-состояния синхронизированы с БД (%s записей)",
            len(persisted_states),
        )
    return persisted_states


async def _get_or_create_observer_settings(session) -> ObserverSettings:
    """Возвращает singleton observer_settings, создавая запись при необходимости."""
    row = await session.scalar(
        select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
    )
    if row is not None:
        return row

    row = ObserverSettings(singleton_key="default")
    session.add(row)
    await session.flush()
    return row


async def _maybe_rollover_cabinet_day(session, snapshot_data: list[dict]) -> None:
    """Переводит границу суток кабинета при полном zero-scan и архивирует прошлый день."""
    if not snapshot_data:
        return

    settings = await _get_or_create_observer_settings(session)
    scan_started_at = max(
        (item.get("last_observed_at") for item in snapshot_data if item.get("last_observed_at")),
        default=datetime.now(UTC),
    )
    is_zero_scan = is_cabinet_day_reset_scan(snapshot_data)

    stmt = select(AdSnapshot)
    if settings.cabinet_day_started_at is not None:
        stmt = stmt.where(AdSnapshot.last_observed_at >= settings.cabinet_day_started_at)

    current_snapshots = (await session.execute(stmt)).scalars().all()

    if settings.cabinet_day_started_at is None:
        if not is_zero_scan:
            return

        baseline_started_at = min(
            (
                snapshot.last_observed_at
                for snapshot in current_snapshots
                if snapshot.last_observed_at is not None
            ),
            default=scan_started_at,
        )
        if current_snapshots and any(
            has_any_metric_value(snapshot) for snapshot in current_snapshots
        ):
            summary_json, campaigns_json = build_cabinet_day_archive_payload(current_snapshots)
            session.add(
                CabinetDayArchive(
                    started_at=baseline_started_at,
                    ended_at=scan_started_at,
                    reset_detected_at=scan_started_at,
                    ads_count=len(current_snapshots),
                    summary_json=summary_json,
                    campaigns_json=campaigns_json,
                )
            )
        settings.cabinet_day_started_at = scan_started_at
        logger.info("Observer: впервые зафиксировано начало суток кабинета по zero-scan")
        return

    if not is_zero_scan:
        return

    if not current_snapshots or not any(
        has_any_metric_value(snapshot) for snapshot in current_snapshots
    ):
        return

    summary_json, campaigns_json = build_cabinet_day_archive_payload(current_snapshots)
    session.add(
        CabinetDayArchive(
            started_at=settings.cabinet_day_started_at,
            ended_at=scan_started_at,
            reset_detected_at=scan_started_at,
            ads_count=len(current_snapshots),
            summary_json=summary_json,
            campaigns_json=campaigns_json,
        )
    )
    settings.cabinet_day_started_at = scan_started_at
    logger.info(
        "Observer: зафиксировано начало новых суток кабинета по zero-scan, архивировано %s объявлений",
        len(current_snapshots),
    )


async def batch_save_snapshots(
    snapshot_data: list[dict],
) -> None:
    """Батчевый upsert снэпшотов через INSERT ... ON CONFLICT DO UPDATE.

    Принимает список словарей с данными для AdSnapshot.
    Одна сессия, один запрос для всех снэпшотов.
    """
    if not snapshot_data:
        return
    if _should_skip_snapshot_batch_persist(snapshot_data):
        return

    factory = get_session_factory()
    async with factory() as session:
        await _maybe_rollover_cabinet_day(session, snapshot_data)

        # INSERT ... ON CONFLICT (offer_id, fb_ad_id) DO UPDATE
        stmt = pg_insert(AdSnapshot).values(snapshot_data)

        # Колонки для обновления при конфликте
        update_cols = {
            # Не даём пустому DOM-ответу Meta затирать уже известные названия.
            "campaign_name": func.coalesce(
                func.nullif(stmt.excluded.campaign_name, ""),
                AdSnapshot.campaign_name,
            ),
            "adset_name": func.coalesce(
                func.nullif(stmt.excluded.adset_name, ""),
                AdSnapshot.adset_name,
            ),
            "ad_name": stmt.excluded.ad_name,
            "delivery_status": stmt.excluded.delivery_status,
            "offer_id": stmt.excluded.offer_id,
            "resolved_offer_code": stmt.excluded.resolved_offer_code,
            "spend": stmt.excluded.spend,
            "budget": stmt.excluded.budget,
            "reach": stmt.excluded.reach,
            "impressions": stmt.excluded.impressions,
            "clicks": stmt.excluded.clicks,
            "cpc": stmt.excluded.cpc,
            "ctr": stmt.excluded.ctr,
            "outbound_clicks": stmt.excluded.outbound_clicks,
            "outbound_ctr": stmt.excluded.outbound_ctr,
            "landing_page_views": stmt.excluded.landing_page_views,
            "cost_per_result": stmt.excluded.cost_per_result,
            "cost_per_landing_page_view": stmt.excluded.cost_per_landing_page_view,
            "cpm": stmt.excluded.cpm,
            "frequency": stmt.excluded.frequency,
            "leads": stmt.excluded.leads,
            "cost_per_lead": stmt.excluded.cost_per_lead,
            "registrations": stmt.excluded.registrations,
            "cost_per_registration": stmt.excluded.cost_per_registration,
            "deposits": stmt.excluded.deposits,
            "alert_state": stmt.excluded.alert_state,
            "current_stage": stmt.excluded.current_stage,
            "early_signal_rule_codes": stmt.excluded.early_signal_rule_codes,
            "warning_rule_codes": stmt.excluded.warning_rule_codes,
            "stop_rule_codes": stmt.excluded.stop_rule_codes,
            "open_state_token": stmt.excluded.open_state_token,
            "telegram_group_key": stmt.excluded.telegram_group_key,
            "last_observed_at": stmt.excluded.last_observed_at,
        }

        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=["fb_ad_id"],
            set_=update_cols,
        )

        await session.execute(upsert_stmt)
        await session.commit()


def compute_jitter(interval_seconds: int, jitter_seconds: int) -> float:
    """Вычисляет интервал сна: interval_seconds ± случайный jitter_seconds.

    При interval=45, jitter=4 → результат от 41 до 49 сек.
    Минимум 5 секунд (защита от слишком частого скана).
    """
    offset = random.uniform(-jitter_seconds, jitter_seconds)
    return max(5.0, interval_seconds + offset)


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


async def load_observer_settings_from_db() -> tuple[int, int, dict[str, Decimal]]:
    """Загружает interval_seconds, jitter_seconds и пороги observer из БД.

    Returns:
        (interval_seconds, jitter_seconds, пороги по шагам)
    """
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
        )
        s = result.scalar_one_or_none()
        if s:
            return (
                s.interval_seconds,
                s.jitter_seconds,
                extract_observer_threshold_values(s),
            )
        return 90, 10, extract_observer_threshold_values()


async def reconcile_disable_tasks_in_db() -> dict[str, list[str]]:
    """Согласовывает очередь отключения с актуальным состоянием снэпшотов."""
    factory = get_session_factory()
    async with factory() as session:
        summary = await reconcile_disable_tasks(session)
        if any(summary.values()):
            await session.commit()
        return summary


def _matched_rule_codes_for_snapshot(snapshot: AdSnapshot) -> list[str]:
    """Возвращает текущие коды правил для открытого инцидента."""
    if snapshot.current_stage == AlertStage.EARLY_SIGNAL:
        return list(snapshot.early_signal_rule_codes or [])
    if snapshot.current_stage == AlertStage.WARNING:
        return list(snapshot.warning_rule_codes or [])
    return list(snapshot.stop_rule_codes or [])


def _build_manual_attention_reason_text(*, retry_count: int, last_error: str | None) -> str:
    """Формирует текст ручной эскалации после исчерпания тихих автоповторов."""
    message = (
        f"Бот выполнил {retry_count} тихих автоповтора без подтверждения OFF. "
        "Проверьте объявление вручную в Ads Manager."
    )
    if last_error:
        return f"{message} Последняя ошибка: {last_error}"
    return message


async def _create_auto_disable_task_for_snapshot(
    session,
    *,
    snapshot: AdSnapshot,
    incident_key: str,
    attempt_sequence: int,
) -> bool:
    """Создаёт очередную auto-disable попытку в рамках существующего инцидента."""
    idempotency_key = f"auto:{snapshot.fb_ad_id}:{incident_key}:{attempt_sequence}"
    stmt = (
        pg_insert(DisableTask)
        .values(
            snapshot_id=snapshot.id,
            offer_id=snapshot.offer_id,
            fb_ad_id=snapshot.fb_ad_id,
            ad_name=snapshot.ad_name,
            open_state_token=incident_key,
            idempotency_key=idempotency_key,
            requested_by_telegram_user_id=None,
            requested_by_username="bot_auto_stop",
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )
    result = await session.execute(stmt)
    return bool(result.rowcount)


async def reconcile_disable_incidents_after_scan() -> list[AlertCandidate]:
    """Переоткрывает disable-попытки внутри того же инцидента без нового STOP-спама."""
    factory = get_session_factory()
    manual_attention_alerts: list[AlertCandidate] = []
    created_attempts = 0
    async with factory() as session:
        last_scan = await session.scalar(select(func.max(AdSnapshot.last_observed_at)))
        if last_scan is None:
            return []
        active_cutoff = last_scan - ACTIVE_ALERT_WINDOW
        recent_cutoff = datetime.now(UTC) - DISABLE_SUCCESS_CONFIRMATION_GRACE_TIMEOUT

        result = await session.execute(
            select(AdSnapshot).where(
                AdSnapshot.alert_state == AlertState.CLAIMED,
                AdSnapshot.current_stage == AlertStage.STOP,
                AdSnapshot.open_state_token.is_not(None),
                AdSnapshot.last_observed_at >= active_cutoff,
                AdSnapshot.delivery_status.notin_(["OFF", "NOT_DELIVERING"]),
            )
        )
        snapshots = result.scalars().all()

        for snapshot in snapshots:
            incident_key = snapshot.open_state_token
            if not incident_key:
                continue

            active_count = await session.scalar(
                select(func.count(DisableTask.id)).where(
                    DisableTask.fb_ad_id == snapshot.fb_ad_id,
                    DisableTask.open_state_token == incident_key,
                    DisableTask.status.in_(ACTIVE_DISABLE_TASK_STATUSES),
                )
            )
            if active_count:
                continue

            recent_succeeded = await session.scalar(
                select(func.count(DisableTask.id)).where(
                    DisableTask.fb_ad_id == snapshot.fb_ad_id,
                    DisableTask.open_state_token == incident_key,
                    DisableTask.status == DisableTaskStatus.SUCCEEDED,
                    DisableTask.completed_at.is_not(None),
                    DisableTask.completed_at >= recent_cutoff,
                )
            )
            if recent_succeeded:
                logger.debug(
                    "Incident reconcile: %s — ждём подтверждение OFF после недавнего успешного клика",
                    snapshot.fb_ad_id,
                )
                continue

            auto_attempts = (
                await session.scalar(
                    select(func.count(DisableTask.id)).where(
                        DisableTask.fb_ad_id == snapshot.fb_ad_id,
                        DisableTask.open_state_token == incident_key,
                        DisableTask.requested_by_username == "bot_auto_stop",
                    )
                )
                or 0
            )
            retry_count = max(int(auto_attempts) - 1, 0)
            latest_task = await session.scalar(
                select(DisableTask)
                .where(
                    DisableTask.fb_ad_id == snapshot.fb_ad_id,
                    DisableTask.open_state_token == incident_key,
                )
                .order_by(DisableTask.updated_at.desc(), DisableTask.created_at.desc())
                .limit(1)
            )

            if retry_count >= SILENT_DISABLE_INCIDENT_RETRY_LIMIT:
                manual_attention_alerts.append(
                    AlertCandidate(
                        snapshot_id=incident_key,
                        offer_id=snapshot.offer_id,
                        fb_ad_id=snapshot.fb_ad_id,
                        ad_name=snapshot.ad_name,
                        campaign_name=snapshot.campaign_name,
                        adset_name=snapshot.adset_name,
                        offer_code=snapshot.resolved_offer_code,
                        offer_name=None,
                        offer_cpa=None,
                        stage=AlertStage.STOP,
                        matched_rule_codes=_matched_rule_codes_for_snapshot(snapshot),
                        reason_title=MANUAL_ATTENTION_REASON_TITLE,
                        reason_text=_build_manual_attention_reason_text(
                            retry_count=retry_count,
                            last_error=(latest_task.last_error if latest_task else None),
                        ),
                        metrics_json={
                            "spend": str(snapshot.spend),
                            "clicks": snapshot.clicks,
                            "cpc": str(snapshot.cpc) if snapshot.cpc is not None else None,
                            "outbound_clicks": snapshot.outbound_clicks,
                            "outbound_ctr": (
                                str(snapshot.outbound_ctr)
                                if snapshot.outbound_ctr is not None
                                else None
                            ),
                            "landing_page_views": snapshot.landing_page_views,
                            "cost_per_landing_page_view": (
                                str(snapshot.cost_per_landing_page_view)
                                if snapshot.cost_per_landing_page_view is not None
                                else None
                            ),
                            "cpm": str(snapshot.cpm) if snapshot.cpm is not None else None,
                            "frequency": (
                                str(snapshot.frequency) if snapshot.frequency is not None else None
                            ),
                            "leads": snapshot.leads,
                            "cost_per_lead": (
                                str(snapshot.cost_per_lead)
                                if snapshot.cost_per_lead is not None
                                else None
                            ),
                            "registrations": snapshot.registrations,
                            "cost_per_registration": (
                                str(snapshot.cost_per_registration)
                                if snapshot.cost_per_registration is not None
                                else None
                            ),
                            "deposits": snapshot.deposits,
                        },
                        persist_event=False,
                    )
                )
                logger.warning(
                    "Incident reconcile: %s — исчерпаны тихие автоповторы (%s), нужен ручной разбор",
                    snapshot.fb_ad_id,
                    retry_count,
                )
                continue

            attempt_sequence = int(auto_attempts) + 1
            created = await _create_auto_disable_task_for_snapshot(
                session,
                snapshot=snapshot,
                incident_key=incident_key,
                attempt_sequence=attempt_sequence,
            )
            if created:
                created_attempts += 1
                logger.warning(
                    "Incident reconcile: %s — создаю тихий автоповтор disable в рамках incident %s (попытка %s)",
                    snapshot.fb_ad_id,
                    incident_key,
                    attempt_sequence,
                )
            else:
                logger.debug(
                    "Incident reconcile: %s — follow-up disable уже создан для incident %s",
                    snapshot.fb_ad_id,
                    incident_key,
                )

        if created_attempts:
            await session.commit()

    return manual_attention_alerts


def resolve_off_alert_state(current_state: AlertState) -> AlertState:
    """Определяет итоговое состояние объявления, когда observer увидел реальный OFF."""
    if current_state in (AlertState.CLAIMED, AlertState.DISABLED):
        return AlertState.DISABLED
    return AlertState.NORMAL


def reopen_reactivated_alert_state(
    current_state: AlertState | None,
    current_token: str | None,
    delivery_status: str | None,
) -> tuple[AlertState | None, str | None]:
    """Сбрасывает терминальное состояние, если объявление снова начали откручивать."""
    # CLAIMED не сбрасываем по одному только ACTIVE/UNKNOWN:
    # после успешного клика Meta ещё может долго не показывать OFF.
    if current_state == AlertState.DISABLED and not is_delivery_disabled(delivery_status):
        return AlertState.NORMAL, None
    return current_state, current_token


async def auto_create_disable_tasks(stop_alerts: list[AlertCandidate]) -> None:
    """Автоматически создаёт DisableTask для объявлений, превысивших порог STOP.

    Вызывается после batch_save_snapshots, чтобы snapshot.id уже существовал в БД.
    Использует idempotency_key для защиты от дублей при повторных циклах.
    """
    if not stop_alerts:
        return
    factory = get_session_factory()
    async with factory() as session:
        last_scan = await session.scalar(select(func.max(AdSnapshot.last_observed_at)))
        active_cutoff = last_scan - ACTIVE_ALERT_WINDOW if last_scan is not None else None
        created_attempts = 0
        for alert in stop_alerts:
            snapshot = await session.scalar(
                select(AdSnapshot).where(AdSnapshot.fb_ad_id == alert.fb_ad_id)
            )
            if snapshot is None:
                logger.warning("Авто-стоп: снэпшот не найден для %s", alert.fb_ad_id)
                continue
            if (
                active_cutoff is None
                or snapshot.last_observed_at is None
                or snapshot.last_observed_at < active_cutoff
            ):
                logger.info(
                    "Авто-стоп: пропускаю %s — объявление уже не входит в актуальную скан-сессию",
                    alert.fb_ad_id,
                )
                continue

            incident_key = snapshot.open_state_token or alert.snapshot_id
            snapshot.open_state_token = incident_key
            snapshot.telegram_group_key = incident_key
            auto_attempts = (
                await session.scalar(
                    select(func.count(DisableTask.id)).where(
                        DisableTask.fb_ad_id == alert.fb_ad_id,
                        DisableTask.open_state_token == incident_key,
                        DisableTask.requested_by_username == "bot_auto_stop",
                    )
                )
                or 0
            )
            attempt_sequence = int(auto_attempts) + 1
            created = await _create_auto_disable_task_for_snapshot(
                session,
                snapshot=snapshot,
                incident_key=incident_key,
                attempt_sequence=attempt_sequence,
            )
            if created:
                created_attempts += 1
                logger.info(
                    "Авто-стоп: создана задача на отключение %s (%s), incident=%s, попытка=%s",
                    alert.ad_name,
                    alert.fb_ad_id,
                    incident_key,
                    attempt_sequence,
                )
            else:
                logger.debug(
                    "Авто-стоп: задача уже существует для %s (incident=%s, попытка=%s)",
                    alert.fb_ad_id,
                    incident_key,
                    attempt_sequence,
                )
        try:
            if created_attempts:
                await session.commit()
        except Exception:
            logger.exception("Авто-стоп: ошибка при сохранении DisableTask")


async def _human_micro_pause() -> None:
    """Случайная микропауза 0.5-2 сек между действиями (имитация человека)."""
    await asyncio.sleep(random.uniform(0.5, 2.0))


async def _maybe_macro_pause() -> None:
    """С вероятностью ~15% — макропауза 5-15 сек (имитация отвлечения)."""
    if random.random() < 0.15:
        pause = random.uniform(5.0, 15.0)
        logger.info("Макропауза %.1f сек (имитация отвлечения)", pause)
        await asyncio.sleep(pause)


async def _collect_reminder_alerts(interval_seconds: int) -> list[AlertCandidate]:
    """Собирает алерты для повторного напоминания.

    Условия:
    - alert_state IN [EARLY_SIGNAL_SENT, WARNING_SENT, STOP_SENT]
    - last_observed_at попадает в актуальную скан-сессию
    - snoozed_until IS NULL или уже истёк
    - последний AlertEvent для этого fb_ad_id был > interval * 10 сек назад
    """
    reminder_threshold = interval_seconds * 10
    now = datetime.now(UTC)

    factory = get_session_factory()
    async with factory() as session:
        last_scan = await session.scalar(select(func.max(AdSnapshot.last_observed_at)))
        if last_scan is None:
            return []
        active_cutoff = last_scan - ACTIVE_ALERT_WINDOW

        result = await session.execute(
            select(AdSnapshot).where(
                AdSnapshot.alert_state.in_(
                    [
                        AlertState.EARLY_SIGNAL_SENT,
                        AlertState.WARNING_SENT,
                        AlertState.STOP_SENT,
                    ]
                ),
                AdSnapshot.last_observed_at >= active_cutoff,
            )
        )
        candidates = result.scalars().all()

        if not candidates:
            return []

        reminders: list[AlertCandidate] = []
        for snap in candidates:
            if snap.last_observed_at is None or snap.last_observed_at < active_cutoff:
                logger.info(
                    "Observer: пропускаю напоминание для %s — объявление уже в архиве",
                    snap.fb_ad_id,
                )
                continue

            # Снуз подавляет только повторные EARLY/WARNING-напоминания.
            if (
                snap.alert_state in {AlertState.EARLY_SIGNAL_SENT, AlertState.WARNING_SENT}
                and snap.snoozed_until
                and snap.snoozed_until > now
            ):
                continue

            incident_key = snap.open_state_token or ""
            # Проверяем когда был последний AlertEvent по текущему инциденту
            last_event_at_stmt = select(func.max(AlertEvent.created_at)).where(
                AlertEvent.fb_ad_id == snap.fb_ad_id
            )
            if incident_key:
                last_event_at_stmt = last_event_at_stmt.where(
                    AlertEvent.telegram_group_key == incident_key
                )
            last_event_at = await session.scalar(last_event_at_stmt)
            if last_event_at is None:
                continue
            secs_since = (now - last_event_at).total_seconds()
            if secs_since < reminder_threshold:
                continue

            last_event_stmt = (
                select(AlertEvent)
                .where(AlertEvent.fb_ad_id == snap.fb_ad_id)
                .order_by(AlertEvent.updated_at.desc(), AlertEvent.created_at.desc())
                .limit(1)
            )
            if incident_key:
                last_event_stmt = last_event_stmt.where(
                    AlertEvent.telegram_group_key == incident_key
                )
            last_event_result = await session.execute(last_event_stmt)
            last_event = last_event_result.scalar_one_or_none()

            # Строим AlertCandidate для напоминания
            if snap.alert_state == AlertState.STOP_SENT:
                stage = AlertStage.STOP
                rule_codes = snap.stop_rule_codes or []
            elif snap.alert_state == AlertState.WARNING_SENT:
                stage = AlertStage.WARNING
                rule_codes = snap.warning_rule_codes or []
            else:
                stage = AlertStage.EARLY_SIGNAL
                rule_codes = snap.early_signal_rule_codes or []

            rule_summaries = None
            traffic_diagnostics = None
            if last_event and isinstance(last_event.metrics_json, dict):
                raw_summaries = last_event.metrics_json.get("rule_summaries")
                if isinstance(raw_summaries, list) and raw_summaries:
                    rule_summaries = [str(summary) for summary in raw_summaries]
                raw_diagnostics = last_event.metrics_json.get("traffic_diagnostics")
                if isinstance(raw_diagnostics, dict) and raw_diagnostics:
                    traffic_diagnostics = dict(raw_diagnostics)

            reminders.append(
                AlertCandidate(
                    snapshot_id=snap.open_state_token or str(snap.id),
                    offer_id=snap.offer_id,
                    fb_ad_id=snap.fb_ad_id,
                    ad_name=snap.ad_name,
                    campaign_name=snap.campaign_name,
                    adset_name=snap.adset_name,
                    offer_code=snap.resolved_offer_code,
                    offer_name=None,
                    offer_cpa=None,
                    stage=stage,
                    matched_rule_codes=rule_codes,
                    reason_title=last_event.reason_title if last_event else None,
                    reason_text=last_event.reason_text if last_event else None,
                    metrics_json={
                        "spend": str(snap.spend),
                        "clicks": snap.clicks,
                        "cpc": str(snap.cpc) if snap.cpc is not None else None,
                        "outbound_clicks": snap.outbound_clicks,
                        "outbound_ctr": str(snap.outbound_ctr)
                        if snap.outbound_ctr is not None
                        else None,
                        "landing_page_views": snap.landing_page_views,
                        "cost_per_landing_page_view": (
                            str(snap.cost_per_landing_page_view)
                            if snap.cost_per_landing_page_view is not None
                            else None
                        ),
                        "cpm": str(snap.cpm) if snap.cpm is not None else None,
                        "frequency": str(snap.frequency) if snap.frequency is not None else None,
                        "leads": snap.leads,
                        "cost_per_lead": str(snap.cost_per_lead)
                        if snap.cost_per_lead is not None
                        else None,
                        "registrations": snap.registrations,
                        "cost_per_registration": str(snap.cost_per_registration)
                        if snap.cost_per_registration is not None
                        else None,
                        "deposits": snap.deposits,
                        **({"rule_summaries": rule_summaries} if rule_summaries else {}),
                        **(
                            {"traffic_diagnostics": traffic_diagnostics}
                            if traffic_diagnostics
                            else {}
                        ),
                    },
                    persist_event=False,
                )
            )
        logger.info(
            "Observer: проверено %s кандидатов на напоминание, найдено %s",
            len(candidates),
            len(reminders),
        )
    return reminders


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

            # После предыдущего прохода таблица может остаться внизу.
            # Перед refresh возвращаемся к началу, чтобы новый цикл
            # не стартовал только с хвоста списка объявлений.
            await _reset_ads_table_scroll(page)

            # 1. Обновляем таблицу (кнопка «Обновить» или reload)
            logger.info("Observer: обновление таблицы")
            refreshed = await refresh_table(page)
            if not refreshed:
                # Если кнопка не найдена — перезагружаем страницу
                await page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2.0, 4.0))

            # 2. Плавный скролл + парсинг
            rows = await _scroll_and_parse(page, parse_fn)
            logger.info("Observer: получено %s объявлений", len(rows))

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

            # 3. Оценка правил и сбор алертов + подготовка батча
            alerts_to_send: list[AlertCandidate] = []
            stop_alerts: list[AlertCandidate] = []  # для авто-стопа
            snapshot_batch: list[dict] = []
            now = datetime.now(UTC)

            for row, offer_code, offer_data in resolved_rows:
                diagnostics = None
                if offer_code and offer_data and offer_data.get("rule_config") is not None:
                    diagnostics = build_ad_quality_diagnostics(
                        cpm_value=row.cpm,
                        cpm_baseline=cpm_baselines.get(offer_code),
                        frequency_value=row.frequency,
                        frequency_elevated_threshold=offer_data[
                            "rule_config"
                        ].frequency_elevated_threshold,
                        frequency_critical_threshold=offer_data[
                            "rule_config"
                        ].frequency_critical_threshold,
                    )

                # Выключенные объявления не оцениваем — сбрасываем FSM и идём дальше
                if is_delivery_disabled(row.delivery_status):
                    current_state, _ = ad_states.get(row.fb_ad_id, (AlertState.NORMAL, None))
                    # Если объявление уже было в процессе отключения — фиксируем терминальное DISABLED.
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

                evaluation = evaluate_row(
                    row=row,
                    offer_cpa=(Decimal(offer_data["offer"].cpa_amount) if offer_data else None),
                    rule_config=(offer_data.get("rule_config") if offer_data else None),
                    warning_percent_of_stop=observer_thresholds["warning_percent_of_stop"],
                    stop_percent_of_base=observer_thresholds["stop_percent_of_base"],
                    observer_thresholds=observer_thresholds,
                )

                # FSM-переход
                current_state, current_token = ad_states.get(
                    row.fb_ad_id, (AlertState.NORMAL, None)
                )
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

                # Добавляем в батч снэпшотов (задача 2.1)
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
                        build_diagnostics_context_text(diagnostics)
                        if diagnostics is not None
                        else None
                    )
                    candidate = AlertCandidate(
                        snapshot_id=token or uuid.uuid4().hex,
                        offer_id=offer_id,
                        fb_ad_id=row.fb_ad_id,
                        ad_name=row.ad_name,
                        campaign_name=row.campaign_name,
                        adset_name=row.adset_name,
                        offer_code=offer_code,
                        offer_name=offer_data["offer"].name if offer_data else None,
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

            # Батчевый upsert снэпшотов (задача 2.1)
            try:
                await batch_save_snapshots(snapshot_batch)
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
                reminders = await _collect_reminder_alerts(interval_seconds)
                if reminders:
                    alerts_to_send.extend(reminders)
                    logger.info(
                        "Observer: добавлено %s напоминаний в очередь отправки", len(reminders)
                    )
            except Exception:
                logger.warning("Не удалось собрать напоминания", exc_info=True)

            # Диагностика: логируем статус алертов и TG перед отправкой
            logger.info(
                "Observer: алертов к отправке: %s (STOP авто-стоп: %s), "
                "tg_client: %s, получателей: %s",
                len(alerts_to_send),
                len(stop_alerts),
                "есть" if tg_client else "НЕТ",
                len(tg_destinations),
            )

            # Микропауза перед отправкой алертов (задача 1.5)
            await _human_micro_pause()

            # 4. Отправка в Telegram всем активным получателям
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

            # Успешный цикл — сбрасываем счётчик ошибок браузера
            consecutive_browser_errors = 0
            cycle_completed = True

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

        # 5. Прерываемый сон с jitter + поллинг scan_requested каждые 5 сек
        sleep_time = compute_jitter(interval_seconds, jitter_seconds)
        logger.info("Observer: следующий цикл через %.0f сек", sleep_time)

        end_at = _time.monotonic() + sleep_time
        POLL_INTERVAL = 5.0  # проверяем флаги каждые 5 секунд

        while True:
            remaining = end_at - _time.monotonic()
            if remaining <= 0:
                break

            # Завершаемся при shutdown
            if shutdown_event is not None and shutdown_event.is_set():
                logger.info("Observer: получен сигнал остановки, завершаем цикл")
                return

            # Проверяем флаг немедленного скана
            if await check_scan_requested_flag():
                logger.info("Observer: прерываем сон — запрошен немедленный скан")
                break

            chunk = min(POLL_INTERVAL, remaining)
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
                    return
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(chunk)


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
        # Парсим текущий view
        visible_rows = await parse_fn(page)
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
    destination: TelegramDestination,
    alerts: list[AlertCandidate],
) -> None:
    """Отправляет или обновляет алерты одному получателю по stream+incident."""
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

        factory = get_session_factory()
        try:
            async with factory() as session:
                snapshot = await session.scalar(
                    select(AdSnapshot).where(AdSnapshot.fb_ad_id == a.fb_ad_id)
                )
                existing_stage_event = await session.scalar(
                    select(AlertEvent)
                    .where(
                        AlertEvent.fb_ad_id == a.fb_ad_id,
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
                    existing_stage_event.snapshot_id = snapshot.id if snapshot else None
                    existing_stage_event.offer_id = a.offer_id
                    existing_stage_event.ad_name = a.ad_name
                    existing_stage_event.matched_rule_codes = a.matched_rule_codes
                    existing_stage_event.reason_title = a.reason_title
                    existing_stage_event.reason_text = a.reason_text
                    existing_stage_event.metrics_json = a.metrics_json
                    existing_stage_event.message_text = message.text
                    existing_stage_event.telegram_message_id = delivered_message_id
                elif a.persist_event or existing_message_id is None:
                    session.add(
                        AlertEvent(
                            snapshot_id=(snapshot.id if snapshot else None),
                            fb_ad_id=a.fb_ad_id,
                            ad_name=a.ad_name,
                            offer_id=a.offer_id,
                            stage=a.stage,
                            state=alert_state,
                            matched_rule_codes=a.matched_rule_codes,
                            reason_title=a.reason_title,
                            reason_text=a.reason_text,
                            metrics_json=a.metrics_json,
                            message_text=message.text,
                            telegram_chat_id=destination.chat_id,
                            telegram_message_id=delivered_message_id,
                            telegram_group_key=a.snapshot_id,
                        )
                    )
                await session.commit()
            if a.persist_event:
                logger.info("AlertEvent сохранён в БД: %s, стадия %s", a.fb_ad_id, a.stage)
            else:
                logger.info(
                    "TG-инцидент обновлён без новой history-записи: %s, стадия %s",
                    a.fb_ad_id,
                    a.stage,
                )
        except Exception:
            logger.exception("Не удалось сохранить AlertEvent для %s", a.fb_ad_id)
