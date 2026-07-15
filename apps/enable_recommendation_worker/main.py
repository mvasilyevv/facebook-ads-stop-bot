# -*- coding: utf-8 -*-
"""Enable Recommendation Worker — основной цикл.

Раз в ENABLE_RECO_INTERVAL_SEC (дефолт 300):
1. SELECT кандидатов: ad_alert_state.state IN ('stop_sent','disabled')
   AND last_transition_at < NOW() - INTERVAL '1 hour'
   AND ad_id NOT IN (SELECT ad_id FROM ad_auto_enable_disabled).
2. Для каждого читает метрики из ad_metrics где cycle_ts > last_transition_at.
3. Прогоняет через analyzer.should_recommend(...).
4. Дедуп через Redis (`enable_reco:last:{ad_id}` TTL 6h, NX).
5. INSERT enable_recommendations + SEND TG-алерт с inline-кнопкой.
6. Heartbeat `worker:heartbeat:enable_reco` TTL 60s.

Graceful shutdown по SIGTERM/SIGINT.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from core.db import WORKER_ENGINE_KWARGS
from core.enable_reco.alert import EnableRecoRenderInput, render_enable_reco_alert
from core.enable_reco.analyzer import (
    AnalyzerThresholds,
    MetricSnapshot,
    OfferThresholds,
    RecommendationDecision,
    should_recommend,
)
from core.observer.queries import load_scanning_enabled
from core.telegram.service import load_active_recipients, load_telegram_config
from core.telegram.web_app_url import load_web_app_url, normalize_web_app_base

logger = logging.getLogger(__name__)

WORKER_NAME = "enable_reco"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60

INTERVAL_SECONDS = int(os.environ.get("ENABLE_RECO_INTERVAL_SEC", "300"))
# Минимальный возраст «отключённости» до выдачи рекомендации (защита от мгновенного включения)
COOLDOWN_SECONDS = int(os.environ.get("ENABLE_RECO_COOLDOWN_SEC", "3600"))
# Дедуп между рекомендациями для одного ad (6 часов по умолчанию)
DEDUP_TTL_SECONDS = int(os.environ.get("ENABLE_RECO_DEDUP_TTL_SEC", str(6 * 3600)))
# Лимит кандидатов за один цикл — защита от лавинной нагрузки
MAX_CANDIDATES_PER_CYCLE = int(os.environ.get("ENABLE_RECO_MAX_PER_CYCLE", "50"))
# Метрики после last_transition_at — окно по умолчанию шире cooldown'а
METRICS_LOOKBACK_SECONDS = int(os.environ.get("ENABLE_RECO_METRICS_LOOKBACK_SEC", str(3 * 3600)))
# Кейс куратора: «показов мало + CTR хороший» → рекомендация hold_until_cpl.
CURATOR_IMPR_CEILING = int(os.environ.get("ENABLE_RECO_CURATOR_IMPR_CEILING", "500"))
CURATOR_CTR_FLOOR = os.environ.get("ENABLE_RECO_CURATOR_CTR_FLOOR", "3.0")
# Денежный фолбэк-кап grace для офферов без cpa_threshold (ревью M-1).
CURATOR_FALLBACK_SPEND_CAP = os.environ.get("ENABLE_RECO_CURATOR_FALLBACK_CAP", "10.00")

DEDUP_KEY_PREFIX = "enable_reco:last:"


# ====================== Контракт строки кандидата ======================


@dataclass(frozen=True)
class CandidateRow:
    """Кандидат на рекомендацию: всё что нужно знать для одного решения."""

    ad_id: uuid.UUID
    fb_ad_id: str
    ad_name: str
    campaign_name: str
    adset_name: str
    alert_state: str
    last_transition_at: datetime
    snoozed_until: datetime | None
    offer_code: str | None
    cpa_threshold: Decimal | None
    open_state_token: uuid.UUID | None = None
    delivery_status: str | None = None
    has_unfinished_pause: bool = False


# ====================== SQL: кандидаты и метрики ======================


_CANDIDATES_SQL = text(
    """
    SELECT
        a.id, a.fb_ad_id, a.ad_name,
        c.campaign_name,
        s.adset_name,
        st.alert_state,
        st.last_transition_at,
        st.snoozed_until,
        o.code AS offer_code,
        r.cpa_threshold,
        st.open_state_token,
        a.delivery_status,
        EXISTS (
            SELECT 1
            FROM task_queue tq
            WHERE tq.task_type = 'meta_api_mutation'
              AND tq.payload->>'mutation_kind' = 'pause_ad'
              AND tq.payload->>'target_id' = a.fb_ad_id
              AND tq.status IN ('draft', 'pending', 'running', 'retrying')
        ) AS has_unfinished_pause
    FROM ad_alert_state st
    JOIN fb_ads a ON a.id = st.ad_id
    JOIN fb_adsets s ON s.id = a.adset_id
    JOIN fb_campaigns c ON c.id = s.campaign_id
    LEFT JOIN offers o ON o.id = c.offer_id
    LEFT JOIN offer_rules r ON r.offer_id = o.id
    WHERE st.alert_state IN ('stop_sent', 'disabled')
      AND st.last_transition_at < NOW() - make_interval(secs => :cool)
      AND NOT EXISTS (
          SELECT 1 FROM ad_auto_enable_disabled d WHERE d.ad_id = st.ad_id
      )
    ORDER BY st.last_transition_at ASC
    LIMIT :lim
    """
)


_METRICS_SQL = text(
    """
    SELECT cycle_ts, spend, cost_per_lead, cost_per_registration, registrations, deposits,
           impressions, ctr
    FROM ad_metrics
    WHERE ad_id = :aid
      AND cycle_ts > :since
    ORDER BY cycle_ts ASC
    """
)


async def fetch_candidates(engine: AsyncEngine, *, limit: int) -> list[CandidateRow]:
    """Загружает кандидатов на рекомендацию."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                _CANDIDATES_SQL,
                {"cool": int(COOLDOWN_SECONDS), "lim": int(limit)},
            )
        ).all()
    return [
        CandidateRow(
            ad_id=r[0],
            fb_ad_id=str(r[1]),
            ad_name=str(r[2] or ""),
            campaign_name=str(r[3] or ""),
            adset_name=str(r[4] or ""),
            alert_state=str(r[5]),
            last_transition_at=r[6],
            snoozed_until=r[7],
            offer_code=str(r[8]) if r[8] else None,
            cpa_threshold=r[9],
            open_state_token=r[10],
            delivery_status=str(r[11]) if r[11] else None,
            has_unfinished_pause=bool(r[12]),
        )
        for r in rows
    ]


async def fetch_metrics_since(
    engine: AsyncEngine,
    *,
    ad_id: uuid.UUID,
    since: datetime,
) -> list[MetricSnapshot]:
    """Метрики после момента отключения."""
    async with engine.connect() as conn:
        rows = (await conn.execute(_METRICS_SQL, {"aid": ad_id, "since": since})).all()
    return [
        MetricSnapshot(
            cycle_ts=r[0],
            spend=r[1],
            cost_per_lead=r[2],
            cost_per_registration=r[3],
            registrations=int(r[4]) if r[4] is not None else None,
            deposits=int(r[5]) if r[5] is not None else None,
            impressions=int(r[6]) if r[6] is not None else None,
            ctr=r[7],
        )
        for r in rows
    ]


# ====================== Redis: дедуп и heartbeat ======================


async def is_recently_recommended(redis_client, ad_id: uuid.UUID) -> bool:
    """Проверка дедупа: уже рекомендовали в течение DEDUP_TTL_SECONDS?"""
    if redis_client is None:
        return False
    key = f"{DEDUP_KEY_PREFIX}{ad_id}"
    try:
        existing = await redis_client.get(key)
        return existing is not None
    except Exception:  # noqa: BLE001
        logger.exception("redis GET %s упал", key)
        return False


async def mark_recommended(redis_client, ad_id: uuid.UUID) -> bool:
    """Ставит дедуп-ключ. Возвращает True если новая запись (SET NX), False если уже стоял."""
    if redis_client is None:
        return True
    key = f"{DEDUP_KEY_PREFIX}{ad_id}"
    try:
        ok = await redis_client.set(key, "1", ex=DEDUP_TTL_SECONDS, nx=True)
        return bool(ok)
    except Exception:  # noqa: BLE001
        logger.exception("redis SET NX %s упал", key)
        return False


async def heartbeat_loop(redis_client, stop: asyncio.Event) -> None:
    """Фоновый heartbeat: пишет worker:heartbeat:enable_reco каждые TTL/2.

    Отдельный таск, НЕ завязан на основной цикл (раз в INTERVAL_SECONDS=300с): при TTL 60с
    ключ протухал между прогонами, и health_watchdog слал ложные «enable_reco не дышит».
    """
    if redis_client is None:
        return
    interval = HEARTBEAT_TTL_SECONDS / 2
    while not stop.is_set():
        try:
            await redis_client.set(HEARTBEAT_KEY, "alive", ex=HEARTBEAT_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.exception("enable_reco heartbeat: ошибка записи в Redis")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


# ====================== Persist ======================


async def insert_recommendation(
    engine: AsyncEngine,
    *,
    ad_id: uuid.UUID,
    level: str,
    snapshot: dict[str, Any],
    live_batch_started_at: datetime,
    idempotency_key: str,
) -> uuid.UUID | None:
    """INSERT в enable_recommendations. Идемпотентен по idempotency_key."""
    rec_id = uuid.uuid4()
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO enable_recommendations
                        (id, ad_id, snapshot_metrics, recommendation_level,
                         live_batch_started_at, idempotency_key)
                    VALUES
                        (:rid, :aid, CAST(:snap AS JSONB), :lvl, :batch, :ik)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "rid": rec_id,
                    "aid": ad_id,
                    "snap": json.dumps(snapshot or {}),
                    "lvl": level,
                    "batch": live_batch_started_at,
                    "ik": idempotency_key,
                },
            )
        ).first()
    return row[0] if row else None


async def delete_unpromoted_recommendation(engine: AsyncEngine, *, rec_id: uuid.UUID) -> None:
    """Откатывает INSERT из insert_recommendation при недоставленном алерте (re-arm).

    LOW (аудит 02.07): idempotency_key = f(ad_id, last_transition_at) не меняется, пока
    ад остаётся в том же инциденте — без отката запись в БД навсегда блокирует повтор
    (ON CONFLICT DO NOTHING на следующих циклах), и рекомендация молча теряется при
    любом сбое TG. Паттерн — как re-arm дедупа в observer._maybe_alert_degraded (246000c7):
    неудачная попытка не должна оставлять постоянный след, мешающий ретраю.
    Удаляем только НЕ подтверждённую (promoted_to_task_id IS NULL) запись — если между
    insert и этим вызовом её кто-то успел confirm'ить (гонка с UI), запись не трогаем.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                DELETE FROM enable_recommendations
                WHERE id = :rid AND promoted_to_task_id IS NULL
                """
            ),
            {"rid": rec_id},
        )


# ====================== TG отправка ======================


async def send_alert(
    tg_client,
    *,
    candidate: CandidateRow,
    decision: RecommendationDecision,
    recommendation_id: uuid.UUID,
    engine: Any,
) -> bool:
    """Шлёт TG-алерт с inline-кнопкой «Включить» всем активным recipients.

    Возвращает True при успешной доставке ≥1 получателю, False при сбое или отсутствии TG.
    mark_recommended должен вызываться ТОЛЬКО при True — иначе рекомендация теряется навсегда.
    """
    web_app_base = normalize_web_app_base(await load_web_app_url(engine))
    text_body, reply_markup = render_enable_reco_alert(
        EnableRecoRenderInput(
            recommendation_id=str(recommendation_id),
            fb_ad_id=candidate.fb_ad_id,
            ad_name=candidate.ad_name,
            campaign_name=candidate.campaign_name,
            adset_name=candidate.adset_name,
            offer_code=candidate.offer_code,
            decision=decision,
            web_app_base=web_app_base,
        )
    )

    if tg_client is None:
        logger.warning(
            "TG не настроен — рекомендация для fb_ad_id=%s только в лог", candidate.fb_ad_id
        )
        return False
    try:
        recipients = await load_active_recipients(engine)
    except Exception:  # noqa: BLE001
        logger.exception("send_alert: не удалось загрузить recipients")
        return False
    if not recipients:
        logger.warning(
            "send_alert: нет активных recipients — рекомендация для %s только в лог",
            candidate.fb_ad_id,
        )
        return False
    delivered = False
    for r in recipients:
        try:
            await tg_client.send_message(
                chat_id=str(r.chat_id),
                text=text_body,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
            delivered = True
        except Exception:  # noqa: BLE001
            logger.exception(
                "send_alert: не доставлено chat_id=%s (fb_ad_id=%s)",
                r.chat_id,
                candidate.fb_ad_id,
            )
    return delivered


# ====================== Один цикл ======================


async def run_once(
    engine: AsyncEngine,
    *,
    redis_client,
    tg_client,
    thresholds: AnalyzerThresholds | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Один прогон. Возвращает counters.

    Параметр `now` нужен только для unit-тестируемости (timer freezing).
    """
    now = now or datetime.now(timezone.utc)
    thresholds = thresholds or AnalyzerThresholds(
        curator_impr_ceiling=CURATOR_IMPR_CEILING,
        curator_ctr_floor=Decimal(CURATOR_CTR_FLOOR),
        curator_fallback_spend_cap=Decimal(CURATOR_FALLBACK_SPEND_CAP),
    )

    # Асимметричный стоп: на паузе сканирования рекомендации включения бессмысленны
    # (детекта нет, включать незащищённое объявление не нужно). Пропускаем цикл.
    if not await load_scanning_enabled(engine):
        return {
            "candidates": 0,
            "skipped_dedup": 0,
            "skipped_decision": 0,
            "recommendations": 0,
            "alerts_sent": 0,
            "skipped_paused": 1,
        }

    candidates = await fetch_candidates(engine, limit=MAX_CANDIDATES_PER_CYCLE)
    counts = {
        "candidates": len(candidates),
        "skipped_dedup": 0,
        "skipped_decision": 0,
        "recommendations": 0,
        "alerts_sent": 0,
    }
    if not candidates:
        return counts

    batch_started_at = now

    for cand in candidates:
        if await is_recently_recommended(redis_client, cand.ad_id):
            counts["skipped_dedup"] += 1
            continue

        since = max(
            cand.last_transition_at,
            now.replace(microsecond=0) - _timedelta_safe(METRICS_LOOKBACK_SECONDS),
        )
        metrics = await fetch_metrics_since(engine, ad_id=cand.ad_id, since=since)

        # Curator hold разрешён только для уже подтверждённо выключенного ad.
        # Незавершённая pause_ad может выиграть гонку у activate_ad и выключить ad позже.
        curator_allowed = (
            cand.alert_state == "disabled"
            and (cand.delivery_status or "").strip().upper() == "OFF"
            and not cand.has_unfinished_pause
        )
        decision = should_recommend(
            alert_state=cand.alert_state,
            snoozed_until=cand.snoozed_until,
            now=now,
            metrics=metrics,
            offer=OfferThresholds(cpa_threshold=cand.cpa_threshold),
            thresholds=thresholds,
            allow_curator=curator_allowed,
        )
        if not decision.recommend:
            counts["skipped_decision"] += 1
            logger.debug(
                "skip ad_id=%s fb_ad_id=%s: %s",
                cand.ad_id,
                cand.fb_ad_id,
                decision.skip_reason,
            )
            continue

        idem_key = f"enable_reco:{cand.ad_id}:{int(cand.last_transition_at.timestamp())}"
        if cand.open_state_token is None:
            # Без incident token рекомендацию нельзя безопасно подтвердить:
            # старая кнопка могла бы включить ad уже в новом STOP-инциденте.
            counts["skipped_decision"] += 1
            logger.warning(
                "skip ad_id=%s fb_ad_id=%s: active incident has no open_state_token",
                cand.ad_id,
                cand.fb_ad_id,
            )
            continue

        recommendation_snapshot = {
            **decision.snapshot,
            "incident_open_state_token": str(cand.open_state_token),
            "incident_last_transition_at": cand.last_transition_at.isoformat(),
        }
        new_id = await insert_recommendation(
            engine,
            ad_id=cand.ad_id,
            level=decision.level or "warning",
            snapshot=recommendation_snapshot,
            live_batch_started_at=batch_started_at,
            idempotency_key=idem_key,
        )

        # Если запись не создалась (idempotency_key уже есть в БД) — алерт не шлём
        if new_id is None:
            counts["skipped_decision"] += 1
            continue

        counts["recommendations"] += 1

        # Сначала шлём алерт — mark_recommended только при успехе.
        # Порядок критичен: если поставить дедуп до отправки, сбой TG потеряет
        # рекомендацию навсегда (idempotency_key в БД + Redis NX уже стоят).
        sent = await send_alert(
            tg_client,
            candidate=cand,
            decision=decision,
            recommendation_id=new_id,
            engine=engine,
        )
        if not sent:
            counts["send_failed"] = counts.get("send_failed", 0) + 1
            # Re-arm (LOW, аудит 02.07): откатываем INSERT, иначе idempotency_key
            # блокирует повтор навсегда — следующий цикл, пока ад в том же инциденте,
            # снова получит new_id=None и рекомендация теряется молча. Redis-дедуп уже
            # не ставится при недоставке (см. комментарий выше) — здесь закрываем
            # аналогичную дыру на стороне БД.
            await delete_unpromoted_recommendation(engine, rec_id=new_id)
            continue

        # Дедуп по Redis (NX) — ставим только после успешной отправки
        if not await mark_recommended(redis_client, cand.ad_id):
            counts["skipped_dedup"] += 1
            continue

        counts["alerts_sent"] += 1

    return counts


def _timedelta_safe(seconds: int):
    """Локальная обёртка чтобы не плодить import — timedelta из stdlib."""
    from datetime import timedelta

    return timedelta(seconds=max(0, int(seconds)))


# ====================== Main loop ======================


async def main_loop(
    *,
    engine_factory: Callable[[], Awaitable[AsyncEngine]] | None = None,
    redis_factory: Callable[[], Awaitable[object]] | None = None,
    tg_factory: Callable[[AsyncEngine], Awaitable[tuple[object, str | None, int | None]]]
    | None = None,
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    """Бесконечный цикл.

    Все factories допускают подмену в тестах. По умолчанию используются прод-реализации.
    """
    engine_factory = engine_factory or _default_engine_factory
    redis_factory = redis_factory or _default_redis_factory
    tg_factory = tg_factory or _default_tg_factory

    engine = await engine_factory()
    redis_client = await redis_factory()
    tg_client, _chat_id, _thread_id = await tg_factory(engine)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    # Фоновый heartbeat — независим от основного цикла (раз в 300с), чтобы ключ
    # worker:heartbeat:enable_reco (TTL 60с) не протухал между прогонами.
    hb_task = asyncio.create_task(heartbeat_loop(redis_client, stop_event))

    logger.info(
        "enable_recommendation_worker запущен (interval=%ss, cooldown=%ss)",
        INTERVAL_SECONDS,
        COOLDOWN_SECONDS,
    )

    try:
        while should_continue() and not stop_event.is_set():
            try:
                summary = await run_once(
                    engine,
                    redis_client=redis_client,
                    tg_client=tg_client,
                )
                if any(v > 0 for v in summary.values()):
                    logger.info("enable_reco counts: %s", summary)
            except Exception:  # noqa: BLE001
                logger.exception("run_once упал — продолжаю цикл")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=INTERVAL_SECONDS)
                break
            except asyncio.TimeoutError:
                pass
    finally:
        logger.info("enable_recommendation_worker остановлен")
        hb_task.cancel()
        try:
            await hb_task
        except asyncio.CancelledError:
            pass
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception:  # noqa: BLE001
                pass
        if tg_client is not None:
            try:
                close = getattr(tg_client, "close", None)
                if close is not None:
                    await close()
            except Exception:  # noqa: BLE001
                pass
        await engine.dispose()


# ====================== Default factories ======================


async def _default_engine_factory() -> AsyncEngine:
    from core.config import get_settings

    return create_async_engine(get_settings().database_url, **WORKER_ENGINE_KWARGS)


async def _default_redis_factory():
    try:
        import redis.asyncio as redis_asyncio  # type: ignore
    except ImportError:
        logger.warning("redis package не установлен — дедуп и heartbeat отключены")
        return None

    url = os.environ.get("REDIS_URL", "redis://localhost:6380/0")
    return redis_asyncio.from_url(url, decode_responses=True)


async def _default_tg_factory(engine: AsyncEngine):
    """Возвращает (client, None, None).

    thread_id убран: алерты идут через send_alert → load_active_recipients (DM каждому).
    chat_id не используется в прод-пути (engine передаётся напрямую в run_once).
    """
    from core.telegram.client import TelegramBotClient

    try:
        cfg = await load_telegram_config(engine)
    except Exception:  # noqa: BLE001
        logger.exception("не удалось прочитать telegram_config")
        return None, None, None

    if cfg is None or not cfg.bot_token:
        logger.warning("telegram_config пуст — алерты только в лог")
        return None, None, None

    client = TelegramBotClient(cfg.bot_token)
    # thread_id убран: рассылка через load_active_recipients в send_alert (нет forum-топика)
    return client, None, None


__all__ = [
    "CandidateRow",
    "delete_unpromoted_recommendation",
    "fetch_candidates",
    "fetch_metrics_since",
    "insert_recommendation",
    "is_recently_recommended",
    "main_loop",
    "mark_recommended",
    "run_once",
    "send_alert",
]
