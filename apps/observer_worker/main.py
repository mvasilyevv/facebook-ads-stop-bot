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
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

from core.cabinet_day import (
    build_cabinet_day_archive_payload,
    has_any_metric_value,
    is_cabinet_day_reset_scan,
)
from core.disable_tasks import (
    DISABLE_TASK_STALE_TIMEOUT,
    is_delivery_disabled,
    reconcile_disable_tasks,
)
from core.db import get_session_factory
from core.domain import AlertStage, AlertState, DisableTaskStatus
from core.models import (
    AdSnapshot,
    AlertEvent,
    CabinetDayArchive,
    DisableTask,
    ObserverSettings,  # noqa: F401 — используется в check_scan_requested_flag
    Offer,
    TelegramRecipient,
    TelegramSettings,
    VisionSettings,
)
from patchright.async_api import Error as PatchrightError

from core.observer.service import AlertCandidate, build_metrics_json, evaluate_row
from core.observer.state_machine import resolve_transition
from core.scanner.models import ScannedAdRow
from core.scanner.parser import refresh_table
from core.telegram.client import TelegramBotClient
from core.telegram.renderer import TelegramAlertItem, render_alert_message

logger = logging.getLogger(__name__)

# Максимальное количество попыток переподключения к браузеру
MAX_RECONNECT_ATTEMPTS = 5
# Базовая задержка для экспоненциального backoff (сек)
BASE_RECONNECT_DELAY = 10
# Пока очередь отключения не опустеет, observer не должен трогать общий браузер.
DISABLE_QUEUE_SCAN_PAUSE_SECONDS = 5.0


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
) -> tuple[str, str]:
    """Загружает настройки Telegram из БД (только если авторизован).

    Приоритет: БД (is_authorized=True) → .env fallback.
    Returns:
        (bot_token, chat_id)
    """
    from core.crypto import decrypt

    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await session.execute(
                select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
            )
            row = result.scalar_one_or_none()
            if row and row.is_authorized and row.bot_token_encrypted and row.chat_id:
                token = decrypt(row.bot_token_encrypted)
                if token:
                    logger.info("Telegram настройки загружены из БД (авторизован)")
                    return token, row.chat_id
            elif row and not row.is_authorized:
                logger.info("Telegram не авторизован — уведомления отключены")
                return "", ""
    except Exception:
        logger.debug("Не удалось загрузить TG настройки из БД", exc_info=True)

    # Fallback на .env (обратная совместимость)
    if fallback_token and fallback_chat_id:
        logger.info("Telegram настройки: используем .env")
        return fallback_token, fallback_chat_id

    logger.warning("Telegram не настроен — уведомления не будут отправляться")
    return "", ""


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
    """Возвращает причину паузы сканирования, если очередь отключения ещё активна."""
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
    retry_times = [
        next_retry_at
        for status, next_retry_at in rows
        if status == DisableTaskStatus.RETRYING and next_retry_at is not None
    ]
    retry_count = sum(1 for status, _ in rows if status == DisableTaskStatus.RETRYING)

    parts: list[str] = []
    if pending_count:
        parts.append(f"ожидают: {pending_count}")
    if running_count:
        parts.append(f"выполняются: {running_count}")
    if retry_count:
        nearest_retry_at = min(retry_times) if retry_times else None
        if nearest_retry_at is not None:
            retry_in_seconds = max(int((nearest_retry_at - now).total_seconds()), 0)
            parts.append(f"повтор: {retry_count} (ближайший через {retry_in_seconds} сек)")
        else:
            parts.append(f"повтор: {retry_count}")

    return ", ".join(parts) if parts else "очередь отключения ещё активна"


async def load_telegram_recipients_from_db() -> list[str]:
    """Загружает список активных chat_id из таблицы telegram_recipients.

    Returns:
        list[str] — список chat_id активных получателей
    """
    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await session.execute(
                select(TelegramRecipient.chat_id).where(TelegramRecipient.is_active.is_(True))
            )
            chat_ids = result.scalars().all()
            return list(chat_ids)
    except Exception:
        logger.debug("Не удалось загрузить получателей Telegram из БД", exc_info=True)
        return []


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
        if current_snapshots and any(has_any_metric_value(snapshot) for snapshot in current_snapshots):
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
        logger.info(
            "Observer: впервые зафиксировано начало суток кабинета по zero-scan"
        )
        return

    if not is_zero_scan:
        return

    if not current_snapshots or not any(has_any_metric_value(snapshot) for snapshot in current_snapshots):
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

    factory = get_session_factory()
    async with factory() as session:
        await _maybe_rollover_cabinet_day(session, snapshot_data)

        # INSERT ... ON CONFLICT (offer_id, fb_ad_id) DO UPDATE
        stmt = pg_insert(AdSnapshot).values(snapshot_data)

        # Колонки для обновления при конфликте
        update_cols = {
            "campaign_name": stmt.excluded.campaign_name,
            "adset_name": stmt.excluded.adset_name,
            "ad_name": stmt.excluded.ad_name,
            "delivery_status": stmt.excluded.delivery_status,
            "offer_id": stmt.excluded.offer_id,
            "resolved_offer_code": stmt.excluded.resolved_offer_code,
            "spend": stmt.excluded.spend,
            "clicks": stmt.excluded.clicks,
            "cpc": stmt.excluded.cpc,
            "leads": stmt.excluded.leads,
            "cost_per_lead": stmt.excluded.cost_per_lead,
            "registrations": stmt.excluded.registrations,
            "cost_per_registration": stmt.excluded.cost_per_registration,
            "deposits": stmt.excluded.deposits,
            "alert_state": stmt.excluded.alert_state,
            "current_stage": stmt.excluded.current_stage,
            "warning_rule_codes": stmt.excluded.warning_rule_codes,
            "stop_rule_codes": stmt.excluded.stop_rule_codes,
            "open_state_token": stmt.excluded.open_state_token,
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


async def load_observer_settings_from_db() -> tuple[int, int, Decimal, Decimal]:
    """Загружает interval_seconds, jitter_seconds и пороги observer из БД.

    Returns:
        (interval_seconds, jitter_seconds, warning_percent_of_stop, stop_percent_of_base)
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
                s.warning_percent_of_stop,
                s.stop_percent_of_base,
            )
        return 90, 10, Decimal("80"), Decimal("100")


async def reconcile_disable_tasks_in_db() -> dict[str, list[str]]:
    """Согласовывает очередь отключения с актуальным состоянием снэпшотов."""
    factory = get_session_factory()
    async with factory() as session:
        summary = await reconcile_disable_tasks(session)
        if any(summary.values()):
            await session.commit()
        return summary


async def requeue_stuck_claimed_ads() -> list[str]:
    """Находит объявления в CLAIMED или DISABLED без активных задач на отключение,
    которые реально ещё не выключены (delivery_status не OFF).

    Сбрасывает их состояние в STOP_SENT в БД, чтобы observer мог
    повторно создать задачу. Вызывается периодически (каждые N циклов).

    Returns:
        Список fb_ad_id, которые были сброшены в STOP_SENT.
    """
    factory = get_session_factory()
    reset_ids: list[str] = []
    async with factory() as session:
        result = await session.execute(
            select(AdSnapshot).where(
                AdSnapshot.alert_state.in_([AlertState.CLAIMED, AlertState.DISABLED]),
                AdSnapshot.delivery_status.notin_(["OFF", "NOT_DELIVERING"]),
            )
        )
        stuck_snapshots = result.scalars().all()

        recent_cutoff = datetime.now(UTC) - DISABLE_TASK_STALE_TIMEOUT

        for snap in stuck_snapshots:
            active_count = await session.scalar(
                select(func.count(DisableTask.id)).where(
                    DisableTask.fb_ad_id == snap.fb_ad_id,
                    DisableTask.status.in_(
                        [
                            DisableTaskStatus.PENDING,
                            DisableTaskStatus.RUNNING,
                            DisableTaskStatus.RETRYING,
                        ]
                    ),
                )
            )
            if active_count:
                continue

            # Короткое окно ожидания даём только для реального OFF/NOT_DELIVERING.
            # UNKNOWN не считаем подтверждённым выключением.
            if is_delivery_disabled(snap.delivery_status):
                recent_succeeded = await session.scalar(
                    select(func.count(DisableTask.id)).where(
                        DisableTask.fb_ad_id == snap.fb_ad_id,
                        DisableTask.status == DisableTaskStatus.SUCCEEDED,
                        DisableTask.completed_at >= recent_cutoff,
                    )
                )
                if recent_succeeded:
                    logger.debug(
                        "Reconcile: %s — задача недавно выполнена, delivery=%s → пропускаю сброс",
                        snap.fb_ad_id,
                        snap.delivery_status,
                    )
                    continue

            old_state = snap.alert_state
            snap.alert_state = AlertState.NORMAL
            reset_ids.append(snap.fb_ad_id)
            logger.info(
                "Reconcile: %s — состояние %s, delivery_status=%s, нет активных задач → сброс→NORMAL",
                snap.fb_ad_id,
                old_state,
                snap.delivery_status,
            )

        if reset_ids:
            await session.commit()

    return reset_ids


def resolve_off_alert_state(current_state: AlertState) -> AlertState:
    """Определяет итоговое состояние объявления, когда observer увидел реальный OFF."""
    if current_state in (AlertState.CLAIMED, AlertState.DISABLED):
        return AlertState.DISABLED
    return AlertState.NORMAL


async def auto_create_disable_tasks(stop_alerts: list[AlertCandidate]) -> None:
    """Автоматически создаёт DisableTask для объявлений, превысивших порог STOP.

    Вызывается после batch_save_snapshots, чтобы snapshot.id уже существовал в БД.
    Использует idempotency_key для защиты от дублей при повторных циклах.
    """
    if not stop_alerts:
        return
    factory = get_session_factory()
    async with factory() as session:
        for alert in stop_alerts:
            snapshot = await session.scalar(
                select(AdSnapshot).where(AdSnapshot.fb_ad_id == alert.fb_ad_id)
            )
            if snapshot is None:
                logger.warning("Авто-стоп: снэпшот не найден для %s", alert.fb_ad_id)
                continue
            idempotency_key = f"auto:{alert.fb_ad_id}:{alert.snapshot_id}"
            # ON CONFLICT DO NOTHING — защита от дублей при гонке или повторных циклах
            stmt = (
                pg_insert(DisableTask)
                .values(
                    snapshot_id=snapshot.id,
                    offer_id=snapshot.offer_id,
                    fb_ad_id=alert.fb_ad_id,
                    ad_name=alert.ad_name,
                    open_state_token=alert.snapshot_id,
                    idempotency_key=idempotency_key,
                    requested_by_telegram_user_id=None,
                    requested_by_username="bot_auto_stop",
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
            )
            result = await session.execute(stmt)
            if result.rowcount:
                logger.info(
                    "Авто-стоп: создана задача на отключение %s (%s)",
                    alert.ad_name,
                    alert.fb_ad_id,
                )
            else:
                logger.debug(
                    "Авто-стоп: задача уже существует для %s (idempotency_key=%s)",
                    alert.fb_ad_id,
                    idempotency_key,
                )
        try:
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
    - alert_state IN [WARNING_SENT, STOP_SENT]
    - snoozed_until IS NULL или уже истёк
    - последний AlertEvent для этого fb_ad_id был > interval * 10 сек назад
    """
    reminder_threshold = interval_seconds * 10
    now = datetime.now(UTC)

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(AdSnapshot).where(
                AdSnapshot.alert_state.in_([AlertState.WARNING_SENT, AlertState.STOP_SENT]),
            )
        )
        candidates = result.scalars().all()

        if not candidates:
            return []

        reminders: list[AlertCandidate] = []
        for snap in candidates:
            # Проверяем снузер
            if snap.snoozed_until and snap.snoozed_until > now:
                continue

            # Проверяем когда был последний AlertEvent
            last_event_at = await session.scalar(
                select(func.max(AlertEvent.created_at)).where(AlertEvent.fb_ad_id == snap.fb_ad_id)
            )
            if last_event_at is None:
                continue
            secs_since = (now - last_event_at).total_seconds()
            if secs_since < reminder_threshold:
                continue

            # Строим AlertCandidate для напоминания
            stage = (
                AlertStage.STOP if snap.alert_state == AlertState.STOP_SENT else AlertStage.WARNING
            )
            rule_codes = snap.stop_rule_codes or snap.warning_rule_codes or []
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
                    metrics_json={
                        "spend": str(snap.spend),
                        "clicks": snap.clicks,
                        "cpc": str(snap.cpc) if snap.cpc is not None else None,
                        "leads": snap.leads,
                        "cost_per_lead": str(snap.cost_per_lead)
                        if snap.cost_per_lead is not None
                        else None,
                        "registrations": snap.registrations,
                        "cost_per_registration": str(snap.cost_per_registration)
                        if snap.cost_per_registration is not None
                        else None,
                        "deposits": snap.deposits,
                    },
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
    warning_percent_of_stop: Decimal = Decimal("80"),
    stop_percent_of_base: Decimal = Decimal("100"),
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
        warning_percent_of_stop: процент предупреждения от стопа
        stop_percent_of_base: глобальный коэффициент досрочного стопа для CPA-правил
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
    tg_token, tg_chat = await load_telegram_settings_from_db(
        fallback_token=telegram_bot_token,
        fallback_chat_id=telegram_chat_id,
    )
    tg_client = None
    if tg_token and tg_chat:
        tg_client = TelegramBotClient(tg_token)
    else:
        logger.warning(
            "Telegram не настроен (token=%s, chat_id=%s) — алерты не будут отправляться",
            "есть" if tg_token else "пусто",
            "есть" if tg_chat else "пусто",
        )

    # Загружаем список получателей из таблицы telegram_recipients
    tg_recipients: list[str] = []
    try:
        tg_recipients = await load_telegram_recipients_from_db()
        if tg_recipients:
            logger.info("Загружено %s получателей Telegram из БД", len(tg_recipients))
    except Exception:
        logger.debug("Не удалось загрузить получателей Telegram", exc_info=True)

    # Загружаем настройки observer из БД при старте
    try:
        (
            interval_seconds,
            jitter_seconds,
            warning_percent_of_stop,
            stop_percent_of_base,
        ) = await load_observer_settings_from_db()
        logger.info(
            "Настройки observer из БД: интервал=%sс, jitter=%sс, warning=%.0f%%, stop=%.0f%%",
            interval_seconds,
            jitter_seconds,
            warning_percent_of_stop,
            stop_percent_of_base,
        )
    except Exception:
        logger.warning("Не удалось загрузить настройки observer из БД", exc_info=True)

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
                    new_token, new_chat = await load_telegram_settings_from_db(
                        fallback_token=telegram_bot_token,
                        fallback_chat_id=telegram_chat_id,
                    )
                    if new_token and new_chat:
                        if new_token != tg_token or new_chat != tg_chat:
                            tg_token, tg_chat = new_token, new_chat
                            tg_client = TelegramBotClient(tg_token)
                            logger.info("Telegram настройки обновлены из БД")
                        elif tg_client is None:
                            tg_client = TelegramBotClient(tg_token)
                    elif not new_token or not new_chat:
                        tg_client = None
                except Exception:
                    logger.debug("Не удалось обновить TG настройки", exc_info=True)

                # Перечитываем список получателей
                try:
                    new_recipients = await load_telegram_recipients_from_db()
                    if new_recipients != tg_recipients:
                        tg_recipients = new_recipients
                        logger.info("Список получателей Telegram обновлён: %s", len(tg_recipients))
                except Exception:
                    logger.debug("Не удалось обновить список получателей Telegram", exc_info=True)

                # Перечитываем интервал и jitter из БД
                try:
                    (
                        new_interval,
                        new_jitter,
                        new_warning,
                        new_stop_percent,
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
                    warning_percent_of_stop = new_warning
                    stop_percent_of_base = new_stop_percent
                except Exception:
                    logger.debug("Не удалось обновить настройки observer из БД", exc_info=True)

                # Проверяем флаг переподключения к браузеру
                try:
                    if await check_vision_reconnect_flag() and browser_manager is not None:
                        logger.info("Переподключение к Vision браузеру по запросу из UI")
                        await browser_manager.disconnect()
                        await browser_manager.connect()
                        page = await browser_manager.get_page()
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

            # Reconcile каждый цикл: сбрасываем застрявшие CLAIMED/DISABLED → NORMAL
            try:
                reset_ids = await requeue_stuck_claimed_ads()
                for fb_ad_id in reset_ids:
                    ad_states[fb_ad_id] = (AlertState.NORMAL, None)
            except Exception:
                logger.debug("Не удалось выполнить reconcile CLAIMED-состояний", exc_info=True)

            # Telegram, UI и фоновые задачи могут менять alert_state вне observer.
            # Перед новым сканом подтягиваем БД, чтобы не слать повторный алерт поверх CLAIMED.
            try:
                ad_states = await refresh_runtime_ad_states(ad_states)
            except Exception:
                logger.debug("Не удалось синхронизировать FSM-состояния из БД", exc_info=True)

            # Проверяем флаг is_scanning_enabled перед каждым сканом
            if not await check_scanning_enabled():
                logger.info("Observer: сканирование отключено, пропускаем цикл")
                # Короткий сон перед следующей проверкой
                await asyncio.sleep(10.0)
                continue

            disable_queue_pause_reason = await get_disable_queue_pause_reason()
            if disable_queue_pause_reason:
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

            # 3. Оценка правил и сбор алертов + подготовка батча
            alerts_to_send: list[AlertCandidate] = []
            stop_alerts: list[AlertCandidate] = []  # для авто-стопа
            snapshot_batch: list[dict] = []
            now = datetime.now(UTC)

            for row in rows:
                # Выключенные объявления не оцениваем — сбрасываем FSM и идём дальше
                if row.delivery_status == "OFF":
                    current_state, _ = ad_states.get(row.fb_ad_id, (AlertState.NORMAL, None))
                    # Если объявление уже было в процессе отключения — фиксируем терминальное DISABLED.
                    off_state = resolve_off_alert_state(current_state)
                    ad_states[row.fb_ad_id] = (off_state, None)
                    offer_code = resolve_offer_code(row.ad_name, row.campaign_name, offers)
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
                            "clicks": row.clicks,
                            "cpc": row.cpc,
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
                            "last_observed_at": now,
                        }
                    )
                    continue

                # Матчинг оффера по названию
                offer_code = resolve_offer_code(row.ad_name, row.campaign_name, offers)
                offer_data = offers.get(offer_code) if offer_code else None

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
                    warning_percent_of_stop=warning_percent_of_stop,
                    stop_percent_of_base=stop_percent_of_base,
                )

                # FSM-переход
                current_state, current_token = ad_states.get(
                    row.fb_ad_id, (AlertState.NORMAL, None)
                )
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
                        "clicks": row.clicks,
                        "cpc": row.cpc,
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
                        "last_observed_at": now,
                    }
                )

                # Собираем алерты для отправки
                if should_emit and evaluation.stage is not None:
                    codes = (
                        evaluation.stop_rule_codes
                        if evaluation.stage == AlertStage.STOP
                        else evaluation.warning_rule_codes
                    )
                    matched_hits = (
                        evaluation.stop_hits if evaluation.stage == AlertStage.STOP else evaluation.warning_hits
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
                        matched_rule_codes=codes,
                        metrics_json=build_metrics_json(
                            row,
                            rule_summaries=[hit.summary for hit in matched_hits],
                        ),
                    )
                    alerts_to_send.append(candidate)
                    if is_auto_stop:
                        stop_alerts.append(candidate)
                    logger.info(
                        "AlertCandidate: %s | стадия=%s | правила=%s | fsm_было=%s",
                        row.ad_name,
                        evaluation.stage,
                        codes,
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
                "tg_client: %s, legacy_chat: %s, получателей: %s",
                len(alerts_to_send),
                len(stop_alerts),
                "есть" if tg_client else "НЕТ",
                "есть" if tg_chat else "нет",
                len(tg_recipients),
            )

            # Микропауза перед отправкой алертов (задача 1.5)
            await _human_micro_pause()

            # 4. Отправка в Telegram всем активным получателям
            if alerts_to_send and tg_client:
                # Собираем уникальный список получателей (recipients + legacy chat_id)
                all_chat_ids = list(dict.fromkeys(([tg_chat] if tg_chat else []) + tg_recipients))
                if not all_chat_ids:
                    logger.warning(
                        "Observer: есть алерты, но список получателей TG пуст — "
                        "настройте бота через UI или отправьте /start боту"
                    )
                for chat_id in all_chat_ids:
                    await _send_alerts_to_telegram(tg_client, chat_id, alerts_to_send)
            elif alerts_to_send and not tg_client:
                logger.warning(
                    "Observer: есть %s алертов, но tg_client=None — "
                    "Telegram не авторизован или не настроен",
                    len(alerts_to_send),
                )

            # Успешный цикл — сбрасываем счётчик ошибок браузера
            consecutive_browser_errors = 0

        except (ConnectionError, OSError, RuntimeError, PatchrightError) as exc:
            # Ошибка связи с браузером — пытаемся переподключиться (задача 2.4)
            consecutive_browser_errors += 1
            logger.error(
                "Ошибка связи с браузером (попытка %s/%s): %s",
                consecutive_browser_errors,
                MAX_RECONNECT_ATTEMPTS,
                exc,
            )

            if consecutive_browser_errors >= MAX_RECONNECT_ATTEMPTS:
                logger.critical(
                    "Превышено максимальное число попыток переподключения (%s). Завершение работы.",
                    MAX_RECONNECT_ATTEMPTS,
                )
                raise

            if browser_manager is not None:
                # Экспоненциальный backoff: 10, 20, 40, 80... сек (макс 30)
                delay = min(
                    BASE_RECONNECT_DELAY * (2 ** (consecutive_browser_errors - 1)),
                    30,
                )
                logger.info("Пауза %s сек перед переподключением к браузеру", delay)
                await asyncio.sleep(delay)

                try:
                    await browser_manager.disconnect()
                    await browser_manager.connect()
                    page = await browser_manager.get_page()
                    logger.info("Успешное переподключение к браузеру")
                except Exception:
                    logger.warning(
                        "Не удалось переподключиться к браузеру",
                        exc_info=True,
                    )
            else:
                # Нет browser_manager — просто ждём
                delay = min(
                    BASE_RECONNECT_DELAY * (2 ** (consecutive_browser_errors - 1)),
                    30,
                )
                await asyncio.sleep(delay)

            continue

        except Exception:
            logger.exception("Observer: ошибка в цикле")

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
    скролл перестаёт давать новые строки.
    """
    all_rows: dict[str, ScannedAdRow] = {}
    max_scroll_passes = 50  # Защита от бесконечного цикла
    prev_count = -1

    # Антидетект: перемещаем мышь в область таблицы перед скроллом
    viewport = page.viewport_size or {"width": 1280, "height": 800}
    table_x = viewport["width"] * random.uniform(0.3, 0.7)
    table_y = viewport["height"] * random.uniform(0.4, 0.6)
    await page.mouse.move(table_x, table_y)

    for pass_num in range(max_scroll_passes):
        # Парсим текущий view
        visible_rows = await parse_fn(page)
        for row in visible_rows:
            all_rows[row.fb_ad_id] = row

        # Если нет новых строк — скролл закончен
        if len(all_rows) == prev_count:
            logger.info(
                "Observer: скролл завершён, проход %s, всего строк %s",
                pass_num + 1,
                len(all_rows),
            )
            break
        prev_count = len(all_rows)

        # Антидетект: mouse.wheel от текущей позиции мыши в области таблицы
        scroll_amount = random.randint(300, 600)
        await page.mouse.wheel(0, scroll_amount)
        # Небольшой сдвиг мыши между скроллами — человек не держит мышь идеально
        jitter_x = random.uniform(-15, 15)
        jitter_y = random.uniform(-10, 10)
        await page.mouse.move(table_x + jitter_x, table_y + jitter_y)
        await asyncio.sleep(random.uniform(0.4, 0.8))

    return list(all_rows.values())


async def _send_alerts_to_telegram(
    client: TelegramBotClient,
    chat_id: str,
    alerts: list[AlertCandidate],
) -> None:
    """Отправляет алерты одному получателю — по одному сообщению на объявление."""
    for a in alerts:
        alert_state = AlertState.CLAIMED if a.stage == AlertStage.STOP else AlertState.WARNING_SENT
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
            metrics_json=a.metrics_json,
        )
        message = render_alert_message(stage=a.stage, items=[item])
        sent_msg_id = None
        try:
            result = await client.send_message(
                chat_id=chat_id,
                text=message.text,
                reply_markup=message.reply_markup,
            )
            sent_msg_id = result.get("message_id") if isinstance(result, dict) else None
            logger.info("Отправлен TG-алерт: %s, стадия=%s", a.ad_name, a.stage)
        except Exception:
            logger.exception("Не удалось отправить TG-сообщение для %s", a.ad_name)

        factory = get_session_factory()
        try:
            async with factory() as session:
                session.add(
                    AlertEvent(
                        fb_ad_id=a.fb_ad_id,
                        ad_name=a.ad_name,
                        offer_id=a.offer_id,
                        stage=a.stage,
                        state=alert_state,
                        matched_rule_codes=a.matched_rule_codes,
                        metrics_json=a.metrics_json,
                        telegram_chat_id=chat_id,
                        telegram_message_id=sent_msg_id,
                    )
                )
                await session.commit()
            logger.info("AlertEvent сохранён в БД: %s, стадия %s", a.fb_ad_id, a.stage)
        except Exception:
            logger.exception("Не удалось сохранить AlertEvent в БД")
