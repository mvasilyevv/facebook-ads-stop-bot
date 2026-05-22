# -*- coding: utf-8 -*-
"""DB-запросы для observer worker: загрузка офферов, состояний, настроек."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from core.db import get_session_factory
from core.disable_tasks import calculate_active_disable_cutoff
from core.domain import AlertStage, AlertState, DisableTaskStatus, EnableTaskStatus
from core.enable_tasks import calculate_active_enable_cutoff
from core.models import (
    AdDepositCorrection,
    AdSnapshot,
    AlertEvent,
    AlertSnooze,
    DisableTask,
    EnableRecommendationEvent,
    EnableTask,
    FbAd,
    FbAdset,
    ObserverSettings,
    Offer,
    VisionSettings,
)
from core.observer.service import AlertCandidate
from core.settings_queries import get_observer_settings
from core.telegram.service import TelegramDestination, load_telegram_runtime_config

logger = logging.getLogger(__name__)

# Период, за который объявление считается активным в рамках скан-сессии
ACTIVE_ALERT_WINDOW = timedelta(minutes=30)


def _format_task_queue_pause_reason(
    active_rows: list[tuple[object, datetime | None]],
    *,
    pending_status,
    running_status,
    retrying_status,
    now: datetime,
) -> str | None:
    """Собирает человекочитаемую причину паузы по активной очереди задач."""
    if not active_rows:
        return None

    pending_count = sum(1 for status, _ in active_rows if status == pending_status)
    running_count = sum(1 for status, _ in active_rows if status == running_status)
    ready_retry_times = [
        next_retry_at
        for status, next_retry_at in active_rows
        if status == retrying_status and next_retry_at is not None and next_retry_at <= now
    ]
    retry_count = sum(
        1
        for status, next_retry_at in active_rows
        if status == retrying_status and (next_retry_at is None or next_retry_at <= now)
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


async def load_fake_deposits() -> dict[str, int]:
    """Загружает карту fb_ad_id → fake_count из БД.

    Returns:
        dict[fb_ad_id -> fake_count] (только записи с fake_count > 0)
    """
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(FbAd.fb_ad_id, AdDepositCorrection.fake_count)
            .join(FbAd, FbAd.id == AdDepositCorrection.ad_id)
            .where(AdDepositCorrection.fake_count > 0)
        )
        fake_map = {fb_ad_id: count for fb_ad_id, count in result.all()}
    logger.info("Загружено %s корректировок ложных депозитов", len(fake_map))
    return fake_map


# Дефолтный горизонт: снэпшоты старше этого порога не участвуют в сканировании
_DEFAULT_AD_STATE_WINDOW_HOURS = 24


async def load_ad_states_from_db(
    cutoff: datetime | None = None,
) -> dict[str, tuple[AlertState, str | None]]:
    """Загружает FSM-состояния из БД при старте воркера.

    Читает alert_state и open_state_token только из «свежих» снэпшотов,
    чтобы не тащить в память тысячи устаревших записей.

    Args:
        cutoff: нижняя граница last_observed_at; если None — дефолт 24 часа назад.
    """
    # Вычисляем горизонт: только снэпшоты не старше cutoff
    if cutoff is None:
        cutoff = datetime.now(UTC) - timedelta(hours=_DEFAULT_AD_STATE_WINDOW_HOURS)

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(
                AdSnapshot.fb_ad_id,
                AdSnapshot.alert_state,
                AdSnapshot.open_state_token,
            ).where(AdSnapshot.last_observed_at >= cutoff)
        )
        rows = result.all()

    states: dict[str, tuple[AlertState, str | None]] = {}
    for fb_ad_id, alert_state, token in rows:
        states[fb_ad_id] = (alert_state, token)

    logger.info(
        "Загружено %s FSM-состояний из БД (за последние %s ч)",
        len(states),
        _DEFAULT_AD_STATE_WINDOW_HOURS,
    )
    return states


async def refresh_runtime_ad_states(
    current_states: dict[str, tuple[AlertState, str | None]],
    cutoff: datetime | None = None,
) -> dict[str, tuple[AlertState, str | None]]:
    """Синхронизирует in-memory FSM с БД, чтобы внешние действия сразу были видны observer.

    Args:
        current_states: текущий in-memory словарь состояний.
        cutoff: горизонт для load_ad_states_from_db; если None — дефолт 24 часа.
    """
    persisted_states = await load_ad_states_from_db(cutoff=cutoff)
    if persisted_states != current_states:
        logger.info(
            "Observer: FSM-состояния синхронизированы с БД (%s записей)",
            len(persisted_states),
        )
    return persisted_states


async def check_scanning_enabled() -> bool:
    """Проверяет флаг is_scanning_enabled из ObserverSettings.

    Returns:
        True если сканирование включено (или настройки не найдены).
    """
    factory = get_session_factory()
    try:
        async with factory() as session:
            row = await get_observer_settings(session)
            if row is not None:
                return bool(row.is_scanning_enabled)
    except Exception:
        logger.debug("Не удалось проверить флаг is_scanning_enabled", exc_info=True)
    return True


async def peek_scan_requested_flag() -> bool:
    """Возвращает True если флаг scan_requested сейчас выставлен.

    В отличие от consume_scan_requested_flag — флаг НЕ сбрасывается. Используется
    для пробуждения наблюдателя из адаптивного сна без потребления флага.
    """
    factory = get_session_factory()
    try:
        async with factory() as session:
            row = await get_observer_settings(session)
            if row is not None:
                return bool(row.scan_requested)
    except Exception:
        logger.debug("Не удалось прочитать флаг scan_requested", exc_info=True)
    return False


async def consume_scan_requested_flag() -> bool:
    """Атомарно проверяет и сбрасывает флаг scan_requested в ObserverSettings.

    Использует UPDATE...WHERE...RETURNING для предотвращения race condition
    между observer и API. Возвращает True ровно один раз на каждое выставление флага.
    """
    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await session.execute(
                update(ObserverSettings)
                .where(
                    ObserverSettings.singleton_key == "default",
                    ObserverSettings.scan_requested.is_(True),
                )
                .values(scan_requested=False)
                .returning(ObserverSettings.id)
            )
            affected = result.first()
            await session.commit()
            if affected:
                logger.info("Флаг scan_requested сброшен — выполняем немедленный скан")
                return True
    except Exception:
        logger.debug("Не удалось сбросить флаг scan_requested", exc_info=True)
    return False


async def check_vision_reconnect_flag() -> bool:
    """Атомарно проверяет и сбрасывает флаг reconnect_requested в VisionSettings.

    Использует UPDATE...WHERE...RETURNING для предотвращения race condition.
    """
    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await session.execute(
                update(VisionSettings)
                .where(
                    VisionSettings.singleton_key == "default",
                    VisionSettings.reconnect_requested.is_(True),
                )
                .values(reconnect_requested=False)
                .returning(VisionSettings.id)
            )
            affected = result.first()
            await session.commit()
            if affected:
                logger.info(
                    "Флаг reconnect_requested сброшен — выполняем переподключение к браузеру"
                )
                return True
    except Exception:
        logger.debug("Не удалось проверить флаг reconnect_requested", exc_info=True)
    return False


async def consume_scan_flags_combined() -> tuple[bool, bool, bool]:
    """Объединённый префлайт: за одну сессию читает и сбрасывает три флага.

    Возвращает кортеж (scanning_enabled, scan_requested_consumed, vision_reconnect_consumed).

    Экономит 2 round-trip к Postgres на каждый цикл observer по сравнению с
    последовательными вызовами check_scanning_enabled + consume_scan_requested_flag +
    check_vision_reconnect_flag.
    """
    scanning_enabled = True
    scan_requested = False
    vision_reconnect = False
    factory = get_session_factory()
    try:
        async with factory() as session:
            observer_row = await get_observer_settings(session)
            if observer_row is not None:
                scanning_enabled = bool(observer_row.is_scanning_enabled)
                if observer_row.scan_requested:
                    upd_scan = await session.execute(
                        update(ObserverSettings)
                        .where(
                            ObserverSettings.singleton_key == "default",
                            ObserverSettings.scan_requested.is_(True),
                        )
                        .values(scan_requested=False)
                        .returning(ObserverSettings.id)
                    )
                    if upd_scan.first():
                        scan_requested = True

            upd_vision = await session.execute(
                update(VisionSettings)
                .where(
                    VisionSettings.singleton_key == "default",
                    VisionSettings.reconnect_requested.is_(True),
                )
                .values(reconnect_requested=False)
                .returning(VisionSettings.id)
            )
            if upd_vision.first():
                vision_reconnect = True

            await session.commit()
    except Exception:
        logger.debug("Объединённый префлайт-запрос флагов не выполнен", exc_info=True)
    if scan_requested:
        logger.info("Флаг scan_requested сброшен — выполняем немедленный скан")
    if vision_reconnect:
        logger.info("Флаг reconnect_requested сброшен — выполняем переподключение к браузеру")
    return scanning_enabled, scan_requested, vision_reconnect


async def get_disable_queue_pause_reason() -> str | None:
    """Возвращает причину паузы сканирования, если очередь отключения блокирует браузер."""
    factory = get_session_factory()
    try:
        async with factory() as session:
            last_scan = await session.scalar(select(func.max(AdSnapshot.last_observed_at)))
            result = await session.execute(
                select(
                    DisableTask.status,
                    DisableTask.next_retry_at,
                    DisableTask.created_at,
                    DisableTask.updated_at,
                    AdSnapshot.last_observed_at,
                )
                .join(AdSnapshot, AdSnapshot.ad_id == DisableTask.ad_id, isouter=True)
                .where(
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
    active_cutoff = calculate_active_disable_cutoff(now=now, last_scan=last_scan)
    active_rows: list[tuple[DisableTaskStatus, datetime | None]] = []

    for status, next_retry_at, created_at, updated_at, snapshot_last_observed_at in rows:
        if snapshot_last_observed_at is not None:
            if snapshot_last_observed_at < active_cutoff:
                continue
        else:
            task_activity_at = updated_at or created_at
            if task_activity_at is None or task_activity_at < active_cutoff:
                continue
        active_rows.append((status, next_retry_at))

    if not active_rows:
        return None

    return _format_task_queue_pause_reason(
        active_rows,
        pending_status=DisableTaskStatus.PENDING,
        running_status=DisableTaskStatus.RUNNING,
        retrying_status=DisableTaskStatus.RETRYING,
        now=now,
    )


async def get_enable_queue_pause_reason() -> str | None:
    """Возвращает причину паузы сканирования, если очередь включения блокирует браузер."""
    factory = get_session_factory()
    try:
        async with factory() as session:
            last_scan = await session.scalar(select(func.max(AdSnapshot.last_observed_at)))
            observer_settings = await get_observer_settings(session)
            cabinet_day_start = (
                observer_settings.cabinet_day_started_at if observer_settings is not None else None
            )
            result = await session.execute(
                select(
                    EnableTask.status,
                    EnableTask.next_retry_at,
                    EnableTask.created_at,
                    EnableTask.updated_at,
                    AdSnapshot.last_observed_at,
                    EnableRecommendationEvent.live_batch_started_at,
                )
                .join(AdSnapshot, AdSnapshot.ad_id == EnableTask.ad_id, isouter=True)
                .join(
                    EnableRecommendationEvent,
                    EnableRecommendationEvent.id == EnableTask.recommendation_event_id,
                    isouter=True,
                )
                .where(
                    EnableTask.status.in_(
                        (
                            EnableTaskStatus.PENDING,
                            EnableTaskStatus.RUNNING,
                            EnableTaskStatus.RETRYING,
                        )
                    )
                )
            )
            rows = result.all()
    except Exception:
        logger.debug("Не удалось проверить активную очередь включения", exc_info=True)
        return None

    if not rows:
        return None

    now = datetime.now(UTC)
    active_cutoff = calculate_active_enable_cutoff(now=now, last_scan=last_scan)
    active_rows: list[tuple[EnableTaskStatus, datetime | None]] = []

    for (
        status,
        next_retry_at,
        created_at,
        updated_at,
        snapshot_last_observed_at,
        live_batch_started_at,
    ) in rows:
        if cabinet_day_start is not None:
            if live_batch_started_at is not None:
                if live_batch_started_at < cabinet_day_start:
                    continue
            elif created_at < cabinet_day_start:
                continue

        if snapshot_last_observed_at is not None:
            if snapshot_last_observed_at < active_cutoff:
                continue
        else:
            task_activity_at = updated_at or created_at
            if task_activity_at is None or task_activity_at < active_cutoff:
                continue

        active_rows.append((status, next_retry_at))

    return _format_task_queue_pause_reason(
        active_rows,
        pending_status=EnableTaskStatus.PENDING,
        running_status=EnableTaskStatus.RUNNING,
        retrying_status=EnableTaskStatus.RETRYING,
        now=now,
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
    from core.settings_queries import get_or_create_observer_settings

    factory = get_session_factory()
    async with factory() as session:
        row = await get_or_create_observer_settings(session)
        row.is_scanning_enabled = enabled
        await session.commit()


# Фиксированный порог для напоминаний: 10 минут (интервал теперь адаптивный)
_REMINDER_THRESHOLD_SECONDS = 600


async def collect_reminder_alerts() -> list[AlertCandidate]:
    """Собирает алерты для повторного напоминания.

    Условия:
    - alert_state IN [WARNING_SENT, STOP_SENT]
    - last_observed_at попадает в актуальную скан-сессию
    - snoozed_until IS NULL или уже истёк
    - последний AlertEvent для этого fb_ad_id был > 10 мин назад
    """
    reminder_threshold = _REMINDER_THRESHOLD_SECONDS
    now = datetime.now(UTC)

    factory = get_session_factory()
    async with factory() as session:
        last_scan = await session.scalar(select(func.max(AdSnapshot.last_observed_at)))
        if last_scan is None:
            return []
        active_cutoff = last_scan - ACTIVE_ALERT_WINDOW

        result = await session.execute(
            select(AdSnapshot)
            .options(
                selectinload(AdSnapshot.fb_ad)
                .selectinload(FbAd.adset)
                .selectinload(FbAdset.campaign),
            )
            .where(
                AdSnapshot.alert_state.in_(
                    [
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

        # Фильтруем кандидатов до batch-запросов
        filtered: list[AdSnapshot] = []
        for snap in candidates:
            if snap.last_observed_at is None or snap.last_observed_at < active_cutoff:
                logger.info(
                    "Observer: пропускаю напоминание для %s — объявление уже в архиве",
                    snap.fb_ad_id,
                )
                continue
            if (
                snap.alert_state == AlertState.WARNING_SENT
                and snap.snoozed_until
                and snap.snoozed_until > now
            ):
                continue
            filtered.append(snap)

        if not filtered:
            return []

        # Batch-загрузка: MAX(created_at) по ad_id — один запрос вместо N
        ad_ids = [snap.ad_id for snap in filtered if snap.ad_id is not None]
        last_event_at_result = await session.execute(
            select(AlertEvent.ad_id, func.max(AlertEvent.created_at).label("max_at"))
            .where(AlertEvent.ad_id.in_(ad_ids))
            .group_by(AlertEvent.ad_id)
        )
        last_event_at_map: dict = {row.ad_id: row.max_at for row in last_event_at_result}

        # Batch-загрузка: последний AlertEvent по ad_id — один запрос через DISTINCT ON
        # (PostgreSQL-специфичный, но проект использует только Postgres)
        latest_events_result = await session.execute(
            select(AlertEvent)
            .where(AlertEvent.ad_id.in_(ad_ids))
            .order_by(AlertEvent.ad_id, AlertEvent.updated_at.desc(), AlertEvent.created_at.desc())
            .distinct(AlertEvent.ad_id)
        )
        latest_events_map: dict = {evt.ad_id: evt for evt in latest_events_result.scalars().all()}

        reminders: list[AlertCandidate] = []
        for snap in filtered:
            last_event_at = last_event_at_map.get(snap.ad_id)
            if last_event_at is None:
                continue
            secs_since = (now - last_event_at).total_seconds()
            if secs_since < reminder_threshold:
                continue

            last_event = latest_events_map.get(snap.ad_id)

            if snap.alert_state == AlertState.STOP_SENT:
                stage = AlertStage.STOP
                rule_codes = snap.stop_rule_codes or []
            else:
                stage = AlertStage.WARNING
                rule_codes = snap.warning_rule_codes or []

            rule_summaries = None
            traffic_diagnostics = None
            if last_event and isinstance(last_event.metrics_json, dict):
                raw_summaries = last_event.metrics_json.get("rule_summaries")
                if isinstance(raw_summaries, list) and raw_summaries:
                    rule_summaries = [str(summary) for summary in raw_summaries]
                raw_diagnostics = last_event.metrics_json.get("traffic_diagnostics")
                if isinstance(raw_diagnostics, dict) and raw_diagnostics:
                    traffic_diagnostics = dict(raw_diagnostics)

            # Получаем данные через нормализованную цепочку
            _fb_ad = snap.fb_ad
            _ad_name = _fb_ad.ad_name if _fb_ad else ""
            _adset = _fb_ad.adset if _fb_ad else None
            _campaign = _adset.campaign if _adset else None
            _campaign_name = _campaign.campaign_name if _campaign else ""
            _adset_name = _adset.adset_name if _adset else ""
            _offer_code = _campaign.offer_code if _campaign else None
            _offer_id = _campaign.offer_id if _campaign else None

            reminders.append(
                AlertCandidate(
                    snapshot_id=snap.open_state_token or str(snap.id),
                    offer_id=_offer_id,
                    fb_ad_id=snap.fb_ad_id,
                    ad_name=_ad_name,
                    campaign_name=_campaign_name,
                    adset_name=_adset_name,
                    offer_code=_offer_code,
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


async def load_active_snooze_ad_ids(session=None) -> set[str]:
    """Возвращает множество fb_ad_id с активным снузом (snoozed_until > now).

    Если session не передана — создаёт собственную.
    """
    now = datetime.now(UTC)

    async def _query(s) -> set[str]:
        result = await s.execute(
            select(AlertSnooze.fb_ad_id).where(AlertSnooze.snoozed_until > now)
        )
        return {row for (row,) in result.all()}

    if session is not None:
        return await _query(session)

    factory = get_session_factory()
    async with factory() as s:
        return await _query(s)


async def load_recent_alerts_with_context(
    session,
    *,
    limit: int = 10,
    since_hours: int = 24,
) -> list[dict]:
    """Загружает последние N AlertEvent за указанный период с контекстом кампании и адсета.

    Возвращает список словарей:
    {
        "alert_event": AlertEvent,
        "fb_ad_id": str,
        "ad_name": str,
        "campaign_name": str,
        "adset_name": str,
        "fsm_state": str,   # значение AlertState
        "created_at": datetime,
    }
    """
    cutoff = datetime.now(tz=UTC) - timedelta(hours=since_hours)

    result = await session.execute(
        select(AlertEvent)
        .options(
            selectinload(AlertEvent.fb_ad).selectinload(FbAd.adset).selectinload(FbAdset.campaign)
        )
        .where(AlertEvent.created_at >= cutoff)
        .order_by(AlertEvent.created_at.desc())
        .limit(limit)
    )
    events = result.scalars().unique().all()

    rows = []
    for event in events:
        fb_ad = event.fb_ad
        if fb_ad is None:
            fb_ad_id = ""
            ad_name = "—"
            campaign_name = "Без кампании"
            adset_name = "Без адсета"
        else:
            fb_ad_id = fb_ad.fb_ad_id
            ad_name = fb_ad.ad_name or fb_ad_id
            adset = fb_ad.adset
            if adset is None:
                campaign_name = "Без кампании"
                adset_name = "Без адсета"
            else:
                adset_name = adset.adset_name or "Без адсета"
                campaign = adset.campaign
                campaign_name = campaign.campaign_name if campaign else "Без кампании"

        rows.append(
            {
                "alert_event": event,
                "fb_ad_id": fb_ad_id,
                "ad_name": ad_name,
                "campaign_name": campaign_name,
                "adset_name": adset_name,
                "fsm_state": str(event.state.value) if event.state else "—",
                "created_at": event.created_at,
            }
        )
    return rows


async def load_history_ad_ids_with_metrics(
    fb_ad_ids: list[str], *, lookback_hours: int = 24
) -> set[str]:
    """Возвращает подмножество fb_ad_id, у которых за последние N часов были непустые метрики.

    Используется outcome_classifier'ом как гард: если у текущих объявлений никогда
    не было данных, то отсутствие метрик — норма, а не STALE_DATA.
    """
    if not fb_ad_ids:
        return set()
    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(AdSnapshot.fb_ad_id)
            .where(
                AdSnapshot.fb_ad_id.in_(fb_ad_ids),
                AdSnapshot.last_observed_at >= cutoff,
                AdSnapshot.impressions > 0,
            )
            .distinct()
        )
        return {row[0] for row in result.all()}
