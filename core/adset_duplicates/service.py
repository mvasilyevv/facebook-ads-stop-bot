# -*- coding: utf-8 -*-
"""Изолированный backend-контур быстрого дублирования adset.

Preview читает локальный каталог, точные metadata кабинета через read-only Meta GET
и Redis. Meta-записи появляются лишь после создания DRAFT и явного подтверждения
владельцем в Telegram.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import secrets
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.meta_api.mutations.set_adset_budget import MAX_DAILY_BUDGET_CENTS
from core.observer.queries import campaign_matches_owner, parse_owner_tags
from core.tasks.queue import DRAFT_TTL_SECONDS, create_task

PREVIEW_TTL_SECONDS = 15 * 60
MAX_CAMPAIGN_COUNT = 5
MAX_ADSETS_PER_CAMPAIGN = 10
MAX_SELECTED_ADS = 10
MAX_TOTAL_ADS = 50
MAX_NAME_LENGTH = 400

_PREVIEW_KEY_PREFIX = "adset_duplicate:preview:"
_TASK_KIND = "duplicate_adset_structure"
_TASK_TYPE = "meta_api_mutation"
_REQUESTED_BY = "api:adset_duplicate"
_DRAFT_NOTIFICATION_MARKER = "draft_notification_delivered"


class AdsetDuplicateError(ValueError):
    """Ожидаемая ошибка preview/draft с безопасным текстом для HTTP detail."""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(slots=True, frozen=True)
class SourceAd:
    fb_ad_id: str
    name: str
    delivery_status: str | None
    creative_thumb_url: str | None


@dataclass(slots=True, frozen=True)
class DuplicateSource:
    account_id: str
    campaign_id: str
    campaign_name: str
    adset_id: str
    adset_name: str
    source_ad_id: str
    source_ad_name: str
    ads: tuple[SourceAd, ...]
    selected_ad_ids: tuple[str, ...]
    source_daily_budget_cents: int | None


@dataclass(slots=True, frozen=True)
class AccountMetadata:
    id: str
    name: str
    currency: str
    timezone_name: str
    timezone_offset_hours: float


@dataclass(slots=True, frozen=True)
class StoredDuplicatePreview:
    preview: dict[str, Any]
    task_params: dict[str, Any]
    plan_digest: str
    idempotency_token: str
    consumed_task_id: int | None = None


@dataclass(slots=True, frozen=True)
class DuplicateTask:
    id: int
    status: str
    payload: dict[str, Any]
    result: dict[str, Any] | None
    attempt_count: int
    max_attempts: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


def validate_structure_caps(
    campaign_count: int,
    adsets_per_campaign: int,
    selected_ad_count: int,
) -> tuple[int, int, int]:
    """Возвращает (adsets, ads, total_objects), проверяя hard caps."""
    if isinstance(campaign_count, bool) or not 1 <= campaign_count <= MAX_CAMPAIGN_COUNT:
        raise AdsetDuplicateError(f"campaign_count должен быть 1..{MAX_CAMPAIGN_COUNT}")
    if (
        isinstance(adsets_per_campaign, bool)
        or not 1 <= adsets_per_campaign <= MAX_ADSETS_PER_CAMPAIGN
    ):
        raise AdsetDuplicateError(f"adsets_per_campaign должен быть 1..{MAX_ADSETS_PER_CAMPAIGN}")
    if selected_ad_count < 1:
        raise AdsetDuplicateError("selected_ad_ids не должен быть пустым")
    if selected_ad_count > MAX_SELECTED_ADS:
        raise AdsetDuplicateError(f"Можно выбрать максимум {MAX_SELECTED_ADS} объявлений")
    total_adsets = campaign_count * adsets_per_campaign
    total_ads = total_adsets * selected_ad_count
    if total_ads > MAX_TOTAL_ADS:
        raise AdsetDuplicateError(
            f"Структура создаёт {total_ads} объявлений; максимум {MAX_TOTAL_ADS}"
        )
    return total_adsets, total_ads, campaign_count + total_adsets + total_ads


def calculate_budget(
    *,
    budget_level: Literal["ABO", "CBO"],
    daily_budget_cents: int,
    campaign_count: int,
    total_adsets: int,
    currency: str,
) -> dict[str, Any]:
    """Считает суммарный дневной бюджет: CBO per campaign, ABO per adset."""
    if (
        isinstance(daily_budget_cents, bool)
        or not 1 <= daily_budget_cents <= MAX_DAILY_BUDGET_CENTS
    ):
        raise AdsetDuplicateError(f"daily_budget_cents должен быть 1..{MAX_DAILY_BUDGET_CENTS}")
    units = campaign_count if budget_level == "CBO" else total_adsets
    return {
        "level": budget_level,
        "unit_daily_budget_cents": daily_budget_cents,
        "total_daily_budget_cents": units * daily_budget_cents,
        "currency": currency,
    }


def build_schedule(
    *,
    requested_start_date: date | None,
    timezone_name: str,
    timezone_offset_hours: float,
    now: datetime | None = None,
) -> dict[str, str]:
    """Полночь кабинета + точный UTC start_time для Meta."""
    if not math.isfinite(timezone_offset_hours) or not -23 <= timezone_offset_hours <= 23:
        raise AdsetDuplicateError("Некорректный timezone offset кабинета", status_code=503)
    fallback_minutes = round(timezone_offset_hours * 60)
    sign = "+" if fallback_minutes >= 0 else "-"
    fallback_hours, fallback_remainder = divmod(abs(fallback_minutes), 60)
    fallback_offset_label = f"{sign}{fallback_hours:02d}:{fallback_remainder:02d}"
    display_timezone = timezone_name.strip()
    try:
        zone = ZoneInfo(display_timezone) if display_timezone else None
    except ZoneInfoNotFoundError:
        zone = None
    if zone is None:
        display_timezone = f"UTC{fallback_offset_label}"
        zone = timezone(timedelta(hours=timezone_offset_hours), name=display_timezone)

    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    local_today = current.astimezone(zone).date()
    start_date = requested_start_date or (local_today + timedelta(days=1))
    if start_date < local_today:
        raise AdsetDuplicateError("start_date не может быть в прошлом")

    local_start = datetime.combine(start_date, time.min, tzinfo=zone)
    utc_start = local_start.astimezone(UTC)
    if utc_start <= current.astimezone(UTC):
        raise AdsetDuplicateError("Время старта должно быть в будущем")
    actual_offset = local_start.utcoffset() or timedelta(0)
    actual_minutes = int(actual_offset.total_seconds() // 60)
    actual_sign = "+" if actual_minutes >= 0 else "-"
    actual_hours, actual_remainder = divmod(abs(actual_minutes), 60)
    offset_label = f"{actual_sign}{actual_hours:02d}:{actual_remainder:02d}"
    return {
        "timezone_name": display_timezone,
        "offset": offset_label,
        "start_time_utc": utc_start.isoformat().replace("+00:00", "Z"),
        "start_time_local": local_start.isoformat(),
    }


def generate_names(
    *,
    campaign_name_base: str,
    adset_name_base: str,
    campaign_count: int,
    adsets_per_campaign: int,
    start_date: date,
) -> dict[str, list[str]]:
    """Детерминированные уникальные имена target campaign/adset."""
    day_label = start_date.strftime("%d.%m")

    def _name(base: str, suffix: str) -> str:
        clean = " ".join(base.split()).strip(" |") or "Duplicate"
        room = MAX_NAME_LENGTH - len(suffix) - 3
        return f"{clean[:room].rstrip()} | {suffix}"

    campaign_names = [
        _name(campaign_name_base, f"DUP {day_label} C{campaign_index}")
        for campaign_index in range(1, campaign_count + 1)
    ]
    adset_names = [
        _name(adset_name_base, f"DUP {day_label} C{campaign_index}A{adset_index}")
        for campaign_index in range(1, campaign_count + 1)
        for adset_index in range(1, adsets_per_campaign + 1)
    ]
    return {"campaigns": campaign_names, "adsets": adset_names}


def _optional_cents(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


async def load_duplicate_source(
    engine: AsyncEngine,
    *,
    source_ad_id: str,
    selected_ad_ids: list[str],
) -> DuplicateSource:
    """Local source lookup + строгая принадлежность selected ads одному local adset."""
    async with engine.connect() as conn:
        source = (
            await conn.execute(
                text(
                    """
                    SELECT a.fb_ad_id, a.ad_name, s.id, s.fb_adset_id, s.adset_name,
                           s.daily_budget, c.fb_campaign_id, c.campaign_name,
                           c.ad_account_id
                    FROM fb_ads a
                    JOIN fb_adsets s ON s.id = a.adset_id
                    JOIN fb_campaigns c ON c.id = s.campaign_id
                    WHERE a.fb_ad_id = :source_ad_id
                    LIMIT 1
                    """
                ),
                {"source_ad_id": source_ad_id},
            )
        ).first()
        if source is None:
            raise AdsetDuplicateError("Исходное объявление не найдено", status_code=404)

        selected_stmt = text(
            """
            SELECT a.fb_ad_id, a.ad_name, a.adset_id,
                   a.delivery_status, a.creative_thumb_url
            FROM fb_ads a
            WHERE a.adset_id = :source_adset_pk
            """
        )
        selected_rows = (await conn.execute(selected_stmt, {"source_adset_pk": source[2]})).all()

    selected_by_id = {str(row[0]): row for row in selected_rows}
    missing = [ad_id for ad_id in selected_ad_ids if ad_id not in selected_by_id]
    if missing:
        raise AdsetDuplicateError(
            "Все selected_ad_ids должны существовать и принадлежать исходному adset; "
            "не подходят: " + ", ".join(missing)
        )

    # Исторические/DOM-scan строки могут не содержать fb_adset_id: scanner видит
    # название adset, campaign/ad ID и метрики, но не всегда сам Meta adset ID.
    # Preview восстановит недостающий hierarchy read-only Graph GET по source_ad_id.
    adset_id = str(source[3] or "").strip()
    campaign_id = str(source[6] or "").strip()
    account_id = str(source[8] or "").strip().removeprefix("act_")

    ads = tuple(
        SourceAd(
            fb_ad_id=str(row[0]),
            name=str(row[1] or ""),
            delivery_status=row[3],
            creative_thumb_url=row[4],
        )
        for row in sorted(selected_rows, key=lambda item: (str(item[1] or ""), str(item[0])))
    )
    return DuplicateSource(
        account_id=f"act_{account_id}",
        campaign_id=campaign_id,
        campaign_name=str(source[7] or ""),
        adset_id=adset_id,
        adset_name=str(source[4] or ""),
        source_ad_id=str(source[0]),
        source_ad_name=str(source[1] or ""),
        ads=ads,
        selected_ad_ids=tuple(selected_ad_ids),
        source_daily_budget_cents=_optional_cents(source[5]),
    )


async def resolve_duplicate_source_hierarchy(
    client: Any,
    source: DuplicateSource,
) -> DuplicateSource:
    """Hydrate missing account/campaign/adset IDs through a read-only Ad GET.

    The local catalog remains the source of names and the selectable sibling-ad
    list. Graph is consulted only when the scanner did not persist the full
    hierarchy. Conflicts with locally known IDs fail closed.
    """

    local_account_id = source.account_id.removeprefix("act_").strip()
    if local_account_id.isdigit() and source.campaign_id.isdigit() and source.adset_id.isdigit():
        return source

    response = await client.execute_graph_call(
        method="GET",
        endpoint=f"/{source.source_ad_id}",
        query_params={"fields": "id,account_id,campaign_id,adset_id"},
        ad_account_id=local_account_id if local_account_id.isdigit() else None,
    )
    returned_source_id = str(response.get("id") or "").strip()
    if returned_source_id != source.source_ad_id:
        raise AdsetDuplicateError(
            "Meta вернула другое исходное объявление при восстановлении hierarchy",
            status_code=409,
        )

    graph_account_id = str(response.get("account_id") or "").strip().removeprefix("act_")
    graph_campaign_id = str(response.get("campaign_id") or "").strip()
    graph_adset_id = str(response.get("adset_id") or "").strip()
    if not all(value.isdigit() for value in (graph_account_id, graph_campaign_id, graph_adset_id)):
        raise AdsetDuplicateError(
            "Meta не вернула полный hierarchy исходного объявления (account/campaign/adset IDs)",
            status_code=422,
        )

    known_ids = {
        "кабинета": (local_account_id, graph_account_id),
        "кампании": (source.campaign_id, graph_campaign_id),
        "adset": (source.adset_id, graph_adset_id),
    }
    for label, (local_id, graph_id) in known_ids.items():
        if local_id and local_id.isdigit() and local_id != graph_id:
            raise AdsetDuplicateError(
                f"Локальный ID {label} не совпадает с Meta; обнови источник",
                status_code=409,
            )

    return replace(
        source,
        account_id=f"act_{graph_account_id}",
        campaign_id=graph_campaign_id,
        adset_id=graph_adset_id,
    )


async def fetch_account_metadata(client: Any, account_id: str) -> AccountMetadata:
    """Read-only Graph lookup точных name/currency/timezone source account."""
    response = await client.execute_graph_call(
        method="GET",
        endpoint=f"/{account_id}",
        query_params={"fields": "id,name,currency,timezone_name,timezone_offset_hours_utc"},
        ad_account_id=account_id,
    )
    returned_id = str(response.get("id") or "").strip()
    expected_numeric = account_id.removeprefix("act_")
    if returned_id.removeprefix("act_") != expected_numeric:
        raise AdsetDuplicateError("Meta вернула другой рекламный кабинет", status_code=503)
    currency = str(response.get("currency") or "").strip().upper()
    if not currency:
        raise AdsetDuplicateError("Meta не вернула currency кабинета", status_code=503)
    try:
        raw_offset = response["timezone_offset_hours_utc"]
        if isinstance(raw_offset, bool):
            raise TypeError("bool timezone offset")
        offset = float(raw_offset)
    except (KeyError, TypeError, ValueError) as exc:
        raise AdsetDuplicateError(
            "Meta не вернула timezone offset кабинета", status_code=503
        ) from exc
    timezone_name = str(response.get("timezone_name") or "").strip()
    return AccountMetadata(
        id=f"act_{expected_numeric}",
        name=str(response.get("name") or account_id).strip() or account_id,
        currency=currency,
        timezone_name=timezone_name,
        timezone_offset_hours=offset,
    )


def build_duplicate_preview(
    *,
    source: DuplicateSource,
    account: AccountMetadata,
    campaign_count: int,
    adsets_per_campaign: int,
    budget_level: Literal["ABO", "CBO"],
    daily_budget_cents: int,
    requested_start_date: date | None,
    campaign_name_base: str | None,
    adset_name_base: str | None,
    owner_tag: str | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pure composition preview + flat params, совместимые с executor."""
    if not all(
        value.isdigit()
        for value in (
            source.account_id.removeprefix("act_"),
            source.campaign_id,
            source.adset_id,
        )
    ):
        raise AdsetDuplicateError(
            "Не удалось определить полный Meta hierarchy исходного объявления",
            status_code=422,
        )
    total_adsets, total_ads, total_objects = validate_structure_caps(
        campaign_count, adsets_per_campaign, len(source.selected_ad_ids)
    )
    schedule = build_schedule(
        requested_start_date=requested_start_date,
        timezone_name=account.timezone_name,
        timezone_offset_hours=account.timezone_offset_hours,
        now=now,
    )
    start_date = date.fromisoformat(schedule["start_time_local"][:10])
    campaign_base = campaign_name_base or source.campaign_name
    owner_tags = parse_owner_tags(owner_tag)
    owner_tag_added = False
    if owner_tags and not campaign_matches_owner(
        campaign_name=campaign_base,
        ad_name="",
        owner_tag=owner_tag,
    ):
        first_owner_tag = owner_tags[0]
        day_label = start_date.strftime("%d.%m")
        longest_suffix = max(
            f"DUP {day_label} C{campaign_index}" for campaign_index in range(1, campaign_count + 1)
        )
        max_base_length = MAX_NAME_LENGTH - len(longest_suffix) - 3
        owner_suffix = f" | {first_owner_tag}"
        if len(owner_suffix) >= max_base_length:
            raise AdsetDuplicateError(
                "Первый owner-tag слишком длинный для имени новой кампании",
                status_code=503,
            )
        clean_base = " ".join(campaign_base.split()).strip(" |") or "Duplicate"
        room = max_base_length - len(owner_suffix)
        campaign_base = f"{clean_base[:room].rstrip(' |')}{owner_suffix}"
        owner_tag_added = True
    names = generate_names(
        campaign_name_base=campaign_base,
        adset_name_base=adset_name_base or source.adset_name,
        campaign_count=campaign_count,
        adsets_per_campaign=adsets_per_campaign,
        start_date=start_date,
    )
    budget = calculate_budget(
        budget_level=budget_level,
        daily_budget_cents=daily_budget_cents,
        campaign_count=campaign_count,
        total_adsets=total_adsets,
        currency=account.currency,
    )
    warnings = [
        "Все новые объекты создаются через DRAFT и требуют подтверждения владельцем в Telegram.",
    ]
    if owner_tag_added:
        warnings.append(
            f"Owner-tag {owner_tags[0]!r} добавлен в имена новых кампаний "
            "для сохранения owner-scope."
        )
    if source.source_daily_budget_cents not in (None, daily_budget_cents):
        warnings.append("Выбранный дневной бюджет отличается от бюджета исходного adset.")
    preview = {
        "source": {
            "account": {
                "id": account.id,
                "name": account.name,
                "currency": account.currency,
            },
            "campaign": {"id": source.campaign_id, "name": source.campaign_name},
            "adset": {"id": source.adset_id, "name": source.adset_name},
            "ads": [
                {
                    "id": ad.fb_ad_id,
                    "fb_ad_id": ad.fb_ad_id,
                    "name": ad.name,
                    "delivery_status": ad.delivery_status,
                    "creative_thumb_url": ad.creative_thumb_url,
                }
                for ad in source.ads
            ],
        },
        "format_code": f"{campaign_count}-{adsets_per_campaign}-{len(source.selected_ad_ids)}",
        "counts": {
            "campaigns": campaign_count,
            "adsets": total_adsets,
            "ads": total_ads,
            "total_objects": total_objects,
        },
        "budget": budget,
        "schedule": schedule,
        "generated_names": names,
        "warnings": warnings,
    }
    task_params = {
        "source_ad_id": source.source_ad_id,
        "source_campaign_id": source.campaign_id,
        "source_adset_id": source.adset_id,
        "selected_ad_ids": list(source.selected_ad_ids),
        "campaign_count": campaign_count,
        "adsets_per_campaign": adsets_per_campaign,
        "budget_level": budget_level,
        "daily_budget_cents": daily_budget_cents,
        "start_time": schedule["start_time_utc"],
        "campaign_names": names["campaigns"],
        "adset_names": names["adsets"],
        "format_code": preview["format_code"],
        "counts": preview["counts"],
    }
    return preview, task_params


def _digest_task_params(task_params: dict[str, Any]) -> str:
    canonical = json.dumps(task_params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def save_stored_preview(
    redis: Any,
    *,
    preview: dict[str, Any],
    task_params: dict[str, Any],
    idempotency_token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Сохраняет непрозрачный preview-token в Redis ровно на 15 минут."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    preview_token = secrets.token_urlsafe(32)
    plan_digest = _digest_task_params(task_params)
    expires_at = current + timedelta(seconds=PREVIEW_TTL_SECONDS)
    public_preview = {
        "preview_token": preview_token,
        **preview,
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
    }
    stored = StoredDuplicatePreview(
        preview=public_preview,
        task_params=task_params,
        plan_digest=plan_digest,
        idempotency_token=idempotency_token,
        consumed_task_id=None,
    )
    await redis.set(
        f"{_PREVIEW_KEY_PREFIX}{preview_token}",
        json.dumps(
            {
                "preview": stored.preview,
                "task_params": stored.task_params,
                "plan_digest": stored.plan_digest,
                "idempotency_token": stored.idempotency_token,
                "consumed_task_id": stored.consumed_task_id,
            },
            separators=(",", ":"),
        ),
        ex=PREVIEW_TTL_SECONDS,
    )
    return public_preview


async def load_stored_preview(redis: Any, preview_token: str) -> StoredDuplicatePreview:
    """Читает подписанный сервером план; клиент не может подменить money-поля."""
    raw = await redis.get(f"{_PREVIEW_KEY_PREFIX}{preview_token}")
    if raw is None:
        raise AdsetDuplicateError("Preview истёк или не найден", status_code=410)
    try:
        data = json.loads(raw)
        stored = StoredDuplicatePreview(
            preview=dict(data["preview"]),
            task_params=dict(data["task_params"]),
            plan_digest=str(data["plan_digest"]),
            idempotency_token=str(data["idempotency_token"]),
            consumed_task_id=(
                int(data["consumed_task_id"]) if data.get("consumed_task_id") is not None else None
            ),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AdsetDuplicateError("Preview повреждён; создай новый", status_code=410) from exc
    if _digest_task_params(stored.task_params) != stored.plan_digest:
        raise AdsetDuplicateError("Preview повреждён; создай новый", status_code=410)
    return stored


async def mark_preview_consumed(
    redis: Any,
    *,
    preview_token: str,
    stored: StoredDuplicatePreview,
    task_id: int,
) -> None:
    """Оставляет token как consumed mapping до исходного TTL для double-submit."""
    key = f"{_PREVIEW_KEY_PREFIX}{preview_token}"
    ttl = int(await redis.ttl(key))
    if ttl <= 0:
        return
    await redis.set(
        key,
        json.dumps(
            {
                "preview": stored.preview,
                "task_params": stored.task_params,
                "plan_digest": stored.plan_digest,
                "idempotency_token": stored.idempotency_token,
                "consumed_task_id": int(task_id),
            },
            separators=(",", ":"),
        ),
        ex=ttl,
    )


def _idempotency_key(token: str) -> str:
    return f"meta:duplicate-adset:{token}"[:128]


async def create_duplicate_draft(
    engine: AsyncEngine,
    *,
    stored: StoredDuplicatePreview,
) -> tuple[int, bool]:
    """Создаёт необратимый meta draft с max_attempts=1; повтор возвращает тот же id."""
    payload = {
        "mutation_kind": _TASK_KIND,
        "target_id": stored.task_params["source_adset_id"],
        "params": {**stored.task_params, "plan_digest": stored.plan_digest},
        "ad_account_id": stored.preview["source"]["account"]["id"],
    }
    key = _idempotency_key(stored.idempotency_token)
    task_id = await create_task(
        engine,
        task_type=_TASK_TYPE,
        status="draft",
        idempotency_key=key,
        payload=payload,
        requested_by=_REQUESTED_BY,
        max_attempts=1,
    )
    if task_id is not None:
        return task_id, True

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, payload
                    FROM task_queue
                    WHERE idempotency_key = :key
                      AND task_type = 'meta_api_mutation'
                    """
                ),
                {"key": key},
            )
        ).first()
    if row is None:
        raise AdsetDuplicateError("Не удалось создать DRAFT", status_code=409)
    existing_payload = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    existing_digest = (existing_payload.get("params") or {}).get("plan_digest")
    if existing_digest != stored.plan_digest:
        raise AdsetDuplicateError(
            "idempotency_token уже использован для другого плана", status_code=409
        )
    return int(row[0]), False


async def get_duplicate_task(engine: AsyncEngine, task_id: int) -> DuplicateTask | None:
    """Загружает только задачу duplicate_adset_structure, не чужой task_queue row."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, status, payload, result, attempt_count, max_attempts,
                           last_error, created_at, updated_at, completed_at
                    FROM task_queue
                    WHERE id = :task_id
                      AND task_type = 'meta_api_mutation'
                      AND payload->>'mutation_kind' = 'duplicate_adset_structure'
                    """
                ),
                {"task_id": int(task_id)},
            )
        ).first()
    if row is None:
        return None
    payload = row[2] if isinstance(row[2], dict) else json.loads(row[2])
    result = row[3] if isinstance(row[3], dict) or row[3] is None else json.loads(row[3])
    return DuplicateTask(
        id=int(row[0]),
        status=str(row[1]),
        payload=payload,
        result=result,
        attempt_count=int(row[4] or 0),
        max_attempts=int(row[5] or 1),
        last_error=row[6],
        created_at=row[7],
        updated_at=row[8],
        completed_at=row[9],
    )


def duplicate_draft_notification_delivered(task: DuplicateTask) -> bool:
    """Durable task-level marker успешной доставки Telegram-кнопок."""
    return bool((task.result or {}).get(_DRAFT_NOTIFICATION_MARKER) is True)


async def mark_duplicate_draft_notification_delivered(
    engine: AsyncEngine,
    *,
    task_id: int,
) -> bool:
    """Атомарно записать delivery marker в task_queue.result без потери checkpoint."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET result = COALESCE(result, '{}'::JSONB) || jsonb_build_object(
                        'draft_notification_delivered', TRUE,
                        'draft_notification_delivered_at', NOW()
                    ),
                    updated_at = NOW()
                WHERE id = :task_id
                  AND task_type = 'meta_api_mutation'
                  AND payload->>'mutation_kind' = 'duplicate_adset_structure'
                """
            ),
            {"task_id": int(task_id)},
        )
    return (result.rowcount or 0) > 0


def serialize_duplicate_task(task: DuplicateTask) -> dict[str, Any]:
    """Канонический lowercase status + прогресс/созданные Meta IDs."""
    params = task.payload.get("params") or {}
    counts = params.get("counts") or {}
    total = int(counts.get("total_objects") or 0)
    result = task.result or {}
    created = result.get("created_ids") if isinstance(result, dict) else None
    created_meta_ids = created if isinstance(created, dict) else {}
    created_count = sum(
        len(values) for values in created_meta_ids.values() if isinstance(values, list)
    )
    checkpoint_phase = (
        str(result.get("phase") or "running")
        if result.get("checkpoint_type") == "duplicate_adset_structure"
        else None
    )
    recovery_requested = result.get("recovery_requested") is True

    def checkpoint_progress(*, default_message: str) -> dict[str, Any]:
        return {
            "phase": checkpoint_phase or "running",
            "completed": created_count,
            "total": total,
            "message": default_message,
        }

    if task.status == "draft":
        progress = {
            "phase": "awaiting_confirmation",
            "completed": 0,
            "total": total,
            "message": "Ожидает подтверждения в Telegram",
        }
    elif task.status in {"pending", "retrying"}:
        if recovery_requested:
            progress = checkpoint_progress(
                default_message="Crash-recovery: повторная постановка созданных объектов на PAUSED"
            )
        elif checkpoint_phase:
            progress = checkpoint_progress(default_message="Продолжение обработки checkpoint")
        else:
            progress = {
                "phase": "queued",
                "completed": 0,
                "total": total,
                "message": "В очереди",
            }
    elif task.status == "running":
        if recovery_requested:
            progress = checkpoint_progress(
                default_message="Crash-recovery: ставим созданные объекты на PAUSED"
            )
        elif checkpoint_phase:
            progress = checkpoint_progress(default_message="Создание структуры в Meta")
        else:
            progress = (
                result.get("progress")
                if isinstance(result.get("progress"), dict)
                else {
                    "phase": "running",
                    "completed": 0,
                    "total": total,
                    "message": "Создание структуры в Meta",
                }
            )
    elif task.status == "succeeded":
        progress = {
            "phase": "completed",
            "completed": total,
            "total": total,
            "message": "Структура создана",
        }
    elif checkpoint_phase:
        progress = checkpoint_progress(
            default_message=(
                "Checkpointed объекты поставлены на PAUSED"
                if checkpoint_phase == "recovery_paused"
                else "Операция завершилась на checkpoint"
            )
        )
    else:
        progress = None

    expires_at = None
    if task.status == "draft":
        expires_at = (task.created_at + timedelta(seconds=DRAFT_TTL_SECONDS)).isoformat()
    return {
        "task_id": task.id,
        "status": task.status,
        "progress": progress,
        "created_meta_ids": created_meta_ids,
        "error": task.last_error,
        "expires_at": expires_at,
    }


def render_draft_notification(task_id: int, stored: StoredDuplicatePreview) -> str:
    """Короткое HTML-превью для owner DM; все DB/user strings экранируются."""
    preview = stored.preview
    source = preview["source"]
    budget = preview["budget"]
    schedule = preview["schedule"]
    selected_ids = set(stored.task_params["selected_ad_ids"])
    selected_ads = [ad for ad in source["ads"] if ad["fb_ad_id"] in selected_ids]

    def _short(value: Any, limit: int) -> str:
        clean = str(value or "")
        return clean if len(clean) <= limit else clean[: limit - 1] + "…"

    selected_lines = "\n".join(
        f"• <code>{html.escape(ad['fb_ad_id'])}</code> {html.escape(_short(ad['name'], 100))}"
        for ad in selected_ads
    )
    campaign_lines = "\n".join(
        f"• {html.escape(_short(name, 120))}" for name in preview["generated_names"]["campaigns"]
    )
    adset_names = preview["generated_names"]["adsets"]
    adset_sample = "\n".join(f"• {html.escape(_short(name, 120))}" for name in adset_names[:2])
    if len(adset_names) > 2:
        adset_sample += f"\n• … ещё {len(adset_names) - 2}"
    unit_amount = budget["unit_daily_budget_cents"] / 100
    total_amount = budget["total_daily_budget_cents"] / 100
    return (
        f"📝 <b>Черновик #{task_id}: дубль adset {html.escape(preview['format_code'])}</b>\n"
        f"Кампания: <code>{html.escape(source['campaign']['name'])}</code>\n"
        f"Adset: <code>{html.escape(source['adset']['name'])}</code>\n"
        f"Итог: <b>{preview['counts']['campaigns']} камп. / "
        f"{preview['counts']['adsets']} adset / {preview['counts']['ads']} ads</b>\n\n"
        f"<b>Выбранные объявления ({len(selected_ads)}):</b>\n{selected_lines}\n\n"
        f"<b>Новые кампании:</b>\n{campaign_lines}\n"
        f"<b>Новые adset ({len(adset_names)}):</b>\n{adset_sample}\n\n"
        f"Бюджет {budget['level']}: <b>{unit_amount:.2f} {budget['currency']}</b> на единицу; "
        f"итого/день <b>{total_amount:.2f} {budget['currency']}</b>\n"
        f"Старт local: <code>{html.escape(schedule['start_time_local'])}</code>\n"
        f"Старт UTC: <code>{html.escape(schedule['start_time_utc'])}</code>\n\n"
        "Подтверди ✅ / ❌."
    )


__all__ = [
    "MAX_ADSETS_PER_CAMPAIGN",
    "MAX_CAMPAIGN_COUNT",
    "MAX_SELECTED_ADS",
    "MAX_TOTAL_ADS",
    "PREVIEW_TTL_SECONDS",
    "AdsetDuplicateError",
    "AccountMetadata",
    "DuplicateSource",
    "StoredDuplicatePreview",
    "build_duplicate_preview",
    "build_schedule",
    "calculate_budget",
    "create_duplicate_draft",
    "duplicate_draft_notification_delivered",
    "fetch_account_metadata",
    "generate_names",
    "get_duplicate_task",
    "load_duplicate_source",
    "load_stored_preview",
    "mark_preview_consumed",
    "mark_duplicate_draft_notification_delivered",
    "resolve_duplicate_source_hierarchy",
    "render_draft_notification",
    "save_stored_preview",
    "serialize_duplicate_task",
    "validate_structure_caps",
]
