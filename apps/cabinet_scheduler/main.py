# -*- coding: utf-8 -*-
"""Cabinet scheduler — раз в минуту проверяет окно автостарта кабинета.

Money-критичный воркер. В плановое время (UTC, ежедневно) автоматически:
1. Включает (enable) объявления СВОИХ кампаний (owner-scoped) с нужной ДАТОЙ
   в названии кампании. Создаёт сразу pending-задачу bulk_status_change activate
   (без draft-подтверждения — это автостарт).
2. Триггерит observer scan (publish в Redis fb_agent:observer:trigger).

Контракт:
- Окно: HH:MM UTC из конфига и до конца суток UTC (catch-up при downtime воркера).
- Защита от повторов: Redis ``cabinet:autostart:YYYY-MM-DD`` TTL 26ч (SET NX) +
  idempotency_key задачи (включает дату запуска) — двойная защита от дубля enable.
- Owner-scoping ОБЯЗАТЕЛЕН (тег из observer_config). Без owner-тега фильтр
  выключен (включаются все кампании с датой) — это осознанный режим владельца.
- Пустой список дат → ничего не включаем (безопасность: НЕ включать весь кабинет).
- Heartbeat: ``worker:heartbeat:cabinet_scheduler`` TTL 60s.

Конфиг (system_config key='cabinet_autostart') меняется без рестарта — читается
на каждом тике.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from core.db import WORKER_ENGINE_KWARGS
from core.meta_api.bulk import resolve_owner_ad_ids_by_campaign_ids
from core.meta_api.queue import create_mutation_task
from core.meta_api.schemas import MetaMutationPayload
from core.observer.queries import load_observer_config, load_scanning_enabled
from core.scheduler.cabinet_autostart import (
    AUTOSTART_DONE_TTL_SECONDS,
    autostart_done_key,
    is_in_autostart_window,
    read_autostart_config,
)
from core.telegram.worker_notify import notify_owners

logger = logging.getLogger("cabinet_scheduler")

WORKER_NAME = "cabinet_scheduler"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60

# Главный цикл — раз в минуту (как digest_scheduler / health_watchdog).
CHECK_INTERVAL_SECONDS = int(os.environ.get("CABINET_CHECK_INTERVAL_SEC", "60"))

# Redis-канал триггера observer scan (тот же, что у /scan-now).
_OBSERVER_TRIGGER_CHANNEL = "fb_agent:observer:trigger"

# Действие mutation — включение (activate). Автостарт всегда включает.
_AUTOSTART_ACTION = "activate"


# ====================== one tick ======================


async def run_one_tick(
    *,
    engine: AsyncEngine,
    redis_client: redis_asyncio.Redis,
    now: datetime,
) -> dict[str, Any]:
    """Один проход автостарта. Возвращает summary dict с ключом 'outcome'.

    outcome ∈ {'scanning_paused', 'disabled', 'not_in_window', 'already_done',
    'no_campaigns', 'no_owner_ads', 'started'}.

    Шаги:
    0. Глобальный стоп: is_scanning_enabled=false → 'scanning_paused' (асимметричный
       стоп — на паузе НИЧЕГО не включаем). Проверяем ПЕРВЫМ и ДО дедуп-ключа: ключ
       не ставится, поэтому после снятия паузы в том же окне (catch-up до конца суток)
       автостарт доработает. Был на паузе всё окно → день пропущен, без сюрпризов.
    1. Читаем конфиг. Выключен → 'disabled'.
    2. Не в окне → 'not_in_window'.
    3. Redis SET NX дедуп-ключ. Уже стоит → 'already_done'.
    4. owner_tag из observer_config.
    5. Резолвим owner-scoped ad_id по датам.
    6. Если есть ad_id → создаём pending bulk_status_change activate (idempotent).
    7. Триггерим observer scan (publish).
    8. summary.
    """
    # Шаг 0 — money-критичный гейт: на паузе сканирования НЕ включаем объявления
    # и НЕ триггерим скан. Без этого автостарт жёг бы бюджет при «выключенном» боте.
    if not await load_scanning_enabled(engine):
        return {"outcome": "scanning_paused"}

    config = await read_autostart_config(engine)
    if not config.get("enabled"):
        return {"outcome": "disabled"}

    hour_utc = int(config.get("hour_utc", 6))
    minute_utc = int(config.get("minute_utc", 0))
    if not is_in_autostart_window(now, hour_utc, minute_utc):
        return {"outcome": "not_in_window"}

    # Дедуп. M8: маркер ставим ПОСЛЕ успешного действия (а не до), иначе транзиентная
    # ошибка resolve/create «съедала» весь день (ключ выставлен, повтора нет до завтра).
    # От гонки двух тиков деньги защищены idempotency_key самой задачи (один task на день).
    done_key = autostart_done_key(now)
    try:
        already_done = await redis_client.get(done_key)
    except Exception:
        # N6: НЕ трактуем ошибку Redis как 'already_done' — иначе money-критичный
        # автостарт МОЛЧА пропускается на весь день при недоступном Redis. Возвращаем
        # retryable-исход: done-маркер НЕ ставим → следующий тик повторит, и при
        # восстановлении Redis в окне (catch-up до конца суток) автостарт сработает.
        # Двойного запуска нет — idempotency_key bulk-задачи (один task на день).
        logger.exception(
            "cabinet_autostart: ошибка GET %s в Redis — повтор на следующем тике", done_key
        )
        return {"outcome": "redis_error"}
    if already_done:
        return {"outcome": "already_done"}

    day = now.astimezone(timezone.utc).strftime("%Y-%m-%d")

    # Источник кампаний автостарта = allowlist отслеживаемых (observer_config.campaign_ids).
    # Объединено: что отслеживаем, то и поднимаем по расписанию. Пустой allowlist →
    # автостарт НИЧЕГО не включает (для observer пусто = «сканировать всё», но включить
    # весь кабинет нельзя — money-дыра).
    observer_config = await load_observer_config(engine)
    owner_tag = (observer_config or {}).get("owner_campaign_tag")
    campaign_ids = list((observer_config or {}).get("campaign_ids") or [])
    if not campaign_ids:
        await _set_autostart_done(redis_client, done_key)
        logger.info(
            "cabinet_autostart: фича включена, но отслеживаемых кампаний нет (allowlist пуст) — "
            "пропускаю день %s",
            day,
        )
        return {"outcome": "no_campaigns", "day": day}

    ad_ids, total = await resolve_owner_ad_ids_by_campaign_ids(
        engine, owner_tag=owner_tag, campaign_ids=campaign_ids
    )

    task_id: int | None = None
    if ad_ids:
        payload = MetaMutationPayload(
            mutation_kind="bulk_status_change",
            target_id=f"autostart:{len(ad_ids)}",
            params={
                "ad_ids": sorted(ad_ids),
                "action": _AUTOSTART_ACTION,
                "resolved_from_campaigns": campaign_ids,
            },
            ad_account_id=None,
        )
        # idempotency_key с датой запуска: повторный тик в тот же день не задвоит.
        idem_key = f"autostart:{day}:{_AUTOSTART_ACTION}"
        task_id = await create_mutation_task(
            engine,
            payload=payload,
            requested_by="cabinet_autostart",
            status="pending",
            idempotency_key=idem_key,
        )
        logger.info(
            "cabinet_autostart: создана enable-задача task_id=%s, ad_ids=%d (total=%d), day=%s",
            task_id,
            len(ad_ids),
            total,
            day,
        )

        # Триггерим observer scan независимо от того, были ли ad_id — кабинет
        # мог измениться, скан подтянет актуальное состояние.
        await _trigger_observer_scan(redis_client)

        # M8: маркер «выполнено» — только при started-пути (задача создана + скан).
        # no_owner_ads не ставит маркер → следующий тик в окне повторит попытку
        # (catch-up до конца суток). Двойного включения нет: idempotency_key задачи.
        await _set_autostart_done(redis_client, done_key)

        return {
            "outcome": "started",
            "day": day,
            "task_id": task_id,
            "ad_count": len(ad_ids),
            "total": total,
            "scan_triggered": True,
        }
    else:
        logger.info(
            "cabinet_autostart: по кампаниям %s owner-объявлений не нашлось (owner_tag=%s), day=%s",
            campaign_ids,
            owner_tag,
            day,
        )
        # Триггерим скан даже при no_owner_ads — кабинет мог измениться.
        await _trigger_observer_scan(redis_client)
        # done-маркер НЕ ставим: позволяем ретрай в окне (catch-up).
        return {"outcome": "no_owner_ads", "day": day, "total": total}


async def _set_autostart_done(redis_client: redis_asyncio.Redis, done_key: str) -> None:
    """Выставить дедуп-маркер автостарта (после успешного прогона). Best-effort."""
    try:
        await redis_client.set(done_key, "1", ex=AUTOSTART_DONE_TTL_SECONDS)
    except Exception:
        logger.exception("cabinet_autostart: не удалось выставить маркер %s", done_key)


async def _trigger_observer_scan(redis_client: redis_asyncio.Redis) -> None:
    """Publish сигнал немедленного scan в observer (как POST /scan-now)."""
    payload = json.dumps(
        {"requested_by": WORKER_NAME, "ts": datetime.now(timezone.utc).isoformat()}
    )
    try:
        await redis_client.publish(_OBSERVER_TRIGGER_CHANNEL, payload)
    except Exception:
        logger.exception("cabinet_autostart: не смог опубликовать observer-trigger в Redis")


async def _alert_autostart(engine: Any, redis_client: Any, summary: dict) -> None:
    """Подтверждение/алерт автостарта кабинета. Best-effort, дедуп по дню."""
    outcome = summary.get("outcome")
    day = summary.get("day", "")
    if outcome == "started":
        text = (
            f"🚀 <b>Автостарт кабинета {day}</b>\n"
            f"Поставлено объявлений: {summary.get('ad_count')} "
            f"(task_id={summary.get('task_id')})."
        )
    elif outcome == "no_owner_ads":
        text = (
            f"⚠️ <b>Автостарт {day}: owner-объявлений не найдено</b>\n"
            f"Кабинет НЕ поднят. Проверь даты в названиях кампаний."
        )
    else:
        return
    await notify_owners(
        engine,
        redis_client,
        category="autostart",
        text=text,
        dedup_key=f"autostart_alert:{day}:{outcome}",
        dedup_ttl_seconds=93600,
    )


# ====================== loops ======================


async def heartbeat_loop(redis_client: redis_asyncio.Redis, stop: asyncio.Event) -> None:
    """Раз в HEARTBEAT_TTL/2 пишет heartbeat в Redis."""
    interval = HEARTBEAT_TTL_SECONDS / 2
    while not stop.is_set():
        try:
            await redis_client.set(HEARTBEAT_KEY, "alive", ex=HEARTBEAT_TTL_SECONDS)
        except Exception:
            logger.exception("heartbeat: ошибка записи в Redis")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def tick_loop(
    *,
    engine: AsyncEngine,
    redis_client: redis_asyncio.Redis,
    stop: asyncio.Event,
) -> None:
    """Основной цикл — раз в минуту прогоняет run_one_tick."""
    while not stop.is_set():
        try:
            now = datetime.now(timezone.utc)
            summary = await run_one_tick(engine=engine, redis_client=redis_client, now=now)
            outcome = summary.get("outcome")
            if outcome not in ("scanning_paused", "disabled", "not_in_window", "already_done"):
                logger.info("cabinet_autostart tick: %s", summary)
            await _alert_autostart(engine, redis_client, summary)
        except Exception:
            logger.exception("Ошибка в cabinet_autostart tick")
        try:
            await asyncio.wait_for(stop.wait(), timeout=CHECK_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


# ====================== entrypoint ======================


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


def _get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6380/0")


async def main_loop(database_url: str | None = None) -> None:
    db_url = database_url or _get_database_url()
    engine = create_async_engine(db_url, **WORKER_ENGINE_KWARGS)
    redis_client = redis_asyncio.from_url(_get_redis_url(), decode_responses=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, sig_name), stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    logger.info("cabinet_scheduler запущен (tick=%ss)", CHECK_INTERVAL_SECONDS)
    try:
        await asyncio.gather(
            heartbeat_loop(redis_client, stop),
            tick_loop(engine=engine, redis_client=redis_client, stop=stop),
        )
    finally:
        try:
            await redis_client.aclose()
        except Exception:
            logger.exception("Ошибка закрытия Redis")
        await engine.dispose()
        logger.info("cabinet_scheduler остановлен")


__all__ = [
    "HEARTBEAT_KEY",
    "WORKER_NAME",
    "_alert_autostart",
    "heartbeat_loop",
    "main_loop",
    "run_one_tick",
    "tick_loop",
]
