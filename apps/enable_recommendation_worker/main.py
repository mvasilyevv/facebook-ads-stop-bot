# -*- coding: utf-8 -*-
"""Enable Recommendation Worker — основной цикл.

Раз в ENABLE_RECO_INTERVAL_SEC (дефолт 300):
1. SELECT кандидатов: ad_alert_state.state IN ('stop_sent','disabled')
   AND last_transition_at < NOW() - INTERVAL '1 hour'. Per-ad opt-out не скрывает
   ручную рекомендацию и проверяется только перед автоматическим исполнением.
2. Для каждого читает метрики из ad_metrics где cycle_ts > last_transition_at.
3. Прогоняет через analyzer.should_recommend(...).
4. INSERT enable_recommendations с PostgreSQL idempotency key; OK при включённом master-toggle безопасно
   переводится в enable-задачу, WARNING остаётся ручным решением.
5. Пишет deterministic event в PostgreSQL notification outbox; доставляет gateway worker.
6. Process liveness is exported through the worker Prometheus endpoint.

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
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from core.db import WORKER_ENGINE_KWARGS
from core.enable_reco.analyzer import (
    AnalyzerThresholds,
    MetricSnapshot,
    OfferThresholds,
    RecommendationDecision,
    should_recommend,
)
from core.enable_reco.confirmation import (
    RecommendationAlreadyPromotedError,
    RecommendationNotFoundError,
    RecommendationUnsafeStateError,
    promote_enable_recommendation,
)
from core.meta_api.account_tz import canonical_account_id, resolve_account_currencies
from core.money import UnsupportedCurrencyExponentError, currency_exponent
from core.observer.queries import load_scanning_enabled
from core.telegram.worker_notify import notify_owners_in_transaction
from core.worker_metrics import mark_worker_heartbeat

logger = logging.getLogger(__name__)

WORKER_NAME = "enable_reco"
_METRICS_INTERVAL_SECONDS = 15.0

INTERVAL_SECONDS = int(os.environ.get("ENABLE_RECO_INTERVAL_SEC", "300"))
# Минимальный возраст «отключённости» до выдачи рекомендации (защита от мгновенного включения)
COOLDOWN_SECONDS = int(os.environ.get("ENABLE_RECO_COOLDOWN_SEC", "3600"))
# Лимит кандидатов за один цикл — защита от лавинной нагрузки
MAX_CANDIDATES_PER_CYCLE = int(os.environ.get("ENABLE_RECO_MAX_PER_CYCLE", "50"))
# Метрики после last_transition_at — окно по умолчанию шире cooldown'а
METRICS_LOOKBACK_SECONDS = int(os.environ.get("ENABLE_RECO_METRICS_LOOKBACK_SEC", str(3 * 3600)))
# Кейс куратора: «показов мало + CTR хороший» → рекомендация hold_until_cpl.
CURATOR_IMPR_CEILING = int(os.environ.get("ENABLE_RECO_CURATOR_IMPR_CEILING", "500"))
CURATOR_CTR_FLOOR = os.environ.get("ENABLE_RECO_CURATOR_CTR_FLOOR", "3.0")

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
    ad_account_id: str | None = None
    offer_currency: str | None = None
    frequency_threshold: Decimal | None = None
    stop_percent_of_rule: Decimal | None = None
    warning_percent_of_stop: Decimal | None = None
    tracker_registrations: int = 0
    tracker_confirmed_deposits: int = 0
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
        c.ad_account_id,
        r.cpa_threshold,
        r.currency,
        r.frequency_threshold,
        r.stop_percent_of_rule,
        r.warning_percent_of_stop,
        COALESCE((
            SELECT COUNT(*)
            FROM tracker_click_state tcs
            WHERE tcs.ad_id = st.ad_id AND tcs.registration = true
        ), 0)::int AS tracker_registrations,
        COALESCE((
            SELECT COUNT(*)
            FROM tracker_click_state tcs
            WHERE tcs.ad_id = st.ad_id AND tcs.confirmed_deposit = true
        ), 0)::int AS tracker_confirmed_deposits,
        st.open_state_token,
        a.delivery_status,
        EXISTS (
            SELECT 1
            FROM task_queue tq
            WHERE tq.task_type = 'meta_api_mutation'
              AND tq.payload->>'mutation_kind' = 'pause_ad'
              AND tq.payload->>'target_id' = a.fb_ad_id
              AND tq.status IN ('pending', 'running', 'retrying')
        ) AS has_unfinished_pause
    FROM ad_alert_state st
    JOIN fb_ads a ON a.id = st.ad_id
    JOIN fb_adsets s ON s.id = a.adset_id
    JOIN fb_campaigns c ON c.id = s.campaign_id
    LEFT JOIN offers o ON o.id = c.offer_id
    LEFT JOIN offer_rules r ON r.offer_id = o.id
    WHERE st.alert_state IN ('stop_sent', 'disabled')
      AND st.last_transition_at < NOW() - make_interval(secs => :cool)
    ORDER BY st.last_transition_at ASC
    LIMIT :lim
    """
)


_METRICS_SQL = text(
    """
    SELECT cycle_ts, spend, cost_per_lead, cost_per_registration, registrations, deposits,
           impressions, ctr, leads, clicks, reach, cpc, frequency
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
            ad_account_id=canonical_account_id(r[9]),
            cpa_threshold=r[10],
            offer_currency=str(r[11]) if r[11] else None,
            frequency_threshold=r[12],
            stop_percent_of_rule=r[13],
            warning_percent_of_stop=r[14],
            tracker_registrations=int(r[15] or 0),
            tracker_confirmed_deposits=int(r[16] or 0),
            open_state_token=r[17],
            delivery_status=str(r[18]) if r[18] else None,
            has_unfinished_pause=bool(r[19]),
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
            leads=int(r[8]) if r[8] is not None else None,
            clicks=int(r[9]) if r[9] is not None else None,
            reach=int(r[10]) if r[10] is not None else None,
            cpc=r[11],
            frequency=r[12],
        )
        for r in rows
    ]


# ====================== Prometheus process signal ======================


async def metrics_loop(stop: asyncio.Event) -> None:
    """Refresh Prometheus independently of the slow recommendation interval."""
    while not stop.is_set():
        mark_worker_heartbeat(WORKER_NAME)
        try:
            await asyncio.wait_for(stop.wait(), timeout=_METRICS_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


# ====================== Persist ======================


async def insert_recommendation(
    connection: AsyncConnection,
    *,
    ad_id: uuid.UUID,
    level: str,
    snapshot: dict[str, Any],
    live_batch_started_at: datetime,
    idempotency_key: str,
) -> uuid.UUID | None:
    """Insert inside the caller's recommendation/outbox transaction."""
    rec_id = uuid.uuid4()
    row = (
        await connection.execute(
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


# ====================== Notification outbox ======================


async def enqueue_recommendation_notification(
    connection: AsyncConnection,
    *,
    candidate: CandidateRow,
    decision: RecommendationDecision,
    recommendation_id: uuid.UUID,
    auto_promoted: bool = False,
) -> bool:
    """Project the card inside the recommendation/task transaction."""
    title = (
        f"Автовключение в очереди · {candidate.offer_code or candidate.ad_name}"
        if auto_promoted
        else f"Рекомендация · {candidate.offer_code or candidate.ad_name}"
    )
    reasons = list(decision.reasons or ())[:2]
    return await notify_owners_in_transaction(
        connection,
        event_type="enable_recommendation",
        severity="warning",
        title=title,
        summary=f"Объявление: {candidate.ad_name}",
        lines=[*(f"Причина: {reason}" for reason in reasons)],
        status="queued" if auto_promoted else "decision_required",
        dedupe_key=f"enable-recommendation:{recommendation_id}",
    )


async def load_auto_enable_recommendations(engine: AsyncEngine) -> bool:
    """Read the master switch; recommendation detection itself always stays enabled."""
    async with engine.connect() as conn:
        value = await conn.scalar(
            text(
                """
                SELECT auto_enable_recommendations
                FROM observer_config
                WHERE singleton_key = 'default'
                LIMIT 1
                """
            )
        )
    return bool(value)


# ====================== Один цикл ======================


async def run_once(
    engine: AsyncEngine,
    *,
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
    )

    # Асимметричный стоп: на паузе сканирования рекомендации включения бессмысленны
    # (детекта нет, включать незащищённое объявление не нужно). Пропускаем цикл.
    if not await load_scanning_enabled(engine):
        return {
            "candidates": 0,
            "skipped_existing": 0,
            "skipped_decision": 0,
            "recommendations": 0,
            "alerts_sent": 0,
            "skipped_paused": 1,
        }

    candidates = await fetch_candidates(engine, limit=MAX_CANDIDATES_PER_CYCLE)
    counts = {
        "candidates": len(candidates),
        "skipped_existing": 0,
        "skipped_decision": 0,
        "recommendations": 0,
        "alerts_sent": 0,
    }
    if not candidates:
        return counts

    auto_enable_on = await load_auto_enable_recommendations(engine)
    batch_started_at = now
    currency_resolution = await resolve_account_currencies(
        engine,
        account_ids=[
            account_id
            for candidate in candidates
            if (account_id := canonical_account_id(candidate.ad_account_id))
        ],
        now=now,
    )

    for cand in candidates:
        account_id = canonical_account_id(cand.ad_account_id)
        account_currency = currency_resolution.currencies.get(account_id)
        if account_currency is None:
            counts["skipped_decision"] += 1
            logger.warning(
                "skip ad_id=%s fb_ad_id=%s: cabinet currency is unavailable",
                cand.ad_id,
                cand.fb_ad_id,
            )
            continue
        try:
            account_currency_exponent = currency_exponent(account_currency)
        except UnsupportedCurrencyExponentError:
            counts["skipped_decision"] += 1
            logger.warning(
                "skip ad_id=%s fb_ad_id=%s: currency %s has no reviewed exponent",
                cand.ad_id,
                cand.fb_ad_id,
                account_currency,
            )
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
            offer=OfferThresholds(
                cpa_threshold=cand.cpa_threshold,
                currency=cand.offer_currency,
                frequency_threshold=cand.frequency_threshold,
                stop_percent_of_rule=cand.stop_percent_of_rule,
                warning_percent_of_stop=cand.warning_percent_of_stop,
            ),
            account_currency=account_currency,
            currency_exponent=account_currency_exponent,
            thresholds=thresholds,
            allow_curator=curator_allowed,
            tracker_registrations=cand.tracker_registrations,
            tracker_confirmed_deposits=cand.tracker_confirmed_deposits,
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
        # Recommendation, optional activation task and its operator card are one
        # durable decision. A projection failure rolls the whole unit back.
        async with engine.begin() as conn:
            new_id = await insert_recommendation(
                conn,
                ad_id=cand.ad_id,
                level=decision.level or "warning",
                snapshot=recommendation_snapshot,
                live_batch_started_at=batch_started_at,
                idempotency_key=idem_key,
            )

            # Если запись не создалась (idempotency_key уже есть в БД) — алерт не шлём
            if new_id is None:
                counts["skipped_existing"] += 1
                continue

            auto_promoted = False
            if auto_enable_on and decision.level == "ok":
                try:
                    await promote_enable_recommendation(
                        engine,
                        recommendation_id=new_id,
                        requested_by="auto_enable_recommendation_worker",
                        auto_mode=True,
                        connection=conn,
                    )
                    auto_promoted = True
                except (
                    RecommendationAlreadyPromotedError,
                    RecommendationNotFoundError,
                    RecommendationUnsafeStateError,
                ) as exc:
                    counts["auto_promotion_failed"] = counts.get("auto_promotion_failed", 0) + 1
                    logger.info(
                        "auto-enable revalidation rejected recommendation=%s ad=%s: %s",
                        new_id,
                        cand.fb_ad_id,
                        exc,
                    )

            sent = await enqueue_recommendation_notification(
                conn,
                candidate=cand,
                decision=decision,
                recommendation_id=new_id,
                auto_promoted=auto_promoted,
            )

        counts["recommendations"] += 1
        if auto_promoted:
            counts["auto_promoted"] = counts.get("auto_promoted", 0) + 1
        if not sent:
            counts["send_failed"] = counts.get("send_failed", 0) + 1
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
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    """Бесконечный цикл.

    Все factories допускают подмену в тестах. По умолчанию используются прод-реализации.
    """
    engine_factory = engine_factory or _default_engine_factory

    engine = await engine_factory()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    metrics_task = asyncio.create_task(metrics_loop(stop_event))

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
        metrics_task.cancel()
        try:
            await metrics_task
        except asyncio.CancelledError:
            pass
        await engine.dispose()


# ====================== Default factories ======================


async def _default_engine_factory() -> AsyncEngine:
    from core.config import get_settings

    return create_async_engine(get_settings().database_url, **WORKER_ENGINE_KWARGS)


__all__ = [
    "CandidateRow",
    "enqueue_recommendation_notification",
    "fetch_candidates",
    "fetch_metrics_since",
    "insert_recommendation",
    "main_loop",
    "run_once",
]
