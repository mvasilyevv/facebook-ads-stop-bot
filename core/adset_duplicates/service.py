# -*- coding: utf-8 -*-
"""Изолированный backend-контур быстрого дублирования adset.

Preview читает локальный каталог и точные metadata кабинета через read-only Meta
GET. Непрозрачный capability token, канонический план и его запуск принадлежат
PostgreSQL; Meta-записи появляются лишь после явного запуска из защищённой панели.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import secrets
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.adset_duplicates.plan_integrity import (
    DUPLICATE_ADSET_STRUCTURE_KIND,
    canonical_duplicate_execution_payload,
    duplicate_execution_plan_digest,
    duplicate_execution_plan_digest_matches,
)
from core.meta_api.account_tz import validated_timezone_name
from core.meta_api.budget_limits import checked_daily_budget_minor_units
from core.meta_api.identity import require_ad_account_id
from core.money import UnsupportedCurrencyExponentError, currency_exponent
from core.observer.queries import campaign_matches_owner, parse_owner_tags
from core.tasks.queue import create_task

PREVIEW_TTL_SECONDS = 15 * 60
MAX_CAMPAIGN_COUNT = 5
MAX_ADSETS_PER_CAMPAIGN = 10
MAX_SELECTED_ADS = 10
MAX_TOTAL_ADS = 50
MAX_NAME_LENGTH = 400

_TASK_KIND = DUPLICATE_ADSET_STRUCTURE_KIND
_TASK_TYPE = "meta_api_mutation"
_PREVIEW_TOKEN_BYTES = 32
_PREVIEW_TOKEN_LENGTH = 43
_MAX_PRINCIPAL_LENGTH = 64


class AdsetDuplicateError(ValueError):
    """Ожидаемая ошибка preview/launch с безопасным текстом для HTTP detail."""

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
    source_daily_budget_minor_units: int | None


@dataclass(slots=True, frozen=True)
class AccountMetadata:
    id: str
    name: str
    currency: str
    currency_exponent: int
    timezone_name: str
    timezone_offset_hours: float


@dataclass(slots=True, frozen=True)
class StoredDuplicatePreview:
    preview: dict[str, Any]
    task_payload: dict[str, Any]
    plan_digest: bytes
    idempotency_key: str
    principal: str
    expires_at: datetime
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
    daily_budget: object,
    campaign_count: int,
    total_adsets: int,
    currency: str,
    currency_exponent: int,
) -> tuple[dict[str, Any], int]:
    """Считает суммарный дневной бюджет: CBO per campaign, ABO per adset."""
    try:
        code, exponent, unit_amount, unit_minor_units = checked_daily_budget_minor_units(
            daily_budget,
            currency=currency,
            currency_exponent=currency_exponent,
        )
    except ValueError as exc:
        raise AdsetDuplicateError("Дневной бюджет не соответствует валюте кабинета") from exc
    units = campaign_count if budget_level == "CBO" else total_adsets
    total_amount = unit_amount * units
    return (
        {
            "level": budget_level,
            "unit_daily_budget": f"{unit_amount:.{exponent}f}",
            "total_daily_budget": f"{total_amount:.{exponent}f}",
            "currency": code,
            "currency_exponent": exponent,
        },
        unit_minor_units,
    )


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
    display_timezone = validated_timezone_name(timezone_name)
    if display_timezone is None:
        raise AdsetDuplicateError(
            "Meta не вернула валидный IANA timezone_name кабинета",
            status_code=503,
        )
    zone = ZoneInfo(display_timezone)

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


def _optional_minor_units(value: Any) -> int | None:
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
        source_daily_budget_minor_units=_optional_minor_units(source[5]),
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

    try:
        local_account_id = require_ad_account_id(source.account_id)
    except ValueError as exc:
        raise AdsetDuplicateError(
            "Каталог не содержит explicit ad_account_id исходного объявления",
            status_code=409,
        ) from exc
    if source.campaign_id.isdigit() and source.adset_id.isdigit():
        return source

    response = await client.execute_graph_call(
        method="GET",
        endpoint=f"/{source.source_ad_id}",
        query_params={"fields": "id,account_id,campaign_id,adset_id"},
        ad_account_id=local_account_id,
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
        exponent = currency_exponent(currency)
    except UnsupportedCurrencyExponentError as exc:
        raise AdsetDuplicateError(
            "Meta вернула currency без проверенного minor-unit exponent",
            status_code=503,
        ) from exc
    try:
        raw_offset = response["timezone_offset_hours_utc"]
        if isinstance(raw_offset, bool):
            raise TypeError("bool timezone offset")
        offset = float(raw_offset)
    except (KeyError, TypeError, ValueError) as exc:
        raise AdsetDuplicateError(
            "Meta не вернула timezone offset кабинета", status_code=503
        ) from exc
    timezone_name = validated_timezone_name(response.get("timezone_name"))
    if timezone_name is None:
        raise AdsetDuplicateError(
            "Meta не вернула валидный IANA timezone_name кабинета",
            status_code=503,
        )
    return AccountMetadata(
        id=f"act_{expected_numeric}",
        name=str(response.get("name") or account_id).strip() or account_id,
        currency=currency,
        currency_exponent=exponent,
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
    daily_budget: object,
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
    budget, daily_budget_minor_units = calculate_budget(
        budget_level=budget_level,
        daily_budget=daily_budget,
        campaign_count=campaign_count,
        total_adsets=total_adsets,
        currency=account.currency,
        currency_exponent=account.currency_exponent,
    )
    warnings = [
        "Создание начнётся только после явного подтверждения в web-preview.",
    ]
    if owner_tag_added:
        warnings.append(
            f"Owner-tag {owner_tags[0]!r} добавлен в имена новых кампаний "
            "для сохранения owner-scope."
        )
    if source.source_daily_budget_minor_units not in (
        None,
        daily_budget_minor_units,
    ):
        warnings.append("Выбранный дневной бюджет отличается от бюджета исходного adset.")
    preview = {
        "source": {
            "account": {
                "id": account.id,
                "name": account.name,
                "currency": account.currency,
                "currency_exponent": account.currency_exponent,
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
        "daily_budget": budget["unit_daily_budget"],
        "currency": budget["currency"],
        "currency_exponent": budget["currency_exponent"],
        "start_time": schedule["start_time_utc"],
        "campaign_names": names["campaigns"],
        "adset_names": names["adsets"],
        "format_code": preview["format_code"],
        "counts": preview["counts"],
    }
    return preview, task_params


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _validated_principal(principal: str) -> str:
    normalized = str(principal).strip()
    if not normalized or len(normalized) > _MAX_PRINCIPAL_LENGTH:
        raise AdsetDuplicateError("Недопустимый operator principal", status_code=403)
    return normalized


def _new_preview_token() -> tuple[str, bytes]:
    token_bytes = secrets.token_bytes(_PREVIEW_TOKEN_BYTES)
    token = base64.urlsafe_b64encode(token_bytes).rstrip(b"=").decode("ascii")
    if len(token) != _PREVIEW_TOKEN_LENGTH:  # pragma: no cover - invariant of 32 bytes
        raise RuntimeError("unexpected duplicate preview token length")
    return token, hashlib.sha256(token_bytes).digest()


def _preview_token_digest(preview_token: str) -> bytes:
    """Decode one canonical 32-byte Base64URL token and return its SHA-256."""
    try:
        encoded = preview_token.encode("ascii")
        if len(encoded) != _PREVIEW_TOKEN_LENGTH:
            raise ValueError("invalid token length")
        token_bytes = base64.b64decode(
            encoded + b"=",
            altchars=b"-_",
            validate=True,
        )
        canonical = base64.urlsafe_b64encode(token_bytes).rstrip(b"=")
    except (UnicodeEncodeError, ValueError) as exc:
        raise AdsetDuplicateError("Preview истёк или не найден", status_code=410) from exc
    if len(token_bytes) != _PREVIEW_TOKEN_BYTES or not secrets.compare_digest(
        canonical,
        encoded,
    ):
        raise AdsetDuplicateError("Preview истёк или не найден", status_code=410)
    return hashlib.sha256(token_bytes).digest()


def _idempotency_key(*, principal: str, token: str) -> str:
    identity = hashlib.sha256(
        principal.encode("utf-8") + b"\x00" + token.encode("utf-8")
    ).hexdigest()
    return f"meta:duplicate-adset:{identity}"


def _execution_task_payload(
    *,
    preview: dict[str, Any],
    task_params: dict[str, Any],
) -> dict[str, Any]:
    """Full immutable execution payload before embedding its own digest."""
    try:
        source_account = preview["source"]["account"]["id"]
        source_adset_id = str(task_params["source_adset_id"])
    except (KeyError, TypeError) as exc:
        raise AdsetDuplicateError("Preview повреждён; создай новый", status_code=410) from exc
    if not source_adset_id.isdigit():
        raise AdsetDuplicateError("Preview повреждён; создай новый", status_code=410)
    try:
        ad_account_id = require_ad_account_id(source_account)
    except ValueError as exc:
        raise AdsetDuplicateError("Preview повреждён; создай новый", status_code=410) from exc
    try:
        return canonical_duplicate_execution_payload(
            mutation_kind=_TASK_KIND,
            target_id=source_adset_id,
            params=task_params,
            ad_account_id=ad_account_id,
        )
    except (TypeError, ValueError) as exc:
        raise AdsetDuplicateError("Preview повреждён; создай новый", status_code=410) from exc


def _ready_task_payload(
    *,
    preview: dict[str, Any],
    task_params: dict[str, Any],
) -> tuple[dict[str, Any], bytes]:
    payload = _execution_task_payload(
        preview=preview,
        task_params=task_params,
    )
    plan_digest = duplicate_execution_plan_digest(**payload)
    payload["params"] = {
        **task_params,
        "plan_digest": plan_digest.hex(),
    }
    return payload, plan_digest


def _task_payload(stored: StoredDuplicatePreview) -> dict[str, Any]:
    return json.loads(_canonical_json(stored.task_payload))


def _validate_task_payload(
    payload: object,
    *,
    stored: StoredDuplicatePreview,
) -> None:
    if not isinstance(payload, dict):
        raise AdsetDuplicateError(
            "idempotency_key связан с повреждённой задачей",
            status_code=409,
        )
    if payload != stored.task_payload:
        raise AdsetDuplicateError(
            "idempotency_token уже использован для другого плана",
            status_code=409,
        )


def _validate_stored_task_payload(
    task_payload: dict[str, Any],
    *,
    plan_digest: bytes,
) -> None:
    params = task_payload.get("params")
    if (
        task_payload.get("mutation_kind") != _TASK_KIND
        or not isinstance(params, dict)
        or not str(task_payload.get("target_id") or "").isdigit()
        or params.get("source_adset_id") != task_payload.get("target_id")
    ):
        raise AdsetDuplicateError("Preview повреждён; создай новый", status_code=410)
    try:
        canonical_account_id = require_ad_account_id(task_payload.get("ad_account_id"))
    except ValueError as exc:
        raise AdsetDuplicateError("Preview повреждён; создай новый", status_code=410) from exc
    if canonical_account_id != task_payload.get("ad_account_id"):
        raise AdsetDuplicateError("Preview повреждён; создай новый", status_code=410)

    try:
        expected_digest = duplicate_execution_plan_digest(
            mutation_kind=str(task_payload.get("mutation_kind") or ""),
            target_id=str(task_payload.get("target_id") or ""),
            params=params,
            ad_account_id=task_payload.get("ad_account_id"),
        )
        embedded_digest_matches = duplicate_execution_plan_digest_matches(
            mutation_kind=str(task_payload.get("mutation_kind") or ""),
            target_id=str(task_payload.get("target_id") or ""),
            params=params,
            ad_account_id=task_payload.get("ad_account_id"),
            plan_digest=params.get("plan_digest"),
        )
    except (TypeError, ValueError) as exc:
        raise AdsetDuplicateError("Preview повреждён; создай новый", status_code=410) from exc
    if (
        not embedded_digest_matches
        or len(plan_digest) != 32
        or not secrets.compare_digest(expected_digest, plan_digest)
    ):
        raise AdsetDuplicateError("Preview повреждён; создай новый", status_code=410)


async def save_stored_preview(
    engine: AsyncEngine,
    *,
    preview: dict[str, Any],
    task_params: dict[str, Any],
    idempotency_token: str,
    principal: str,
) -> dict[str, Any]:
    """Persist an opaque capability; PostgreSQL's clock owns its exact expiry."""
    normalized_principal = _validated_principal(principal)
    task_payload, plan_digest = _ready_task_payload(
        preview=preview,
        task_params=task_params,
    )
    idempotency_key = _idempotency_key(
        principal=normalized_principal,
        token=idempotency_token,
    )

    async with engine.begin() as conn:
        for _attempt in range(3):
            preview_token, token_digest = _new_preview_token()
            row = (
                await conn.execute(
                    text(
                        """
                        INSERT INTO adset_duplicate_previews (
                            token_digest,
                            principal,
                            preview,
                            task_payload,
                            plan_digest,
                            idempotency_key,
                            expires_at
                        )
                        VALUES (
                            :token_digest,
                            :principal,
                            CAST(:preview AS JSONB),
                            CAST(:task_payload AS JSONB),
                            :plan_digest,
                            :idempotency_key,
                            clock_timestamp()
                                + CAST(:ttl_seconds AS integer) * INTERVAL '1 second'
                        )
                        ON CONFLICT (token_digest) DO NOTHING
                        RETURNING expires_at
                        """
                    ),
                    {
                        "token_digest": token_digest,
                        "principal": normalized_principal,
                        "preview": _canonical_json(preview).decode("utf-8"),
                        "task_payload": _canonical_json(task_payload).decode("utf-8"),
                        "plan_digest": plan_digest,
                        "idempotency_key": idempotency_key,
                        "ttl_seconds": PREVIEW_TTL_SECONDS,
                    },
                )
            ).first()
            if row is not None:
                expires_at = row[0]
                return {
                    "preview_token": preview_token,
                    **preview,
                    "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
                }
    raise AdsetDuplicateError("Не удалось сохранить preview", status_code=503)


def _stored_from_row(
    row: Any,
    *,
    principal: str,
    allow_consumed_expired: bool,
    db_now: datetime | None = None,
) -> StoredDuplicatePreview:
    try:
        row_principal = str(row.principal)
        normalized_principal = _validated_principal(principal)
        if not secrets.compare_digest(
            row_principal.encode("utf-8"),
            normalized_principal.encode("utf-8"),
        ):
            raise AdsetDuplicateError("Preview принадлежит другому оператору", status_code=403)
        preview = row.preview if isinstance(row.preview, dict) else json.loads(row.preview)
        task_payload = (
            row.task_payload if isinstance(row.task_payload, dict) else json.loads(row.task_payload)
        )
        stored = StoredDuplicatePreview(
            preview=dict(preview),
            task_payload=dict(task_payload),
            plan_digest=bytes(row.plan_digest),
            idempotency_key=str(row.idempotency_key),
            principal=row_principal,
            expires_at=row.expires_at,
            consumed_task_id=int(row.task_id) if row.task_id is not None else None,
        )
        observed_db_now = db_now if db_now is not None else row.db_now
        if not isinstance(observed_db_now, datetime):
            raise TypeError("invalid database timestamp")
    except AdsetDuplicateError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AdsetDuplicateError("Preview повреждён; создай новый", status_code=410) from exc
    _validate_stored_task_payload(
        stored.task_payload,
        plan_digest=stored.plan_digest,
    )
    if observed_db_now >= stored.expires_at and not (
        allow_consumed_expired and stored.consumed_task_id is not None
    ):
        raise AdsetDuplicateError("Preview истёк или не найден", status_code=410)
    return stored


async def load_stored_preview(
    engine: AsyncEngine,
    preview_token: str,
    *,
    principal: str,
) -> StoredDuplicatePreview:
    """Read a server-owned plan for diagnostics without consuming it."""
    token_digest = _preview_token_digest(preview_token)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT principal, preview, task_payload, plan_digest,
                           idempotency_key, expires_at, task_id,
                           clock_timestamp() AS db_now
                    FROM adset_duplicate_previews
                    WHERE token_digest = :token_digest
                    """
                ),
                {"token_digest": token_digest},
            )
        ).first()
    if row is None:
        raise AdsetDuplicateError("Preview истёк или не найден", status_code=410)
    return _stored_from_row(
        row,
        principal=principal,
        allow_consumed_expired=True,
    )


async def create_duplicate_task(
    engine: AsyncEngine,
    *,
    preview_token: str,
    principal: str,
) -> tuple[int, bool]:
    """Atomically consume a preview and queue exactly one irreversible task."""
    token_digest = _preview_token_digest(preview_token)
    normalized_principal = _validated_principal(principal)
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT principal, preview, task_payload, plan_digest,
                           idempotency_key, expires_at, task_id
                    FROM adset_duplicate_previews
                    WHERE token_digest = :token_digest
                    FOR UPDATE
                    """
                ),
                {"token_digest": token_digest},
            )
        ).first()
        if row is None:
            raise AdsetDuplicateError("Preview истёк или не найден", status_code=410)
        # Expiry is observed only after the authority row lock is acquired.
        # A request that arrived before expiry but waited behind another
        # transaction must not consume the capability after its deadline.
        locked_db_now = await conn.scalar(text("SELECT clock_timestamp()"))
        stored = _stored_from_row(
            row,
            principal=normalized_principal,
            allow_consumed_expired=True,
            db_now=locked_db_now,
        )
        payload = _task_payload(stored)

        if stored.consumed_task_id is not None:
            existing = (
                await conn.execute(
                    text(
                        """
                        SELECT payload
                        FROM task_queue
                        WHERE id = :task_id
                          AND task_type = 'meta_api_mutation'
                        """
                    ),
                    {"task_id": stored.consumed_task_id},
                )
            ).first()
            if existing is None:
                raise AdsetDuplicateError(
                    "Preview связан с отсутствующей задачей",
                    status_code=409,
                )
            existing_payload = (
                existing[0] if isinstance(existing[0], dict) else json.loads(existing[0])
            )
            _validate_task_payload(existing_payload, stored=stored)
            return stored.consumed_task_id, False

        task_id = await create_task(
            engine,
            task_type=_TASK_TYPE,
            status="pending",
            idempotency_key=stored.idempotency_key,
            payload=payload,
            requested_by=stored.principal,
            max_attempts=1,
            connection=conn,
        )
        created = task_id is not None
        if task_id is None:
            existing = (
                await conn.execute(
                    text(
                        """
                        SELECT id, payload
                        FROM task_queue
                        WHERE idempotency_key = :idempotency_key
                          AND task_type = 'meta_api_mutation'
                        """
                    ),
                    {"idempotency_key": stored.idempotency_key},
                )
            ).first()
            if existing is None:
                raise AdsetDuplicateError("Не удалось создать задачу", status_code=409)
            existing_payload = (
                existing[1] if isinstance(existing[1], dict) else json.loads(existing[1])
            )
            _validate_task_payload(existing_payload, stored=stored)
            task_id = int(existing[0])

        consumed = (
            await conn.execute(
                text(
                    """
                    UPDATE adset_duplicate_previews
                    SET task_id = :task_id,
                        consumed_at = clock_timestamp()
                    WHERE token_digest = :token_digest
                      AND task_id IS NULL
                    RETURNING task_id
                    """
                ),
                {
                    "task_id": task_id,
                    "token_digest": token_digest,
                },
            )
        ).first()
        if consumed is None or int(consumed[0]) != int(task_id):
            raise AdsetDuplicateError("Не удалось зафиксировать запуск", status_code=409)
        return int(task_id), created


async def get_duplicate_task(
    engine: AsyncEngine,
    task_id: int,
    *,
    principal: str,
) -> DuplicateTask | None:
    """Load only one duplicate task backed by this exact operator principal."""
    normalized_principal = _validated_principal(principal)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT task.id, task.status, task.payload, task.result,
                           task.attempt_count, task.max_attempts, task.last_error,
                           task.created_at, task.updated_at, task.completed_at
                    FROM task_queue AS task
                    WHERE task.id = :task_id
                      AND task.task_type = 'meta_api_mutation'
                      AND task.payload->>'mutation_kind' = 'duplicate_adset_structure'
                      AND task.requested_by = :principal
                    """
                ),
                {
                    "task_id": int(task_id),
                    "principal": normalized_principal,
                },
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

    public_error: str | None = None
    if task.status in {"failed", "cancelled"}:
        outcome = str(result.get("outcome") or "").upper()
        if outcome == "UNKNOWN" or result.get("reconcile_required") is True:
            public_error = (
                "Результат дублирования не подтверждён. Проверьте созданные объекты "
                "в Meta перед повтором."
            )
        elif result.get("checkpoint_type") == "duplicate_adset_structure":
            public_error = (
                "Дублирование остановлено. Созданные объекты оставлены PAUSED; проверьте их в Meta."
            )
        elif task.status == "cancelled":
            public_error = "Дублирование отменено до подтверждения результата."
        else:
            public_error = (
                "Дублирование завершилось ошибкой. Проверьте состояние в Meta перед повтором."
            )

    def checkpoint_progress(*, default_message: str) -> dict[str, Any]:
        return {
            "phase": checkpoint_phase or "running",
            "completed": created_count,
            "total": total,
            "message": default_message,
        }

    if task.status in {"pending", "retrying"}:
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

    return {
        "task_id": task.id,
        "status": task.status,
        "progress": progress,
        "created_meta_ids": created_meta_ids,
        "error": public_error,
    }


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
    "create_duplicate_task",
    "fetch_account_metadata",
    "generate_names",
    "get_duplicate_task",
    "load_duplicate_source",
    "load_stored_preview",
    "resolve_duplicate_source_hierarchy",
    "save_stored_preview",
    "serialize_duplicate_task",
    "validate_structure_caps",
]
