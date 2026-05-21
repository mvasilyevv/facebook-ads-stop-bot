# -*- coding: utf-8 -*-
"""Батчевый upsert снэпшотов observer'а.

Граница суток кабинета теперь сдвигается только вручную через
POST /api/observer/start-new-cabinet-day и не зависит от scan-цикла.
"""

from __future__ import annotations

import logging
import uuid as _uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_session_factory
from core.models import (
    AdMetricHistory,
    AdSnapshot,
    FbAd,
    FbAdset,
    FbCampaign,
)
from core.observer.regression_guard import RegressionGuard
from core.observer.scan_guard import ZeroScanGuard

logger = logging.getLogger(__name__)


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
_CUMULATIVE_METRICS = (
    "spend",
    "clicks",
    "leads",
    "registrations",
    "deposits",
    "outbound_clicks",
    "landing_page_views",
)


def _has_cumulative_metric_regression(
    old_snap: AdSnapshot | None,
    new_data: dict,
) -> bool:
    """Проверяет, что накопительные метрики не откатились назад внутри дня."""
    if old_snap is None:
        return False
    for key in _CUMULATIVE_METRICS:
        old_val = getattr(old_snap, key, None)
        new_val = new_data.get(key)
        if old_val is None or new_val is None:
            continue
        if Decimal(str(new_val)) < Decimal(str(old_val)):
            return True
    return False


async def _upsert_fb_campaigns(
    session: AsyncSession,
    snapshot_data: list[dict],
) -> dict[str, _uuid.UUID]:
    """Upsert справочника fb_campaigns и возврат маппинга campaign_name → id.

    Args:
        session: Async SQLAlchemy сессия.
        snapshot_data: Список словарей с данными снэпшотов.

    Returns:
        Маппинг campaign_name → UUID id записи в fb_campaigns.
    """
    now = datetime.now(UTC)
    # Собираем уникальные кампании
    campaigns_seen: dict[str, dict] = {}
    for item in snapshot_data:
        cname = item.get("campaign_name", "")
        if not cname or cname in campaigns_seen:
            continue
        campaigns_seen[cname] = {
            "id": _uuid.uuid4(),
            "campaign_name": cname,
            "offer_id": item.get("offer_id"),
            "offer_code": item.get("resolved_offer_code"),
            "first_seen_at": now,
            "last_seen_at": now,
        }

    if not campaigns_seen:
        return {}

    rows = list(campaigns_seen.values())
    stmt = pg_insert(FbCampaign).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["campaign_name"],
        set_={
            "offer_id": stmt.excluded.offer_id,
            "offer_code": stmt.excluded.offer_code,
            "last_seen_at": stmt.excluded.last_seen_at,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)

    # Загружаем маппинг campaign_name → id
    names = list(campaigns_seen.keys())
    result = await session.execute(
        select(FbCampaign.campaign_name, FbCampaign.id).where(FbCampaign.campaign_name.in_(names))
    )
    return {row.campaign_name: row.id for row in result.all()}


async def _upsert_fb_adsets(
    session: AsyncSession,
    snapshot_data: list[dict],
    campaign_id_map: dict[str, _uuid.UUID],
) -> dict[tuple[str, str], _uuid.UUID]:
    """Upsert справочника fb_adsets и возврат маппинга (campaign_name, adset_name) → id.

    Args:
        session: Async SQLAlchemy сессия.
        snapshot_data: Список словарей с данными снэпшотов.
        campaign_id_map: Маппинг campaign_name → fb_campaigns.id.

    Returns:
        Маппинг (campaign_name, adset_name) → UUID id записи в fb_adsets.
    """
    now = datetime.now(UTC)
    adsets_seen: dict[tuple[str, str], dict] = {}
    for item in snapshot_data:
        cname = item.get("campaign_name", "")
        aname = item.get("adset_name", "")
        campaign_id = campaign_id_map.get(cname)
        if not campaign_id or not aname:
            continue
        key = (cname, aname)
        if key in adsets_seen:
            continue
        adsets_seen[key] = {
            "id": _uuid.uuid4(),
            "adset_name": aname,
            "campaign_id": campaign_id,
            "first_seen_at": now,
            "last_seen_at": now,
        }

    if not adsets_seen:
        return {}

    rows = list(adsets_seen.values())
    stmt = pg_insert(FbAdset).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["campaign_id", "adset_name"],
        set_={
            "last_seen_at": stmt.excluded.last_seen_at,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)

    # Загружаем маппинг (campaign_name, adset_name) → id
    campaign_ids = list(campaign_id_map.values())
    result = await session.execute(
        select(FbCampaign.campaign_name, FbAdset.adset_name, FbAdset.id)
        .join(FbCampaign, FbCampaign.id == FbAdset.campaign_id)
        .where(FbAdset.campaign_id.in_(campaign_ids))
    )
    return {(row.campaign_name, row.adset_name): row.id for row in result.all()}


async def _upsert_fb_ads(
    session: AsyncSession,
    snapshot_data: list[dict],
    adset_id_map: dict[tuple[str, str], _uuid.UUID],
) -> dict[str, _uuid.UUID]:
    """Upsert справочника fb_ads и возврат маппинга fb_ad_id → id.

    Args:
        session: Async SQLAlchemy сессия.
        snapshot_data: Список словарей с данными снэпшотов.
        adset_id_map: Маппинг (campaign_name, adset_name) → fb_adsets.id.

    Returns:
        Маппинг fb_ad_id → UUID id записи в fb_ads.
    """
    now = datetime.now(UTC)

    # Собираем fb_ad_id объявлений без адсета для fallback-запроса
    missing_adset_fb_ids = [
        item["fb_ad_id"]
        for item in snapshot_data
        if not adset_id_map.get((item.get("campaign_name", ""), item.get("adset_name", "")))
    ]
    # Fallback: ищем уже существующие fb_ads по fb_ad_id напрямую
    fallback_ad_id_map: dict[str, _uuid.UUID] = {}
    if missing_adset_fb_ids:
        result = await session.execute(
            select(FbAd.fb_ad_id, FbAd.id).where(FbAd.fb_ad_id.in_(missing_adset_fb_ids))
        )
        fallback_ad_id_map = {row.fb_ad_id: row.id for row in result.all()}

    fb_ad_rows = []
    for item in snapshot_data:
        cname = item.get("campaign_name", "")
        aname = item.get("adset_name", "")
        adset_id = adset_id_map.get((cname, aname))
        if adset_id is None:
            if item["fb_ad_id"] in fallback_ad_id_map:
                logger.debug(
                    "Объявление %s: адсет (%s, %s) не найден, используем существующую запись fb_ads",
                    item["fb_ad_id"],
                    cname,
                    aname,
                )
                continue  # не обновляем fb_ads, но ad_id уже есть в fallback_ad_id_map
            logger.warning(
                "Пропускаю объявление %s — не найден адсет (%s, %s)",
                item["fb_ad_id"],
                cname,
                aname,
            )
            continue
        fb_ad_rows.append(
            {
                "id": _uuid.uuid4(),
                "fb_ad_id": item["fb_ad_id"],
                "ad_name": item.get("ad_name", ""),
                "adset_id": adset_id,
                "first_seen_at": now,
                "last_seen_at": now,
            }
        )

    if fb_ad_rows:
        stmt = pg_insert(FbAd).values(fb_ad_rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["fb_ad_id"],
            set_={
                "ad_name": stmt.excluded.ad_name,
                "adset_id": stmt.excluded.adset_id,
                "last_seen_at": stmt.excluded.last_seen_at,
                "updated_at": func.now(),
            },
        )
        await session.execute(stmt)

    # Если нет ни новых строк, ни fallback-записей — возвращаем пустой маппинг
    if not fb_ad_rows and not fallback_ad_id_map:
        return {}

    # Загружаем маппинг fb_ad_id → id (включает и новые, и fallback-записи)
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
    session: AsyncSession,
    snapshot_data: list[dict],
    ad_id_map: dict[str, _uuid.UUID],
    regression_guard: RegressionGuard | None = None,
) -> int:
    """Записывает в ad_metric_history только изменённые метрики.

    Args:
        session: Async SQLAlchemy сессия.
        snapshot_data: Список новых данных снэпшотов.
        ad_id_map: Маппинг fb_ad_id → fb_ads.id.

    Returns:
        Количество записанных строк истории.
    """
    if not ad_id_map:
        logger.debug("MetricHistory: ad_id_map пуст — пропускаю запись истории")
        return 0

    # Загружаем текущие снэпшоты для сравнения
    fb_ad_ids = [item["fb_ad_id"] for item in snapshot_data]
    result = await session.execute(select(AdSnapshot).where(AdSnapshot.fb_ad_id.in_(fb_ad_ids)))
    current_snaps = {snap.fb_ad_id: snap for snap in result.scalars().all()}

    logger.debug(
        "MetricHistory: проверяю %d объявлений, в БД найдено %d текущих снэпшотов",
        len(snapshot_data),
        len(current_snaps),
    )

    now = datetime.now(UTC)
    history_rows = []
    skipped_regression = 0
    skipped_no_change = 0
    for item in snapshot_data:
        fb_ad_id = item["fb_ad_id"]
        ad_id = ad_id_map.get(fb_ad_id)
        if ad_id is None:
            continue
        old_snap = current_snaps.get(fb_ad_id)
        if regression_guard is not None:
            if regression_guard.should_block(fb_ad_id, old_snap, item):
                logger.warning(
                    "MetricHistory: пропускаю запись для %s — "
                    "накопительные метрики откатились назад (цикл %d из %d)",
                    fb_ad_id,
                    regression_guard._counters.get(fb_ad_id, 1),
                    3,
                )
                skipped_regression += 1
                continue
        elif _has_cumulative_metric_regression(old_snap, item):
            logger.warning(
                "MetricHistory: пропускаю запись для %s — накопительные метрики откатились назад",
                fb_ad_id,
            )
            skipped_regression += 1
            continue
        if not _metrics_changed(old_snap, item):
            skipped_no_change += 1
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

    logger.info(
        "MetricHistory: проверено %d, пропущено регрессия=%d, без изменений=%d, к записи=%d",
        len(snapshot_data),
        skipped_regression,
        skipped_no_change,
        len(history_rows),
    )

    if history_rows:
        stmt = pg_insert(AdMetricHistory).values(history_rows)
        # on_conflict_do_nothing защищает от IntegrityError при повторной вставке
        # с одинаковым (ad_id, cycle_ts) — например при быстром рестарте воркера
        stmt = stmt.on_conflict_do_nothing(constraint="uq_ad_metric_history_ad_ts")
        await session.execute(stmt)
    return len(history_rows)


def _prepare_snapshot_upsert_data(
    snapshot_data: list[dict],
    ad_id_map: dict[str, _uuid.UUID],
    *,
    current_scan_id: int | None,
) -> list[dict]:
    """Подготавливает данные снэпшотов для upsert, убирая лишние поля.

    Удаляет campaign_name, adset_name, ad_name, resolved_offer_code, offer_id —
    эти данные теперь живут в нормализованных таблицах fb_campaigns/fb_adsets/fb_ads.
    Дополнительно проставляет last_scan_id для каждого ряда — это ссылка на
    текущий монотонный scan_id наблюдателя.
    """
    # Поля, которых нет в ad_snapshots после нормализации
    _REMOVED_FIELDS = {
        "campaign_name",
        "adset_name",
        "ad_name",
        "resolved_offer_code",
        "offer_id",
    }
    cleaned: list[dict] = []
    for item in snapshot_data:
        ad_id = ad_id_map.get(item["fb_ad_id"])
        if ad_id is None:
            continue
        row = {k: v for k, v in item.items() if k not in _REMOVED_FIELDS}
        row["ad_id"] = ad_id
        row["last_scan_id"] = current_scan_id
        cleaned.append(row)
    return cleaned


async def _upsert_ad_snapshots(
    session: AsyncSession,
    snapshot_rows: list[dict],
    *,
    allow_metric_regression: bool = False,
    force_accept_fb_ad_ids: frozenset[str] = frozenset(),
) -> None:
    """Выполняет upsert ad_snapshots без нормализованных полей.

    force_accept_fb_ad_ids — набор fb_ad_id, для которых WHERE-условие регрессии
    не применяется (принудительно принятый новый базовый снимок через RegressionGuard).
    """
    if not snapshot_rows:
        return

    # Разбиваем на две группы: принудительно принятые и обычные
    if force_accept_fb_ad_ids and not allow_metric_regression:
        force_rows = [r for r in snapshot_rows if r["fb_ad_id"] in force_accept_fb_ad_ids]
        normal_rows = [r for r in snapshot_rows if r["fb_ad_id"] not in force_accept_fb_ad_ids]
        if force_rows:
            await _upsert_ad_snapshots(session, force_rows, allow_metric_regression=True)
        if normal_rows:
            await _upsert_ad_snapshots(session, normal_rows, allow_metric_regression=False)
        return

    stmt = pg_insert(AdSnapshot).values(snapshot_rows)

    update_cols = {
        "ad_id": stmt.excluded.ad_id,
        "delivery_status": stmt.excluded.delivery_status,
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
        "warning_rule_codes": stmt.excluded.warning_rule_codes,
        "stop_rule_codes": stmt.excluded.stop_rule_codes,
        "open_state_token": stmt.excluded.open_state_token,
        "telegram_group_key": stmt.excluded.telegram_group_key,
        "last_observed_at": stmt.excluded.last_observed_at,
        "last_scan_id": stmt.excluded.last_scan_id,
    }

    upsert_kwargs = {
        "index_elements": ["fb_ad_id"],
        "set_": update_cols,
    }
    if not allow_metric_regression:
        upsert_kwargs["where"] = and_(
            *(
                or_(
                    getattr(AdSnapshot, metric).is_(None),
                    getattr(stmt.excluded, metric).is_(None),
                    getattr(stmt.excluded, metric) >= getattr(AdSnapshot, metric),
                )
                for metric in _CUMULATIVE_METRICS
            )
        )

    upsert_stmt = stmt.on_conflict_do_update(**upsert_kwargs)
    await session.execute(upsert_stmt)


async def batch_save_snapshots(
    snapshot_data: list[dict],
    scan_guard: ZeroScanGuard,
    *,
    regression_guard: RegressionGuard | None = None,
    allow_cabinet_rollover: bool = True,
    bypass_scan_guard: bool = False,
    current_scan_id: int | None = None,
) -> bool:
    """Батчевый upsert снэпшотов через INSERT ... ON CONFLICT DO UPDATE.

    Принимает список словарей с данными для AdSnapshot.
    Одна сессия, один запрос для всех снэпшотов.
    bypass_scan_guard используется только для точечного сохранения одной или нескольких
    STOP-строк быстрого стопа, а не для полного среза сканирования.
    regression_guard — гард от ложных откатов накопительных метрик; при None используется
    старое поведение (любая регрессия блокирует).
    current_scan_id — текущий монотонный scan_id наблюдателя, проставляется в last_scan_id
    каждой записи ad_snapshots для последующей фильтрации stale-снимков.
    """
    if not snapshot_data:
        return False
    if not bypass_scan_guard and scan_guard.should_skip(snapshot_data) is not None:
        return False

    # Параметр allow_cabinet_rollover зарезервирован для обратной совместимости:
    # rollover суток теперь делается только вручную через
    # POST /api/observer/start-new-cabinet-day и больше не триггерится из observer'а.
    _ = allow_cabinet_rollover

    factory = get_session_factory()
    async with factory() as session:
        # 1. Upsert fb_campaigns — справочник кампаний с привязкой оффера
        campaign_id_map = await _upsert_fb_campaigns(session, snapshot_data)

        # 2. Upsert fb_adsets — справочник адсетов
        adset_id_map = await _upsert_fb_adsets(session, snapshot_data, campaign_id_map)

        # 3. Upsert fb_ads — справочник объявлений (adset_id FK)
        ad_id_map = await _upsert_fb_ads(session, snapshot_data, adset_id_map)

        # 4. Записываем дельты метрик в ad_metric_history (до перезаписи снэпшотов)
        history_count = await _save_metric_deltas(
            session, snapshot_data, ad_id_map, regression_guard
        )
        if history_count:
            logger.info("MetricHistory: записано %s строк в ad_metric_history", history_count)
        else:
            logger.info(
                "MetricHistory: 0 строк записано — метрики не изменились или все заблокированы"
            )

        # 5. Upsert ad_snapshots — только метрики и состояние алертов
        snapshot_rows = _prepare_snapshot_upsert_data(
            snapshot_data, ad_id_map, current_scan_id=current_scan_id
        )
        # После выноса cabinet_day rollover в отдельный endpoint observer всегда
        # работает только с инкрементальными метриками — regression guard поднимает
        # принудительно принятые fb_ad_id без автоматических исключений.
        force_ids: frozenset[str] = frozenset()
        if regression_guard:
            force_ids = frozenset(regression_guard.pop_force_accepted())
        await _upsert_ad_snapshots(
            session,
            snapshot_rows,
            allow_metric_regression=False,
            force_accept_fb_ad_ids=force_ids,
        )

        await session.commit()
        return True
