# -*- coding: utf-8 -*-
"""Money-safety watchdog for Meta probes, auto-stop and reporting shadow.

All decision evidence, incident state and recovery predicates live in
PostgreSQL.  Process liveness is exported only through Prometheus.

Graceful shutdown по SIGTERM/SIGINT.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import signal
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from core.campaign_builder.money import (
    UnsupportedCampaignCurrencyError,
    campaign_currency_exponent,
    minor_units_to_major_amount,
    nonnegative_major_amount_to_minor_units,
)
from core.db import WORKER_ENGINE_KWARGS
from core.meta_api.autostop_alert import AUTOSTOP_CHANNEL_INCIDENT_KEY
from core.meta_api.browser_readiness import (
    BROWSER_READINESS_DEFAULT_TTL_SECONDS,
    probe_and_publish_browser_readiness,
)
from core.meta_api.client import BROWSER_CONTRACT_VERSION
from core.meta_api.shadow_spend import (
    DEFAULT_BILLING_MIN_DELTA_MINOR as _SHADOW_DEFAULT_BILLING_MIN,
)
from core.meta_api.shadow_spend import (
    DEFAULT_REPORTED_MAX_DELTA_MINOR as _SHADOW_DEFAULT_REPORTED_MAX,
)
from core.meta_api.shadow_spend import (
    DEFAULT_WINDOW_SECONDS as _SHADOW_DEFAULT_WINDOW,
)
from core.meta_api.shadow_spend import (
    ShadowSample,
    ShadowVerdict,
    detect_shadow,
)
from core.models.settings.vision_config import VisionConfig
from core.observer.cabinet_tab_incident import sync_cabinet_tab_incident
from core.observer.login_required import notify_login_required_incident
from core.observer.scan_tasks import enqueue_observer_scan, observer_scan_idempotency_key
from core.safe_diagnostics import safe_exception_diagnostic
from core.tasks.browser_fence import (
    BrowserFenceLeaseLost,
    BrowserOperationBlocked,
    BrowserOperationFence,
)
from core.telegram.worker_notify import (
    notify_recurring_incident,
    notify_recurring_incident_in_transaction,
    resolve_recurring_incident,
    resolve_recurring_incident_in_transaction,
)
from core.vision.channel_config import load_vision_channel_configuration
from core.vision.token_refresh import refresh_vision_token_if_needed
from core.wording import ads_ru, commands_ru
from core.worker_liveness import record_worker_heartbeat
from core.worker_metrics import mark_worker_heartbeat

logger = logging.getLogger("health_watchdog")

WORKER_NAME = "health_watchdog"

CHECK_INTERVAL_SECONDS = int(os.environ.get("HEALTH_WATCHDOG_INTERVAL_SEC", "60"))
# Пауза перед перезапуском упавшего цикла (_supervised, инцидент 01.07:
# gather без защиты — одно исключение молча гасило весь воркер-сторож).
LOOP_RESTART_DELAY_SECONDS = float(os.environ.get("HEALTH_WATCHDOG_LOOP_RESTART_SEC", "5"))
# Grace-период перед browser/Meta probes после совместного старта.
STARTUP_GRACE_SECONDS = int(os.environ.get("HEALTH_WATCHDOG_STARTUP_GRACE_SEC", "90"))
REPORTED_SNAPSHOT_MAX_AGE_SECONDS = int(
    os.environ.get("HEALTH_WATCHDOG_SNAPSHOT_MAX_AGE_SEC", "300")
)
# Облачное продление идёт в уже наблюдаемом watchdog, а не в отдельном
# host-таймере. Суточная каденция повторяет проверенный production-режим.
VISION_TOKEN_REFRESH_INTERVAL_SECONDS = int(
    os.environ.get("HEALTH_WATCHDOG_VISION_TOKEN_REFRESH_SEC", "86400")
)


def _validated_browser_readiness_schedule(
    interval_seconds: float,
    ttl_seconds: int,
) -> tuple[float, int]:
    interval = float(interval_seconds)
    ttl = int(ttl_seconds)
    if not math.isfinite(interval) or not 1 <= interval < ttl <= 30:
        raise RuntimeError(
            "browser readiness cadence must satisfy 1 <= interval_seconds < ttl_seconds <= 30"
        )
    return interval, ttl


(
    BROWSER_READINESS_INTERVAL_SECONDS,
    BROWSER_READINESS_TTL_SECONDS,
) = _validated_browser_readiness_schedule(
    float(os.environ.get("HEALTH_WATCHDOG_BROWSER_READINESS_SEC", "2")),
    int(
        os.environ.get(
            "HEALTH_WATCHDOG_BROWSER_READINESS_TTL_SEC",
            str(BROWSER_READINESS_DEFAULT_TTL_SECONDS),
        )
    ),
)
_BROWSER_READINESS_WRITER_INSTANCE = uuid.uuid4()


def _validated_browser_workspace_interval(interval_seconds: float) -> float:
    """Каденция подготовки обязана быть редкой, и это проверяется на старте.

    Подготовка открывает и навигирует вкладки. Ошибка в единице измерения
    вернула бы каденцию того же порядка, что и проба готовности, — то есть
    ровно цикл вкладок, ради которого всё это и делалось. Нижняя граница ещё и
    строго больше времени жизни evidence: чаще неё подготовка реагировала бы на
    наблюдение, которое проба не успела переписать.
    """
    interval = float(interval_seconds)
    if (
        not math.isfinite(interval)
        or interval <= BROWSER_READINESS_TTL_SECONDS
        or not 30.0 <= interval <= 3600.0
    ):
        raise RuntimeError(
            "browser workspace cadence must be between 30 and 3600 seconds "
            "and longer than the readiness TTL"
        )
    return interval


BROWSER_WORKSPACE_INTERVAL_SECONDS = _validated_browser_workspace_interval(
    float(os.environ.get("HEALTH_WATCHDOG_BROWSER_WORKSPACE_SEC", "60"))
)

# ====================== канал авто-стопа (money-критичный мониторинг) ======================
# Инцидент 2026-06-19: канал исполнения авто-стопа (Marketing API через Vision
# page.evaluate(fetch)) лёг — fetch начал падать «Failed to fetch» (code=-2). Задачи
# task_queue pause_ad/bot_auto_stop зависли в retrying (15-16 из 72 попыток), объявления
# остались в FSM=stop_sent, но delivery_status=ACTIVE и продолжали тратить. Сигнала не было.
# Watchdog независимым внешним наблюдателем ловит отказ канала по двум триггерам:
#   (3) задачи pause_ad/bot_auto_stop незавершены дольше STUCK_MIN — прямой признак отказа;
#   (2) рассинхрон stop_sent при delivery_status=ACTIVE дольше DESYNC_MIN — money-симптом.
AUTOSTOP_STUCK_AFTER_MINUTES = int(os.environ.get("HEALTH_WATCHDOG_AUTOSTOP_STUCK_MIN", "15"))
AUTOSTOP_DESYNC_AFTER_MINUTES = int(os.environ.get("HEALTH_WATCHDOG_AUTOSTOP_DESYNC_MIN", "15"))

# ====================== сетевой probe канала Marketing API (money-критичный) ======================
# Инцидент 2026-06-19: token-only health давал false-positive «healthy» при мёртвом
# сетевом канале (Failed to fetch). Watchdog — единственный прободер: раз в
# META_PROBE_INTERVAL_SECONDS делает реальный GET /me (full_probe) через browser-agent,
# при отказе канала фиксирует CRITICAL в durable incident/notification plane.
META_PROBE_INTERVAL_SECONDS = int(os.environ.get("HEALTH_WATCHDOG_META_PROBE_SEC", "300"))
META_CHANNEL_INCIDENT_KEY = AUTOSTOP_CHANNEL_INCIDENT_KEY
AUTOSTOP_BACKLOG_INCIDENT_KEY = "health:autostop_backlog"

# ====================== сторожок «тени отчётности Meta» (money-смежный, alert-only) ======================
# Замер на проде 03.07 08:31–09:40 UTC: биллинговый счётчик кабинета (amount_spent,
# lifetime в minor units) двигается РАНЬШЕ пер-адной отчётности am_tabular —
# биллинг видит «тень» открута, которую
# пер-адные снимки ещё не показывают. Класс утренних перекрутов (18 минут нулей при
# реальном откруте). Сторожок ловит «кабинет тратит, отчётность стоит» → CRITICAL
# владельцу. Alert-only, без авто-паузы (безопасно включён по умолчанию).
SHADOW_SPEND_WATCH_ENABLED = os.environ.get(
    "SHADOW_SPEND_WATCH_ENABLED", "true"
).strip().lower() not in ("0", "false", "no", "off")
SHADOW_SPEND_INTERVAL_SECONDS = int(os.environ.get("HEALTH_WATCHDOG_SHADOW_SPEND_SEC", "30"))
# Окно среза и пороги детектора в minor units подтверждённой валюты.
SHADOW_WINDOW_SECONDS = int(
    os.environ.get("HEALTH_WATCHDOG_SHADOW_WINDOW_SEC", str(_SHADOW_DEFAULT_WINDOW))
)
SHADOW_BILLING_MIN_DELTA_MINOR = int(
    os.environ.get("HEALTH_WATCHDOG_SHADOW_BILLING_MIN_MINOR", str(_SHADOW_DEFAULT_BILLING_MIN))
)
SHADOW_REPORTED_MAX_DELTA_MINOR = int(
    os.environ.get("HEALTH_WATCHDOG_SHADOW_REPORTED_MAX_MINOR", str(_SHADOW_DEFAULT_REPORTED_MAX))
)
# PostgreSQL keeps a bounded per-account JSONB evidence window.  The incident
# baseline is stored separately in the same row and survives evidence pruning.
SHADOW_SAMPLE_MAX_LEN = 20
SHADOW_EVIDENCE_MAX_AGE_SECONDS = 3600
SHADOW_INCIDENT_KEY_PREFIX = "health:shadow:"
SHADOW_CURRENCY_INCIDENT_KEY_PREFIX = "health:shadow_currency:"


class _MetaProbeClient(Protocol):
    """Минимальный контракт MetaApiClient для probe (для тестируемости)."""

    async def check_health(
        self,
        *,
        full_probe: bool = ...,
        expected_profile_id: str | None = ...,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class StuckPauseTask:
    """Застрявшая задача авто-стопа (pause_ad от bot_auto_stop, не дошла до succeeded)."""

    task_id: int
    target_id: str  # fb_ad_id объявления, которое не удалось остановить
    attempt_count: int
    age_minutes: int  # сколько минут задача не завершается с момента создания
    last_error: str | None  # последняя ошибка (часто «Failed to fetch» при отказе канала)


@dataclass(frozen=True)
class DesyncedStopAd:
    """Рассинхрон: объявление в FSM=stop_sent, но delivery_status=ACTIVE (крутится/тратит)."""

    fb_ad_id: str
    age_minutes: int  # сколько минут держится рассинхрон (с момента перехода в stop_sent)


@dataclass(frozen=True)
class ShadowObservationDecision:
    """Persisted shadow detector result for one committed observation."""

    previous_sample: ShadowSample | None
    verdict: ShadowVerdict | None
    recovery_confirmed: bool
    incident_event_committed: bool
    currency_reset: bool = False


# MID X-16: маркер разлогина/чекпоинта из probe-ответа browser-agent (probe_detail/detail).
# browser-agent отдаёт 'login_required' при 190 с login-subcode/redirect на login.php.
# Отличаем от рядового network-down/token-invalid → отдельный текст «нужен ре-логин».
LOGIN_REQUIRED_MARKER = "login_required"


def is_login_required_reason(reason: str) -> bool:
    """True, если reason из probe — маркер разлогина/чекпоинта (нужен ре-логин профиля)."""
    return LOGIN_REQUIRED_MARKER in str(reason).lower()


async def _alert_login_required_accounts(engine: AsyncEngine) -> bool:
    """Open the canonical per-cabinet incident instead of channel-down."""
    from core.observer.accounts import resolve_configured_ad_account_ids

    account_ids = await resolve_configured_ad_account_ids(engine)
    if not account_ids:
        logger.error("meta probe: login_required без активного кабинета")
        return False

    accepted = False
    for account_id in account_ids:
        accepted = (
            await notify_login_required_incident(
                engine,
                ad_account_id=account_id,
            )
            or accepted
        )
    return accepted


def classify_meta_probe(
    probe: dict[str, Any],
    *,
    expected_profile_id: str,
) -> tuple[bool, str]:
    """Классифицирует результат check_health(full_probe=True): жив ли канал.

    Возвращает ``(is_down, reason)``. Канал мёртв (is_down=True), если probe вернул
    ``healthy=False`` — это покрывает network-down (Failed to fetch), протухший токен (190),
    разлогин/чекпоинт (login_required), недоступность browser-agent (circuit_open) и
    отсутствие токена. Meta-side ошибки (rate-limit) оставляют ``healthy=True`` → канал
    жив (не считаем outage'ом, согласовано с ``autostop_alert.is_channel_down_error``).

    reason — наиболее информативная причина: ``probe_detail`` при выполненном probe,
    иначе ``detail`` (например circuit_open / token_not_found / login_required).
    Вызывающий смотрит ``is_login_required_reason(reason)``, чтобы выбрать текст алерта.
    """
    if bool(probe.get("healthy", False)):
        observed_contract = int(probe.get("browser_contract_version") or 0)
        if observed_contract != BROWSER_CONTRACT_VERSION:
            return (
                True,
                "browser_contract_incompatible:"
                f"required={BROWSER_CONTRACT_VERSION},observed={observed_contract}",
            )
        live_profile_id = str(probe.get("vision_profile_id") or "").strip()
        if not expected_profile_id.strip() or live_profile_id != expected_profile_id.strip():
            return True, "vision_profile_mismatch"
        if not probe.get("probe_performed") or not probe.get("probe_ok"):
            detail = str(probe.get("probe_detail") or probe.get("detail") or "not_performed")
            return True, f"full_probe_unconfirmed:{detail}"
        return False, str(probe.get("probe_detail") or probe.get("detail") or "ok")

    if probe.get("probe_performed"):
        reason = probe.get("probe_detail") or probe.get("detail") or "down"
    else:
        reason = probe.get("detail") or "unreachable"
    return True, str(reason)


async def _load_canonical_vision_profile_id(engine: AsyncEngine) -> str:
    """Load the operator-selected profile without decrypting unrelated credentials."""
    async with AsyncSession(engine) as session:
        profile_id = await session.scalar(
            select(VisionConfig.profile_id).where(VisionConfig.singleton_key == "default")
        )
    normalized = str(profile_id or "").strip()
    if not normalized:
        raise RuntimeError("canonical Vision profile is unavailable")
    return normalized


def _has_unreported_billing_movement(
    previous: ShadowSample | None,
    current: ShadowSample,
) -> bool:
    """True, когда billing уже вырос, а последний per-ad снимок ещё не сдвинулся."""
    if previous is None or previous.ts >= current.ts:
        return False
    if previous.currency != current.currency:
        return False
    return (
        current.billing_minor > previous.billing_minor
        and current.reported_minor <= previous.reported_minor
    )


def _shadow_reporting_caught_up(
    baseline: ShadowSample,
    current: ShadowSample,
    *,
    tolerance_minor: int = SHADOW_REPORTED_MAX_DELTA_MINOR,
) -> bool:
    """Return True only when reporting covers billing movement since the incident.

    Merely receiving two timestamps is not recovery: after a process/cache loss
    that would resolve an active incident while reported spend was still frozen.
    The durable incident baseline makes the predicate explicit and monotonic.
    """
    if current.ts <= baseline.ts:
        return False
    if current.currency != baseline.currency:
        return False
    billing_delta = current.billing_minor - baseline.billing_minor
    reported_delta = current.reported_minor - baseline.reported_minor
    if billing_delta < 0 or reported_delta <= tolerance_minor:
        return False
    return reported_delta + tolerance_minor >= billing_delta


async def _activate_shadow_burst(
    engine: AsyncEngine,
    *,
    account_id: str,
    sample: ShadowSample,
) -> None:
    """Persist one immediate observer scan command for reporting shadow."""
    try:
        await enqueue_observer_scan(
            engine,
            requested_by="health_watchdog",
            reason=f"shadow_spend:act_{account_id}",
            idempotency_key=observer_scan_idempotency_key(
                "shadow-spend",
                f"{account_id}:{sample.ts.isoformat()}",
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "shadow: durable observer scan was not enqueued for act_%s",
            account_id,
        )


# ====================== Durable notification events ======================


async def _enqueue_critical_notification(
    *,
    incident_key: str,
    engine: AsyncEngine,
    event_type: str,
    title: str,
    summary: str | None = None,
    lines: Sequence[str] = (),
    risk: str | None = None,
    resource_type: str = "system",
    resource_id: str | None = None,
) -> bool:
    """Open or refresh one persisted CRITICAL incident generation.

    Every detected condition reaches the durable incident plane. PostgreSQL
    serializes repeats into one active generation and one editable message card.
    """
    accepted = await notify_recurring_incident(
        engine,
        incident_key=incident_key,
        audience="all",
        event_type=event_type,
        severity="critical",
        title=title,
        summary=summary,
        lines=lines,
        risk=risk,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    if accepted:
        logger.info("critical incident %s принят durable plane", incident_key)
    return accepted


async def _resolve_critical_notification(
    *,
    incident_key: str,
    engine: AsyncEngine,
    summary: str,
) -> bool:
    """Resolve only from a confirmed healthy observation; unknown is a no-op."""
    resolved = await resolve_recurring_incident(
        engine,
        incident_key=incident_key,
        audience="all",
        summary=summary,
    )
    if resolved:
        logger.info("critical incident %s подтверждённо восстановлен", incident_key)
    return resolved


# ====================== проверки ======================


_STUCK_PAUSE_TASKS_SQL = text(
    """
    SELECT id,
           payload->>'target_id' AS target_id,
           attempt_count,
           last_error,
           CAST(FLOOR(EXTRACT(EPOCH FROM (NOW() - created_at)) / 60) AS INTEGER) AS age_minutes
    FROM task_queue
    WHERE task_type = 'meta_api_mutation'
      AND payload->>'mutation_kind' = 'pause_ad'
      AND requested_by = 'bot_auto_stop'
      AND status IN ('pending', 'retrying', 'running')
      AND created_at < NOW() - make_interval(mins => :minutes)
    ORDER BY created_at ASC
    """
)

_DESYNCED_STOP_ADS_SQL = text(
    """
    SELECT fb_ads.fb_ad_id,
           CAST(FLOOR(EXTRACT(EPOCH FROM (NOW() - s.last_transition_at)) / 60) AS INTEGER)
               AS age_minutes
    FROM fb_ads
    JOIN ad_alert_state s ON s.ad_id = fb_ads.id
    WHERE s.alert_state = 'stop_sent'
      AND UPPER(fb_ads.delivery_status) = 'ACTIVE'
      AND s.last_transition_at < NOW() - make_interval(mins => :minutes)
    ORDER BY s.last_transition_at ASC
    """
)


async def query_stuck_pause_tasks(engine: AsyncEngine, *, minutes: int) -> list[StuckPauseTask]:
    """Задачи авто-стопа (pause_ad/bot_auto_stop), не завершённые дольше ``minutes`` минут.

    Триггер 3: незавершённый статус (pending/retrying/running) + возраст created_at >
    порога = прямой признак отказа канала исполнения (Vision fetch лёг, токен протух и т.п.).
    """
    async with engine.connect() as conn:
        result = await conn.execute(_STUCK_PAUSE_TASKS_SQL, {"minutes": int(minutes)})
        rows = result.all()
    return [
        StuckPauseTask(
            task_id=int(r[0]),
            target_id=str(r[1]),
            attempt_count=int(r[2]),
            last_error=r[3],
            age_minutes=int(r[4]),
        )
        for r in rows
    ]


async def query_desynced_stop_ads(engine: AsyncEngine, *, minutes: int) -> list[DesyncedStopAd]:
    """Объявления в FSM=stop_sent, но delivery_status=ACTIVE дольше ``minutes`` минут.

    Триггер 2 (money-симптом): авто-стоп вынес решение (stop_sent), но не довёл объявление
    до OFF — оно крутится и тратит. Ловит проблему по финальному симптому независимо от
    причины. delivery_status сравнивается без учёта регистра; NULL (не сканировали) не
    считается ACTIVE — не алертим вслепую.
    """
    async with engine.connect() as conn:
        result = await conn.execute(_DESYNCED_STOP_ADS_SQL, {"minutes": int(minutes)})
        rows = result.all()
    return [DesyncedStopAd(fb_ad_id=str(r[0]), age_minutes=int(r[1])) for r in rows]


async def check_autostop_channel(
    engine: AsyncEngine,
    *,
    stuck_after_minutes: int = AUTOSTOP_STUCK_AFTER_MINUTES,
    desync_after_minutes: int = AUTOSTOP_DESYNC_AFTER_MINUTES,
) -> bool:
    """Проверяет здоровье канала авто-стопа. Возвращает True, если алерт был отправлен.

    Money-критично: при отказе канала исполнения авто-стопа (инцидент 2026-06-19)
    объявления остаются крутиться при FSM=stop_sent. Шлёт CRITICAL всем активным
    recipients через PostgreSQL incident plane; повторные тики обновляют одну generation.
    """
    try:
        stuck = await query_stuck_pause_tasks(engine, minutes=stuck_after_minutes)
        desynced = await query_desynced_stop_ads(engine, minutes=desync_after_minutes)
    except Exception:  # noqa: BLE001
        logger.exception("ошибка проверки канала авто-стопа")
        return False

    if not stuck and not desynced:
        await _resolve_critical_notification(
            incident_key=AUTOSTOP_BACKLOG_INCIDENT_KEY,
            engine=engine,
            summary="Команды авто-стопа снова доходят, статусы сошлись.",
        )
        return False

    logger.error("канал авто-стопа деградировал: stuck=%d desync=%d", len(stuck), len(desynced))
    targets = [task.target_id for task in stuck[:2]]
    targets.extend(ad.fb_ad_id for ad in desynced[: 2 - len(targets)])
    return await _enqueue_critical_notification(
        incident_key=AUTOSTOP_BACKLOG_INCIDENT_KEY,
        engine=engine,
        event_type="autostop_backlog_degraded",
        title="Авто-стоп не доводит объявления до выключения",
        summary=(
            f"Зависло {commands_ru(len(stuck))} · "
            f"после стопа всё ещё работает {ads_ru(len(desynced))}"
        ),
        risk="Объявления могут продолжать тратить бюджет",
        lines=[
            # Имени объявления в этих выборках нет — показываем ID как есть.
            *(f"Объявление {target}" for target in targets),
            "Проверь Vision-профиль и отключи эти объявления вручную",
        ],
        resource_type="meta_channel",
        resource_id="auto_stop",
    )


async def _reattach_and_reprobe(
    meta_client: _MetaProbeClient,
    reattach_session: Callable[[], Awaitable[None]],
    *,
    engine: AsyncEngine,
    expected_profile_id: str,
    reason: str,
) -> tuple[bool, str]:
    """Одна попытка вернуть сессию канала. Возвращает ``(канал жив, причина)``.

    Живое наблюдение 20.08.2026: browser-agent перезапустили, Vision-профиль
    остался жив и виден в ``/list``, но процесс-локальной сессии у него больше не
    было. Проба честно сказала «канал мёртв» и завела CRITICAL — и на этом всё.
    Очередь заливов встала на «ждут готовности браузера»: проба намеренно не
    открывает кабинет (issue #189), а сессию больше никто не просил.

    ``StartBrowser`` присоединяется к **уже живому** профилю, не перезапуская его,
    поэтому «профиль забран другой машиной» здесь не автоматизируется: этот случай
    browser-agent отклоняет сам, и отказ доходит сюда исключением.

    Попытка ровно одна за проход. Молчаливый бесконечный ретрай хуже честного
    отказа: он прячет причину и не даёт оператору повода вмешаться.
    """
    try:
        await reattach_session()
    except Exception as exc:  # noqa: BLE001 — причина отказа известна только по факту
        logger.warning(
            "meta probe: присоединить сессию не удалось (%s)",
            safe_exception_diagnostic(exc),
        )
        return False, reason

    try:
        async with BrowserOperationFence(
            engine,
            operation_kind="health_meta_probe",
            target="full_probe",
        ) as fence:
            probe = await meta_client.check_health(
                full_probe=True,
                expected_profile_id=expected_profile_id,
            )
            await fence.assert_held()
    except Exception as exc:  # noqa: BLE001 — повторная проба best-effort, как основная
        logger.warning(
            "meta probe: повторная проба после присоединения не прошла (%s)",
            safe_exception_diagnostic(exc),
        )
        return False, reason

    is_down, retry_reason = classify_meta_probe(probe, expected_profile_id=expected_profile_id)
    if is_down:
        return False, retry_reason
    logger.info("meta probe: сессия восстановлена присоединением (%s)", retry_reason)
    return True, retry_reason


async def check_meta_api_channel(
    meta_client: _MetaProbeClient,
    *,
    engine: AsyncEngine,
    reattach_session: Callable[[], Awaitable[None]] | None = None,
) -> bool:
    """Проактивный probe канала Marketing API. Возвращает True, если алерт отправлен.

    Единственный прободер: делает реальный GET /me (full_probe) через browser-agent
    и при отказе канала фиксирует CRITICAL в PostgreSQL outbox.
    Best-effort: исключения check_health трактуются как «канал мёртв».

    Тумблер сканирования гасит только транспортные причины: без постоянной сессии
    observer'а «сессия не найдена» — ожидаемое состояние, а не отказ. Разлогин
    профиля к сканированию отношения не имеет: каналом пользуется ещё и залив
    кампаний, а действие оператора одно и то же — зайти в Facebook заново.
    """
    try:
        from core.observer.queries import load_observer_config

        obs_config = await load_observer_config(engine)
        scanning_on = bool(obs_config and obs_config.get("is_scanning_enabled"))
    except Exception:  # noqa: BLE001
        logger.exception("meta probe: observer_config недоступен — probe пропущен")
        return False

    # Ненастроенный канал — штатное состояние первого запуска, а не авария:
    # учётные данные вводит оператор уже после выкатки. Условие смотрит именно
    # на введённость, а не на ответ канала: настроенный канал падает по той же
    # ветке кода, и подавление по факту отказа закрыло бы настоящую аварию.
    try:
        configuration = await load_vision_channel_configuration(engine)
    except Exception:  # noqa: BLE001
        # Нечитаемая конфигурация — это «неизвестно», а не «не настроено».
        # Тишина по неизвестности глушила бы настоящий отказ, поэтому проба
        # идёт своим обычным путём и отказ канала остаётся аварией.
        logger.exception("meta probe: конфигурация канала недоступна — проба идёт как обычно")
    else:
        if not configuration.has_token or not configuration.profile_id:
            logger.info(
                "meta probe: канал Vision не настроен (токен %s, профиль %s) — проба пропущена",
                "введён" if configuration.has_token else "не введён",
                "выбран" if configuration.profile_id else "не выбран",
            )
            return False

    expected_profile_id = ""
    try:
        async with BrowserOperationFence(
            engine,
            operation_kind="health_meta_probe",
            target="full_probe",
        ) as fence:
            # Vision settings updates take the exclusive side of this same
            # fence. Read the canonical identity only after the shared lease
            # is held so A→B cannot race into a false mismatch incident.
            expected_profile_id = await _load_canonical_vision_profile_id(engine)
            probe = await meta_client.check_health(
                full_probe=True,
                expected_profile_id=expected_profile_id,
            )
            await fence.assert_held()
    except BrowserOperationBlocked:
        logger.info("meta probe: Vision maintenance active — probe deferred")
        return False
    except BrowserFenceLeaseLost:
        logger.warning("meta probe: browser-operation fence lost — probe discarded")
        return False
    except Exception as exc:  # noqa: BLE001
        logger.exception("meta probe: check_health бросил исключение")
        probe = {
            "healthy": False,
            "detail": f"probe_exception: {exc}",
            "probe_performed": False,
            "probe_ok": False,
            "probe_status_code": 0,
            "probe_duration_ms": 0,
            "probe_detail": "probe_exception",
        }

    is_down, reason = classify_meta_probe(
        probe,
        expected_profile_id=expected_profile_id,
    )

    if not is_down:
        # INFO-след каждого healthy-прохода: тишина в логах ≠ живой probe (урок 01.07).
        logger.info("meta probe: канал жив (%s)", reason)
        await _resolve_critical_notification(
            incident_key=META_CHANNEL_INCIDENT_KEY,
            engine=engine,
            summary="Проверочный запрос к Meta снова подтверждён.",
        )
        return False

    login_required = is_login_required_reason(reason)
    if login_required:
        # Разлогин говорит всегда, даже при выключенном скане: 18.08.2026 Facebook
        # инвалидировал сессию в 13:35, и до 18:00 об этом не сказал никто.
        logger.error("канал Marketing API мёртв (probe): %s", reason)
        # Если раньше причина была транспортной, закрой старую channel-down
        # generation. Login-required имеет другой operator action и отдельный key.
        await _resolve_critical_notification(
            incident_key=META_CHANNEL_INCIDENT_KEY,
            engine=engine,
            summary="Причина уточнена: Facebook требует повторного входа.",
        )
        return await _alert_login_required_accounts(engine)

    if not scanning_on:
        # След намеренного пропуска: после 11:24 01.07 probe молчал «по дизайну»,
        # и тишина в логах выглядела как зависание воркера.
        logger.info(
            "meta probe: сканирование выключено — транспортный отказ не эскалируем (%s)",
            reason,
        )
        return False

    if reattach_session is not None:
        recovered, reason = await _reattach_and_reprobe(
            meta_client,
            reattach_session,
            engine=engine,
            expected_profile_id=expected_profile_id,
            reason=reason,
        )
        if recovered:
            await _resolve_critical_notification(
                incident_key=META_CHANNEL_INCIDENT_KEY,
                engine=engine,
                summary="Сессия канала восстановлена без вмешательства.",
            )
            return False

    logger.error("канал Marketing API мёртв (probe): %s", reason)
    return await _enqueue_critical_notification(
        incident_key=META_CHANNEL_INCIDENT_KEY,
        engine=engine,
        event_type="meta_channel_unavailable",
        title="Канал Marketing API недоступен",
        summary="Проверочный запрос к Meta не подтверждён",
        risk="Авто-стоп может не дойти до Meta",
        lines=[
            "Проверь browser-agent и Vision-профиль",
            "При риске расхода отключи объявления вручную",
        ],
        resource_type="meta_channel",
        resource_id="auto_stop",
    )


# ====================== сторожок «тени отчётности Meta» ======================


async def _is_reported_side_live(
    engine: AsyncEngine,
    *,
    account_ids: Sequence[str],
    now: datetime,
) -> bool:
    """Гейт сторожка: reported-сторона (пер-адная отчётность) реально работает.

    Работаем ТОЛЬКО когда сканирование включено и все зарегистрированные cabinet actors
    имеют свежий подтверждённый snapshot в PostgreSQL. Ошибку чтения трактуем как
    «не работаем»: лишний пропуск alert-only сторожка безопаснее ложного CRITICAL.
    """
    try:
        from core.observer.queries import load_observer_config

        obs_config = await load_observer_config(engine)
        scanning_on = bool(obs_config and obs_config.get("is_scanning_enabled"))
    except Exception:  # noqa: BLE001
        logger.warning("shadow: не удалось прочитать observer_config — тик пропущен")
        return False

    if not scanning_on:
        return False

    normalized_accounts = sorted(
        {
            str(account_id).removeprefix("act_").strip()
            for account_id in account_ids
            if str(account_id).removeprefix("act_").strip()
        }
    )
    if not normalized_accounts:
        return False
    fresh_after = now - timedelta(seconds=REPORTED_SNAPSHOT_MAX_AGE_SECONDS)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        WITH active_accounts AS (
                            SELECT UNNEST(CAST(:account_ids AS TEXT[])) AS ad_account_id
                        )
                        SELECT COUNT(*) AS actor_count,
                               COALESCE(
                                   BOOL_AND(
                                       runtime.last_snapshot_at IS NOT NULL
                                       AND runtime.last_snapshot_at >= :fresh_after
                                   ),
                                   FALSE
                               ) AS all_fresh
                        FROM active_accounts AS active
                        LEFT JOIN cabinet_runtime AS runtime
                          ON runtime.ad_account_id = active.ad_account_id
                        """
                    ),
                    {
                        "account_ids": normalized_accounts,
                        "fresh_after": fresh_after,
                    },
                )
            ).one()
    except Exception:  # noqa: BLE001
        logger.warning("shadow: не удалось прочитать cabinet_runtime — тик пропущен")
        return False
    return int(row.actor_count or 0) > 0 and bool(row.all_fresh)


async def _fetch_billing_minor(meta_client: Any, account_id: str) -> int | None:
    """Read Meta lifetime spend as the currency's integer minor unit.

    None при ошибке Graph-вызова / отсутствии поля (тик пропускается, канал probe
    мониторит канал отдельно). ``amount_spent`` is already returned in the
    account currency's smallest unit and is never rescaled here.
    """
    acct = (account_id or "").removeprefix("act_")
    if not acct:
        return None
    resp = await meta_client.execute_graph_call(
        method="GET",
        endpoint=f"/act_{acct}",
        query_params={"fields": "amount_spent"},
        ad_account_id=acct,
    )
    raw = resp.get("amount_spent")
    if raw is None:
        return None
    try:
        amount = int(str(raw))
    except (TypeError, ValueError):
        return None
    return amount if amount >= 0 else None


async def _database_now(engine: AsyncEngine) -> datetime:
    """Use PostgreSQL time for persisted leases, freshness and cabinet-day evidence."""
    async with engine.connect() as conn:
        observed_at = (await conn.execute(text("SELECT NOW()"))).scalar_one()
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise RuntimeError("PostgreSQL returned an invalid timezone-aware clock value")
    return observed_at.astimezone(UTC)


def _decode_shadow_samples(raw: object) -> list[ShadowSample]:
    """Decode the bounded PostgreSQL evidence list; malformed entries are ignored."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    samples: list[ShadowSample] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            observed_at = datetime.fromisoformat(str(item["ts"]))
            if observed_at.tzinfo is None:
                continue
            currency = str(item["currency"])
            campaign_currency_exponent(currency)
            billing_minor = int(item["billing_minor"])
            reported_minor = int(item["reported_minor"])
            if billing_minor < 0 or reported_minor < 0:
                continue
            samples.append(
                ShadowSample(
                    ts=observed_at.astimezone(UTC),
                    currency=currency,
                    billing_minor=billing_minor,
                    reported_minor=reported_minor,
                )
            )
        except (UnsupportedCampaignCurrencyError, ValueError, KeyError, TypeError):
            continue
    return samples


def _encode_shadow_samples(samples: Sequence[ShadowSample]) -> str:
    return json.dumps(
        [
            {
                "ts": sample.ts.astimezone(UTC).isoformat(),
                "currency": sample.currency,
                "billing_minor": sample.billing_minor,
                "reported_minor": sample.reported_minor,
            }
            for sample in samples
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


async def _record_shadow_observation(
    engine: AsyncEngine,
    *,
    account_id: str,
    sample: ShadowSample,
    cabinet_day_start: datetime,
) -> ShadowObservationDecision:
    """Commit one sample and evaluate detection/recovery under a row lock.

    The bounded samples accelerate the rolling detector.  The separate incident
    baseline is never pruned and therefore remains valid across process restarts
    until an atomic incident recovery clears it.
    """
    if sample.ts.tzinfo is None:
        raise ValueError("shadow sample timestamp must be timezone-aware")
    if cabinet_day_start.tzinfo is None:
        raise ValueError("cabinet_day_start must be timezone-aware")
    campaign_currency_exponent(sample.currency)
    if sample.billing_minor < 0 or sample.reported_minor < 0:
        raise ValueError("shadow counters must be non-negative minor units")
    normalized = ShadowSample(
        ts=sample.ts.astimezone(UTC),
        currency=sample.currency,
        billing_minor=sample.billing_minor,
        reported_minor=sample.reported_minor,
    )
    normalized_day_start = cabinet_day_start.astimezone(UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO meta_shadow_spend_state
                    (account_id, currency, samples, cabinet_day_start, last_observed_at)
                VALUES (
                    :account_id, :currency, '[]'::jsonb,
                    :cabinet_day_start, :observed_at
                )
                ON CONFLICT (account_id) DO NOTHING
                """
            ),
            {
                "account_id": account_id,
                "currency": normalized.currency,
                "cabinet_day_start": normalized_day_start,
                "observed_at": normalized.ts,
            },
        )
        row = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT currency,
                           samples,
                           cabinet_day_start,
                           incident_baseline_at,
                           incident_baseline_billing_minor,
                           incident_baseline_reported_minor,
                           recovery_candidate_at,
                           last_observed_at
                    FROM meta_shadow_spend_state
                    WHERE account_id = :account_id
                    FOR UPDATE
                    """
                    ),
                    {"account_id": account_id},
                )
            )
            .mappings()
            .one()
        )

        baseline: ShadowSample | None = None
        baseline_at = row["incident_baseline_at"]
        baseline_billing = row["incident_baseline_billing_minor"]
        baseline_reported = row["incident_baseline_reported_minor"]
        persisted_currency = str(row["currency"])
        if (
            baseline_at is not None
            and baseline_billing is not None
            and baseline_reported is not None
        ):
            baseline = ShadowSample(
                ts=baseline_at,
                currency=persisted_currency,
                billing_minor=int(baseline_billing),
                reported_minor=int(baseline_reported),
            )

        day_changed = row["cabinet_day_start"] != normalized_day_start
        currency_changed = persisted_currency != normalized.currency
        context_reset = day_changed or currency_changed
        existing = [] if context_reset else _decode_shadow_samples(row["samples"])
        if currency_changed:
            baseline = None
        elif day_changed and baseline is not None:
            # Reported spend is current-day while billing is lifetime.  Rebase
            # an active episode atomically at midnight instead of comparing values
            # across two cabinet days or falsely declaring recovery.
            baseline = normalized
        previous_sample = max(
            (item for item in existing if item.ts < normalized.ts),
            key=lambda item: item.ts,
            default=None,
        )
        by_timestamp = {item.ts: item for item in existing}
        by_timestamp[normalized.ts] = normalized
        ordered = sorted(by_timestamp.values(), key=lambda item: item.ts)
        newest = ordered[-1]
        cutoff = newest.ts - timedelta(seconds=SHADOW_EVIDENCE_MAX_AGE_SECONDS)
        ordered = [item for item in ordered if item.ts >= cutoff][-SHADOW_SAMPLE_MAX_LEN:]
        verdict = detect_shadow(
            ordered,
            window_seconds=SHADOW_WINDOW_SECONDS,
            billing_min_delta_minor=SHADOW_BILLING_MIN_DELTA_MINOR,
            reported_max_delta_minor=SHADOW_REPORTED_MAX_DELTA_MINOR,
        )

        if baseline is None and verdict is not None:
            baseline = next(item for item in ordered if item.ts == verdict.oldest_ts)

        candidate_at = None if context_reset else row["recovery_candidate_at"]
        recovery_confirmed = False
        if verdict is not None:
            candidate_at = None
        elif baseline is not None and _shadow_reporting_caught_up(baseline, newest):
            if candidate_at is not None and candidate_at < newest.ts:
                recovery_confirmed = True
            else:
                candidate_at = newest.ts
        else:
            candidate_at = None

        incident_event_committed = False
        incident_key = f"{SHADOW_INCIDENT_KEY_PREFIX}{account_id}"
        if currency_changed:
            incident_event_committed = await resolve_recurring_incident_in_transaction(
                conn,
                incident_key=incident_key,
                audience="all",
                summary=(
                    f"Кабинет {account_id}: валюта изменилась "
                    f"с {persisted_currency} на {normalized.currency}, "
                    "начинаю наблюдение заново."
                ),
            )
            baseline = None
            candidate_at = None
        elif verdict is not None:
            billing_amount = minor_units_to_major_amount(
                verdict.billing_delta_minor,
                currency=verdict.currency,
            )
            reported_amount = minor_units_to_major_amount(
                verdict.reported_delta_minor,
                currency=verdict.currency,
            )
            incident_event_committed = await notify_recurring_incident_in_transaction(
                conn,
                incident_key=incident_key,
                audience="all",
                event_type="meta_reporting_shadow",
                severity="critical",
                title=f"Кабинет тратит больше, чем видно в отчётах · {account_id}",
                summary=(
                    f"Списано +{billing_amount} {verdict.currency}, "
                    f"а в отчётах видно только +{reported_amount} {verdict.currency}"
                ),
                lines=["Проверь расход в Ads Manager вручную"],
                risk="Часть открута не видна скану, авто-стоп её не поймает",
                resource_type="ad_account",
                resource_id=account_id,
            )
        elif recovery_confirmed:
            incident_event_committed = await resolve_recurring_incident_in_transaction(
                conn,
                incident_key=incident_key,
                audience="all",
                summary=f"Кабинет {account_id}: отчёты догнали фактическое списание.",
            )
            baseline = None
            candidate_at = None

        await conn.execute(
            text(
                """
                UPDATE meta_shadow_spend_state
                SET samples = CAST(:samples AS jsonb),
                    currency = :currency,
                    cabinet_day_start = :cabinet_day_start,
                    incident_baseline_at = :baseline_at,
                    incident_baseline_billing_minor = :baseline_billing,
                    incident_baseline_reported_minor = :baseline_reported,
                    recovery_candidate_at = :candidate_at,
                    last_observed_at = GREATEST(last_observed_at, :last_observed_at),
                    updated_at = NOW()
                WHERE account_id = :account_id
                """
            ),
            {
                "account_id": account_id,
                "currency": normalized.currency,
                "samples": _encode_shadow_samples(ordered),
                "cabinet_day_start": normalized_day_start,
                "baseline_at": baseline.ts if baseline is not None else None,
                "baseline_billing": baseline.billing_minor if baseline is not None else None,
                "baseline_reported": baseline.reported_minor if baseline is not None else None,
                "candidate_at": candidate_at,
                "last_observed_at": newest.ts,
            },
        )
    return ShadowObservationDecision(
        previous_sample=previous_sample,
        verdict=verdict,
        recovery_confirmed=recovery_confirmed,
        incident_event_committed=incident_event_committed,
        currency_reset=currency_changed,
    )


async def _check_shadow_for_account(
    meta_client: Any,
    engine: AsyncEngine,
    *,
    account_id: str,
    currency: str,
    cabinet_day_start: datetime,
    now: datetime,
) -> bool:
    """Один тик сторожка для одного кабинета. Возвращает True, если алерт отправлен.

    Шаги: (а) биллинг act_{id}?fields=amount_spent → minor units; (б) пер-адная
    отчётность конвертируется по явному exponent валюты; (в) атомарно сохраняем evidence;
    (г) при вердикте открываем persisted recurring incident.
    """
    from core.dashboard.cabinet_spend import current_day_spend_for_account

    # (а) биллинг кабинета. Ошибка Graph-вызова → warning-лог, тик пропущен (probe и так
    # мониторит канал отдельно). LoginRequired/TokenInvalid НЕ дублируем алертом — зона probe.
    try:
        async with BrowserOperationFence(
            engine,
            operation_kind="shadow_billing_read",
            target=account_id,
        ) as fence:
            billing_minor = await _fetch_billing_minor(meta_client, account_id)
            await fence.assert_held()
    except BrowserOperationBlocked:
        logger.info(
            "shadow: Vision maintenance active — act_%s пропущен",
            account_id,
        )
        return False
    except BrowserFenceLeaseLost:
        logger.warning(
            "shadow: browser-operation fence lost — act_%s discarded",
            account_id,
        )
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("shadow: биллинг act_%s не получен: %s", account_id, exc)
        return False
    if billing_minor is None:
        logger.warning("shadow: биллинг act_%s пуст (нет amount_spent)", account_id)
        return False

    # (б) пер-адная отчётность текущих суток кабинета → exact minor units.
    try:
        reported_major = await current_day_spend_for_account(
            engine,
            account_id=account_id,
            currency=currency,
            cabinet_day_start=cabinet_day_start,
        )
        reported_minor = nonnegative_major_amount_to_minor_units(
            format(reported_major, "f"),
            currency=currency,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("shadow: пер-адная отчётность act_%s не посчитана: %s", account_id, exc)
        return False

    # (в) durable evidence. Если commit/read не удался, исключение поднимется к
    # per-account supervisor: неизвестность никогда не превращается в recovery.
    sample = ShadowSample(
        ts=now,
        currency=currency,
        billing_minor=billing_minor,
        reported_minor=reported_minor,
    )
    decision = await _record_shadow_observation(
        engine,
        account_id=account_id,
        sample=sample,
        cabinet_day_start=cabinet_day_start,
    )
    burst_activated = _has_unreported_billing_movement(decision.previous_sample, sample)
    if burst_activated:
        await _activate_shadow_burst(
            engine,
            account_id=account_id,
            sample=sample,
        )

    # (г) detector/recovery уже вычислены под row lock по committed evidence.
    verdict = decision.verdict
    if verdict is None:
        if decision.recovery_confirmed and decision.incident_event_committed:
            logger.info(
                "critical incident %s%s подтверждённо восстановлен",
                SHADOW_INCIDENT_KEY_PREFIX,
                account_id,
            )
        return False

    logger.error(
        "shadow: тень отчётности act_%s — billing +%s %s, reporting +%s %s за %dс",
        account_id,
        minor_units_to_major_amount(
            verdict.billing_delta_minor,
            currency=verdict.currency,
        ),
        verdict.currency,
        minor_units_to_major_amount(
            verdict.reported_delta_minor,
            currency=verdict.currency,
        ),
        verdict.currency,
        verdict.window_seconds,
    )
    # Полный CRITICAL обязан держать быстрый режим даже если reported сдвинулся на
    # допустимое малое движение и ранний exact-zero trigger на этом тике не сработал.
    if not burst_activated:
        await _activate_shadow_burst(
            engine,
            account_id=account_id,
            sample=sample,
        )
    return decision.incident_event_committed


async def check_shadow_spend(
    meta_client: Any,
    *,
    engine: AsyncEngine,
    now: datetime | None = None,
) -> bool:
    """Один прогон сторожка по всем активным кабинетам. Возвращает True, если был алерт.

    Гейт: работает только когда сканирование включено и cabinet_runtime подтверждает
    свежие snapshots. Graph-ошибки на конкретном кабинете не валят остальные.
    """
    now = now or await _database_now(engine)

    from core.meta_api.account_tz import (
        resolve_account_currencies,
        resolve_cabinet_days,
    )
    from core.observer.accounts import resolve_configured_ad_account_ids

    account_ids = await resolve_configured_ad_account_ids(engine)
    if not account_ids:
        return False
    if not await _is_reported_side_live(engine, account_ids=account_ids, now=now):
        logger.info("shadow: сканирование выключено/observer стоит — тик пропущен")
        return False
    cabinet_days = await resolve_cabinet_days(engine, account_ids=account_ids, now=now)
    currencies = await resolve_account_currencies(
        engine,
        account_ids=account_ids,
        now=now,
    )

    alerted = False
    for account_id in account_ids:
        canonical_account_id = str(account_id).removeprefix("act_")
        currency = currencies.currencies.get(canonical_account_id)
        currency_issue: str | None = None
        if currency is None:
            currency_issue = "Facebook не подтвердил валюту кабинета"
        else:
            try:
                campaign_currency_exponent(currency)
            except UnsupportedCampaignCurrencyError:
                currency_issue = f"Для валюты {currency} мы не знаем точность округления"
        currency_incident_key = f"{SHADOW_CURRENCY_INCIDENT_KEY_PREFIX}{canonical_account_id}"
        if currency_issue is not None:
            currency_alerted = await notify_recurring_incident(
                engine,
                incident_key=currency_incident_key,
                audience="all",
                event_type="meta_reporting_shadow_currency_unknown",
                severity="critical",
                title=f"Не могу сверить расход кабинета {canonical_account_id}",
                summary=currency_issue,
                lines=[
                    "Суммы не показываю: без валюты это просто числа",
                    "Открой кабинет в Ads Manager, чтобы бот обновил его данные",
                ],
                risk="Расхождение между списанием и отчётами останется незамеченным",
                resource_type="ad_account",
                resource_id=canonical_account_id,
            )
            alerted = alerted or currency_alerted
            continue
        await resolve_recurring_incident(
            engine,
            incident_key=currency_incident_key,
            audience="all",
            summary=(f"Кабинет {canonical_account_id}: валюта {currency} снова подтверждена."),
        )
        if canonical_account_id in cabinet_days.missing_account_ids:
            logger.warning(
                "shadow: act_%s пропущен — IANA timezone не подтверждена в PostgreSQL",
                canonical_account_id,
            )
            continue
        try:
            sent = await _check_shadow_for_account(
                meta_client,
                engine,
                account_id=canonical_account_id,
                currency=currency,
                cabinet_day_start=cabinet_days.query_boundaries[canonical_account_id],
                now=now,
            )
            alerted = alerted or sent
        except Exception:  # noqa: BLE001
            logger.exception("shadow: тик кабинета act_%s упал", account_id)
    return alerted


async def run_one_check(*, engine: AsyncEngine) -> None:
    """Run the durable auto-stop channel check once."""
    await check_autostop_channel(engine)


# ====================== loops ======================


async def metrics_loop(stop: asyncio.Event, engine: AsyncEngine) -> None:
    """Refresh process metrics without publishing a second liveness authority."""
    interval = 15.0
    while not stop.is_set():
        mark_worker_heartbeat(WORKER_NAME)
        await record_worker_heartbeat(engine, WORKER_NAME)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def browser_readiness_loop(
    meta_client: _MetaProbeClient,
    *,
    stop: asyncio.Event,
    engine: AsyncEngine,
    interval: float = BROWSER_READINESS_INTERVAL_SECONDS,
    ttl_seconds: int = BROWSER_READINESS_TTL_SECONDS,
) -> None:
    """Continuously publish bounded v5/profile evidence for task scheduling."""
    # Публикуем только смену состояния: цикл идёт раз в 2 секунды, и лог на
    # каждом тике утопил бы остальные записи. Причина недоступности видна в
    # browser_channel_readiness.reason_code и в снимке оператора.
    last_published: bool | None = None
    while not stop.is_set():
        published_ready = await probe_and_publish_browser_readiness(
            engine,
            meta_client,
            writer_instance=_BROWSER_READINESS_WRITER_INSTANCE,
            ttl_seconds=ttl_seconds,
        )
        if published_ready != last_published:
            logger.info(
                "browser readiness: %s",
                "ready" if published_ready else "not ready",
            )
            last_published = published_ready
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.1, interval))
        except asyncio.TimeoutError:
            pass


async def prepare_browser_workspace(
    engine: AsyncEngine,
    *,
    open_cabinet_tabs: Callable[[list[str]], Awaitable[list[dict[str, Any]]]],
) -> None:
    """Одна попытка подготовить рабочее место: вкладка Ads Manager каждому кабинету.

    Кто открывает вкладку кабинета при выключенном сканировании — watchdog.
    Observer делает это в своей фазе подготовки, но его цикл живёт задачами
    `observer_scan`, а на паузе они не публикуются вовсе. Watchdog же и так
    единственный владелец браузерного канала, не зависящий от тумблера: он
    присоединяет сессию профиля и он же публикует готовность. Тот, кто видит
    «вкладки нет», её и открывает — без передачи сигнала между воркерами.

    Подготовка идёт ТОЛЬКО когда наблюдение говорит, что рабочего места нет: на
    подтверждённом канале цикл не трогает браузер вообще. Отказ по кабинету не
    превращается в цикл вкладок — следующая попытка не раньше следующего тика.
    """
    from core.deadlines import bind_absolute_deadline
    from core.meta_api.browser_readiness import browser_channel_ready_now
    from core.observer.accounts import resolve_configured_ad_account_ids

    if await browser_channel_ready_now(engine):
        return
    accounts = await resolve_configured_ad_account_ids(engine)
    if not accounts:
        # Кабинета нет ни в одном активном оффере: открывать наугад нечего.
        return

    # Дедлайн той же формы, что у близнеца в observer. Он нужен не ради скорости:
    # живая строка в browser_operation_leases считается активной работой для
    # drain обслуживания, поэтому неограниченная подготовка уронила бы по
    # таймауту операторский ensure-cdp ровно тогда, когда канал сломан.
    prepare_deadline_seconds = max(60, 20 * len(accounts))
    prepare_deadline_at = datetime.now(UTC) + timedelta(seconds=prepare_deadline_seconds)
    try:
        async with BrowserOperationFence(
            engine,
            operation_kind="browser_workspace_prepare",
            target="cabinet_tabs",
        ) as fence:
            with bind_absolute_deadline(prepare_deadline_at):
                async with asyncio.timeout(prepare_deadline_seconds):
                    results = await open_cabinet_tabs(list(accounts))
            await fence.assert_held()
    except BrowserOperationBlocked:
        logger.info("workspace: Vision maintenance active — подготовка отложена")
        return
    except BrowserFenceLeaseLost:
        logger.warning("workspace: browser-operation fence lost — подготовка отброшена")
        return
    except TimeoutError:
        logger.warning(
            "workspace: подготовка не уложилась в %dс — попытка брошена",
            prepare_deadline_seconds,
        )
        return
    except Exception as exc:  # noqa: BLE001 — класс отказа браузера/gRPC не ограничен
        # Причина называется безопасным диагностиком и повторяется следующим
        # тиком: молчаливая тишина здесь неотличима от мёртвого воркера.
        logger.warning(
            "workspace: вкладки кабинетов открыть не удалось (%s)",
            safe_exception_diagnostic(exc),
        )
        return

    result_by_account = {str(r.get("ad_account_id") or "").removeprefix("act_"): r for r in results}
    confirmed_ids: list[str] = []
    failed_ids: list[str] = []
    for account_id in accounts:
        result = result_by_account.get(account_id) or {
            "ad_account_id": account_id,
            "opened": False,
        }
        is_confirmed = bool(result.get("opened"))
        (confirmed_ids if is_confirmed else failed_ids).append(account_id)

    logger.info(
        "workspace: подготовка рабочего места — подтверждено %d из %d кабинетов",
        len(confirmed_ids),
        len(accounts),
    )

    # per-cabinet инциденты поднимаются только при частичном отказе: если ни один
    # кабинет не открылся, канал целиком недоступен и общий инцидент достаточен.
    if confirmed_ids:
        for account_id in confirmed_ids:
            await sync_cabinet_tab_incident(engine, account_id=account_id, confirmed=True)
        for account_id in failed_ids:
            await sync_cabinet_tab_incident(engine, account_id=account_id, confirmed=False)


async def browser_workspace_loop(
    *,
    stop: asyncio.Event,
    engine: AsyncEngine,
    open_cabinet_tabs: Callable[[list[str]], Awaitable[list[dict[str, Any]]]],
    interval: float = BROWSER_WORKSPACE_INTERVAL_SECONDS,
) -> None:
    """Явная подготовка рабочего места, не зависящая от тумблера сканирования.

    Перед первой попыткой выжидает общий startup grace: browser-agent и Vision
    поднимаются дольше воркеров, и без паузы каждый рестарт начинался бы с
    предупреждения о недоступном браузере, которое ничего не означает.
    """
    try:
        await asyncio.wait_for(stop.wait(), timeout=STARTUP_GRACE_SECONDS)
    except asyncio.TimeoutError:
        pass

    while not stop.is_set():
        await prepare_browser_workspace(engine, open_cabinet_tabs=open_cabinet_tabs)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def check_loop(
    *,
    stop: asyncio.Event,
    engine: AsyncEngine,
) -> None:
    """Главный цикл проверок раз в CHECK_INTERVAL_SECONDS.

    Перед первой проверкой выжидает STARTUP_GRACE_SECONDS, чтобы дать control plane
    завершить совместный старт.
    """
    # Grace при старте, прерываемый shutdown'ом.
    try:
        await asyncio.wait_for(stop.wait(), timeout=STARTUP_GRACE_SECONDS)
    except asyncio.TimeoutError:
        pass

    while not stop.is_set():
        try:
            await run_one_check(engine=engine)
        except Exception:  # noqa: BLE001
            logger.exception("ошибка в цикле проверок")
        # run_one_check уже поймал и залогировал свою ошибку — дойти досюда
        # значит цикл проверок жив и реально трогает БД (issue #176).
        await record_worker_heartbeat(engine, WORKER_NAME, poll_success=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=CHECK_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def meta_probe_loop(
    meta_client: _MetaProbeClient,
    *,
    stop: asyncio.Event,
    engine: AsyncEngine,
    interval: int = META_PROBE_INTERVAL_SECONDS,
    reattach_session: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Цикл сетевого probe канала Marketing API раз в ``interval`` секунд.

    Отдельная (более редкая) каденция от check_loop: реальный fetch к Meta не должен
    выполняться слишком часто. Перед первой проверкой выжидает STARTUP_GRACE_SECONDS
    (browser-agent/Vision стартуют дольше). Best-effort: ошибки не валят цикл.
    """
    try:
        await asyncio.wait_for(stop.wait(), timeout=STARTUP_GRACE_SECONDS)
    except asyncio.TimeoutError:
        pass

    while not stop.is_set():
        try:
            await check_meta_api_channel(
                meta_client, engine=engine, reattach_session=reattach_session
            )
        except Exception:  # noqa: BLE001
            logger.exception("ошибка в meta_probe_loop")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def shadow_spend_loop(
    meta_client: Any,
    *,
    stop: asyncio.Event,
    engine: AsyncEngine,
    interval: int = SHADOW_SPEND_INTERVAL_SECONDS,
) -> None:
    """Цикл сторожка «тени отчётности Meta» раз в ``interval`` секунд (alert-only).

    Ловит money-класс перекрута: биллинг кабинета (amount_spent) растёт, а пер-адная
    отчётность (am_tabular → current_day_spend) стоит → реальный открут не виден скану.
    Гейт check_shadow_spend работает только при включённом сканировании и свежем
    cabinet_runtime snapshot. Перед первой проверкой выжидает STARTUP_GRACE_SECONDS.
    Best-effort: ошибки не валят цикл.
    """
    try:
        await asyncio.wait_for(stop.wait(), timeout=STARTUP_GRACE_SECONDS)
    except asyncio.TimeoutError:
        pass

    while not stop.is_set():
        try:
            await check_shadow_spend(meta_client, engine=engine)
        except Exception:  # noqa: BLE001
            logger.exception("ошибка в shadow_spend_loop")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def vision_token_refresh_loop(
    *,
    stop: asyncio.Event,
    engine: AsyncEngine,
    vision_cloud_url: str,
    interval: int = VISION_TOKEN_REFRESH_INTERVAL_SECONDS,
) -> None:
    """Check the canonical Vision token once per day inside the watchdog."""
    if interval <= 0:
        raise RuntimeError("Vision token refresh interval must be positive")
    try:
        await asyncio.wait_for(stop.wait(), timeout=STARTUP_GRACE_SECONDS)
    except asyncio.TimeoutError:
        pass

    while not stop.is_set():
        try:
            await refresh_vision_token_if_needed(
                engine,
                vision_cloud_url=vision_cloud_url,
            )
        except Exception as exc:  # noqa: BLE001 - supervisor must retain the daily loop
            # Не печатаем exception: внешние client errors могут удерживать
            # request с X-Token. Ожидаемые cloud-ошибки сам модуль уже превращает
            # в дедуплицируемый incident; здесь остаётся безопасный fallback.
            logger.error(
                "vision token refresh tick failed (error_type=%s)",
                type(exc).__name__,
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def _supervised(
    name: str,
    factory: Any,
    stop: asyncio.Event,
) -> None:
    """Перезапускает упавший цикл вместо тихой смерти (инцидент 01.07).

    main_loop раньше собирал циклы голым asyncio.gather: одно исключение гасило
    весь воркер-сторож, а «сторожа за сторожем» нет — молчание длилось часами.
    factory — zero-arg callable, возвращающий корутину цикла; цикл сам крутится
    до stop. Исключение → лог + пауза LOOP_RESTART_DELAY_SECONDS + новый запуск.
    """
    while not stop.is_set():
        try:
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception(
                "цикл %s упал — перезапуск через %sс", name, LOOP_RESTART_DELAY_SECONDS
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=LOOP_RESTART_DELAY_SECONDS)
            except asyncio.TimeoutError:
                pass


# ====================== entrypoint ======================


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


def _get_vision_cloud_url() -> str:
    from core.config import get_settings

    return get_settings().vision_cloud_url


async def main_loop(database_url: str | None = None) -> None:
    from core.meta_api.client import MetaApiClient

    db_url = database_url or _get_database_url()
    vision_cloud_url = _get_vision_cloud_url()
    engine = create_async_engine(db_url, **WORKER_ENGINE_KWARGS)

    # MetaApiClient для сетевого probe канала auto-stop (eager-init: gRPC-канал ленивый,
    # старт не блокирует; недоступность browser-agent probe-цикл трактует как «канал мёртв»).
    meta_client = MetaApiClient(
        host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
        port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
    )
    await meta_client.start()

    async def new_browser_agent_client():
        """Клиент browser-agent на одну попытку.

        Держать второй постоянный gRPC-канал ради редкого события незачем, а
        реквизиты Vision читаются каждый раз — они меняются при обновлении
        токена.
        """
        from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
        from core.config import get_settings
        from core.vision_runtime import load_vision_runtime_config

        settings = get_settings()
        vision = await load_vision_runtime_config(engine)
        client = BrowserAgentClient(
            BrowserAgentConfig(
                vision_x_token=vision.x_token,
                vision_api_url=settings.vision_api_url,
                vision_profile_id=vision.profile_id,
                vision_folder_id=vision.folder_id,
                grpc_host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
                grpc_port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
            )
        )
        await client.start()
        return client

    async def reattach_browser_session() -> None:
        """Присоединить процесс-локальную сессию к уже живому Vision-профилю."""
        client = await new_browser_agent_client()
        try:
            await client.start_browser()
        finally:
            # asyncio.shield изолирует задачу закрытия от внешней отмены: если
            # корутина отменяется (например, вторым cancel() во время finally),
            # inner-task close() доживает до конца независимо от статуса outer-task.
            close_task = asyncio.ensure_future(client.close())
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                await close_task
                raise

    async def open_cabinet_tabs(ad_account_ids: list[str]) -> list[dict[str, Any]]:
        """Открыть вкладку Ads Manager каждому настроенному кабинету.

        Тот же RPC, которым observer готовит рабочее место перед сканом:
        идемпотентный, per-cabinet, отказ одного кабинета не валит остальные.
        Сессию профиля вызов поднимает сам, если её не осталось.
        """
        client = await new_browser_agent_client()
        try:
            return await client.open_cabinet_tabs(ad_account_ids)
        finally:
            close_task = asyncio.ensure_future(client.close())
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                await close_task
                raise

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, sig_name), stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    logger.info("health_watchdog запущен (interval=%ss)", CHECK_INTERVAL_SECONDS)
    try:
        # Каждый цикл под _supervised + return_exceptions: упавший цикл
        # перезапускается, а не гасит воркер молча (инцидент 01.07).
        loops = [
            _supervised("metrics_loop", lambda: metrics_loop(stop, engine), stop),
            _supervised(
                "browser_readiness_loop",
                lambda: browser_readiness_loop(
                    meta_client,
                    stop=stop,
                    engine=engine,
                ),
                stop,
            ),
            _supervised(
                "browser_workspace_loop",
                lambda: browser_workspace_loop(
                    stop=stop,
                    engine=engine,
                    open_cabinet_tabs=open_cabinet_tabs,
                ),
                stop,
            ),
            _supervised(
                "check_loop",
                lambda: check_loop(
                    stop=stop,
                    engine=engine,
                ),
                stop,
            ),
            _supervised(
                "meta_probe_loop",
                lambda: meta_probe_loop(
                    meta_client,
                    stop=stop,
                    engine=engine,
                    reattach_session=reattach_browser_session,
                ),
                stop,
            ),
            _supervised(
                "vision_token_refresh_loop",
                lambda: vision_token_refresh_loop(
                    stop=stop,
                    engine=engine,
                    vision_cloud_url=vision_cloud_url,
                ),
                stop,
            ),
        ]
        # Сторожок «тени отчётности» — alert-only, включён по умолчанию (выключатель SHADOW_SPEND_WATCH_ENABLED).
        if SHADOW_SPEND_WATCH_ENABLED:
            loops.append(
                _supervised(
                    "shadow_spend_loop",
                    lambda: shadow_spend_loop(
                        meta_client,
                        stop=stop,
                        engine=engine,
                    ),
                    stop,
                )
            )
        else:
            logger.info("shadow_spend_loop выключен (SHADOW_SPEND_WATCH_ENABLED=false)")
        await asyncio.gather(*loops, return_exceptions=True)
    finally:
        try:
            await meta_client.close()
        except Exception:  # noqa: BLE001
            logger.exception("ошибка закрытия MetaApiClient")
        await engine.dispose()
        logger.info("health_watchdog остановлен")
