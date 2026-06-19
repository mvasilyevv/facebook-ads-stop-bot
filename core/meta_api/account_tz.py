# -*- coding: utf-8 -*-
"""Таймзона рекламного аккаунта (timezone_offset_hours_utc) — для границы суток кабинета.

Оффсет статичен → кэшируем в Redis (`account_tz:{id}`, TTL сутки, переживает рестарты).
Фетч — через существующий `MetaApiClient.execute_graph_call` (как `get_account_health`).
Warmup крутится в meta_api_worker, НЕ в observer — money-путь авто-стопа не трогаем (Волна 2/E).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_REDIS_PREFIX = "account_tz:"
_TTL_SECONDS = 86400  # сутки: оффсет статичен
_REFRESH_THROTTLE_KEY = "account_tz:last_refresh"
DEFAULT_OFFSET_HOURS = 0.0  # фолбэк = UTC, если оффсет неизвестен


def _key(account_id: str) -> str:
    return f"{_REDIS_PREFIX}{account_id}"


async def fetch_offset_hours(client: Any, account_id: str) -> float | None:
    """GET /act_{id}?fields=timezone_offset_hours_utc через Vision-сессию. None при ошибке/отсутствии."""
    acct = (account_id or "").removeprefix("act_")
    if not acct:
        return None
    try:
        resp = await client.execute_graph_call(
            method="GET",
            endpoint=f"/act_{acct}",
            query_params={"fields": "timezone_offset_hours_utc,timezone_name"},
            ad_account_id=acct,
        )
    except Exception as exc:  # noqa: BLE001 — фетч best-effort, не должен ронять воркер
        logger.warning("account_tz: фетч оффсета act_%s упал: %s", acct, exc)
        return None
    raw = resp.get("timezone_offset_hours_utc")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


async def cache_offset(redis: Any, account_id: str, offset_hours: float) -> None:
    try:
        await redis.set(_key(account_id), str(offset_hours), ex=_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("account_tz: запись в Redis act_%s упала: %s", account_id, exc)


async def load_offset(
    redis: Any, account_id: str, *, default: float = DEFAULT_OFFSET_HOURS
) -> float:
    """Оффсет из Redis-кэша. default (UTC), если ключа нет/Redis недоступен."""
    if not account_id:
        return default
    try:
        raw = await redis.get(_key(account_id))
    except Exception:  # noqa: BLE001
        return default
    if raw is None:
        return default
    try:
        return float(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except (TypeError, ValueError):
        return default


async def load_offset_map(
    redis: Any, account_ids: list[str], *, default: float = DEFAULT_OFFSET_HOURS
) -> dict[str, float]:
    """Карта ad_account_id → offset_hours из кэша (per-account, мульти-кабинет)."""
    out: dict[str, float] = {}
    for aid in account_ids:
        out[aid] = await load_offset(redis, aid, default=default)
    return out


async def active_account_ids(engine: AsyncEngine) -> list[str]:
    """DISTINCT активные кабинеты из каталога кампаний."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT DISTINCT ad_account_id FROM fb_campaigns "
                    "WHERE ad_account_id IS NOT NULL AND is_active = true"
                )
            )
        ).fetchall()
    return [r[0] for r in rows if r[0]]


async def refresh_account_tz_cache(engine: AsyncEngine, redis: Any, client: Any) -> int:
    """Фетчит и кэширует оффсеты всех активных кабинетов. Возвращает число обновлённых.

    Идемпотентно, ошибки глотает (best-effort). Вызывается из meta_api_worker.
    """
    try:
        account_ids = await active_account_ids(engine)
    except Exception as exc:  # noqa: BLE001
        logger.warning("account_tz: список кабинетов не получен: %s", exc)
        return 0
    updated = 0
    for aid in account_ids:
        offset = await fetch_offset_hours(client, aid)
        if offset is not None:
            await cache_offset(redis, aid, offset)
            updated += 1
    if updated:
        logger.info("account_tz: обновлено оффсетов кабинетов: %d", updated)
    return updated


async def maybe_refresh_account_tz(
    engine: AsyncEngine,
    redis: Any,
    client: Any,
    *,
    min_interval_seconds: int = 21600,
) -> bool:
    """Троттлинг-обёртка: реально фетчит не чаще раза в min_interval_seconds (Redis SET NX).

    Возвращает True если в этот раз обновляли. Безопасно звать на каждой итерации воркера.
    """
    try:
        acquired = await redis.set(_REFRESH_THROTTLE_KEY, "1", ex=min_interval_seconds, nx=True)
    except Exception:  # noqa: BLE001 — Redis недоступен → пропускаем тихо
        return False
    if not acquired:
        return False
    await refresh_account_tz_cache(engine, redis, client)
    return True
