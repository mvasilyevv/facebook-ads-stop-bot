# -*- coding: utf-8 -*-
"""Observer worker main loop.

Цикл:
1. load_observer_config + check is_scanning_enabled
2. INSERT в scan_runs → scan_id
3. gate.run_scan_cycle() → ScanResult.rows
4. process_scan_rows(...) → метрики/FSM/outbox
5. UPDATE scan_runs финальным результатом
6. Sleep до следующего старта по адаптивному периоду (база = interval_seconds = CALM,
   дефолт 30с): stop/теневой spend→CRITICAL 10с, warning→ELEVATED 15с,
   офферные ads→CALM 30с, пусто→IDLE 45с. Длительность цикла вычитается из периода;
   jitter ±10%. См. core/observer/adaptive_interval.py.

Gate инжектируется (паттерн как у toggle_workers): в проде это BrowserAgentClient,
в тестах — fake который возвращает заранее подготовленные ScannedAdRow.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from core.config import get_settings
from core.db import WORKER_ENGINE_KWARGS
from core.meta_api.account_tz import (
    CabinetCurrencyUnknownError,
    CabinetTimezoneUnknownError,
)
from core.meta_api.identity import require_ad_account_id
from core.observer.accounts import (
    allowlist_blocks_scan,
    list_offers_without_accounts,
    resolve_scan_account_ids,
)
from core.observer.adaptive_interval import (
    DEFAULT_BASE_INTERVAL_SECONDS,
    JITTER_FRACTION,
    clamp_interval,
    compute_adaptive_interval,
    compute_remaining_sleep,
    resolve_scan_mode,
)
from core.observer.cabinet_supervisor import (
    CabinetLease,
    CabinetSupervisor,
    assert_cabinet_lease,
    publish_next_scan_at,
)
from core.observer.pipeline import CycleResult, process_scan_rows
from core.observer.queries import (
    load_observer_config,
    multi_cabinet_requires_owner_tag,
)
from core.observer.scan_tasks import (
    OBSERVER_SCAN_POLL_SECONDS,
    ObserverScanCancelled,
    ObserverScanFenceLost,
    claim_observer_scan,
    enqueue_scheduled_observer_scan,
    run_with_observer_scan_control,
)
from core.scanner.identity import find_incomplete_scan_row_ids
from core.scanner.models import (
    SCANNER_METRICS_CONTRACT_REVISION,
    ScannedAdRow,
)
from core.tasks.queue import Task, mark_cancelled, mark_failed, mark_succeeded
from core.telegram.worker_notify import (
    notify_recurring_incident,
    resolve_recurring_incident,
)
from core.wording import times_ru
from core.worker_metrics import SNAPSHOT_AGE, mark_worker_heartbeat, start_worker_metrics_server

logger = logging.getLogger(__name__)

_OBSERVER_INSTANCE_ID = uuid.uuid4()
_CABINET_SCAN_CONCURRENCY = max(1, int(os.environ.get("OBSERVER_CABINET_CONCURRENCY", "2")))
_CABINET_SCAN_DEADLINE_SECONDS = max(
    30,
    int(os.environ.get("OBSERVER_SCAN_DEADLINE_SECONDS", "120")),
)


def _get_database_url() -> str:
    """Resolve the observer database URL without importing legacy TG runtime."""
    return get_settings().database_url


WORKER_NAME = "observer"

# Layer 3 — алерт о «тихой» деградации: процесс жив, но сканы
# стабильно падают и self-heal (Layer 1/2) не помог. Без него мониторинг был слеп ~104 минуты.
DEGRADED_ALERT_THRESHOLD = int(os.environ.get("OBSERVER_DEGRADED_ALERT_THRESHOLD", "3"))
OBSERVER_DEGRADED_INCIDENT_KEY = "observer:degraded"

# MID X-16 (аудит 02.07): разлогин/чекпоинт Vision-профиля. browser-agent детектит
# redirect на login.php/checkpoint, HTML вместо JSON или Graph 190 с login-subcode и
# отдаёт empty_reason='login_required'. Money-критично: скан слеп, авто-стоп не работает —
# owner должен получить ЯВНЫЙ алерт «нужен ре-логин», а не тихий пустой скан.
OBSERVER_LOGIN_REQUIRED_INCIDENT_PREFIX = "observer:login_required:"
OBSERVER_TIMEZONE_UNKNOWN_INCIDENT_PREFIX = "observer:cabinet_timezone_unknown:"
OBSERVER_CURRENCY_UNKNOWN_INCIDENT_PREFIX = "observer:cabinet_currency_unknown:"
OBSERVER_OFFER_CURRENCY_INCIDENT_PREFIX = "observer:offer_currency_mismatch:"
OBSERVER_CABINET_TAB_UNAVAILABLE_INCIDENT_PREFIX = "observer:cabinet_tab_unavailable:"

# Money-гард R4: мульти-каб (>1 кабинета) без owner_tag → скан остановлен ради безопасности
# (иначе авто-стоп чужой рекламы в shared-кабинете).
OBSERVER_MULTI_CAB_UNSAFE_INCIDENT_KEY = "observer:multi_cabinet_unsafe"


@dataclass
class ScanCycleOutput:
    """То что вернул scanner gate за один цикл."""

    rows: list[ScannedAdRow]
    metrics_contract_revision: int
    total_passes: int = 0
    duration_seconds: float = 0.0
    empty_reason: str | None = None
    warnings: list[str] | None = None
    partial_row_ids: list[str] | None = None
    rows_with_all_metrics_empty: int = 0


class ScannerGate(Protocol):
    """Минимальный интерфейс gRPC-клиента observer'а.

    Прод-реализация — обёртка над BrowserAgentClient.run_scan_cycle().
    Тест-реализация — заранее заготовленный список ScannedAdRow.
    """

    async def run_one_scan(
        self,
        ad_account_id: str,
        campaign_ids: list[str] | None = None,
        owner_tag: str | None = None,
    ) -> ScanCycleOutput:
        """Делает один scan-цикл (am_tabular) и возвращает строки + метаданные.

        campaign_ids — allowlist кампаний (#3). owner_tag — am-резолв campaign.id по тегу
        (тянуть сразу свой скоуп, не весь кабинет).
        ad_account_id — обязательный кабинет скана. Ошибка сканера → исключение.
        """
        ...

    async def open_cabinet_tabs(self, ad_account_ids: list[str]) -> list[dict]:
        """Фаза подготовки: открыть вкладки Ads Manager для кабинетов перед сканом.

        Идемпотентно (уже открытая вкладка переиспользуется). Возвращает per-cabinet
        результаты [{ad_account_id, opened, url, error}]. Не бросает на ошибке одного
        кабинета (агрегирует в результат).
        """
        ...


# ====================== Scan_runs writers ======================


async def _begin_scan_run(engine: AsyncEngine, *, ad_account_id: str) -> int:
    """INSERT в partitioned scan_runs → возвращаем монотонный id.

    Атомарный: scan_id = id за один INSERT через CTE с явным nextval.
    Никакого последующего UPDATE — если процесс крашится до RETURNING, sequence
    откатится вместе с транзакцией и осиротевшего scan_id не возникнет.

    ad_account_id — явный кабинет скана; пустое значение отклоняется до INSERT.
    """
    account_id = require_ad_account_id(ad_account_id)
    started_at = datetime.now(timezone.utc)
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    WITH next_id AS (SELECT nextval('scan_runs_id_seq') AS sid)
                    INSERT INTO scan_runs (id, scan_id, started_at, ad_account_id)
                    SELECT sid, sid, :sa, :acct FROM next_id
                    RETURNING id
                    """
                ),
                {"sa": started_at, "acct": account_id},
            )
        ).first()
    return int(row[0])


async def _finish_scan_run(
    engine: AsyncEngine,
    *,
    scan_id: int,
    outcome: str,
    cycle_result: CycleResult | None,
    error_message: str | None = None,
    duration_ms: int = 0,
) -> None:
    """UPDATE финальным результатом цикла."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE scan_runs
                SET finished_at = NOW(),
                    outcome = :oc,
                    rows_total = :rt,
                    alerts_warning = :aw,
                    alerts_stop = :as,
                    error_message = :em,
                    duration_ms = :dm
                WHERE id = :id
                """
            ),
            {
                "id": scan_id,
                "oc": outcome,
                "rt": cycle_result.rows_total if cycle_result else 0,
                "aw": cycle_result.alerts_warning if cycle_result else 0,
                "as": cycle_result.alerts_stop if cycle_result else 0,
                "em": error_message,
                "dm": duration_ms,
            },
        )


async def metrics_loop(
    stop: asyncio.Event,
    *,
    snapshot_age_provider: Callable[[], float | None] | None = None,
) -> None:
    """Refresh process-local Prometheus liveness and snapshot age."""
    interval = 15.0
    while not stop.is_set():
        mark_worker_heartbeat(WORKER_NAME)
        snapshot_age = None if snapshot_age_provider is None else snapshot_age_provider()
        SNAPSHOT_AGE.labels(source="observer_ads").set(
            float("inf") if snapshot_age is None else max(0.0, snapshot_age)
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


# ====================== One cycle ======================


_AUTHENTICATED_EMPTY_REASONS = frozenset({"no_active_ads", "filter_excludes_all"})


def _scan_confirms_authenticated_session(scan_out: ScanCycleOutput) -> bool:
    """Return true only for a live response that proves the profile is logged in."""
    return scan_out.metrics_contract_revision == SCANNER_METRICS_CONTRACT_REVISION and (
        bool(scan_out.rows) or (scan_out.empty_reason or "") in _AUTHENTICATED_EMPTY_REASONS
    )


async def _run_account_scan(
    engine: AsyncEngine,
    *,
    gate: ScannerGate,
    config: dict,
    ad_account_id: str,
    accounts_total: int | None = None,
    cabinet_lease: CabinetLease | None = None,
) -> dict:
    """Скан одного явно выбранного кабинета.

    Свой scan_run, свой process_scan_rows, свой TG-dispatch. Не бросает исключения
    наверх — ошибки пишутся в scan_runs.outcome (цикл по кабинетам продолжается).
    """
    scan_id = await _begin_scan_run(engine, ad_account_id=ad_account_id)
    started_monotonic = time.monotonic()

    cycle_result: CycleResult | None = None
    outcome = "success"
    error_msg: str | None = None
    dispatched: dict | None = None

    # Allowlist кампаний (observer_config.campaign_ids) — ГЛОБАЛЬНЫЙ. campaign.id уникальны
    # per кабинет → при НЕСКОЛЬКИХ кабинетах в чужом фильтр отсёк бы ВСЁ (скан пуст →
    # слепота, FSM не реагирует). Поэтому allowlist применяем, когда в scan set один кабинет
    # (его кампании = allowlist, фильтр безопасен); при мульти-кабе — скоупинг через owner_tag.
    single_cabinet = (accounts_total or 1) <= 1
    campaign_ids = list(config.get("campaign_ids") or []) if single_cabinet else []

    try:
        # Opt-in мониторинг: при ОДНОМ кабинете пустой allowlist = НИЧЕГО не отслеживаем
        # (раньше пусто = «все мои кампании»). Скан не гоняем — отдаём пустой результат,
        # FSM не трогается, авто-стоп по этим объявлениям не работает (так и задумано).
        live_scan_performed = False
        if allowlist_blocks_scan(single_cabinet, campaign_ids):
            scan_out = ScanCycleOutput(
                rows=[],
                metrics_contract_revision=0,
                empty_reason="ничего не отслеживается (allowlist пуст)",
            )
        else:
            live_scan_performed = True
            scan_out = await gate.run_one_scan(
                campaign_ids=campaign_ids,
                owner_tag=config.get("owner_campaign_tag"),
                ad_account_id=ad_account_id,
            )

        if live_scan_performed and _scan_confirms_authenticated_session(scan_out):
            confirmed_account_id = require_ad_account_id(ad_account_id)
            await resolve_recurring_incident(
                engine,
                incident_key=(f"{OBSERVER_LOGIN_REQUIRED_INCIDENT_PREFIX}{confirmed_account_id}"),
                audience="all",
                summary=(f"Кабинет {confirmed_account_id}: вход в Facebook подтверждён."),
            )

        scan_issues = list(scan_out.warnings or [])
        if (
            live_scan_performed
            and scan_out.metrics_contract_revision != SCANNER_METRICS_CONTRACT_REVISION
        ):
            scan_issues.append(f"metrics_contract_revision:{scan_out.metrics_contract_revision}")
        # Revalidate producer output locally as a deployment-skew guard. A
        # malformed identity/hierarchy skips the whole cabinet snapshot before
        # any catalog, metric, FSM, incident or money-task write.
        partial_row_ids = list(
            dict.fromkeys(
                [
                    *(scan_out.partial_row_ids or []),
                    *find_incomplete_scan_row_ids(scan_out.rows),
                ]
            )
        )
        scan_out.partial_row_ids = partial_row_ids
        if partial_row_ids:
            scan_issues.append(f"partial_rows:{len(partial_row_ids)}")
        if scan_out.rows_with_all_metrics_empty:
            scan_issues.append(f"all_metrics_empty:{scan_out.rows_with_all_metrics_empty}")

        if not scan_out.rows:
            # Разлогин/чекпоинт Vision-профиля (money-критично): browser-agent отдал
            # empty_reason='login_required'. Это НЕ «пустой кабинет» (нет активной рекламы) —
            # это слепота канала: скан ничего не видит, авто-стоп не работает. Классифицируем
            # как error (→ resolve_scan_mode=CALM, degraded-счётчик, а не тихий IDLE) и шлём
            # recurring incident «нужен ре-логин». Проверяем ДО обычной empty-ветки.
            if (scan_out.empty_reason or "") == "login_required":
                outcome = "error"
                error_msg = "login_required"
                await _maybe_alert_login_required(engine, ad_account_id=ad_account_id)
            elif scan_issues:
                outcome = "partial"
                error_msg = ";".join(scan_issues)[:8000]
            elif (scan_out.empty_reason or "") in {
                "no_active_ads",
                "filter_excludes_all",
                "ничего не отслеживается (allowlist пуст)",
            }:
                outcome = "empty"
                error_msg = scan_out.empty_reason or "no rows"
            else:
                # An unexplained empty response is not a confirmed zero. It can
                # be a lost final gRPC message or incomplete Meta response.
                outcome = "partial"
                error_msg = scan_out.empty_reason or "unclassified_empty_scan"
        elif scan_issues:
            # Incomplete rows must never drive FSM or money decisions. A later
            # complete scan will persist the snapshot and resume evaluation.
            outcome = "partial"
            error_msg = ";".join(scan_issues)[:8000]
        else:
            if cabinet_lease is not None and not await assert_cabinet_lease(
                engine,
                cabinet_lease,
            ):
                raise RuntimeError("cabinet_lease_lost_before_scan_commit")
            cycle_result = await process_scan_rows(
                engine,
                rows=scan_out.rows,
                scan_id=scan_id,
                owner_tag=config.get("owner_campaign_tag"),
                ad_account_id=ad_account_id,
                **({"cabinet_lease": cabinet_lease} if cabinet_lease is not None else {}),
            )
            await resolve_recurring_incident(
                engine,
                incident_key=(f"{OBSERVER_TIMEZONE_UNKNOWN_INCIDENT_PREFIX}{ad_account_id}"),
                audience="all",
                summary=f"Кабинет {ad_account_id}: часовой пояс подтверждён.",
            )
            await resolve_recurring_incident(
                engine,
                incident_key=(f"{OBSERVER_CURRENCY_UNKNOWN_INCIDENT_PREFIX}{ad_account_id}"),
                audience="all",
                summary=f"Кабинет {ad_account_id}: валюта Meta подтверждена.",
            )
            if cycle_result.currency_mismatch_offers:
                mismatches = ", ".join(cycle_result.currency_mismatch_offers[:3])
                await notify_recurring_incident(
                    engine,
                    incident_key=(f"{OBSERVER_OFFER_CURRENCY_INCIDENT_PREFIX}{ad_account_id}"),
                    audience="all",
                    event_type="observer_offer_currency_mismatch",
                    severity="critical",
                    title="Авто-стоп остановлен: валюта правила не совпадает",
                    summary=f"Кабинет {ad_account_id} · офферы: {mismatches}",
                    risk="Пороги считать не по чему: CPA и расход в разных валютах",
                    lines=[
                        "Поставь офферу валюту кабинета — пересчёт курсом мы не делаем",
                        "Пока валюты разные, объявления не отключаются автоматически",
                    ],
                    resource_type="ad_account",
                    resource_id=ad_account_id,
                )
            else:
                await resolve_recurring_incident(
                    engine,
                    incident_key=(f"{OBSERVER_OFFER_CURRENCY_INCIDENT_PREFIX}{ad_account_id}"),
                    audience="all",
                    summary=(f"Кабинет {ad_account_id}: валюты Meta и CPA-правил совпадают."),
                )

            if cycle_result.row_errors:
                outcome = "partial"
                error_msg = f"row_processing_errors:{len(cycle_result.row_errors)}"

            # Alert events and recipient deliveries are committed by
            # core.observer.writers in the same transaction as the FSM change.
            # The observer never performs Telegram network I/O.
            if cycle_result.alerts_warning + cycle_result.alerts_stop > 0:
                dispatched = {
                    "outbox_events": cycle_result.alerts_warning + cycle_result.alerts_stop
                }
    except CabinetTimezoneUnknownError as exc:
        logger.critical("observer cabinet timezone is unknown: %s", exc)
        outcome = "error"
        error_msg = "cabinet_timezone_unknown"
        await notify_recurring_incident(
            engine,
            incident_key=f"{OBSERVER_TIMEZONE_UNKNOWN_INCIDENT_PREFIX}{ad_account_id}",
            audience="all",
            event_type="observer_cabinet_timezone_unknown",
            severity="critical",
            title="Авто-стоп остановлен: неизвестен часовой пояс кабинета",
            summary=f"Кабинет {ad_account_id} · Facebook не отдал часовой пояс",
            risk="Не понять, где граница суток, а значит и дневные пороги",
            lines=[
                "Открой кабинет в Ads Manager, чтобы бот обновил его данные",
                "Пока пояс неизвестен, объявления не отключаются автоматически",
            ],
            resource_type="ad_account",
            resource_id=ad_account_id,
        )
    except CabinetCurrencyUnknownError as exc:
        logger.critical("observer cabinet currency is unknown: %s", exc)
        outcome = "error"
        error_msg = "cabinet_currency_unknown"
        await notify_recurring_incident(
            engine,
            incident_key=f"{OBSERVER_CURRENCY_UNKNOWN_INCIDENT_PREFIX}{ad_account_id}",
            audience="all",
            event_type="observer_cabinet_currency_unknown",
            severity="critical",
            title="Авто-стоп остановлен: неизвестна валюта кабинета",
            summary=f"Кабинет {ad_account_id} · Facebook не подтвердил валюту",
            risk="Сумму без валюты нельзя сравнить с порогом",
            lines=[
                "Открой кабинет в Ads Manager, чтобы бот обновил его данные",
                "Пока валюта неизвестна, объявления не отключаются автоматически",
            ],
            resource_type="ad_account",
            resource_id=ad_account_id,
        )
    except Exception as exc:
        logger.exception("scan cycle crashed (кабинет=%s): %s", ad_account_id or "-", exc)
        outcome = "error"
        error_msg = f"{type(exc).__name__}: {exc}"

    duration_ms = int((time.monotonic() - started_monotonic) * 1000)
    await _finish_scan_run(
        engine,
        scan_id=scan_id,
        outcome=outcome,
        cycle_result=cycle_result,
        error_message=error_msg,
        duration_ms=duration_ms,
    )

    return {
        "outcome": outcome,
        "scan_id": scan_id,
        "ad_account_id": ad_account_id,
        "duration_ms": duration_ms,
        "rows_total": cycle_result.rows_total if cycle_result else 0,
        "rows_with_offer": cycle_result.rows_with_offer if cycle_result else 0,
        "alerts_warning": cycle_result.alerts_warning if cycle_result else 0,
        "alerts_stop": cycle_result.alerts_stop if cycle_result else 0,
        "ads_in_warning_state": cycle_result.ads_in_warning_state if cycle_result else 0,
        "ads_in_stop_state": cycle_result.ads_in_stop_state if cycle_result else 0,
        "tg_dispatched": dispatched,
        "error": error_msg,
    }


# Пауза между кабинетами внутри одного цикла (анти-«дёрганье» вкладок очередью).
ACCOUNT_SCAN_PAUSE_SECONDS = 3.0


def _aggregate_cycle_summary(per_account: list[dict]) -> dict:
    """Свод цикла из per-account summary (для логов и адаптивного интервала).

    Семантика outcome сохраняет полноту, а не скрывает частичные провалы:
      - "error"   — все кабинеты failed/timeout/skipped;
      - "partial" — хотя бы один кабинет не дал полный snapshot;
      - "success" — все завершились, хотя бы один вернул строки;
      - "empty"   — все кабинеты подтвердили отсутствие строк.
    Счётчики суммируются → worst-case агрегация для адаптивного интервала:
    stop-хит в любом кабинете даёт CRITICAL всему циклу.
    """
    outcomes = [s["outcome"] for s in per_account]
    hard_failure = {"error", "timeout", "skipped"}
    degraded = hard_failure | {"partial"}
    if outcomes and all(o in hard_failure for o in outcomes):
        outcome = "error"
    elif any(o in degraded for o in outcomes):
        outcome = "partial"
    elif any(o == "success" for o in outcomes):
        outcome = "success"
    elif outcomes and all(o == "empty" for o in outcomes):
        outcome = "empty"
    else:
        outcome = "partial"
    first_error = next(
        (s["error"] for s in per_account if s["outcome"] in degraded and s.get("error")),
        None,
    )
    return {
        "outcome": outcome,
        "duration_ms": sum(s.get("duration_ms", 0) for s in per_account),
        "rows_total": sum(s.get("rows_total", 0) for s in per_account),
        "rows_with_offer": sum(s.get("rows_with_offer", 0) for s in per_account),
        "alerts_warning": sum(s.get("alerts_warning", 0) for s in per_account),
        "alerts_stop": sum(s.get("alerts_stop", 0) for s in per_account),
        "ads_in_warning_state": sum(s.get("ads_in_warning_state", 0) for s in per_account),
        "ads_in_stop_state": sum(s.get("ads_in_stop_state", 0) for s in per_account),
        "tg_dispatched": next(
            (s["tg_dispatched"] for s in per_account if s.get("tg_dispatched")), None
        ),
        "error": first_error,
        "accounts": [
            {
                "ad_account_id": s.get("ad_account_id"),
                "scan_id": s.get("scan_id"),
                "outcome": s["outcome"],
            }
            for s in per_account
        ],
    }


# Module-level: набор кабинетов, для которого уже выполнена подготовка (вкладки открыты).
# При смене набора (активирован оффер / изменились offer-account links) — переподготовка.
# None = подготовка ещё не выполнялась (первый цикл после старта процесса).
_prepared_accounts: frozenset[str] | None = None


def _reset_prepared_accounts() -> None:
    """Сброс флага подготовки (для тестов / форс-переподготовки)."""
    global _prepared_accounts
    _prepared_accounts = None


def _cabinet_tab_is_confirmed(result: dict, *, account_id: str) -> bool:
    """Trust only an exact Ads Manager URL for the requested cabinet."""
    if result.get("opened") is not True:
        return False
    if str(result.get("ad_account_id") or "").removeprefix("act_") != account_id:
        return False
    try:
        parsed = urlsplit(str(result.get("url") or ""))
    except ValueError:
        return False
    if parsed.scheme.lower() != "https":
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    hostname = (parsed.hostname or "").lower()
    pathname = parsed.path.lower()
    is_facebook_host = hostname == "facebook.com" or hostname.endswith(".facebook.com")
    has_ads_manager_path = pathname == "/adsmanager" or pathname.startswith("/adsmanager/")
    if not (is_facebook_host and has_ads_manager_path):
        return False
    return parse_qs(parsed.query).get("act") == [account_id]


async def _sync_cabinet_tab_incident(
    engine: AsyncEngine,
    *,
    account_id: str,
    confirmed: bool,
) -> None:
    incident_key = f"{OBSERVER_CABINET_TAB_UNAVAILABLE_INCIDENT_PREFIX}{account_id}"
    if confirmed:
        await resolve_recurring_incident(
            engine,
            incident_key=incident_key,
            audience="all",
            summary=f"Вкладка кабинета {account_id} снова подтверждена.",
        )
        return
    await notify_recurring_incident(
        engine,
        incident_key=incident_key,
        audience="all",
        event_type="observer_cabinet_tab_unavailable",
        severity="critical",
        title="Кабинет не открыт в Ads Manager",
        summary=f"Кабинет {account_id}: сам открыть вкладку не получилось.",
        risk="Пока вкладки нет, скан и авто-стоп не работают",
        lines=["Открой кабинет в Ads Manager в Vision-профиле"],
        resource_type="ad_account",
        resource_id=account_id,
    )


async def _prepare_workspace(
    engine: AsyncEngine,
    *,
    gate: ScannerGate,
    accounts: list[str],
) -> None:
    """Фаза «подготовка рабочего места»: открыть вкладки кабинетов активных офферов
    перед сканом (manage/campaigns + колонки пользователя).

    Выполняется при первом цикле после старта и при изменении набора кабинетов
    (активирован новый оффер). Не блокирует скан:
    при сбое open_cabinet_tabs скан сам переоткроет вкладки по ходу (ensureAdsManagerPage).
    """
    global _prepared_accounts
    current = frozenset(accounts)
    if current == _prepared_accounts:
        return  # набор не менялся — вкладки уже открыты

    n = len(accounts)
    msg = f"Подготавливаю рабочее место: открываю кабинеты ({n})…"
    logger.info("observer: %s [%s]", msg, ", ".join(accounts))

    results: list[dict] = []
    try:
        from core.deadlines import bind_absolute_deadline

        prepare_deadline_seconds = max(60, 20 * len(accounts))
        prepare_deadline_at = datetime.now(timezone.utc) + timedelta(
            seconds=prepare_deadline_seconds
        )
        with bind_absolute_deadline(prepare_deadline_at):
            async with asyncio.timeout(prepare_deadline_seconds):
                results = await gate.open_cabinet_tabs(accounts)
    except Exception:
        logger.exception("observer: фаза подготовки — open_cabinet_tabs упал")
        # The per-cabinet scan will retry its role page, but browser blindness
        # must be visible immediately instead of waiting for a failure streak.

    result_by_account = {
        str(result.get("ad_account_id") or "").removeprefix("act_"): result for result in results
    }
    confirmed: list[dict] = []
    failed: list[dict] = []
    for account_id in accounts:
        result = result_by_account.get(account_id) or {
            "ad_account_id": account_id,
            "opened": False,
            "url": "",
            "error": "cabinet_tab_not_confirmed",
        }
        is_confirmed = _cabinet_tab_is_confirmed(result, account_id=account_id)
        await _sync_cabinet_tab_incident(
            engine,
            account_id=account_id,
            confirmed=is_confirmed,
        )
        (confirmed if is_confirmed else failed).append(result)
    if failed:
        logger.warning(
            "observer: не подтверждены вкладки кабинетов: %s",
            ", ".join(str(r.get("ad_account_id") or "-") for r in failed),
        )
    logger.info(
        "observer: подготовка завершена — подтверждено %d/%d кабинетов",
        len(confirmed),
        n,
    )
    if len(confirmed) == n:
        _prepared_accounts = current


async def _maybe_alert_multi_cab_no_owner(
    engine: AsyncEngine,
    *,
    account_count: int,
) -> bool:
    """Persist the fail-closed multi-cabinet recurring incident."""
    logger.critical(
        "observer: мульти-каб (%d кабинетов) без owner_tag — скан остановлен ради безопасности",
        account_count,
    )
    return await notify_recurring_incident(
        engine,
        incident_key=OBSERVER_MULTI_CAB_UNSAFE_INCIDENT_KEY,
        audience="all",
        event_type="observer_multi_cabinet_unsafe",
        severity="critical",
        title="Скан остановлен ради безопасности",
        summary=f"Кабинетов {account_count}, а метка своих кампаний не задана.",
        risk="Авто-стоп мог бы затронуть чужую рекламу",
        lines=["Задай owner_campaign_tag в настройках Observer"],
        resource_type="observer",
        resource_id="multi_cabinet_scope",
    )


async def run_one_cycle(
    engine: AsyncEngine,
    *,
    gate: ScannerGate,
) -> dict:
    """Один полный цикл observer'а. Возвращает summary для логов/тестов.

    Scan set = union нормализованных связей активных офферов с кабинетами. Каждый кабинет
    получает собственный actor/lease и scan_run; ошибка одного кабинета не
    прерывает остальные. Пустой scan set останавливается fail-closed: текущая
    вкладка браузера никогда не используется как неявный кабинет.

    Не бросает исключения наверх — все ошибки логирует и записывает в scan_runs.outcome.

    """
    config = await load_observer_config(engine)
    if config is None or not config["is_scanning_enabled"]:
        return {"outcome": "paused", "accounts": []}

    # Кабинет обязан быть выбран явно. Неявная текущая вкладка не имеет
    # identity/fencing и может направить money-решение не в тот аккаунт.
    accounts = await resolve_scan_account_ids(engine)
    if not accounts:
        orphan_offers = await list_offers_without_accounts(engine)
        logger.warning(
            "observer: скан пропущен — нет явно настроенных кабинетов%s",
            f"; офферы без кабинета: {', '.join(orphan_offers)}" if orphan_offers else "",
        )
        return {
            "outcome": "skipped",
            "accounts": [],
            "reason": "no_configured_cabinets",
            "orphan_offers": orphan_offers,
        }

    # Money-гард R4: мульти-каб (>1 кабинета) без owner_tag → скоупинг чужих кампаний
    # отсутствует (allowlist в мульти-кабе игнорируется, campaign_matches_owner→True для
    # ВСЕХ). Без тега бот авто-стопнул бы чужую рекламу в shared-кабинете (необратимо).
    # Зеркалит single-cab guard (allowlist_blocks_scan): скан этого набора кабинетов НЕ
    # запускаем, открываем recurring incident. Возобновится после безопасного owner_tag.
    if multi_cabinet_requires_owner_tag(len(accounts), config.get("owner_campaign_tag")):
        await _maybe_alert_multi_cab_no_owner(engine, account_count=len(accounts))
        return {"outcome": "skipped", "accounts": [], "reason": "multi_cab_no_owner_tag"}
    await resolve_recurring_incident(
        engine,
        incident_key=OBSERVER_MULTI_CAB_UNSAFE_INCIDENT_KEY,
        audience="all",
        summary="Owner scope кабинетов снова задан безопасно.",
    )

    # Warning о выпавших из скана офферах (активны, но без кабинетов) — раз в цикл в лог.
    orphan_offers = await list_offers_without_accounts(engine)
    if orphan_offers:
        logger.warning(
            "observer: офферы без привязанных кабинетов не сканируются: %s",
            ", ".join(orphan_offers),
        )

    # Глобальный allowlist несовместим с мульти-кабом (>1 кабинета): см. _run_account_scan.
    # При одном кабинете в scan set allowlist применяется (фильтр по campaign.id безопасен).
    if config.get("campaign_ids") and len(accounts) > 1:
        logger.warning(
            "observer: мульти-каб (>1 кабинета) — allowlist campaign_ids (%d шт.) игнорируется, "
            "скоупинг только через owner_tag",
            len(config.get("campaign_ids") or []),
        )

    # Фаза «подготовка рабочего места»: открыть вкладки всех кабинетов (с колонками)
    # перед сканом. Идемпотентно: только при первом цикле / смене набора кабинетов.
    await _prepare_workspace(
        engine,
        gate=gate,
        accounts=accounts,
    )

    supervisor = CabinetSupervisor(
        engine,
        owner_instance=_OBSERVER_INSTANCE_ID,
        concurrency=_CABINET_SCAN_CONCURRENCY,
        scan_deadline_seconds=_CABINET_SCAN_DEADLINE_SECONDS,
    )

    async def _run_cabinet(account_id: str, _index: int, lease: CabinetLease) -> dict:
        return await _run_account_scan(
            engine,
            gate=gate,
            config=config,
            ad_account_id=account_id,
            accounts_total=len(accounts),
            cabinet_lease=lease,
        )

    # Each cabinet is an isolated actor. The TaskGroup cancels and drains all
    # children on shutdown; the semaphore enforces the approved production
    # browser-operation concurrency.
    per_account = await supervisor.run_cycle(accounts, _run_cabinet)

    return _aggregate_cycle_summary(per_account)


# ====================== Process-local runtime state ======================


@dataclass
class _ObserverState:
    """In-process diagnostics; never a command or deduplication authority."""

    consecutive_scan_failures: int = 0  # подряд error-циклов (Layer 3 degraded-алерт)
    last_complete_snapshot_at: datetime | None = None


async def _maybe_alert_login_required(
    engine: AsyncEngine,
    *,
    ad_account_id: str,
) -> bool:
    """Persist a login-required incident; PostgreSQL is the suppression authority."""
    account_id = require_ad_account_id(ad_account_id)
    logger.error("ALERT (observer login_required): кабинет=%s", account_id)

    sent = await notify_recurring_incident(
        engine,
        incident_key=f"{OBSERVER_LOGIN_REQUIRED_INCIDENT_PREFIX}{account_id}",
        audience="all",
        event_type="observer_login_required",
        severity="critical",
        title="Vision-профиль требует повторного входа",
        summary=f"Кабинет {account_id}: Facebook разлогинил профиль.",
        risk="Скан и авто-стоп не работают",
        lines=[
            "Войди в Facebook в Vision-профиле и открой Ads Manager",
            "При риске расхода отключи объявления вручную",
        ],
        resource_type="ad_account",
        resource_id=account_id,
    )
    if not sent:
        logger.warning("observer login_required event не принят durable outbox")
    return bool(sent)


async def _maybe_alert_degraded(
    engine: AsyncEngine,
    *,
    consecutive_failures: int,
    last_error: str | None,
) -> bool:
    """Persist a degraded-scan incident; PostgreSQL collapses repeated ticks."""
    logger.error(
        "ALERT (observer degraded): failures=%s error=%r",
        consecutive_failures,
        last_error,
    )

    sent = await notify_recurring_incident(
        engine,
        incident_key=OBSERVER_DEGRADED_INCIDENT_KEY,
        audience="all",
        event_type="observer_degraded",
        severity="critical",
        title="Бот не может отсканировать кабинет",
        summary=f"Скан не удался {times_ru(consecutive_failures)} подряд.",
        risk="Данные устарели, авто-стоп может не сработать",
        lines=[
            "Проверь Vision-профиль, browser-agent и вкладку Ads Manager",
            "Пока скана нет, следи за расходом вручную",
        ],
        resource_type="worker",
        resource_id="observer",
    )
    if not sent:
        logger.warning("observer degraded event не принят durable outbox")
    return bool(sent)


async def _track_degraded_incident(
    engine: AsyncEngine,
    *,
    state: _ObserverState,
    summary: dict,
) -> None:
    """Advance degraded lifecycle without ever treating partial/unknown as healthy."""
    outcome = str(summary.get("outcome") or "")
    if outcome in {"error", "partial"}:
        state.consecutive_scan_failures += 1
        if state.consecutive_scan_failures >= DEGRADED_ALERT_THRESHOLD:
            await _maybe_alert_degraded(
                engine,
                consecutive_failures=state.consecutive_scan_failures,
                last_error=summary.get("error"),
            )
        return
    if outcome not in {"success", "empty"}:
        return

    # Resolve on every confirmed complete/known-empty cycle so a persisted
    # incident is also closed after process restart (local counter starts at 0).
    await resolve_recurring_incident(
        engine,
        incident_key=OBSERVER_DEGRADED_INCIDENT_KEY,
        audience="all",
        summary="Кабинет снова сканируется полностью.",
    )
    state.consecutive_scan_failures = 0


async def _wait_for_durable_scan(
    engine: AsyncEngine,
    shutdown_event: asyncio.Event,
    *,
    worker_id: uuid.UUID,
    seconds: float,
) -> Task | None:
    """Poll PostgreSQL for durable work during the adaptive idle interval.

    A claim is made only when the observer is ready to execute it, so its lease
    is not consumed behind an already-running browser scan.  Missing wake-ups
    cannot lose work: every poll reconciles against ``task_queue``.
    """

    loop = asyncio.get_running_loop()
    stop_at = loop.time() + max(0.0, float(seconds))
    while not shutdown_event.is_set():
        try:
            task = await claim_observer_scan(engine, worker_id=worker_id)
        except Exception:
            logger.exception("observer: failed to poll durable scan queue")
            task = None
        if task is not None:
            return task

        remaining = stop_at - loop.time()
        if remaining <= 0:
            return None
        wait_for = min(float(OBSERVER_SCAN_POLL_SECONDS), remaining)
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=wait_for)
        except asyncio.TimeoutError:
            pass
    return None


async def _run_claimed_observer_scan(
    engine: AsyncEngine,
    *,
    task: Task,
    gate: ScannerGate,
) -> dict:
    """Execute and terminally finalize one claimed scan under its lease fence."""
    fence = {
        "task_id": task.id,
        "lease_owner": task.lease_owner,
        "lease_token": task.lease_token,
    }
    try:
        summary = await run_with_observer_scan_control(
            engine,
            task,
            lambda: run_one_cycle(
                engine,
                gate=gate,
            ),
        )
    except ObserverScanCancelled as exc:
        if exc.reason == "cancel_requested":
            finalized = await mark_cancelled(
                engine,
                reason="operator cancelled observer scan",
                **fence,
            )
            if not finalized:
                logger.critical("observer scan task %s lost fence during cancellation", task.id)
            return {"outcome": "cancelled", "task_id": task.id}
        finalized = await mark_failed(
            engine,
            error="observer scan absolute deadline exceeded",
            result={"outcome": "REJECTED", "reason": exc.reason},
            **fence,
        )
        if not finalized:
            logger.critical("observer scan task %s lost fence at deadline", task.id)
        return {"outcome": "error", "task_id": task.id, "error": exc.reason}
    except ObserverScanFenceLost:
        logger.critical("observer scan task %s lost its lease; finalization refused", task.id)
        return {"outcome": "error", "task_id": task.id, "error": "lease_lost"}
    except Exception as exc:
        logger.exception("observer scan task %s crashed", task.id)
        finalized = await mark_failed(
            engine,
            error=f"{type(exc).__name__}: {exc}",
            result={"outcome": "REJECTED", "reason": "scan_crashed"},
            **fence,
        )
        if not finalized:
            logger.critical("observer scan task %s lost fence after crash", task.id)
        return {"outcome": "error", "task_id": task.id, "error": type(exc).__name__}

    scan_outcome = str(summary.get("outcome") or "")
    task_result = {
        **summary,
        "outcome": "CONFIRMED" if scan_outcome in {"success", "empty"} else "REJECTED",
        "scan_outcome": scan_outcome,
        "task_id": task.id,
    }
    if scan_outcome in {"success", "empty"}:
        finalized = await mark_succeeded(engine, result=task_result, **fence)
    else:
        finalized = await mark_failed(
            engine,
            error=f"observer scan finished without a complete snapshot: {scan_outcome}",
            result=task_result,
            **fence,
        )
    if not finalized:
        logger.critical("observer scan task %s lost fence during finalization", task.id)
    return summary


async def _close_scanner_gate(gate: ScannerGate | None) -> None:
    """Close a production gate when its canonical Vision revision is replaced."""
    if gate is None:
        return
    close = getattr(gate, "close", None)
    if close is None:
        return
    result = close()
    if result is not None:
        await result


# ====================== Main loop ======================


async def main_loop(
    *,
    gate_factory: Callable[[], Awaitable[ScannerGate]] | None = None,
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    """Бесконечный цикл observer.

    Args:
        gate_factory: создаёт ScannerGate (default: BrowserAgentClient wrapper).
        should_continue: для тестов — управляет выходом из цикла.
    """
    start_worker_metrics_server(WORKER_NAME)

    db_url = _get_database_url()
    engine = create_async_engine(db_url, **WORKER_ENGINE_KWARGS)

    uses_default_gate = gate_factory is None
    if gate_factory is None:

        async def create_default_gate() -> ScannerGate:
            return await _default_gate_factory(engine)

        gate_factory = create_default_gate
    # Graceful shutdown по SIGTERM/SIGINT.
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    state = _ObserverState()

    gate: ScannerGate | None = None
    metrics_task: asyncio.Task | None = None
    pending_scan_task: Task | None = None

    try:
        metrics_task = asyncio.create_task(
            metrics_loop(
                shutdown_event,
                snapshot_age_provider=lambda: (
                    None
                    if state.last_complete_snapshot_at is None
                    else (
                        datetime.now(timezone.utc) - state.last_complete_snapshot_at
                    ).total_seconds()
                ),
            )
        )

        logger.info("observer_worker запущен")

        while should_continue() and not shutdown_event.is_set():
            if pending_scan_task is None:
                try:
                    pending_scan_task = await claim_observer_scan(
                        engine,
                        worker_id=_OBSERVER_INSTANCE_ID,
                    )
                except Exception:
                    logger.exception("observer: failed to claim durable scan task")

            if pending_scan_task is None:
                try:
                    await enqueue_scheduled_observer_scan(engine)
                    pending_scan_task = await claim_observer_scan(
                        engine,
                        worker_id=_OBSERVER_INSTANCE_ID,
                    )
                except Exception:
                    logger.exception("observer: failed to publish/claim scheduled scan")

            if pending_scan_task is None:
                pending_scan_task = await _wait_for_durable_scan(
                    engine,
                    shutdown_event,
                    worker_id=_OBSERVER_INSTANCE_ID,
                    seconds=float(OBSERVER_SCAN_POLL_SECONDS),
                )
                continue

            claimed_task = pending_scan_task
            pending_scan_task = None
            try:
                if gate is not None and uses_default_gate:
                    from core.vision_runtime import load_vision_runtime_config

                    current_vision = await load_vision_runtime_config(engine)
                    if (
                        getattr(gate, "configuration_revision", None)
                        != current_vision.configuration_revision
                    ):
                        await _close_scanner_gate(gate)
                        gate = None
                if gate is None:
                    gate = await gate_factory()
            except Exception as exc:
                logger.exception("observer: could not create the canonical scanner gate")
                finalized = await mark_failed(
                    engine,
                    task_id=claimed_task.id,
                    lease_owner=claimed_task.lease_owner,
                    lease_token=claimed_task.lease_token,
                    error=f"{type(exc).__name__}: scanner gate unavailable",
                    result={
                        "outcome": "REJECTED",
                        "reason": "scanner_gate_unavailable",
                    },
                )
                if not finalized:
                    logger.critical(
                        "observer scan task %s lost fence before gate creation",
                        claimed_task.id,
                    )
                summary = {
                    "outcome": "error",
                    "task_id": claimed_task.id,
                    "error": "scanner_gate_unavailable",
                }
                await _track_degraded_incident(
                    engine,
                    state=state,
                    summary=summary,
                )
                continue

            cycle_started_monotonic = time.monotonic()
            try:
                summary = await _run_claimed_observer_scan(
                    engine,
                    task=claimed_task,
                    gate=gate,
                )
                if summary.get("outcome") in {"success", "empty"}:
                    state.last_complete_snapshot_at = datetime.now(timezone.utc)
                    SNAPSHOT_AGE.labels(source="observer_ads").set(0.0)
                logger.info("cycle done: %s", summary)
            except Exception as exc:
                # MID-6 (аудит 02.07): падение ВНЕ _run_account_scan (например DB-ошибка
                # в load_observer_config/resolve_scan_account_ids) раньше просто
                # пересоздавало gate и уходило на следующую итерацию молча — мимо Layer 3
                # degraded-детектора (он считает только summary["outcome"] == "error" из
                # штатного пути). Теперь такой краш тоже засчитывается в тот же счётчик
                # consecutive_scan_failures — иначе воркер мог биться в этой ветке часами
                # с живым процессом, но без единого operator-visible инцидента.
                logger.exception("claimed observer scan crashed — пересоздаю gate")
                await _close_scanner_gate(gate)
                gate = None
                state.consecutive_scan_failures += 1
                if state.consecutive_scan_failures >= DEGRADED_ALERT_THRESHOLD:
                    await _maybe_alert_degraded(
                        engine,
                        consecutive_failures=state.consecutive_scan_failures,
                        last_error=f"{type(exc).__name__}: {exc}",
                    )
                await asyncio.sleep(10.0)
                continue

            # Layer 3: трекинг «тихой» деградации — N подряд error-циклов → degraded-алерт.
            await _track_degraded_incident(
                engine,
                state=state,
                summary=summary,
            )

            # Reconcile work committed during the just-finished browser scan
            # before entering the adaptive sleep.
            try:
                pending_scan_task = await claim_observer_scan(
                    engine,
                    worker_id=_OBSERVER_INSTANCE_ID,
                )
            except Exception:
                logger.exception("observer: failed to reconcile durable scan queue")
                pending_scan_task = None
            if pending_scan_task is not None:
                continue

            # Адаптивный интервал: база (UI-слайдер interval_seconds) = CALM-режим,
            # частота скана зависит от угрозы в этом цикле (у порога — чаще).
            config = await load_observer_config(engine)
            base_interval = float(
                (config or {}).get("interval_seconds", DEFAULT_BASE_INTERVAL_SECONDS)
            )
            scan_mode = resolve_scan_mode(summary)
            interval = compute_adaptive_interval(base_interval, scan_mode)
            # Jitter ±10% применяется к ЦЕЛЕВОМУ периоду между началами циклов.
            jitter_offset = interval * JITTER_FRACTION
            target_period = clamp_interval(interval + random.uniform(-jitter_offset, jitter_offset))
            cycle_elapsed = time.monotonic() - cycle_started_monotonic
            sleep_for = compute_remaining_sleep(
                target_period_seconds=target_period,
                elapsed_seconds=cycle_elapsed,
            )
            scanned_account_ids = [
                require_ad_account_id(account["ad_account_id"])
                for account in summary.get("accounts", [])
                if isinstance(account, dict) and account.get("ad_account_id")
            ]
            if scanned_account_ids:
                try:
                    await publish_next_scan_at(
                        engine,
                        ad_account_ids=scanned_account_ids,
                        next_scan_at=datetime.now(timezone.utc) + timedelta(seconds=sleep_for),
                    )
                except Exception:
                    # This is an operator projection, not scheduling authority:
                    # the durable queue remains authoritative and must continue.
                    logger.exception("observer: failed to publish cabinet next_scan_at")
            logger.info(
                "observer: режим=%s период=%.0fс (база=%.0f, цикл=%.1f, sleep=%.1f)",
                scan_mode,
                target_period,
                base_interval,
                cycle_elapsed,
                sleep_for,
            )

            pending_scan_task = await _wait_for_durable_scan(
                engine,
                shutdown_event,
                worker_id=_OBSERVER_INSTANCE_ID,
                seconds=sleep_for,
            )
    finally:
        logger.info("observer_worker завершён")

        if metrics_task is not None:
            metrics_task.cancel()
            try:
                await metrics_task
            except asyncio.CancelledError:
                pass

        await _close_scanner_gate(gate)
        await engine.dispose()


# ====================== Default factories (прод-реализация) ======================


async def _default_gate_factory(engine: AsyncEngine) -> ScannerGate:
    """Прод-реализация: оборачивает BrowserAgentClient в ScannerGate-протокол."""
    from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
    from clients.python_grpc.client import ScanResult as GrpcScanResult
    from core.config import get_settings
    from core.vision_runtime import load_vision_runtime_config

    s = get_settings()
    vision = await load_vision_runtime_config(engine)
    client = BrowserAgentClient(
        BrowserAgentConfig(
            # grpc_host/port из env — иначе в Docker observer пойдёт на localhost
            # внутри контейнера и не достучится до browser-agent на хосте
            # (host.docker.internal). Консистентно с meta_api/creator-воркерами.
            grpc_host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
            grpc_port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
            vision_x_token=vision.x_token,
            vision_api_url=s.vision_api_url,
            vision_profile_id=vision.profile_id,
            vision_folder_id=os.environ.get("VISION_FOLDER_ID") or None,
        )
    )
    await client.start()
    # run_scan_cycle сам поднимет browser-сессию (ensure_browser_session внутри).

    class _BrowserAgentScannerGate:
        configuration_revision = vision.configuration_revision

        async def run_one_scan(
            self,
            ad_account_id: str,
            campaign_ids: list[str] | None = None,
            owner_tag: str | None = None,
        ) -> ScanCycleOutput:
            final_result: GrpcScanResult | None = None
            async for event in client.run_scan_cycle(
                campaign_ids=campaign_ids or [],
                owner_tag=owner_tag,
                ad_account_id=ad_account_id,
            ):
                # ScanProgress нам пока не нужен — слушаем только финальный ScanResult
                if isinstance(event, GrpcScanResult):
                    final_result = event
            if final_result is None:
                return ScanCycleOutput(
                    rows=[],
                    metrics_contract_revision=0,
                    empty_reason="no final result",
                )
            return ScanCycleOutput(
                rows=final_result.rows,
                metrics_contract_revision=final_result.metrics_contract_revision,
                total_passes=final_result.total_passes,
                duration_seconds=final_result.duration_seconds,
                empty_reason=final_result.empty_reason,
                warnings=list(final_result.warnings),
                partial_row_ids=list(final_result.partial_row_ids),
                rows_with_all_metrics_empty=final_result.rows_with_all_metrics_empty,
            )

        async def open_cabinet_tabs(self, ad_account_ids: list[str]) -> list[dict]:
            return await client.open_cabinet_tabs(ad_account_ids)

        async def close(self) -> None:
            await client.close()

    return _BrowserAgentScannerGate()
