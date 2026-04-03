# -*- coding: utf-8 -*-
"""Согласование disable-задач с актуальными снэпшотами объявлений."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.db import get_session_factory
from core.disable_tasks import (
    ACTIVE_DISABLE_TASK_STATUSES,
    SILENT_DISABLE_INCIDENT_RETRY_LIMIT,
    reconcile_disable_tasks,
)
from core.domain import AlertStage, AlertState, DisableTaskStatus
from core.models import AdSnapshot, DisableTask
from core.observer.db_queries import ACTIVE_ALERT_WINDOW
from core.observer.service import AlertCandidate

logger = logging.getLogger(__name__)

# После успешного клика Meta может ещё долго отдавать UNKNOWN/ACTIVE, поэтому
# не сбрасываем CLAIMED слишком рано и ждём подтверждение следующими сканами.
DISABLE_SUCCESS_CONFIRMATION_GRACE_TIMEOUT = timedelta(minutes=30)
MANUAL_ATTENTION_REASON_TITLE = "Нужна ручная проверка отключения"


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
            ad_id=snapshot.ad_id,
            snapshot_id=snapshot.id,
            offer_id=snapshot.offer_id,
            open_state_token=incident_key,
            idempotency_key=idempotency_key,
            requested_by_telegram_user_id=None,
            requested_by_username="bot_auto_stop",
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )
    result = await session.execute(stmt)
    return bool(result.rowcount)


async def reconcile_disable_tasks_in_db() -> dict[str, list[str]]:
    """Согласовывает очередь отключения с актуальным состоянием снэпшотов."""
    factory = get_session_factory()
    async with factory() as session:
        summary = await reconcile_disable_tasks(session)
        if any(summary.values()):
            await session.commit()
        return summary


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
                    DisableTask.ad_id == snapshot.ad_id,
                    DisableTask.open_state_token == incident_key,
                    DisableTask.status.in_(ACTIVE_DISABLE_TASK_STATUSES),
                )
            )
            if active_count:
                continue

            recent_succeeded = await session.scalar(
                select(func.count(DisableTask.id)).where(
                    DisableTask.ad_id == snapshot.ad_id,
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
                        DisableTask.ad_id == snapshot.ad_id,
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
                    DisableTask.ad_id == snapshot.ad_id,
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
                    "Incident reconcile: %s — создаю тихий автоповтор disable "
                    "в рамках incident %s (попытка %s)",
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


async def auto_create_disable_tasks(
    stop_alerts: list[AlertCandidate],
    active_alert_window: object = ACTIVE_ALERT_WINDOW,
) -> None:
    """Автоматически создаёт DisableTask для объявлений, превысивших порог STOP.

    Вызывается после batch_save_snapshots, чтобы snapshot.id уже существовал в БД.
    Использует idempotency_key для защиты от дублей при повторных циклах.
    """
    if not stop_alerts:
        return
    factory = get_session_factory()
    async with factory() as session:
        last_scan = await session.scalar(select(func.max(AdSnapshot.last_observed_at)))
        active_cutoff = last_scan - active_alert_window if last_scan is not None else None
        created_attempts = 0
        modified_snapshots = 0
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
            modified_snapshots += 1
            auto_attempts = (
                await session.scalar(
                    select(func.count(DisableTask.id)).where(
                        DisableTask.ad_id == snapshot.ad_id,
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
        if modified_snapshots > 0 or created_attempts > 0:
            try:
                await session.commit()
            except Exception:
                logger.exception("Авто-стоп: ошибка при сохранении DisableTask")
