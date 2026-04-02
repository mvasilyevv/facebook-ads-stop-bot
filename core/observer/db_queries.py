# -*- coding: utf-8 -*-
"""DB-запросы для observer worker: загрузка офферов, состояний, настроек."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from core.db import get_session_factory
from core.domain import AlertStage, AlertState, DisableTaskStatus
from core.models import (
    AdSnapshot,
    AlertEvent,
    DisableTask,
    ObserverSettings,
    Offer,
    VisionSettings,
)
from core.observer.service import AlertCandidate
from core.observer.thresholds import extract_observer_threshold_values
from core.telegram.service import TelegramDestination, load_telegram_runtime_config

logger = logging.getLogger(__name__)

# Период, за который объявление считается активным в рамках скан-сессии
ACTIVE_ALERT_WINDOW = timedelta(minutes=30)


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


async def load_telegram_settings_from_db(
    fallback_token: str = "",
    fallback_chat_id: str = "",
) -> tuple[str, list[TelegramDestination]]:
    """Обёртка для загрузки токена и активных получателей Telegram."""
    return await load_telegram_runtime_config(
        fallback_token=fallback_token,
        fallback_chat_id=fallback_chat_id,
    )


async def set_observer_scanning_enabled(enabled: bool) -> None:
    """Переключает флаг сканирования observer в singleton-настройках."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = ObserverSettings(singleton_key="default")
            session.add(row)
        row.is_scanning_enabled = enabled
        await session.commit()


async def collect_reminder_alerts(interval_seconds: int) -> list[AlertCandidate]:
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
                        "outbound_clicks": snap.outbound_clicks,
                        "outbound_ctr": str(snap.outbound_ctr)
                        if snap.outbound_ctr is not None
                        else None,
                        "landing_page_views": snap.landing_page_views,
                        "cost_per_landing_page_view": str(snap.cost_per_landing_page_view)
                        if snap.cost_per_landing_page_view is not None
                        else None,
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
