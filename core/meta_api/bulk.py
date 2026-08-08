# -*- coding: utf-8 -*-
"""Owner-scoped резолв ad_id по offer-коду для массовых mutations (bulk pause/activate).

Используется TG-командами /pause /resume. Возвращает только активные объявления
СВОИХ кампаний (owner-scoping), чтобы массовая операция не задела чужую рекламу
в общем кабинете.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.meta_api.identity import require_ad_account_id
from core.observer.queries import campaign_matches_owner

logger = logging.getLogger(__name__)

MAX_BULK = 50

# LOW (аудит 02.07): SQL-запросы ниже читали ВСЕ совпавшие строки без LIMIT (Python-срез
# owned[:limit] применялся уже ПОСЛЕ полной выборки в память) — на большом каталоге это
# unbounded read по offer-код/campaign_ids паттерну. SQL LIMIT ставим с большим запасом
# над реальными limit'ами вызовов (MAX_BULK=50, _AUTOSTART_MAX_ADS=2000), чтобы:
# (1) ограничить pathological сканы, (2) НЕ исказить total (второй элемент кортежа —
# используется вызывающими для честного "усечено до N" сообщения/лога) для любых
# реалистичных объёмов каталога.
_SQL_ROW_CAP = 20000


@dataclass(frozen=True, slots=True)
class AccountScopedAdResolution:
    """Owner ads grouped by the only account they may be mutated through."""

    ads_by_account: dict[str, tuple[str, ...]]
    total: int
    missing_account_count: int


@dataclass(frozen=True, slots=True)
class AutostartActivationGuards:
    """Per-ad scheduler snapshot plus explicit fail-closed rejections."""

    guards_by_ad_id: dict[str, dict[str, object]]
    rejected_by_ad_id: dict[str, str]


@dataclass(frozen=True, slots=True)
class GuardedAutostartExecution:
    """Result of the committed pre-Meta CAS while locks remain held."""

    payload: Any | None
    requested_ad_ids: tuple[str, ...]
    executable_ad_ids: tuple[str, ...]
    rejected_by_ad_id: dict[str, str]
    external_started: bool
    control_reason: str | None = None


@dataclass(frozen=True, slots=True)
class AutostartTargetLocks:
    """Deterministic session-lock set held for one reconciliation lifecycle."""

    connection: AsyncConnection
    requested_ad_ids: tuple[str, ...]
    busy_ad_id: str | None = None


_AUTOSTART_GUARD_VERSION = 1
_AUTOSTART_ALLOWED_DELIVERY = frozenset({"ACTIVE", "OFF", "PAUSED"})


def _normalized_guard(
    *,
    ad_account_id: object,
    delivery_status: object,
    alert_state: object,
    open_state_token: object,
    last_transition_at: object,
) -> dict[str, object]:
    account_id = require_ad_account_id(ad_account_id)
    delivery = str(delivery_status or "").strip().upper()
    state = str(alert_state or "normal").strip().lower()
    token = str(open_state_token) if open_state_token is not None else None
    transition = (
        last_transition_at.isoformat() if isinstance(last_transition_at, datetime) else None
    )
    generation_source = "|".join(
        (
            account_id,
            delivery,
            state,
            token or "",
            transition or "",
        )
    )
    return {
        "version": _AUTOSTART_GUARD_VERSION,
        "ad_account_id": account_id,
        "delivery_status": delivery,
        "alert_state": state,
        "open_state_token": token,
        "last_transition_at": transition,
        "generation": hashlib.sha256(generation_source.encode("utf-8")).hexdigest()[:32],
    }


def _task_targets(payload: object) -> tuple[str, tuple[str, ...]]:
    values = payload if isinstance(payload, dict) else {}
    mutation_kind = str(values.get("mutation_kind") or "")
    if mutation_kind in {"pause_ad", "activate_ad"}:
        target_id = str(values.get("target_id") or "").strip()
        return mutation_kind, (target_id,) if target_id else ()
    if mutation_kind != "bulk_status_change":
        return mutation_kind, ()
    params = values.get("params") if isinstance(values.get("params"), dict) else {}
    action = str(params.get("action") or "").strip().lower()
    ids = params.get("ad_ids") if isinstance(params.get("ad_ids"), list) else []
    return (
        f"bulk_status_change:{action}",
        tuple(str(ad_id).strip() for ad_id in ids if str(ad_id).strip()),
    )


async def _active_status_tasks_by_ad(
    connection: AsyncConnection,
    *,
    ad_ids: tuple[str, ...],
    exclude_task_id: int | None,
) -> dict[str, str]:
    if not ad_ids:
        return {}
    rows = (
        await connection.execute(
            text(
                """
                SELECT id, payload
                FROM task_queue
                WHERE task_type = 'meta_api_mutation'
                  AND status IN ('pending', 'retrying', 'running')
                  AND (
                      CAST(:exclude_task_id AS BIGINT) IS NULL
                      OR id <> CAST(:exclude_task_id AS BIGINT)
                  )
                  AND (
                      payload @> '{"mutation_kind":"pause_ad"}'::jsonb
                      OR payload @> '{"mutation_kind":"activate_ad"}'::jsonb
                      OR payload @> '{"mutation_kind":"bulk_status_change"}'::jsonb
                  )
                ORDER BY id
                """
            ),
            {"exclude_task_id": exclude_task_id},
        )
    ).all()
    requested = set(ad_ids)
    conflicts: dict[str, str] = {}
    for row in rows:
        action, task_ad_ids = _task_targets(row.payload)
        for ad_id in task_ad_ids:
            if ad_id in requested:
                conflicts.setdefault(ad_id, f"active_task:{int(row.id)}:{action}")
    return conflicts


async def capture_autostart_activation_guards(
    connection: AsyncConnection,
    *,
    ad_ids: list[str] | tuple[str, ...],
    expected_ad_account_id: str | None = None,
    exclude_task_id: int | None = None,
) -> AutostartActivationGuards:
    """Read the current delivery/FSM generation under caller-owned ad locks.

    Only an explicitly known delivery state and a normal FSM are eligible.
    Any active status command is a conflict, irrespective of its scheduler
    lane, so legacy/misrouted work cannot be silently crossed.
    """
    canonical_ids = tuple(sorted({str(ad_id).strip() for ad_id in ad_ids if str(ad_id).strip()}))
    if not canonical_ids:
        return AutostartActivationGuards({}, {})

    rows = (
        await connection.execute(
            text(
                """
                SELECT
                    ad.fb_ad_id,
                    campaign.ad_account_id,
                    ad.delivery_status,
                    state.alert_state,
                    state.open_state_token,
                    state.last_transition_at
                FROM fb_ads AS ad
                JOIN fb_adsets AS adset ON adset.id = ad.adset_id
                JOIN fb_campaigns AS campaign ON campaign.id = adset.campaign_id
                LEFT JOIN ad_alert_state AS state ON state.ad_id = ad.id
                WHERE ad.fb_ad_id = ANY(:ad_ids)
                ORDER BY ad.fb_ad_id
                """
            ),
            {"ad_ids": list(canonical_ids)},
        )
    ).all()
    rows_by_id = {str(row.fb_ad_id): row for row in rows}
    active_tasks = await _active_status_tasks_by_ad(
        connection,
        ad_ids=canonical_ids,
        exclude_task_id=exclude_task_id,
    )
    expected_account = (
        require_ad_account_id(expected_ad_account_id)
        if expected_ad_account_id is not None
        else None
    )

    guards: dict[str, dict[str, object]] = {}
    rejected: dict[str, str] = {}
    for ad_id in canonical_ids:
        row = rows_by_id.get(ad_id)
        if row is None:
            rejected[ad_id] = "catalog_target_missing"
            continue
        try:
            guard = _normalized_guard(
                ad_account_id=row.ad_account_id,
                delivery_status=row.delivery_status,
                alert_state=row.alert_state,
                open_state_token=row.open_state_token,
                last_transition_at=row.last_transition_at,
            )
        except ValueError:
            rejected[ad_id] = "ad_account_identity_missing"
            continue
        if expected_account is not None and guard["ad_account_id"] != expected_account:
            rejected[ad_id] = "ad_account_identity_changed"
            continue
        if guard["delivery_status"] not in _AUTOSTART_ALLOWED_DELIVERY:
            rejected[ad_id] = "delivery_status_unknown_or_unsafe"
            continue
        if guard["alert_state"] != "normal":
            rejected[ad_id] = f"fsm_state:{guard['alert_state']}"
            continue
        if ad_id in active_tasks:
            rejected[ad_id] = active_tasks[ad_id]
            continue
        guards[ad_id] = guard
    return AutostartActivationGuards(guards, rejected)


async def revalidate_autostart_activation_guards(
    connection: AsyncConnection,
    *,
    payload: Any,
    task_id: int,
) -> AutostartActivationGuards:
    """CAS the scheduled generation against current state immediately pre-Meta."""
    params = payload.params if hasattr(payload, "params") else {}
    raw_ids = params.get("ad_ids") if isinstance(params.get("ad_ids"), list) else []
    ad_ids = tuple(str(ad_id).strip() for ad_id in raw_ids if str(ad_id).strip())
    expected_guards = (
        params.get("activation_guards") if isinstance(params.get("activation_guards"), dict) else {}
    )
    current = await capture_autostart_activation_guards(
        connection,
        ad_ids=ad_ids,
        expected_ad_account_id=str(payload.ad_account_id),
        exclude_task_id=task_id,
    )
    accepted: dict[str, dict[str, object]] = {}
    rejected = dict(current.rejected_by_ad_id)
    for ad_id in ad_ids:
        if ad_id in rejected:
            continue
        expected = expected_guards.get(ad_id)
        observed = current.guards_by_ad_id.get(ad_id)
        if not isinstance(expected, dict) or expected.get("version") != _AUTOSTART_GUARD_VERSION:
            rejected[ad_id] = "scheduler_guard_missing"
            continue
        if observed is None or expected.get("generation") != observed.get("generation"):
            rejected[ad_id] = "scheduler_generation_changed"
            continue
        accepted[ad_id] = observed
    return AutostartActivationGuards(accepted, rejected)


def is_guarded_autostart_activation(payload: Any) -> bool:
    """Return whether a bulk command requires the scheduler-generation CAS."""
    if getattr(payload, "mutation_kind", None) != "bulk_status_change":
        return False
    params = getattr(payload, "params", None)
    if not isinstance(params, dict):
        return False
    return str(params.get("action") or "").strip().lower() in {"activate", "active"} and isinstance(
        params.get("autostart_day"), str
    )


def merge_guarded_bulk_result(
    result: dict[str, Any],
    execution: GuardedAutostartExecution,
) -> dict[str, Any]:
    """Merge pre-Meta CAS rejections into the canonical bulk result."""
    rejected_items = [
        {
            "id": ad_id,
            "success": False,
            "code": 0,
            "error": f"activation_guard:{reason}",
        }
        for ad_id, reason in sorted(execution.rejected_by_ad_id.items())
    ]
    merged = dict(result)
    merged["requested_ids"] = list(execution.requested_ad_ids)
    merged["executed_ids"] = list(execution.executable_ad_ids)
    merged["guard_rejected"] = dict(sorted(execution.rejected_by_ad_id.items()))
    merged["failed"] = int(merged.get("failed") or 0) + len(rejected_items)
    merged["sub_results"] = list(merged.get("sub_results") or []) + rejected_items
    return merged


@asynccontextmanager
async def locked_autostart_targets(
    engine: AsyncEngine,
    *,
    ad_ids: tuple[str, ...] | list[str],
) -> AsyncIterator[AutostartTargetLocks]:
    """Hold sorted per-ad locks through reconciliation and terminalization.

    A busy target is reported without waiting. Already acquired locks stay
    held until the caller terminalizes UNKNOWN, so a partial lock set cannot
    race a newer money command.
    """
    requested = tuple(sorted({str(ad_id).strip() for ad_id in ad_ids if str(ad_id).strip()}))
    if not requested:
        raise ValueError("autostart target locks require at least one ad id")

    acquired: list[str] = []
    async with engine.connect() as conn:
        try:
            busy_ad_id: str | None = None
            for ad_id in requested:
                locked = await conn.scalar(
                    text("SELECT pg_try_advisory_lock(hashtext(:ad_id))"),
                    {"ad_id": ad_id},
                )
                if not bool(locked):
                    busy_ad_id = ad_id
                    break
                acquired.append(ad_id)
            await conn.commit()
            yield AutostartTargetLocks(
                connection=conn,
                requested_ad_ids=requested,
                busy_ad_id=busy_ad_id,
            )
        finally:

            async def _unlock() -> None:
                if conn.in_transaction():
                    await conn.rollback()
                unlock_failed = False
                for ad_id in reversed(acquired):
                    unlocked = await conn.scalar(
                        text("SELECT pg_advisory_unlock(hashtext(:ad_id))"),
                        {"ad_id": ad_id},
                    )
                    unlock_failed = unlock_failed or not bool(unlocked)
                await conn.commit()
                if unlock_failed:
                    raise RuntimeError("one or more autostart reconciliation locks were not owned")

            release = asyncio.create_task(_unlock())
            try:
                await asyncio.shield(release)
            except asyncio.CancelledError:
                release_error: BaseException | None = None
                try:
                    await release
                except BaseException as exc:  # noqa: BLE001
                    release_error = exc
                if release_error is not None:
                    try:
                        await conn.invalidate()
                    except Exception:
                        logger.exception(
                            "failed to invalidate cancelled reconciliation lock connection"
                        )
                raise
            except Exception:
                logger.exception("failed to release autostart reconciliation locks")
                await conn.invalidate()


def _boundary_control_reason(row: Any | None, *, now: datetime) -> str:
    if row is None or str(row.status) != "running":
        return "lease_lost"
    if row.cancel_requested_at is not None:
        return "cancel_requested"
    deadline_at = row.deadline_at
    if deadline_at is not None:
        aware_deadline = (
            deadline_at if deadline_at.tzinfo is not None else deadline_at.replace(tzinfo=UTC)
        )
        if aware_deadline <= now:
            return "deadline_exceeded"
    return "lease_lost"


@asynccontextmanager
async def guarded_autostart_execution_boundary(
    engine: AsyncEngine,
    *,
    task: Any,
    payload: Any,
) -> AsyncIterator[GuardedAutostartExecution]:
    """Hold every ad mutex from committed CAS through terminal FSM projection.

    Session advisory locks intentionally survive the short transaction that
    commits ``external_started_at``.  This preserves crash evidence before the
    Meta request while also serializing a concurrent per-ad pause through the
    end of the activation lifecycle.
    """
    params = payload.params if isinstance(getattr(payload, "params", None), dict) else {}
    raw_ad_ids = params.get("ad_ids")
    if not isinstance(raw_ad_ids, list):
        raise ValueError("guarded autostart activation requires an ad_ids list")
    requested_ad_ids = tuple(
        sorted({str(ad_id).strip() for ad_id in raw_ad_ids if str(ad_id).strip()})
    )
    if not requested_ad_ids:
        raise ValueError("guarded autostart activation requires ad_ids")

    acquired: list[str] = []
    async with engine.connect() as conn:
        try:
            for ad_id in requested_ad_ids:
                locked = await conn.scalar(
                    text("SELECT pg_try_advisory_lock(hashtext(:ad_id))"),
                    {"ad_id": ad_id},
                )
                if not bool(locked):
                    await conn.commit()
                    yield GuardedAutostartExecution(
                        payload=None,
                        requested_ad_ids=requested_ad_ids,
                        executable_ad_ids=(),
                        rejected_by_ad_id={
                            requested_id: (
                                "target_lock_busy"
                                if requested_id == ad_id
                                else "batch_target_lock_conflict"
                            )
                            for requested_id in requested_ad_ids
                        },
                        external_started=False,
                    )
                    return
                acquired.append(ad_id)
            # End SQLAlchemy's implicit transaction. Session locks remain held.
            await conn.commit()

            async with conn.begin():
                guards = await revalidate_autostart_activation_guards(
                    conn,
                    payload=payload,
                    task_id=int(task.id),
                )
                executable_ids = tuple(
                    ad_id for ad_id in requested_ad_ids if ad_id in guards.guards_by_ad_id
                )
                if not executable_ids:
                    decision = GuardedAutostartExecution(
                        payload=None,
                        requested_ad_ids=requested_ad_ids,
                        executable_ad_ids=(),
                        rejected_by_ad_id=guards.rejected_by_ad_id,
                        external_started=False,
                    )
                else:
                    checkpoint = {
                        "bulk_requested_ad_ids": list(requested_ad_ids),
                        "bulk_execution_ad_ids": list(executable_ids),
                        "bulk_guard_rejected": dict(sorted(guards.rejected_by_ad_id.items())),
                        "bulk_external_deadline_at": (
                            task.deadline_at.isoformat()
                            if isinstance(task.deadline_at, datetime)
                            else None
                        ),
                    }
                    updated = await conn.execute(
                        text(
                            """
                            UPDATE task_queue
                            SET external_started_at = COALESCE(external_started_at, NOW()),
                                result = COALESCE(result, '{}'::jsonb)
                                    || CAST(:checkpoint AS JSONB),
                                updated_at = NOW()
                            WHERE id = :task_id
                              AND status = 'running'
                              AND lease_owner = :lease_owner
                              AND lease_token = :lease_token
                              AND lease_expires_at > clock_timestamp()
                              AND cancel_requested_at IS NULL
                              AND (
                                  deadline_at IS NULL
                                  OR deadline_at > clock_timestamp()
                              )
                            """
                        ),
                        {
                            "task_id": int(task.id),
                            "lease_owner": task.lease_owner,
                            "lease_token": int(task.lease_token),
                            "checkpoint": json.dumps(checkpoint),
                        },
                    )
                    if not (updated.rowcount or 0):
                        control = (
                            await conn.execute(
                                text(
                                    """
                                    SELECT status, cancel_requested_at, deadline_at
                                    FROM task_queue
                                    WHERE id = :task_id
                                      AND lease_owner = :lease_owner
                                      AND lease_token = :lease_token
                                    """
                                ),
                                {
                                    "task_id": int(task.id),
                                    "lease_owner": task.lease_owner,
                                    "lease_token": int(task.lease_token),
                                },
                            )
                        ).first()
                        decision = GuardedAutostartExecution(
                            payload=None,
                            requested_ad_ids=requested_ad_ids,
                            executable_ad_ids=(),
                            rejected_by_ad_id=guards.rejected_by_ad_id,
                            external_started=False,
                            control_reason=_boundary_control_reason(
                                control,
                                now=datetime.now(UTC),
                            ),
                        )
                    else:
                        from core.meta_api.schemas import MetaMutationPayload

                        execution_params = dict(params)
                        execution_params["ad_ids"] = list(executable_ids)
                        execution_params["activation_guards"] = {
                            ad_id: guards.guards_by_ad_id[ad_id] for ad_id in executable_ids
                        }
                        decision = GuardedAutostartExecution(
                            payload=MetaMutationPayload(
                                mutation_kind=payload.mutation_kind,
                                target_id=payload.target_id,
                                ad_account_id=payload.ad_account_id,
                                params=execution_params,
                            ),
                            requested_ad_ids=requested_ad_ids,
                            executable_ad_ids=executable_ids,
                            rejected_by_ad_id=guards.rejected_by_ad_id,
                            external_started=True,
                        )

            # No DB transaction is open here; external_started_at is durable.
            # The same physical PostgreSQL session still owns every target lock.
            yield decision
        finally:

            async def _unlock() -> None:
                if conn.in_transaction():
                    await conn.rollback()
                unlock_failed = False
                for ad_id in reversed(acquired):
                    unlocked = await conn.scalar(
                        text("SELECT pg_advisory_unlock(hashtext(:ad_id))"),
                        {"ad_id": ad_id},
                    )
                    unlock_failed = unlock_failed or not bool(unlocked)
                await conn.commit()
                if unlock_failed:
                    raise RuntimeError("one or more autostart advisory locks were not owned")

            release = asyncio.create_task(_unlock())
            try:
                await asyncio.shield(release)
            except asyncio.CancelledError:
                release_error: BaseException | None = None
                try:
                    await release
                except BaseException as exc:  # noqa: BLE001 — preserve cancellation
                    release_error = exc
                if release_error is not None:
                    try:
                        await conn.invalidate()
                    except Exception:
                        logger.exception("failed to invalidate cancelled autostart lock connection")
                raise
            except Exception:
                logger.exception("failed to release guarded autostart advisory locks")
                await conn.invalidate()


async def resolve_owner_ad_ids(
    engine: AsyncEngine,
    *,
    offer_code: str,
    owner_tag: str | None = None,
    limit: int = MAX_BULK,
) -> tuple[list[str], int]:
    """Активные fb_ad_id по offer-коду (word-boundary), отфильтрованные owner-тегом.

    Owner-scoping: если owner_tag задан — оставляем только кампании/объявления,
    чьё название содержит любой owner-тег (через campaign_matches_owner). Защита
    от массового отключения чужих кампаний в общем кабинете.

    Возвращает (ad_ids[:limit], total_matched_after_owner) — второй элемент нужен,
    чтобы предупредить пользователя об усечении до limit.
    """
    escaped = re.escape(offer_code.lower())
    pattern = rf"(^|[^a-z0-9]){escaped}([^a-z0-9]|$)"
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT DISTINCT a.fb_ad_id, c.campaign_name, a.ad_name
                    FROM fb_ads a
                    JOIN fb_adsets s ON s.id = a.adset_id
                    JOIN fb_campaigns c ON c.id = s.campaign_id
                    WHERE (c.campaign_name ~* :pattern OR a.ad_name ~* :pattern)
                      AND a.fb_ad_id IS NOT NULL
                      AND a.is_active = TRUE
                    LIMIT :sql_cap
                    """
                ),
                {"pattern": pattern, "sql_cap": _SQL_ROW_CAP},
            )
        ).all()

    owned: list[str] = []
    for fb_ad_id, campaign_name, ad_name in rows:
        if not fb_ad_id:
            continue
        if not campaign_matches_owner(
            campaign_name=campaign_name or "", ad_name=ad_name or "", owner_tag=owner_tag
        ):
            continue
        owned.append(str(fb_ad_id))

    return owned[:limit], len(owned)


async def resolve_owner_ads_by_account(
    engine: AsyncEngine,
    *,
    owner_tag: str | None,
    campaign_ids: list[str],
    since: datetime | None = None,
    limit: int = MAX_BULK,
) -> AccountScopedAdResolution:
    """Активные fb_ad_id ВЫБРАННЫХ кампаний (по Meta campaign_id), owner-scoped.

    Используется автостартом кабинета по расписанию (money-критично): включаются
    объявления только тех кампаний, которые (1) пользователь выбрал галочками
    (campaign_ids = fb_campaigns.fb_campaign_id) и (2) принадлежат владельцу
    (owner-scoping через campaign_matches_owner). Двойная защита от включения
    чужих/не тех кампаний в общем кабинете.

    Фильтр свежести (``since``): ``fb_ads.is_active`` монотонно-истинный — он
    выставляется в TRUE на каждом скане и НИГДЕ не сбрасывается в FALSE, поэтому
    сам по себе НЕ отличает живые объявления от давно снятых/удалённых. Без
    фильтра автостарт каждое утро bulk-активировал бы ВСЕ когда-либо
    отсканированные ad_id выбранных кампаний (включая объявления прошлых
    cabinet-дней) → нецелевой открут бюджета. Если ``since`` задан, оставляем
    только объявления со свежим ``last_seen_at >= since`` (т.е. виденные последним
    сканом кабинета). ``None`` (дефолт) — фильтр свежести выключен.

    Если campaign_ids пуст, результат пуст. НЕ включаем всё подряд — это была бы дыра
    в безопасности (без фильтра сработало бы по всему кабинету).

    Возвращает объявления, сгруппированные по явному ``ad_account_id``. Строки
    без identity не попадают ни в одну группу и считаются отдельно, чтобы caller
    отклонил весь money-run без частичного включения.
    """
    clean_ids = [str(c).strip() for c in campaign_ids if c and str(c).strip()]
    if not clean_ids:
        return AccountScopedAdResolution({}, 0, 0)

    params: dict[str, object] = {"ids": clean_ids, "sql_cap": _SQL_ROW_CAP}
    freshness_clause = ""
    if since is not None:
        freshness_clause = "AND a.last_seen_at >= :since"
        params["since"] = since

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    f"""
                    SELECT DISTINCT
                        a.fb_ad_id, c.campaign_name, a.ad_name, c.ad_account_id
                    FROM fb_ads a
                    JOIN fb_adsets s ON s.id = a.adset_id
                    JOIN fb_campaigns c ON c.id = s.campaign_id
                    WHERE c.fb_campaign_id = ANY(:ids)
                      AND a.fb_ad_id IS NOT NULL
                      AND a.is_active = TRUE
                      {freshness_clause}
                    ORDER BY c.ad_account_id, a.fb_ad_id
                    LIMIT :sql_cap
                    """
                ),
                params,
            )
        ).all()

    grouped: dict[str, list[str]] = {}
    total = 0
    missing_account_count = 0
    selected_count = 0
    for fb_ad_id, campaign_name, ad_name, ad_account_id in rows:
        if not fb_ad_id:
            continue
        if not campaign_matches_owner(
            campaign_name=campaign_name or "", ad_name=ad_name or "", owner_tag=owner_tag
        ):
            continue
        total += 1
        try:
            account_id = require_ad_account_id(ad_account_id)
        except ValueError:
            missing_account_count += 1
            continue
        if selected_count >= limit:
            continue
        grouped.setdefault(account_id, []).append(str(fb_ad_id))
        selected_count += 1

    return AccountScopedAdResolution(
        ads_by_account={key: tuple(value) for key, value in grouped.items()},
        total=total,
        missing_account_count=missing_account_count,
    )


__all__ = [
    "MAX_BULK",
    "AccountScopedAdResolution",
    "AutostartActivationGuards",
    "AutostartTargetLocks",
    "GuardedAutostartExecution",
    "capture_autostart_activation_guards",
    "guarded_autostart_execution_boundary",
    "is_guarded_autostart_activation",
    "locked_autostart_targets",
    "merge_guarded_bulk_result",
    "resolve_owner_ad_ids",
    "resolve_owner_ads_by_account",
    "revalidate_autostart_activation_guards",
]
