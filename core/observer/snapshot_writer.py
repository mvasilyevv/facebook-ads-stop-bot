# -*- coding: utf-8 -*-
"""Батчевый upsert снэпшотов и управление границей суток кабинета."""

from __future__ import annotations

import logging
import uuid as _uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.cabinet_day import (
    build_cabinet_day_archive_payload,
    has_any_metric_value,
    is_cabinet_day_reset_scan,
)
from core.db import get_session_factory
from core.models import AdMetricHistory, AdSnapshot, CabinetDayArchive, FbAd
from core.observer.scan_guard import ZeroScanGuard
from core.settings_queries import get_or_create_observer_settings

logger = logging.getLogger(__name__)


async def _maybe_rollover_cabinet_day(session, snapshot_data: list[dict]) -> None:
    """Переводит границу суток кабинета при полном zero-scan и архивирует прошлый день."""
    from sqlalchemy import select

    if not snapshot_data:
        return

    settings = await get_or_create_observer_settings(session)
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
            summary_json, campaigns_json, ads_json = build_cabinet_day_archive_payload(
                current_snapshots
            )
            session.add(
                CabinetDayArchive(
                    started_at=baseline_started_at,
                    ended_at=scan_started_at,
                    reset_detected_at=scan_started_at,
                    ads_count=len(current_snapshots),
                    summary_json=summary_json,
                    campaigns_json=campaigns_json,
                    ads_json=ads_json,
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

    summary_json, campaigns_json, ads_json = build_cabinet_day_archive_payload(current_snapshots)
    session.add(
        CabinetDayArchive(
            started_at=settings.cabinet_day_started_at,
            ended_at=scan_started_at,
            reset_detected_at=scan_started_at,
            ads_count=len(current_snapshots),
            summary_json=summary_json,
            campaigns_json=campaigns_json,
            ads_json=ads_json,
        )
    )
    settings.cabinet_day_started_at = scan_started_at
    logger.info(
        "Observer: зафиксировано начало новых суток кабинета по zero-scan, "
        "архивировано %s объявлений",
        len(current_snapshots),
    )


# Метрики, изменение которых триггерит запись в ad_metric_history
_TRACKED_METRICS = (
    "spend",
    "clicks",
    "leads",
    "registrations",
    "deposits",
    "outbound_clicks",
    "landing_page_views",
)


async def _upsert_fb_ads(
    session,
    snapshot_data: list[dict],
) -> dict[str, _uuid.UUID]:
    """Upsert справочника fb_ads и возврат маппинга fb_ad_id → id.

    Args:
        session: Async SQLAlchemy сессия.
        snapshot_data: Список словарей с данными снэпшотов.

    Returns:
        Маппинг fb_ad_id → UUID id записи в fb_ads.
    """
    now = datetime.now(UTC)
    fb_ad_rows = []
    for item in snapshot_data:
        fb_ad_rows.append(
            {
                "id": _uuid.uuid4(),
                "fb_ad_id": item["fb_ad_id"],
                "campaign_name": item.get("campaign_name", ""),
                "adset_name": item.get("adset_name", ""),
                "ad_name": item.get("ad_name", ""),
                "offer_id": item.get("offer_id"),
                "offer_code": item.get("resolved_offer_code"),
                "first_seen_at": now,
                "last_seen_at": now,
            }
        )

    stmt = pg_insert(FbAd).values(fb_ad_rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["fb_ad_id"],
        set_={
            "campaign_name": func.coalesce(
                func.nullif(stmt.excluded.campaign_name, ""),
                FbAd.campaign_name,
            ),
            "adset_name": func.coalesce(
                func.nullif(stmt.excluded.adset_name, ""),
                FbAd.adset_name,
            ),
            "ad_name": stmt.excluded.ad_name,
            "offer_id": stmt.excluded.offer_id,
            "offer_code": stmt.excluded.offer_code,
            "last_seen_at": stmt.excluded.last_seen_at,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)

    # Загружаем маппинг fb_ad_id → id
    fb_ad_ids = [item["fb_ad_id"] for item in snapshot_data]
    result = await session.execute(
        select(FbAd.fb_ad_id, FbAd.id).where(FbAd.fb_ad_id.in_(fb_ad_ids))
    )
    return {row.fb_ad_id: row.id for row in result.all()}


def _metrics_changed(
    old_snap: AdSnapshot | None,
    new_data: dict,
) -> bool:
    """Проверяет, изменились ли ключевые метрики по сравнению с текущим снэпшотом."""
    if old_snap is None:
        return True
    for key in _TRACKED_METRICS:
        old_val = getattr(old_snap, key, None)
        new_val = new_data.get(key)
        # Приводим к общему типу для сравнения
        if old_val is None and new_val is None:
            continue
        if old_val is None or new_val is None:
            return True
        if isinstance(old_val, Decimal):
            old_val = float(old_val)
        if isinstance(new_val, Decimal):
            new_val = float(new_val)
        if old_val != new_val:
            return True
    return False


async def _save_metric_deltas(
    session,
    snapshot_data: list[dict],
    ad_id_map: dict[str, _uuid.UUID],
) -> int:
    """Записывает в ad_metric_history только изменённые метрики.

    Args:
        session: Async SQLAlchemy сессия.
        snapshot_data: Список новых данных снэпшотов.
        ad_id_map: Маппинг fb_ad_id → fb_ads.id.

    Returns:
        Количество записанных строк истории.
    """
    # Загружаем текущие снэпшоты для сравнения
    fb_ad_ids = [item["fb_ad_id"] for item in snapshot_data]
    result = await session.execute(select(AdSnapshot).where(AdSnapshot.fb_ad_id.in_(fb_ad_ids)))
    current_snaps = {snap.fb_ad_id: snap for snap in result.scalars().all()}

    now = datetime.now(UTC)
    history_rows = []
    for item in snapshot_data:
        fb_ad_id = item["fb_ad_id"]
        ad_id = ad_id_map.get(fb_ad_id)
        if ad_id is None:
            continue
        old_snap = current_snaps.get(fb_ad_id)
        if not _metrics_changed(old_snap, item):
            continue
        history_rows.append(
            {
                "id": _uuid.uuid4(),
                "ad_id": ad_id,
                "cycle_ts": now,
                "spend": item.get("spend", Decimal("0")),
                "reach": item.get("reach", 0),
                "impressions": item.get("impressions", 0),
                "clicks": item.get("clicks", 0),
                "cpc": item.get("cpc"),
                "ctr": item.get("ctr"),
                "cost_per_result": item.get("cost_per_result"),
                "cpm": item.get("cpm"),
                "frequency": item.get("frequency"),
                "leads": item.get("leads", 0),
                "cost_per_lead": item.get("cost_per_lead"),
                "registrations": item.get("registrations", 0),
                "cost_per_registration": item.get("cost_per_registration"),
                "deposits": item.get("deposits", 0),
                "outbound_clicks": item.get("outbound_clicks", 0),
                "outbound_ctr": item.get("outbound_ctr"),
                "landing_page_views": item.get("landing_page_views", 0),
                "cost_per_landing_page_view": item.get("cost_per_landing_page_view"),
            }
        )

    if history_rows:
        await session.execute(pg_insert(AdMetricHistory).values(history_rows))
    return len(history_rows)


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

        # 1. Upsert в fb_ads — справочник объявлений
        ad_id_map = await _upsert_fb_ads(session, snapshot_data)

        # 2. Записываем дельты метрик в ad_metric_history (до перезаписи снэпшотов)
        history_count = await _save_metric_deltas(session, snapshot_data, ad_id_map)
        if history_count:
            logger.info("Записано %s строк в ad_metric_history", history_count)

        # 3. Добавляем ad_id в данные снэпшотов
        for item in snapshot_data:
            item["ad_id"] = ad_id_map.get(item["fb_ad_id"])

        stmt = pg_insert(AdSnapshot).values(snapshot_data)

        update_cols = {
            "ad_id": stmt.excluded.ad_id,
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
