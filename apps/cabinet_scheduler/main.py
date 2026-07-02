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
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as redis_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from core.db import WORKER_ENGINE_KWARGS
from core.meta_api.bulk import MAX_BULK, resolve_owner_ad_ids_by_campaign_ids
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

# Потолок резолва объявлений автостарта (защита от резолва всего кабинета). Один
# bulk_status_change ограничен MAX_BULK (лимит Meta Batch API), поэтому всё, что
# выше, режем на чанки по MAX_BULK и создаём отдельную задачу на каждый чанк
# (M3: раньше включались только первые 50, остальные молча отбрасывались).
_AUTOSTART_MAX_ADS = MAX_BULK * 40

# Окно свежести объявления для автостарта (часы). fb_ads.is_active монотонно-истинный
# (выставляется в TRUE и нигде не сбрасывается), поэтому сам по себе НЕ отличает живые
# объявления от давно снятых. Автостарт поднимает только объявления, виденные сканом за
# последние FRESHNESS_HOURS — иначе каждое утро bulk-активировались бы ВСЕ когда-либо
# отсканированные ады (включая прошлые cabinet-дни) → нецелевой открут бюджета. 48ч
# щедро покрывают вчерашний cabinet-день даже при разовом пропуске скана.
AUTOSTART_FRESHNESS_HOURS = int(os.environ.get("CABINET_AUTOSTART_FRESHNESS_HOURS", "48"))


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

    # Фильтр свежести: включаем только объявления, виденные последним сканом кабинета
    # (last_seen_at >= now - FRESHNESS). Защита от реактивации давно снятых ads —
    # is_active=TRUE монотонно-истинный и мёртвые объявления не отсекает.
    since = now.astimezone(timezone.utc) - timedelta(hours=AUTOSTART_FRESHNESS_HOURS)
    ad_ids, total = await resolve_owner_ad_ids_by_campaign_ids(
        engine,
        owner_tag=owner_tag,
        campaign_ids=campaign_ids,
        since=since,
        limit=_AUTOSTART_MAX_ADS,
    )

    if ad_ids:
        # M3: режем на чанки по MAX_BULK (лимит Meta Batch API) — одна bulk-задача на
        # чанк с УНИКАЛЬНЫМ idempotency_key (...:{idx}), иначе при >50 объявлениях
        # включались бы только первые 50, а остальные молча оставались на паузе.
        sorted_ids = sorted(ad_ids)
        chunks = [sorted_ids[i : i + MAX_BULK] for i in range(0, len(sorted_ids), MAX_BULK)]
        truncated = total > len(ad_ids)
        task_ids: list[int] = []
        for idx, chunk in enumerate(chunks):
            payload = MetaMutationPayload(
                mutation_kind="bulk_status_change",
                target_id=f"autostart:{len(chunk)}:{idx}",
                params={
                    "ad_ids": chunk,
                    "action": _AUTOSTART_ACTION,
                    "resolved_from_campaigns": campaign_ids,
                },
                ad_account_id=None,
            )
            # idempotency_key с датой запуска И индексом чанка: повторный тик в тот же
            # день не задвоит ни один чанк.
            idem_key = f"autostart:{day}:{_AUTOSTART_ACTION}:{idx}"
            tid = await create_mutation_task(
                engine,
                payload=payload,
                requested_by="cabinet_autostart",
                status="pending",
                idempotency_key=idem_key,
            )
            if tid is not None:
                task_ids.append(tid)
        logger.info(
            "cabinet_autostart: создано enable-задач=%d (chunks), ad_ids=%d (total=%d, "
            "truncated=%s), day=%s",
            len(task_ids),
            len(ad_ids),
            total,
            truncated,
            day,
        )
        if truncated:
            logger.warning(
                "cabinet_autostart: total=%d превысил потолок %d — включены не все "
                "объявления, остаток требует ручной проверки (day=%s)",
                total,
                _AUTOSTART_MAX_ADS,
                day,
            )

        # Триггерим observer scan независимо от того, были ли ad_id — кабинет
        # мог измениться, скан подтянет актуальное состояние.
        await _trigger_observer_scan(redis_client)

        # M8: маркер «выполнено» — только при started-пути (задачи созданы + скан).
        # no_owner_ads не ставит маркер → следующий тик в окне повторит попытку
        # (catch-up до конца суток). Двойного включения нет: idempotency_key задач.
        await _set_autostart_done(redis_client, done_key)

        return {
            "outcome": "started",
            "day": day,
            "task_id": task_ids[0] if task_ids else None,  # backward-compat (первая задача)
            "task_ids": task_ids,
            "ad_count": len(ad_ids),
            "total": total,
            "chunks": len(chunks),
            "truncated": truncated,
            "scan_triggered": True,
        }
    else:
        logger.info(
            "cabinet_autostart: по кампаниям %s owner-объявлений не нашлось (owner_tag=%s), day=%s",
            campaign_ids,
            owner_tag,
            day,
        )
        # M5: скан при no_owner_ads — НЕ чаще раза в сутки (SET NX), иначе каждый тик
        # (60с) до конца суток форсил бы observer-scan (~960/день) и обнулял адаптивный
        # интервал (anti-detect риск). done-маркер НЕ ставим — резолв всё равно
        # ретраится в окне (catch-up enable, если объявления появятся позже).
        scan_triggered = await _trigger_scan_once_per_day(redis_client, now)
        return {
            "outcome": "no_owner_ads",
            "day": day,
            "total": total,
            "scan_triggered": scan_triggered,
        }


async def _set_autostart_done(redis_client: redis_asyncio.Redis, done_key: str) -> None:
    """Выставить дедуп-маркер автостарта (после успешного прогона). Best-effort."""
    try:
        await redis_client.set(done_key, "1", ex=AUTOSTART_DONE_TTL_SECONDS)
    except Exception:
        logger.exception("cabinet_autostart: не удалось выставить маркер %s", done_key)


async def _trigger_scan_once_per_day(redis_client: redis_asyncio.Redis, now: datetime) -> bool:
    """Триггернуть observer-scan не чаще раза в сутки (SET NX дневной ключ).

    Возвращает True, если скан триггернут сейчас, False — если уже был сегодня или
    Redis недоступен. Защита от спама форс-сканов в ветке no_owner_ads (M5).
    """
    day = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    key = f"cabinet:autostart:scan:{day}"
    try:
        was_set = await redis_client.set(key, "1", ex=AUTOSTART_DONE_TTL_SECONDS, nx=True)
    except Exception:
        logger.exception("cabinet_autostart: ошибка SET NX scan-маркера %s", key)
        return False
    if not was_set:
        return False
    await _trigger_observer_scan(redis_client)
    return True


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
        task_ids = summary.get("task_ids")
        if not task_ids and summary.get("task_id") is not None:
            task_ids = [summary["task_id"]]
        task_ids = task_ids or []
        lines = [
            f"🚀 <b>Автостарт кабинета {day}</b>",
            f"Поставлено объявлений: {summary.get('ad_count')} (задач: {len(task_ids)}).",
        ]
        if summary.get("truncated"):
            lines.append(
                f"⚠️ Найдено {summary.get('total')} — превышен потолок автостарта, "
                f"включены не все. Проверь остаток вручную."
            )
        text = "\n".join(lines)
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
