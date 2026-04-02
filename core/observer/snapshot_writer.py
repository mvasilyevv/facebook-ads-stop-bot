# -*- coding: utf-8 -*-
"""Батчевый upsert снэпшотов и управление границей суток кабинета."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.cabinet_day import (
    build_cabinet_day_archive_payload,
    has_any_metric_value,
    is_cabinet_day_reset_scan,
)
from core.db import get_session_factory
from core.models import AdSnapshot, CabinetDayArchive, ObserverSettings
from core.observer.scan_guard import ZeroScanGuard

logger = logging.getLogger(__name__)


async def _get_or_create_observer_settings(session) -> ObserverSettings:
    """Возвращает singleton observer_settings, создавая запись при необходимости."""
    from sqlalchemy import select

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
    from sqlalchemy import select

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
        "Observer: зафиксировано начало новых суток кабинета по zero-scan, "
        "архивировано %s объявлений",
        len(current_snapshots),
    )


async def batch_save_snapshots(
    snapshot_data: list[dict],
    scan_guard: ZeroScanGuard,
) -> None:
    """Батчевый upsert снэпшотов через INSERT ... ON CONFLICT DO UPDATE.

    Принимает список словарей с данными для AdSnapshot.
    Одна сессия, один запрос для всех снэпшотов.
    """
    if not snapshot_data:
        return
    if scan_guard.should_skip(snapshot_data):
        return

    factory = get_session_factory()
    # async with factory() as session автоматически делает rollback при исключении,
    # поэтому cabinet_day rollover и upsert атомарны — либо всё, либо ничего.
    async with factory() as session:
        await _maybe_rollover_cabinet_day(session, snapshot_data)

        stmt = pg_insert(AdSnapshot).values(snapshot_data)

        update_cols = {
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
            "cost_per_result": stmt.excluded.cost_per_result,
            "cpm": stmt.excluded.cpm,
            "frequency": stmt.excluded.frequency,
            "leads": stmt.excluded.leads,
            "cost_per_lead": stmt.excluded.cost_per_lead,
            "registrations": stmt.excluded.registrations,
            "cost_per_registration": stmt.excluded.cost_per_registration,
            "deposits": stmt.excluded.deposits,
            "outbound_clicks": stmt.excluded.outbound_clicks,
            "outbound_ctr": stmt.excluded.outbound_ctr,
            "landing_page_views": stmt.excluded.landing_page_views,
            "cost_per_landing_page_view": stmt.excluded.cost_per_landing_page_view,
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
