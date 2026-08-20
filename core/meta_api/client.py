# -*- coding: utf-8 -*-
"""MetaApiClient — тонкий Python-клиент над gRPC MetaApiService browser-agent.

Архитектурно: client.py НЕ исполняет HTTP-запросы напрямую. Он шлёт gRPC к
browser-agent, который через page.evaluate(fetch) дёргает Graph API изнутри
активной Vision-сессии. Так Meta видит request с правильными cookies/fingerprint.

Изолирован от BrowserAgentClient (см. § 3.3 плана) — может работать на своём
канале либо на общем (через ctor параметр channel).
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import urllib.parse
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import grpc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from clients.python_grpc.v1 import meta_api_pb2, meta_api_pb2_grpc
from core.browser.circuit_breaker import AsyncCircuitBreaker, CircuitOpenError
from core.deadlines import remaining_deadline_seconds
from core.meta_api.budget_limits import checked_daily_budget_minor_units
from core.meta_api.dispatch import mark_graph_call_observed, mark_graph_dispatched
from core.meta_api.errors import (
    BROWSER_OPERATION_REJECTION_REASONS,
    AmbiguousResultError,
    BrowserOperationRejectedError,
    BrowserReadinessRejectedError,
    MetaApiError,
    PermanentError,
    PreDispatchRejectedError,
    SessionUnavailableError,
    classify_graph_error,
)
from core.meta_api.identity import graph_ad_account_id, require_ad_account_id

logger = logging.getLogger(__name__)

# Дефолтный таймаут одного Graph-вызова. Browser-agent внутри ставит 30с,
# Здесь даём небольшой запас (на gRPC прохождение).
_DEFAULT_TIMEOUT_SECONDS = 35.0
# Token-only health (без сетевого запроса) — быстрый.
_HEALTH_CHECK_TIMEOUT_SECONDS = 10.0
# full_probe делает реальный fetch (browser-agent внутри ставит 8с) — даём запас на gRPC.
_HEALTH_PROBE_TIMEOUT_SECONDS = 15.0
# Вердикты пробы, означающие мёртвый канал (зеркалит runNetworkProbe в browser-agent).
# Всё остальное в detail — свободный текст и наружу не выносится.
_LIVE_PROBE_REJECT_VERDICTS = frozenset(
    {"login_required", "probe_token_invalid", "probe_network_down"}
)
_OPERATION_AUTHORITY_DB_TIMEOUT_SECONDS = 2.0

# Трейлер gRPC с кодом причины отказа собственной авторизации операции.
# `details()` наружу не читается: свободный текст, изредка с токеном из
# Graph-ответа. Трейлер несёт значение из закрытого словаря
# BROWSER_OPERATION_REJECTION_REASONS (core/meta_api/errors.py).
BROWSER_OPERATION_REJECTION_METADATA_KEY = "x-browser-operation-rejection"


def _browser_operation_rejection_reason(exc: grpc.RpcError) -> str | None:
    """Достать код причины из трейлера, если он из известного словаря."""
    trailers = exc.trailing_metadata() if hasattr(exc, "trailing_metadata") else None
    for key, value in trailers or ():
        if str(key).lower() != BROWSER_OPERATION_REJECTION_METADATA_KEY:
            continue
        reason = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
        if reason in BROWSER_OPERATION_REJECTION_REASONS:
            return reason
        # Код из более нового browser-agent: назвать причину нечем, а
        # выдать чужой код за доказанный pre-send отказ нельзя.
        logger.warning("unknown browser operation rejection reason=%s", reason)
        return None
    return None


def browser_operation_rejection_error(
    exc: grpc.RpcError,
    *,
    endpoint: str,
) -> BrowserOperationRejectedError | None:
    """Отказ, который browser-agent вынес сам, — с названной причиной.

    Все предикаты этой семьи проверяются до первого fetch в Meta, поэтому код
    причины одновременно доказывает, что наружу ничего не ушло. Без кода
    доказательства нет: тогда возвращается None и вызывающий остаётся на
    прежней, более осторожной классификации.

    Статусов два, потому что отказы разной природы: PERMISSION_DENIED — прав
    не хватило, INVALID_ARGUMENT — запрос собран неверно (например, кабинет
    задан не числом). На вопрос «ушло ли наружу» оба отвечают одинаково.

    Второй вопрос — стоит ли повторять — здесь не решается: тот же код причины
    отвечает на него отдельно, через ``unretryable_browser_rejection``
    (``core/meta_api/errors.py``). Ответ на исход у всей семьи один, ответ на
    политику повтора — разный.
    """
    code = exc.code() if hasattr(exc, "code") else None  # type: ignore[union-attr]
    if code not in (grpc.StatusCode.PERMISSION_DENIED, grpc.StatusCode.INVALID_ARGUMENT):
        return None
    reason = _browser_operation_rejection_reason(exc)
    if reason is None:
        return None
    return BrowserOperationRejectedError(
        "browser-agent отверг операцию до отправки в Meta: "
        f"{BROWSER_OPERATION_REJECTION_REASONS[reason]}",
        reason_code=reason,
        endpoint=endpoint,
    )


# v5 removes URL-backed image upload and accepts only capability-bound bytes.
# Older agents must fail contract health rather than retain the broader path.
BROWSER_CONTRACT_VERSION = 5
_AUTHORIZED_OPERATION_CALLERS = frozenset({"autopause", "meta_api", "campaign_creator"})
_OPERATION_RPC_TTL_SECONDS = {
    # ExecuteGraphCallV5 has a 30 second browser timeout plus a five second gRPC
    # transport margin by default.
    "execute_graph_call": 40,
    # MediaUploader gives both upload RPCs 180 seconds.
    "upload_image": 185,
    "upload_video": 185,
}
_CALLER_TASK_BINDINGS = {
    "autopause": ("meta_api_mutation", frozenset({"money"})),
    "meta_api": (
        "meta_api_mutation",
        frozenset({"interactive", "bulk", "background"}),
    ),
    "campaign_creator": ("campaign_create", frozenset({"bulk"})),
}
_CALLER_MUTATION_KINDS = {
    # The money worker is intentionally incapable of signing any command
    # except the deterministic single-ad PAUSE claimed from the money lane.
    "autopause": frozenset({"pause_ad"}),
    # Owner-confirmed status actions and duplicate execution are isolated on
    # the interactive/bulk/background worker lanes.
    "meta_api": frozenset(
        {
            "pause_ad",
            "activate_ad",
            "bulk_status_change",
            "duplicate_adset_structure",
        }
    ),
}
_UUID_TEXT_RE = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_GRAPH_OPERATION_RE = re.compile(
    r"^(GET|POST|DELETE):(/[^|]*)\|q=([0-9a-f]{64})\|b=([0-9a-f]{64})$"
)
_SAFE_MONEY_GRAPH_ENDPOINT_RE = re.compile(
    r"^/(?:$|(?:act_[0-9]+|[0-9]+)(?:/[A-Za-z][A-Za-z0-9_]*)?)$"
)
_CAMPAIGN_CREATE_EDGES = frozenset({"campaigns", "adsets", "adcreatives", "ads"})
_CAMPAIGN_PAUSED_CREATE_EDGES = frozenset({"campaigns", "adsets", "ads"})
_CAMPAIGN_CREATE_REQUIRED_KEYS = {
    "campaigns": frozenset({"name", "objective", "status", "special_ad_categories"}),
    "adsets": frozenset(
        {
            "name",
            "billing_event",
            "optimization_goal",
            "destination_type",
            "promoted_object",
            "attribution_spec",
            "targeting",
            "start_time",
            "status",
            "campaign_id",
        }
    ),
    "adcreatives": frozenset({"name", "object_story_spec", "url_tags", "degrees_of_freedom_spec"}),
    "ads": frozenset({"name", "adset_id", "creative", "status"}),
}
_CAMPAIGN_CREATE_ALLOWED_KEYS = {
    "campaigns": _CAMPAIGN_CREATE_REQUIRED_KEYS["campaigns"]
    | frozenset({"daily_budget", "bid_strategy"}),
    "adsets": _CAMPAIGN_CREATE_REQUIRED_KEYS["adsets"]
    | frozenset({"daily_budget", "bid_strategy", "bid_amount"}),
    "adcreatives": _CAMPAIGN_CREATE_REQUIRED_KEYS["adcreatives"],
    "ads": _CAMPAIGN_CREATE_REQUIRED_KEYS["ads"],
}
DUPLICATE_SOURCE_CAMPAIGN_FIELDS = (
    "id,account_id,name,objective,special_ad_categories,special_ad_category_country,"
    "buying_type,bid_strategy,status,daily_budget"
)
DUPLICATE_SOURCE_ADSET_FIELDS = (
    "id,account_id,campaign_id,name,status,effective_status,daily_budget,start_time,"
    "billing_event,optimization_goal,bid_strategy,bid_amount,targeting,promoted_object,"
    "attribution_spec,destination_type,pacing_type"
)
DUPLICATE_SOURCE_AD_FIELDS = "id,account_id,campaign_id,adset_id,name,status,creative{id}"
DUPLICATE_PROVE_CAMPAIGN_FIELDS = "id,account_id,name,objective,status,daily_budget"
DUPLICATE_PROVE_ADSET_FIELDS = "id,account_id,campaign_id,status"
DUPLICATE_PROVE_AD_FIELDS = "id,account_id,campaign_id,adset_id,name,status,creative{id}"
DUPLICATE_VERIFY_AD_FIELDS = "id,adset_id,status,effective_status,creative{id}"
_DUPLICATE_VERIFY_CAMPAIGN_FIELDS = "id,status,daily_budget"
_DUPLICATE_VERIFY_ADSET_FIELDS = "id,campaign_id,status,daily_budget,lifetime_budget,start_time"
_DUPLICATE_CLEANUP_FIELDS = "id,status,effective_status"
_DUPLICATE_CREATED_ID_LIMITS = {"campaigns": 5, "adsets": 50, "ads": 50}


@dataclass(frozen=True, slots=True)
class _DuplicateOperationPlan:
    account_root: str
    source_campaign_id: str
    source_adset_id: str
    selected_ad_ids: tuple[str, ...]
    budget_level: str
    currency: str
    currency_exponent: int
    daily_budget_minor_units: int
    start_time: str
    campaign_names: tuple[str, ...]
    adset_names: tuple[tuple[str, ...], ...]


def _duplicate_numeric_id(value: Any, *, label: str) -> str:
    if isinstance(value, bool):
        raise PermanentError(f"duplicate Graph {label} is invalid")
    normalized = str(value).strip() if isinstance(value, (str, int)) else ""
    if not normalized.isdigit() or int(normalized) <= 0:
        raise PermanentError(f"duplicate Graph {label} is invalid")
    return normalized


def _duplicate_string_id(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise PermanentError(f"duplicate Graph {label} is invalid")
    return _duplicate_numeric_id(value, label=label)


def _duplicate_positive_count(value: Any, *, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise PermanentError(f"duplicate Graph {label} is invalid")
    return value


def _duplicate_names(value: Any, *, label: str, count: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) != count:
        raise PermanentError(f"duplicate Graph {label} is invalid")
    names: list[str] = []
    for raw_name in value:
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        if not name or len(name) > 400:
            raise PermanentError(f"duplicate Graph {label} is invalid")
        names.append(name)
    return tuple(names)


def _duplicate_operation_plan(
    payload: Mapping[str, Any],
    *,
    ad_account_id: str,
) -> _DuplicateOperationPlan:
    params = payload.get("params")
    if not isinstance(params, Mapping):
        raise PermanentError("duplicate Graph task params are invalid")
    source_campaign_id = _duplicate_string_id(
        params.get("source_campaign_id"),
        label="source_campaign_id",
    )
    source_adset_id = _duplicate_string_id(
        params.get("source_adset_id"),
        label="source_adset_id",
    )
    target_id = _duplicate_string_id(payload.get("target_id"), label="target_id")
    if target_id != source_adset_id:
        raise PermanentError("duplicate Graph target_id does not match source_adset_id")

    raw_selected_ids = params.get("selected_ad_ids")
    if not isinstance(raw_selected_ids, list) or not raw_selected_ids:
        raise PermanentError("duplicate Graph selected_ad_ids are invalid")
    selected_ad_ids = tuple(
        _duplicate_string_id(value, label="selected_ad_id") for value in raw_selected_ids
    )
    if len(selected_ad_ids) > 10 or len(set(selected_ad_ids)) != len(selected_ad_ids):
        raise PermanentError("duplicate Graph selected_ad_ids are invalid")

    campaign_count = _duplicate_positive_count(
        params.get("campaign_count"),
        label="campaign_count",
        maximum=5,
    )
    adsets_per_campaign = _duplicate_positive_count(
        params.get("adsets_per_campaign"),
        label="adsets_per_campaign",
        maximum=10,
    )
    if campaign_count * adsets_per_campaign * len(selected_ad_ids) > 50:
        raise PermanentError("duplicate Graph create cardinality is invalid")

    raw_budget_level = params.get("budget_level")
    budget_level = raw_budget_level.strip().upper() if isinstance(raw_budget_level, str) else ""
    if budget_level not in {"ABO", "CBO"}:
        raise PermanentError("duplicate Graph budget_level is invalid")
    try:
        (
            currency,
            currency_exponent,
            _daily_budget,
            daily_budget_minor_units,
        ) = checked_daily_budget_minor_units(
            params.get("daily_budget"),
            currency=params.get("currency"),
            currency_exponent=params.get("currency_exponent"),
        )
    except ValueError as exc:
        raise PermanentError("duplicate Graph daily_budget is invalid") from exc
    start_time = (
        params.get("start_time", "").strip() if isinstance(params.get("start_time"), str) else ""
    )
    if not start_time:
        raise PermanentError("duplicate Graph start_time is invalid")

    campaign_names = _duplicate_names(
        params.get("campaign_names"),
        label="campaign_names",
        count=campaign_count,
    )
    raw_adset_names = params.get("adset_names")
    if (
        isinstance(raw_adset_names, list)
        and len(raw_adset_names) == campaign_count
        and all(isinstance(item, list) for item in raw_adset_names)
    ):
        adset_names = tuple(
            _duplicate_names(
                item,
                label=f"adset_names[{index}]",
                count=adsets_per_campaign,
            )
            for index, item in enumerate(raw_adset_names)
        )
    elif (
        isinstance(raw_adset_names, list)
        and len(raw_adset_names) == campaign_count * adsets_per_campaign
        and all(isinstance(item, str) for item in raw_adset_names)
    ):
        flattened = _duplicate_names(
            raw_adset_names,
            label="adset_names",
            count=campaign_count * adsets_per_campaign,
        )
        adset_names = tuple(
            flattened[index * adsets_per_campaign : (index + 1) * adsets_per_campaign]
            for index in range(campaign_count)
        )
    elif (
        isinstance(raw_adset_names, list)
        and len(raw_adset_names) == adsets_per_campaign
        and all(isinstance(item, str) for item in raw_adset_names)
    ):
        shared = _duplicate_names(
            raw_adset_names,
            label="adset_names",
            count=adsets_per_campaign,
        )
        adset_names = tuple(shared for _ in range(campaign_count))
    else:
        raise PermanentError("duplicate Graph adset_names are invalid")

    return _DuplicateOperationPlan(
        account_root=f"/{graph_ad_account_id(ad_account_id)}",
        source_campaign_id=source_campaign_id,
        source_adset_id=source_adset_id,
        selected_ad_ids=selected_ad_ids,
        budget_level=budget_level,
        currency=currency,
        currency_exponent=currency_exponent,
        daily_budget_minor_units=daily_budget_minor_units,
        start_time=start_time,
        campaign_names=campaign_names,
        adset_names=adset_names,
    )


def _duplicate_checkpoint_ids(
    task_result: Mapping[str, Any],
    *,
    recovery_requested: bool,
    plan: _DuplicateOperationPlan | None = None,
) -> dict[str, tuple[str, ...]]:
    checkpoint_type = task_result.get("checkpoint_type")
    checkpoint_version = task_result.get("checkpoint_version")
    raw_created = task_result.get("created_ids")
    if raw_created is None:
        if recovery_requested or checkpoint_type is not None:
            raise PermanentError("duplicate Graph recovery checkpoint is invalid")
        return {key: () for key in _DUPLICATE_CREATED_ID_LIMITS}
    if checkpoint_type != "duplicate_adset_structure" or checkpoint_version != 2:
        raise PermanentError("duplicate Graph recovery checkpoint type is invalid")
    if not isinstance(raw_created, Mapping) or set(raw_created) != set(
        _DUPLICATE_CREATED_ID_LIMITS
    ):
        raise PermanentError("duplicate Graph created_ids checkpoint is invalid")

    created: dict[str, tuple[str, ...]] = {}
    for key, maximum in _DUPLICATE_CREATED_ID_LIMITS.items():
        values = raw_created.get(key)
        if not isinstance(values, list) or len(values) > maximum:
            raise PermanentError(f"duplicate Graph checkpoint {key} are invalid")
        normalized = [_duplicate_string_id(value, label=f"checkpoint {key} id") for value in values]
        if len(set(normalized)) != len(normalized):
            raise PermanentError(f"duplicate Graph checkpoint {key} contain duplicates")
        created[key] = tuple(normalized)
    bucket_sets = {key: set(values) for key, values in created.items()}
    if (
        bucket_sets["campaigns"] & bucket_sets["adsets"]
        or bucket_sets["campaigns"] & bucket_sets["ads"]
        or bucket_sets["adsets"] & bucket_sets["ads"]
    ):
        raise PermanentError("duplicate Graph checkpoint buckets overlap")
    if plan is not None:
        source_ids = {
            plan.source_campaign_id,
            plan.source_adset_id,
            *plan.selected_ad_ids,
        }
        if source_ids & set().union(*bucket_sets.values()):
            raise PermanentError("duplicate Graph checkpoint collides with source ids")
        expected_campaigns = len(plan.campaign_names)
        expected_adsets = sum(len(names) for names in plan.adset_names)
        expected_ads = expected_adsets * len(plan.selected_ad_ids)
        if (
            len(created["campaigns"]) > expected_campaigns
            or len(created["adsets"]) > expected_adsets
            or len(created["ads"]) > expected_ads
        ):
            raise PermanentError("duplicate Graph checkpoint cardinality exceeds the task plan")
        for adset_index in range(len(created["adsets"])):
            campaign_index = adset_index // len(plan.adset_names[0])
            if campaign_index >= len(created["campaigns"]):
                raise PermanentError("duplicate Graph checkpoint adset has no campaign parent")
        for ad_index in range(len(created["ads"])):
            adset_index = ad_index // len(plan.selected_ad_ids)
            if adset_index >= len(created["adsets"]):
                raise PermanentError("duplicate Graph checkpoint ad has no adset parent")
    if recovery_requested and not any(created.values()):
        raise PermanentError("duplicate Graph recovery checkpoint is empty")
    return created


def _decode_graph_component(value: str, *, plus_as_space: bool) -> str:
    """Decode nested percent-encoding so aliases cannot hide control keys."""
    decoded = str(value)
    for _ in range(5):
        if re.search(r"%(?![0-9A-Fa-f]{2})", decoded):
            raise ValueError("Graph request method semantics are ambiguous")
        try:
            candidate = (
                urllib.parse.unquote_plus(decoded, errors="strict")
                if plus_as_space
                else urllib.parse.unquote(decoded, errors="strict")
            )
        except UnicodeDecodeError as exc:
            raise ValueError("Graph request method semantics are ambiguous") from exc
        if candidate == decoded:
            return decoded
        decoded = candidate
    # A bounded decoder must fail closed when the input is still changing at
    # the bound. Otherwise a sixth layer can conceal ``method`` or ``?`` from
    # the classifier and be decoded later by another transport.
    if re.search(r"%(?![0-9A-Fa-f]{2})", decoded):
        raise ValueError("Graph request method semantics are ambiguous")
    try:
        candidate = (
            urllib.parse.unquote_plus(decoded, errors="strict")
            if plus_as_space
            else urllib.parse.unquote(decoded, errors="strict")
        )
    except UnicodeDecodeError as exc:
        raise ValueError("Graph request method semantics are ambiguous") from exc
    if candidate != decoded:
        raise ValueError("Graph request method semantics are ambiguous")
    return decoded


def _normalized_graph_parameter_name(value: str) -> str:
    return _decode_graph_component(str(value), plus_as_space=True).strip().casefold()


def _load_canonical_graph_body(body_json: str) -> Any:
    """Parse JSON while rejecting duplicate/aliased keys and method overrides."""

    def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        seen: set[str] = set()
        for raw_key, value in pairs:
            normalized_key = _normalized_graph_parameter_name(raw_key)
            if normalized_key == "method":
                raise ValueError("Graph method override is not authorized")
            if normalized_key in seen:
                raise ValueError("Graph JSON parameter semantics are ambiguous")
            seen.add(normalized_key)
            result[raw_key] = value
        return result

    try:
        return json.loads(body_json, object_pairs_hook=_object_pairs)
    except json.JSONDecodeError as exc:
        raise ValueError("Graph body_json must be canonical JSON") from exc


def _campaign_mapping(
    value: Any,
    *,
    required: frozenset[str],
    allowed: frozenset[str],
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PermanentError(f"campaign Graph {label} must be an object")
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(allowed):
        raise PermanentError(f"campaign Graph {label} schema is not authorized")
    return value


def _campaign_nonempty_text(value: Any, *, label: str) -> str:
    normalized = str(value).strip() if isinstance(value, str) else ""
    if not normalized:
        raise PermanentError(f"campaign Graph {label} is invalid")
    return normalized


def _campaign_numeric_id(value: Any, *, label: str) -> str:
    normalized = str(value).strip() if isinstance(value, (str, int)) else ""
    if isinstance(value, bool) or not normalized.isdigit():
        raise PermanentError(f"campaign Graph {label} is invalid")
    return normalized


def _campaign_positive_integer(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PermanentError(f"campaign Graph {label} is invalid")
    return value


# Списки продублированы намеренно: capability — независимый рубеж, он не должен
# доверять тому же перечислению, по которому строилось тело запроса.
_CAMPAIGN_GENDER_IDS: frozenset[int] = frozenset({1, 2})
# Четыре стратегии ставок Meta — тот же набор, что в контракте черновика
# (core/campaign_drafts/contracts.py::BidStrategy). Держим списком здесь, а не
# импортом: guard money-пути не должен зависеть от слоя черновиков.
_CAMPAIGN_BID_STRATEGIES: frozenset[str] = frozenset(
    {
        "COST_CAP",
        "LOWEST_COST_WITHOUT_CAP",
        "LOWEST_COST_WITH_BID_CAP",
        "LOWEST_COST_WITH_MIN_ROAS",
    }
)
_CAMPAIGN_PUBLISHER_PLATFORMS: frozenset[str] = frozenset(
    {"facebook", "instagram", "messenger", "audience_network"}
)


def _campaign_optional_closed_list(
    mapping: Mapping[str, Any],
    *,
    key: str,
    allowed: frozenset[Any],
    label: str,
) -> None:
    """Проверить необязательный список с закрытым набором значений.

    Отсутствие ключа — нормальный случай «оператор не сужал выбор». Если ключ
    есть, он обязан нести непустой список без повторов и без значений вне
    набора: молча пропущенное сужение уводит открутку не на ту аудиторию.
    """
    if key not in mapping:
        return
    value = mapping[key]
    if not isinstance(value, list) or not value:
        raise PermanentError(f"campaign Graph {label} is invalid")
    if any(isinstance(item, bool) or item not in allowed for item in value):
        raise PermanentError(f"campaign Graph {label} is invalid")
    if len(value) != len(set(value)):
        raise PermanentError(f"campaign Graph {label} is invalid")


def _validate_campaign_call_to_action(value: Any) -> None:
    cta = _campaign_mapping(
        value,
        required=frozenset({"type", "value"}),
        allowed=frozenset({"type", "value"}),
        label="creative call_to_action",
    )
    _campaign_nonempty_text(cta.get("type"), label="creative call_to_action type")
    cta_value = _campaign_mapping(
        cta.get("value"),
        required=frozenset({"link"}),
        allowed=frozenset({"link"}),
        label="creative call_to_action value",
    )
    _campaign_nonempty_text(cta_value.get("link"), label="creative destination link")


def _validate_campaign_create_body(
    *,
    authority: BrowserOperationAuthority,
    edge: str,
    value: Any,
) -> None:
    body = _campaign_mapping(
        value,
        required=_CAMPAIGN_CREATE_REQUIRED_KEYS[edge],
        allowed=_CAMPAIGN_CREATE_ALLOWED_KEYS[edge],
        label=f"{edge} create body",
    )
    _campaign_nonempty_text(body.get("name"), label=f"{edge} name")

    if edge in _CAMPAIGN_PAUSED_CREATE_EDGES and body.get("status") != "PAUSED":
        raise PermanentError("campaign Graph create status must be PAUSED")

    for budget_key in ("daily_budget", "bid_amount"):
        if budget_key in body:
            _campaign_positive_integer(body[budget_key], label=budget_key)
    if "bid_strategy" in body:
        _campaign_nonempty_text(body["bid_strategy"], label="bid_strategy")
        # Набор закрыт четырьмя стратегиями Meta. Раньше проверялась только
        # непустота, и опечатка в money-поле уезжала бы в Meta, возвращаясь
        # невнятной ошибкой уже после того, как кампания создана.
        if body["bid_strategy"] not in _CAMPAIGN_BID_STRATEGIES:
            raise PermanentError("campaign Graph bid_strategy is not authorized")

    if edge == "campaigns":
        _campaign_nonempty_text(body.get("objective"), label="campaign objective")
        categories = body.get("special_ad_categories")
        if (
            not isinstance(categories, list)
            or not categories
            or any(not isinstance(category, str) or not category.strip() for category in categories)
        ):
            raise PermanentError("campaign Graph special_ad_categories are invalid")
        return

    if edge == "adsets":
        campaign_id = _campaign_numeric_id(body.get("campaign_id"), label="campaign_id")
        if campaign_id not in authority.created_campaign_ids:
            raise PermanentError(
                "campaign Graph adset target has no task-local campaign provenance"
            )
        if body.get("billing_event") != "IMPRESSIONS" or body.get("destination_type") != "WEBSITE":
            raise PermanentError("campaign Graph adset delivery semantics are not authorized")
        _campaign_nonempty_text(body.get("optimization_goal"), label="optimization_goal")
        _campaign_nonempty_text(body.get("start_time"), label="start_time")

        promoted = _campaign_mapping(
            body.get("promoted_object"),
            required=frozenset({"pixel_id", "custom_event_type", "smart_pse_enabled"}),
            allowed=frozenset({"pixel_id", "custom_event_type", "smart_pse_enabled"}),
            label="promoted_object",
        )
        _campaign_numeric_id(promoted.get("pixel_id"), label="pixel_id")
        _campaign_nonempty_text(promoted.get("custom_event_type"), label="custom_event_type")
        if not isinstance(promoted.get("smart_pse_enabled"), bool):
            raise PermanentError("campaign Graph smart_pse_enabled is invalid")

        attribution = body.get("attribution_spec")
        if not isinstance(attribution, list) or not attribution:
            raise PermanentError("campaign Graph attribution_spec is invalid")
        for item in attribution:
            spec = _campaign_mapping(
                item,
                required=frozenset({"event_type", "window_days"}),
                allowed=frozenset({"event_type", "window_days"}),
                label="attribution_spec entry",
            )
            if spec.get("event_type") not in {"CLICK_THROUGH", "VIEW_THROUGH"}:
                raise PermanentError("campaign Graph attribution event is invalid")
            _campaign_positive_integer(spec.get("window_days"), label="attribution window")

        targeting = _campaign_mapping(
            body.get("targeting"),
            required=frozenset({"geo_locations", "age_min", "age_max", "targeting_automation"}),
            allowed=frozenset(
                {
                    "geo_locations",
                    "age_min",
                    "age_max",
                    "targeting_automation",
                    # Пол и плейсменты необязательны: пустой выбор оператора означает
                    # «все», и билдер тогда ключ не кладёт. Но если выбор сделан, он
                    # обязан доехать — иначе кампания уже создана, а adset падает.
                    "genders",
                    "publisher_platforms",
                    # Ключи рабочего шаблона кабинета (замер 17.08 по 360 живым
                    # группам): без них наша группа уходит в Meta не такой, как
                    # те, что реально откручиваются. Значения проверяются ниже —
                    # allowlist разрешает поле, но не любое его содержимое.
                    "age_range",
                    "targeting_optimization",
                    "brand_safety_content_filter_levels",
                }
            ),
            label="targeting",
        )
        geo = _campaign_mapping(
            targeting.get("geo_locations"),
            required=frozenset({"countries", "location_types"}),
            allowed=frozenset({"countries", "location_types"}),
            label="geo_locations",
        )
        if not isinstance(geo.get("countries"), list) or not geo["countries"]:
            raise PermanentError("campaign Graph targeting countries are invalid")
        if not isinstance(geo.get("location_types"), list) or not geo["location_types"]:
            raise PermanentError("campaign Graph targeting location_types are invalid")
        _campaign_positive_integer(targeting.get("age_min"), label="age_min")
        _campaign_positive_integer(targeting.get("age_max"), label="age_max")
        age_range = targeting.get("age_range")
        if age_range is not None:
            if not isinstance(age_range, list) or len(age_range) != 2:
                raise PermanentError("campaign Graph targeting age_range is invalid")
            for bound in age_range:
                _campaign_positive_integer(bound, label="age_range bound")
            if age_range[0] > age_range[1]:
                raise PermanentError("campaign Graph targeting age_range is inverted")
        optimization = targeting.get("targeting_optimization")
        # Единственное значение, которое стоит в живых группах. Любое другое —
        # не «новая возможность», а незамеченная правка money-пути.
        if optimization is not None and optimization != "expansion_all":
            raise PermanentError("campaign Graph targeting_optimization is not authorized")
        brand_safety = targeting.get("brand_safety_content_filter_levels")
        if brand_safety is not None and (
            not isinstance(brand_safety, list)
            or not set(brand_safety) <= {"FACEBOOK_RELAXED", "AN_RELAXED"}
        ):
            raise PermanentError("campaign Graph brand_safety levels are not authorized")
        automation = _campaign_mapping(
            targeting.get("targeting_automation"),
            required=frozenset({"advantage_audience"}),
            allowed=frozenset({"advantage_audience", "individual_setting"}),
            label="targeting_automation",
        )
        individual = automation.get("individual_setting")
        if individual is not None:
            individual = _campaign_mapping(
                individual,
                required=frozenset({"age", "gender"}),
                allowed=frozenset({"age", "gender"}),
                label="individual_setting",
            )
            if any(individual.get(axis) not in {0, 1} for axis in ("age", "gender")):
                raise PermanentError("campaign Graph individual_setting is invalid")
        if automation.get("advantage_audience") not in {0, 1}:
            raise PermanentError("campaign Graph advantage_audience is invalid")
        _campaign_optional_closed_list(
            targeting,
            key="genders",
            allowed=_CAMPAIGN_GENDER_IDS,
            label="targeting genders",
        )
        _campaign_optional_closed_list(
            targeting,
            key="publisher_platforms",
            allowed=_CAMPAIGN_PUBLISHER_PLATFORMS,
            label="targeting publisher_platforms",
        )
        return

    if edge == "adcreatives":
        _campaign_nonempty_text(body.get("url_tags"), label="creative url_tags")
        story = _campaign_mapping(
            body.get("object_story_spec"),
            required=frozenset({"page_id"}),
            allowed=frozenset({"page_id", "link_data", "video_data"}),
            label="object_story_spec",
        )
        _campaign_numeric_id(story.get("page_id"), label="page_id")
        media_keys = {"link_data", "video_data"} & set(story)
        if len(media_keys) != 1:
            raise PermanentError("campaign Graph creative media schema is not authorized")
        media_key = media_keys.pop()
        if media_key == "link_data":
            media = _campaign_mapping(
                story[media_key],
                required=frozenset({"link", "call_to_action", "image_hash"}),
                allowed=frozenset(
                    {
                        "link",
                        "call_to_action",
                        "image_hash",
                        "message",
                        "name",
                        "description",
                    }
                ),
                label="link_data",
            )
            _campaign_nonempty_text(media.get("link"), label="creative destination link")
            image_hash = _campaign_nonempty_text(
                media.get("image_hash"),
                label="creative image_hash",
            )
            if image_hash not in authority.uploaded_image_hashes:
                raise PermanentError(
                    "campaign Graph creative image has no task-local upload provenance"
                )
        else:
            media = _campaign_mapping(
                story[media_key],
                required=frozenset({"video_id", "call_to_action"}),
                allowed=frozenset(
                    {
                        "video_id",
                        "call_to_action",
                        "image_url",
                        "message",
                        "title",
                        "link_description",
                    }
                ),
                label="video_data",
            )
            video_id = _campaign_numeric_id(media.get("video_id"), label="creative video_id")
            if video_id not in authority.uploaded_video_ids:
                raise PermanentError(
                    "campaign Graph creative video has no task-local upload provenance"
                )
        _validate_campaign_call_to_action(media.get("call_to_action"))

        freedom = _campaign_mapping(
            body.get("degrees_of_freedom_spec"),
            required=frozenset({"creative_features_spec"}),
            allowed=frozenset({"creative_features_spec"}),
            label="degrees_of_freedom_spec",
        )
        features = _campaign_mapping(
            freedom.get("creative_features_spec"),
            required=frozenset({"text_optimizations"}),
            allowed=frozenset({"text_optimizations"}),
            label="creative_features_spec",
        )
        optimizations = _campaign_mapping(
            features.get("text_optimizations"),
            required=frozenset({"enroll_status"}),
            allowed=frozenset({"enroll_status"}),
            label="text_optimizations",
        )
        _campaign_nonempty_text(
            optimizations.get("enroll_status"),
            label="text_optimizations enroll_status",
        )
        return

    adset_id = _campaign_numeric_id(body.get("adset_id"), label="adset_id")
    if adset_id not in authority.created_adset_ids:
        raise PermanentError("campaign Graph ad target has no task-local adset provenance")
    creative = _campaign_mapping(
        body.get("creative"),
        required=frozenset({"creative_id"}),
        allowed=frozenset({"creative_id"}),
        label="ad creative",
    )
    creative_id = _campaign_numeric_id(creative.get("creative_id"), label="creative_id")
    if creative_id not in authority.created_creative_ids:
        raise PermanentError("campaign Graph ad target has no task-local creative provenance")


def validate_graph_request_semantics(
    *,
    method: str,
    endpoint: str,
    query_params: Mapping[str, str],
    body_json: str,
) -> None:
    """Reject transport aliases that can change Graph's effective HTTP method."""
    raw_endpoint = str(endpoint)
    if "?" in raw_endpoint or "#" in raw_endpoint:
        raise ValueError("Graph endpoint query/fragment semantics are not authorized")
    decoded_endpoint = _decode_graph_component(raw_endpoint, plus_as_space=False)
    if "?" in decoded_endpoint or "#" in decoded_endpoint:
        raise ValueError("Graph endpoint query/fragment semantics are not authorized")

    seen_query_keys: set[str] = set()
    for raw_key in query_params:
        normalized_key = _normalized_graph_parameter_name(str(raw_key))
        if normalized_key == "method":
            raise ValueError("Graph method override is not authorized")
        if normalized_key in seen_query_keys:
            raise ValueError("Graph query parameter semantics are ambiguous")
        seen_query_keys.add(normalized_key)

    if body_json:
        _load_canonical_graph_body(body_json)
        if method.strip().upper() == "GET":
            raise ValueError("Graph GET body semantics are not authorized")


def _canonical_string_map_digest(values: Mapping[str, str]) -> str:
    canonical = json.dumps(
        sorted((str(key), str(value)) for key, value in values.items()),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def graph_operation_binding(
    *,
    method: str,
    endpoint: str,
    query_params: Mapping[str, str],
    body_json: str,
) -> str:
    """Bind the exact Graph method, target, form/query values and body bytes."""
    normalized_method = method.strip().upper()
    validate_graph_request_semantics(
        method=normalized_method,
        endpoint=endpoint,
        query_params=query_params,
        body_json=body_json,
    )
    query_digest = _canonical_string_map_digest(query_params)
    body_digest = hashlib.sha256(body_json.encode("utf-8")).hexdigest()
    return f"{normalized_method}:{endpoint}|q={query_digest}|b={body_digest}"


def media_operation_binding(
    *,
    rpc: str,
    attributes: Mapping[str, str | int],
) -> str:
    """Bind media identity and content digest without putting media in the HMAC."""
    request_digest = _canonical_string_map_digest(
        {str(key): str(value) for key, value in attributes.items()}
    )
    return f"{rpc}|r={request_digest}"


def browser_operation_payload(
    *,
    browser_contract_version: int,
    rpc: str,
    operation: str,
    session_id: str,
    vision_profile_id: str,
    ad_account_id: str,
    caller: str,
    task_id: int,
    lease_owner: uuid.UUID | str,
    lease_token: int,
    expires_at_epoch: int,
    nonce: str,
) -> str:
    """Canonical cross-runtime capability payload and durable row digest."""
    return "\n".join(
        (
            "browser_operation/v2",
            str(browser_contract_version),
            rpc,
            operation,
            session_id,
            vision_profile_id,
            ad_account_id,
            caller,
            str(task_id),
            str(lease_owner),
            str(lease_token),
            str(expires_at_epoch),
            nonce,
        )
    )


_LIVE_OPERATION_AUTHORITY_SQL = text(
    """
    SELECT
        tq.task_type,
        tq.lane,
        tq.requested_by,
        tq.payload,
        tq.result,
        FLOOR(EXTRACT(EPOCH FROM clock_timestamp()))::bigint AS db_now_epoch,
        FLOOR(EXTRACT(EPOCH FROM tq.lease_expires_at))::bigint AS lease_expires_epoch,
        FLOOR(EXTRACT(EPOCH FROM tq.deadline_at))::bigint AS deadline_epoch,
        CASE
            WHEN tq.task_type = 'meta_api_mutation'
                THEN tq.payload->>'ad_account_id'
            WHEN tq.task_type = 'campaign_create'
                THEN cr.config#>>'{account,act_id}'
            ELSE NULL
        END AS bound_ad_account_id
    FROM task_queue AS tq
    LEFT JOIN campaign_run AS cr
      ON tq.task_type = 'campaign_create'
     AND cr.id = CASE
         WHEN COALESCE(tq.payload->>'run_id', '') ~ :uuid_re
             THEN (tq.payload->>'run_id')::uuid
         ELSE NULL
     END
    WHERE tq.id = :task_id
      AND tq.status = 'running'
      AND tq.lease_owner = :lease_owner
      AND tq.lease_token = :lease_token
      AND tq.lease_expires_at > clock_timestamp()
      AND tq.cancel_requested_at IS NULL
      AND tq.deadline_at IS NOT NULL
      AND tq.deadline_at > clock_timestamp()
    LIMIT 1
    FOR SHARE OF tq
    """
)
_SET_LOCAL_STATEMENT_TIMEOUT_SQL = text("SELECT set_config('statement_timeout', :timeout_ms, true)")
_INSERT_PENDING_CAPABILITY_SQL = text(
    """
    INSERT INTO browser_operation_capability_uses (
        nonce_sha256,
        capability_digest,
        operation_digest,
        browser_contract_version,
        caller,
        rpc,
        task_id,
        lease_owner,
        lease_token,
        session_id,
        vision_profile_id,
        ad_account_id,
        expires_at
    )
    VALUES (
        :nonce_sha256,
        :capability_digest,
        :operation_digest,
        :browser_contract_version,
        :caller,
        :rpc,
        :task_id,
        :lease_owner,
        :lease_token,
        :session_id,
        :vision_profile_id,
        :ad_account_id,
        to_timestamp(:expires_at_epoch)
    )
    """
)


@dataclass(frozen=True, slots=True)
class BrowserOperationAuthority:
    caller: str
    task_id: int
    lease_owner: uuid.UUID
    lease_token: int
    vision_profile_id: str
    browser_readiness_generation: int | None = None
    uploaded_video_ids: set[str] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    uploaded_image_hashes: set[str] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    created_campaign_ids: set[str] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    created_adset_ids: set[str] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    created_creative_ids: set[str] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    created_ad_ids: set[str] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    duplicate_plan_state: dict[str, _DuplicateOperationPlan] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    duplicate_pending_roles: dict[str, list[str]] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    duplicate_source_campaign_template: dict[str, Any] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    duplicate_loaded_source_adset_ids: set[str] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    duplicate_source_ads: dict[str, tuple[str, str]] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    duplicate_counters: dict[str, int] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    duplicate_created_campaign_slots: dict[str, int] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    duplicate_candidate_campaign_slots: dict[str, int] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    duplicate_created_adset_slots: dict[str, tuple[int, int]] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    duplicate_candidate_adset_slots: dict[str, tuple[int, int, str]] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    duplicate_configured_adset_ids: set[str] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    duplicate_created_ad_ids: set[str] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )
    duplicate_candidate_ad_ids: dict[str, tuple[str, str]] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    duplicate_checkpoint_ids: dict[str, set[str]] = field(
        default_factory=lambda: {
            "campaigns": set(),
            "adsets": set(),
            "ads": set(),
        },
        compare=False,
        repr=False,
    )
    duplicate_recovery_verified_ids: dict[str, set[str]] = field(
        default_factory=lambda: {
            "campaigns": set(),
            "adsets": set(),
            "ads": set(),
        },
        compare=False,
        repr=False,
    )


_OPERATION_AUTHORITY: contextvars.ContextVar[BrowserOperationAuthority | None] = (
    contextvars.ContextVar("browser_operation_authority", default=None)
)


def _duplicate_queue_role(
    authority: BrowserOperationAuthority,
    *,
    operation: str,
    role: str,
) -> None:
    authority.duplicate_pending_roles.setdefault(operation, []).append(role)


def _duplicate_role_is_pending(
    authority: BrowserOperationAuthority,
    *,
    role: str,
) -> bool:
    return any(role in roles for roles in authority.duplicate_pending_roles.values())


def _duplicate_extract_exact_result_id(
    result: Mapping[str, Any],
    *,
    key: str,
    label: str,
) -> str:
    if set(result) != {key}:
        raise AmbiguousResultError(f"duplicate Graph {label} response schema is not exact")
    try:
        return _duplicate_string_id(
            result.get(key),
            label=f"{label} response id",
        )
    except PermanentError as exc:
        raise AmbiguousResultError(
            f"duplicate Graph {label} response has no confirmed object id"
        ) from exc


def _duplicate_known_ids(
    authority: BrowserOperationAuthority,
    plan: _DuplicateOperationPlan,
) -> set[str]:
    source_creative_ids = {
        creative_id for _, creative_id in authority.duplicate_source_ads.values()
    }
    checkpoint_ids = set().union(*authority.duplicate_checkpoint_ids.values())
    return {
        plan.source_campaign_id,
        plan.source_adset_id,
        *plan.selected_ad_ids,
        *source_creative_ids,
        *authority.duplicate_candidate_campaign_slots,
        *authority.duplicate_created_campaign_slots,
        *authority.duplicate_candidate_adset_slots,
        *authority.duplicate_created_adset_slots,
        *authority.duplicate_candidate_ad_ids,
        *authority.duplicate_created_ad_ids,
        *checkpoint_ids,
    }


def _duplicate_require_fresh_candidate_id(
    *,
    authority: BrowserOperationAuthority,
    plan: _DuplicateOperationPlan,
    object_id: str,
    label: str,
) -> None:
    if object_id in _duplicate_known_ids(authority, plan):
        raise AmbiguousResultError(
            f"duplicate Graph {label} response id collides with source or created provenance"
        )


def _duplicate_require_exact_keys(
    result: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
    label: str,
) -> None:
    optional_keys = optional or set()
    keys = set(result)
    if not required.issubset(keys) or not keys.issubset(required | optional_keys):
        raise PermanentError(f"duplicate Graph {label} response schema is invalid")


def _duplicate_require_paused(result: Mapping[str, Any], *, label: str) -> None:
    if result.get("status") != "PAUSED":
        raise PermanentError(f"duplicate Graph {label} is not configured PAUSED")


def _duplicate_campaign_id_for_index(
    authority: BrowserOperationAuthority,
    campaign_index: int,
) -> str:
    matches = [
        object_id
        for object_id, slot in authority.duplicate_created_campaign_slots.items()
        if slot == campaign_index
    ]
    if len(matches) != 1:
        raise PermanentError("duplicate Graph campaign slot provenance is ambiguous")
    return matches[0]


def _duplicate_require_source_identity(
    result: Mapping[str, Any],
    *,
    expected_id: str,
    expected_account_id: str,
    label: str,
) -> None:
    if not isinstance(result.get("id"), str) or result.get("id") != expected_id:
        raise PermanentError(f"duplicate Graph {label} returned the wrong object")
    account_id = str(result.get("account_id") or "").strip().removeprefix("act_")
    if account_id != expected_account_id:
        raise PermanentError(f"duplicate Graph {label} belongs to another cabinet")


def _record_duplicate_operation_result(
    *,
    authority: BrowserOperationAuthority,
    operation: str,
    result: Mapping[str, Any],
    ad_account_id: str,
) -> None:
    roles = authority.duplicate_pending_roles.get(operation)
    if not roles:
        return
    role = roles.pop(0)
    if not roles:
        authority.duplicate_pending_roles.pop(operation, None)
    plan = authority.duplicate_plan_state.get("plan")
    if plan is None:
        raise PermanentError("duplicate Graph task-local plan provenance is unavailable")

    if role == "source_campaign":
        _duplicate_require_source_identity(
            result,
            expected_id=plan.source_campaign_id,
            expected_account_id=ad_account_id,
            label="source campaign",
        )
        objective = (
            result.get("objective", "").strip() if isinstance(result.get("objective"), str) else ""
        )
        if not objective:
            raise PermanentError("duplicate Graph source campaign objective is invalid")
        raw_categories = result.get("special_ad_categories")
        categories = raw_categories or ["NONE"]
        if (
            not isinstance(categories, list)
            or not categories
            or not all(isinstance(value, str) and value.strip() for value in categories)
        ):
            raise PermanentError("duplicate Graph source campaign special categories are invalid")
        template: dict[str, Any] = {
            "objective": objective,
            "special_ad_categories": list(categories),
        }
        for key in ("buying_type", "bid_strategy"):
            value = result.get(key)
            if value in (None, ""):
                continue
            if not isinstance(value, str) or not value.strip():
                raise PermanentError(f"duplicate Graph source campaign {key} is invalid")
            template[key] = value
        category_countries = result.get("special_ad_category_country")
        if category_countries not in (None, "", []):
            if not isinstance(category_countries, list) or not all(
                isinstance(value, str) and value.strip() for value in category_countries
            ):
                raise PermanentError(
                    "duplicate Graph source campaign category countries are invalid"
                )
            template["special_ad_category_country"] = list(category_countries)
        authority.duplicate_source_campaign_template.update(template)
        return

    if role == "source_adset":
        _duplicate_require_source_identity(
            result,
            expected_id=plan.source_adset_id,
            expected_account_id=ad_account_id,
            label="source adset",
        )
        if str(result.get("campaign_id") or "") != plan.source_campaign_id:
            raise PermanentError("duplicate Graph source adset belongs to another campaign")
        authority.duplicate_loaded_source_adset_ids.add(plan.source_adset_id)
        return

    if role.startswith("source_ad:"):
        source_ad_id = role.removeprefix("source_ad:")
        _duplicate_require_source_identity(
            result,
            expected_id=source_ad_id,
            expected_account_id=ad_account_id,
            label="selected source ad",
        )
        if (
            str(result.get("campaign_id") or "") != plan.source_campaign_id
            or str(result.get("adset_id") or "") != plan.source_adset_id
        ):
            raise PermanentError("duplicate Graph selected source ad belongs to another source")
        name = result.get("name", "").strip() if isinstance(result.get("name"), str) else ""
        creative = result.get("creative")
        if not name or not isinstance(creative, Mapping):
            raise PermanentError("duplicate Graph selected source ad is invalid")
        creative_id = _duplicate_string_id(
            creative.get("id"),
            label="selected source ad creative_id",
        )
        authority.duplicate_source_ads[source_ad_id] = (name, creative_id)
        return

    if role.startswith("recover_prove_campaign:"):
        _, campaign_id, campaign_index_text = role.split(":", 2)
        campaign_index = int(campaign_index_text)
        _duplicate_require_exact_keys(
            result,
            required={"id", "account_id", "name", "objective", "status"},
            optional={"daily_budget"},
            label="recovery campaign proof",
        )
        _duplicate_require_source_identity(
            result,
            expected_id=campaign_id,
            expected_account_id=ad_account_id,
            label="recovery campaign",
        )
        if (
            result.get("name") != plan.campaign_names[campaign_index]
            or not isinstance(result.get("objective"), str)
            or not result["objective"].strip()
            or not isinstance(result.get("status"), str)
            or not result["status"].strip()
        ):
            raise PermanentError("duplicate Graph recovery campaign proof does not match the task")
        authority.duplicate_recovery_verified_ids["campaigns"].add(campaign_id)
        return

    if role.startswith("recover_prove_adset:"):
        _, adset_id, campaign_id = role.split(":", 2)
        _duplicate_require_exact_keys(
            result,
            required={"id", "account_id", "campaign_id", "status"},
            label="recovery adset proof",
        )
        _duplicate_require_source_identity(
            result,
            expected_id=adset_id,
            expected_account_id=ad_account_id,
            label="recovery adset",
        )
        if (
            result.get("campaign_id") != campaign_id
            or not isinstance(result.get("status"), str)
            or not result["status"].strip()
        ):
            raise PermanentError("duplicate Graph recovery adset proof does not match its parent")
        authority.duplicate_recovery_verified_ids["adsets"].add(adset_id)
        return

    if role.startswith("recover_prove_ad:"):
        _, ad_id, campaign_id, adset_id = role.split(":", 3)
        _duplicate_require_exact_keys(
            result,
            required={
                "id",
                "account_id",
                "campaign_id",
                "adset_id",
                "name",
                "status",
                "creative",
            },
            label="recovery ad proof",
        )
        _duplicate_require_source_identity(
            result,
            expected_id=ad_id,
            expected_account_id=ad_account_id,
            label="recovery ad",
        )
        creative = result.get("creative")
        if (
            result.get("campaign_id") != campaign_id
            or result.get("adset_id") != adset_id
            or not isinstance(result.get("name"), str)
            or not result["name"].strip()
            or not isinstance(result.get("status"), str)
            or not result["status"].strip()
            or not isinstance(creative, Mapping)
            or set(creative) != {"id"}
        ):
            raise PermanentError("duplicate Graph recovery ad proof does not match its parent")
        _duplicate_string_id(creative.get("id"), label="recovery ad creative_id")
        authority.duplicate_recovery_verified_ids["ads"].add(ad_id)
        return

    if role.startswith("create_campaign:"):
        campaign_index = int(role.removeprefix("create_campaign:"))
        campaign_id = _duplicate_extract_exact_result_id(
            result,
            key="id",
            label="campaign create",
        )
        _duplicate_require_fresh_candidate_id(
            authority=authority,
            plan=plan,
            object_id=campaign_id,
            label="campaign create",
        )
        authority.duplicate_candidate_campaign_slots[campaign_id] = campaign_index
        return

    if role.startswith("prove_campaign:"):
        campaign_id = role.removeprefix("prove_campaign:")
        campaign_index = authority.duplicate_candidate_campaign_slots.get(campaign_id)
        if campaign_index is None:
            raise PermanentError("duplicate Graph campaign candidate provenance is unavailable")
        _duplicate_require_exact_keys(
            result,
            required={"id", "account_id", "name", "objective", "status"},
            optional={"daily_budget"},
            label="campaign proof",
        )
        _duplicate_require_source_identity(
            result,
            expected_id=campaign_id,
            expected_account_id=ad_account_id,
            label="created campaign",
        )
        _duplicate_require_paused(result, label="created campaign")
        if result.get("name") != plan.campaign_names[campaign_index] or result.get(
            "objective"
        ) != authority.duplicate_source_campaign_template.get("objective"):
            raise PermanentError("duplicate Graph campaign proof does not match the task plan")
        budget = result.get("daily_budget")
        if plan.budget_level == "CBO":
            if isinstance(budget, bool) or str(budget) != str(plan.daily_budget_minor_units):
                raise PermanentError("duplicate Graph campaign proof budget is invalid")
        elif budget not in (None, "", 0, "0"):
            raise PermanentError("duplicate Graph ABO campaign unexpectedly owns a budget")
        authority.duplicate_candidate_campaign_slots.pop(campaign_id)
        authority.duplicate_created_campaign_slots[campaign_id] = campaign_index
        return

    if role.startswith("copy_adset:"):
        _, campaign_index_text, adset_index_text = role.split(":", 2)
        adset_id = _duplicate_extract_exact_result_id(
            result,
            key="copied_adset_id",
            label="adset copy",
        )
        _duplicate_require_fresh_candidate_id(
            authority=authority,
            plan=plan,
            object_id=adset_id,
            label="adset copy",
        )
        campaign_index = int(campaign_index_text)
        campaign_id = _duplicate_campaign_id_for_index(authority, campaign_index)
        authority.duplicate_candidate_adset_slots[adset_id] = (
            campaign_index,
            int(adset_index_text),
            campaign_id,
        )
        return

    if role.startswith("prove_adset:"):
        adset_id = role.removeprefix("prove_adset:")
        candidate = authority.duplicate_candidate_adset_slots.get(adset_id)
        if candidate is None:
            raise PermanentError("duplicate Graph adset candidate provenance is unavailable")
        campaign_index, adset_index, campaign_id = candidate
        _duplicate_require_exact_keys(
            result,
            required={"id", "account_id", "campaign_id", "status"},
            label="adset proof",
        )
        _duplicate_require_source_identity(
            result,
            expected_id=adset_id,
            expected_account_id=ad_account_id,
            label="copied adset",
        )
        _duplicate_require_paused(result, label="copied adset")
        if result.get("campaign_id") != campaign_id:
            raise PermanentError("duplicate Graph copied adset has the wrong campaign parent")
        authority.duplicate_candidate_adset_slots.pop(adset_id)
        authority.duplicate_created_adset_slots[adset_id] = (
            campaign_index,
            adset_index,
        )
        return

    if role.startswith("configure_adset:"):
        adset_id = role.removeprefix("configure_adset:")
        if set(result) != {"success"}:
            raise AmbiguousResultError(
                "duplicate Graph adset configuration response schema is not exact"
            )
        if result.get("success") is False:
            raise PermanentError("duplicate Graph adset configuration was explicitly rejected")
        if result.get("success") is not True:
            raise AmbiguousResultError("duplicate Graph adset configuration lacks success=true")
        authority.duplicate_configured_adset_ids.add(adset_id)
        return

    if role.startswith("create_ad:"):
        _, adset_id, source_ad_id = role.split(":", 2)
        ad_id = _duplicate_extract_exact_result_id(
            result,
            key="id",
            label="ad create",
        )
        _duplicate_require_fresh_candidate_id(
            authority=authority,
            plan=plan,
            object_id=ad_id,
            label="ad create",
        )
        authority.duplicate_candidate_ad_ids[ad_id] = (adset_id, source_ad_id)
        return

    if role.startswith("prove_ad:"):
        ad_id = role.removeprefix("prove_ad:")
        candidate = authority.duplicate_candidate_ad_ids.get(ad_id)
        if candidate is None:
            raise PermanentError("duplicate Graph ad candidate provenance is unavailable")
        adset_id, source_ad_id = candidate
        adset_slot = authority.duplicate_created_adset_slots.get(adset_id)
        if adset_slot is None:
            raise PermanentError("duplicate Graph ad parent provenance is unavailable")
        campaign_id = _duplicate_campaign_id_for_index(authority, adset_slot[0])
        source_name, source_creative_id = authority.duplicate_source_ads[source_ad_id]
        _duplicate_require_exact_keys(
            result,
            required={
                "id",
                "account_id",
                "campaign_id",
                "adset_id",
                "name",
                "status",
                "creative",
            },
            label="ad proof",
        )
        _duplicate_require_source_identity(
            result,
            expected_id=ad_id,
            expected_account_id=ad_account_id,
            label="created ad",
        )
        _duplicate_require_paused(result, label="created ad")
        creative = result.get("creative")
        if (
            result.get("campaign_id") != campaign_id
            or result.get("adset_id") != adset_id
            or result.get("name") != source_name
            or not isinstance(creative, Mapping)
            or set(creative) != {"id"}
            or creative.get("id") != source_creative_id
        ):
            raise PermanentError("duplicate Graph created ad proof does not match its parent")
        authority.duplicate_candidate_ad_ids.pop(ad_id)
        authority.duplicate_created_ad_ids.add(ad_id)
        return

    raise PermanentError("duplicate Graph operation provenance role is invalid")


def _validate_duplicate_graph_operation(
    *,
    authority: BrowserOperationAuthority,
    payload: Mapping[str, Any],
    task_result: Mapping[str, Any],
    method: str,
    endpoint: str,
    operation: str,
    ad_account_id: str,
    graph_semantics: tuple[str, str, dict[str, str], str] | None,
) -> None:
    if graph_semantics is None:
        raise PermanentError("duplicate Graph capability requires canonical request semantics")
    semantic_method, semantic_endpoint, semantic_query, semantic_body = graph_semantics
    if (
        method != semantic_method
        or endpoint != semantic_endpoint
        or operation
        != graph_operation_binding(
            method=semantic_method,
            endpoint=semantic_endpoint,
            query_params=semantic_query,
            body_json=semantic_body,
        )
    ):
        raise PermanentError("duplicate Graph capability binding is inconsistent")

    plan = _duplicate_operation_plan(payload, ad_account_id=ad_account_id)
    stored_plan = authority.duplicate_plan_state.get("plan")
    if stored_plan is None:
        authority.duplicate_plan_state["plan"] = plan
    elif stored_plan != plan:
        raise PermanentError("duplicate Graph task plan changed during execution")

    recovery_value = task_result.get("recovery_requested")
    if recovery_value is not None and not isinstance(recovery_value, bool):
        raise PermanentError("duplicate Graph recovery flag is invalid")
    recovery_requested = recovery_value is True
    checkpoint_ids = _duplicate_checkpoint_ids(
        task_result,
        recovery_requested=recovery_requested,
        plan=plan,
    )
    for key, values in checkpoint_ids.items():
        authority.duplicate_checkpoint_ids[key].clear()
        authority.duplicate_checkpoint_ids[key].update(values)
    checkpoint_all_ids = set().union(*checkpoint_ids.values())

    if recovery_requested:
        target_match = re.fullmatch(r"/([0-9]+)", endpoint)
        if target_match is None or target_match.group(1) not in checkpoint_all_ids:
            raise PermanentError(
                "duplicate Graph recovery target is not in the persisted checkpoint"
            )
        target_id = target_match.group(1)
        if method == "GET" and not semantic_body:
            if target_id in checkpoint_ids["campaigns"]:
                campaign_index = checkpoint_ids["campaigns"].index(target_id)
                expected_query = {"fields": DUPLICATE_PROVE_CAMPAIGN_FIELDS}
                role = f"recover_prove_campaign:{target_id}:{campaign_index}"
                bucket = "campaigns"
            elif target_id in checkpoint_ids["adsets"]:
                adset_index = checkpoint_ids["adsets"].index(target_id)
                campaign_index = adset_index // len(plan.adset_names[0])
                campaign_id = checkpoint_ids["campaigns"][campaign_index]
                expected_query = {"fields": DUPLICATE_PROVE_ADSET_FIELDS}
                role = f"recover_prove_adset:{target_id}:{campaign_id}"
                bucket = "adsets"
            else:
                ad_index = checkpoint_ids["ads"].index(target_id)
                adset_index = ad_index // len(plan.selected_ad_ids)
                adset_id = checkpoint_ids["adsets"][adset_index]
                campaign_index = adset_index // len(plan.adset_names[0])
                campaign_id = checkpoint_ids["campaigns"][campaign_index]
                expected_query = {"fields": DUPLICATE_PROVE_AD_FIELDS}
                role = f"recover_prove_ad:{target_id}:{campaign_id}:{adset_id}"
                bucket = "ads"
            if semantic_query == expected_query:
                if target_id in authority.duplicate_recovery_verified_ids[
                    bucket
                ] or _duplicate_role_is_pending(authority, role=role):
                    raise PermanentError("duplicate Graph recovery target proof was already used")
                _duplicate_queue_role(authority, operation=operation, role=role)
                return
            all_checkpoint_ids_verified = all(
                set(checkpoint_ids[bucket]) == authority.duplicate_recovery_verified_ids[bucket]
                for bucket in _DUPLICATE_CREATED_ID_LIMITS
            )
            if (
                all_checkpoint_ids_verified
                and target_id in authority.duplicate_recovery_verified_ids[bucket]
                and semantic_query == {"fields": _DUPLICATE_CLEANUP_FIELDS}
            ):
                return
        all_checkpoint_ids_verified = all(
            set(checkpoint_ids[bucket]) == authority.duplicate_recovery_verified_ids[bucket]
            for bucket in _DUPLICATE_CREATED_ID_LIMITS
        )
        if (
            method == "POST"
            and not semantic_query
            and bool(semantic_body)
            and all_checkpoint_ids_verified
            and any(
                target_id in values for values in authority.duplicate_recovery_verified_ids.values()
            )
            and _load_canonical_graph_body(semantic_body) == {"status": "PAUSED"}
        ):
            return
        raise PermanentError("duplicate Graph recovery requires exact typed proof before PAUSE")

    if method == "GET":
        if semantic_body:
            raise PermanentError("duplicate Graph GET body is not authorized")
        if endpoint == f"/{plan.source_campaign_id}" and semantic_query == {
            "fields": DUPLICATE_SOURCE_CAMPAIGN_FIELDS
        }:
            if authority.duplicate_source_campaign_template or _duplicate_role_is_pending(
                authority, role="source_campaign"
            ):
                raise PermanentError("duplicate Graph source campaign read was already used")
            _duplicate_queue_role(
                authority,
                operation=operation,
                role="source_campaign",
            )
            return
        if endpoint == f"/{plan.source_adset_id}" and semantic_query == {
            "fields": DUPLICATE_SOURCE_ADSET_FIELDS
        }:
            if (
                plan.source_adset_id in authority.duplicate_loaded_source_adset_ids
                or _duplicate_role_is_pending(authority, role="source_adset")
            ):
                raise PermanentError("duplicate Graph source adset read was already used")
            _duplicate_queue_role(
                authority,
                operation=operation,
                role="source_adset",
            )
            return
        source_ad_match = re.fullmatch(r"/([0-9]+)", endpoint)
        if (
            source_ad_match is not None
            and source_ad_match.group(1) in plan.selected_ad_ids
            and semantic_query == {"fields": DUPLICATE_SOURCE_AD_FIELDS}
        ):
            source_ad_id = source_ad_match.group(1)
            role = f"source_ad:{source_ad_id}"
            if source_ad_id in authority.duplicate_source_ads or _duplicate_role_is_pending(
                authority, role=role
            ):
                raise PermanentError("duplicate Graph selected source ad read was already used")
            _duplicate_queue_role(authority, operation=operation, role=role)
            return

        target_match = re.fullmatch(r"/([0-9]+)(/ads)?", endpoint)
        if target_match is None:
            raise PermanentError("duplicate Graph read target is not authorized")
        target_id, ads_edge = target_match.groups()
        if ads_edge is None and target_id in authority.duplicate_candidate_campaign_slots:
            role = f"prove_campaign:{target_id}"
            if semantic_query != {"fields": DUPLICATE_PROVE_CAMPAIGN_FIELDS}:
                raise PermanentError("duplicate Graph campaign proof is not exact")
            if _duplicate_role_is_pending(authority, role=role):
                raise PermanentError("duplicate Graph campaign proof is already pending")
            _duplicate_queue_role(authority, operation=operation, role=role)
            return
        if ads_edge is None and target_id in authority.duplicate_candidate_adset_slots:
            role = f"prove_adset:{target_id}"
            if semantic_query != {"fields": DUPLICATE_PROVE_ADSET_FIELDS}:
                raise PermanentError("duplicate Graph adset proof is not exact")
            if _duplicate_role_is_pending(authority, role=role):
                raise PermanentError("duplicate Graph adset proof is already pending")
            _duplicate_queue_role(authority, operation=operation, role=role)
            return
        if ads_edge is None and target_id in authority.duplicate_candidate_ad_ids:
            role = f"prove_ad:{target_id}"
            if semantic_query != {"fields": DUPLICATE_PROVE_AD_FIELDS}:
                raise PermanentError("duplicate Graph ad proof is not exact")
            if _duplicate_role_is_pending(authority, role=role):
                raise PermanentError("duplicate Graph ad proof is already pending")
            _duplicate_queue_role(authority, operation=operation, role=role)
            return
        created_campaign_ids = set(authority.duplicate_created_campaign_slots)
        created_adset_ids = set(authority.duplicate_created_adset_slots)
        created_all_ids = (
            created_campaign_ids | created_adset_ids | authority.duplicate_created_ad_ids
        )
        if (
            ads_edge is None
            and target_id in created_all_ids
            and semantic_query == {"fields": _DUPLICATE_CLEANUP_FIELDS}
        ):
            return
        if (
            ads_edge is None
            and target_id in created_campaign_ids
            and semantic_query == {"fields": _DUPLICATE_VERIFY_CAMPAIGN_FIELDS}
        ):
            return
        if (
            ads_edge is None
            and target_id in created_adset_ids
            and semantic_query == {"fields": _DUPLICATE_VERIFY_ADSET_FIELDS}
        ):
            return
        if (
            ads_edge == "/ads"
            and target_id in created_adset_ids
            and semantic_query == {"fields": DUPLICATE_VERIFY_AD_FIELDS, "limit": "100"}
        ):
            return
        raise PermanentError("duplicate Graph read action is not authorized")

    if method != "POST" or semantic_query or not semantic_body:
        raise PermanentError("duplicate Graph write action is not authorized")
    body = _load_canonical_graph_body(semantic_body)
    if not isinstance(body, Mapping):
        raise PermanentError("duplicate Graph write body is not authorized")

    direct_target_match = re.fullmatch(r"/([0-9]+)", endpoint)
    created_all_ids = (
        set(authority.duplicate_created_campaign_slots)
        | set(authority.duplicate_created_adset_slots)
        | authority.duplicate_created_ad_ids
    )
    if (
        direct_target_match is not None
        and direct_target_match.group(1) in created_all_ids
        and body == {"status": "PAUSED"}
    ):
        return

    sources_ready = (
        bool(authority.duplicate_source_campaign_template)
        and plan.source_adset_id in authority.duplicate_loaded_source_adset_ids
        and set(authority.duplicate_source_ads) == set(plan.selected_ad_ids)
    )
    if endpoint == f"{plan.account_root}/campaigns":
        if not sources_ready:
            raise PermanentError("duplicate Graph create requires complete source-read provenance")
        campaign_index = authority.duplicate_counters.get("campaign_create", 0)
        if campaign_index >= len(plan.campaign_names):
            raise PermanentError("duplicate Graph campaign create cardinality exceeded")
        expected_body: dict[str, Any] = {
            "name": plan.campaign_names[campaign_index],
            **authority.duplicate_source_campaign_template,
            "status": "PAUSED",
        }
        if plan.budget_level == "CBO":
            expected_body["daily_budget"] = plan.daily_budget_minor_units
        if body != expected_body or (
            plan.budget_level == "CBO"
            and (
                isinstance(body.get("daily_budget"), bool)
                or not isinstance(body.get("daily_budget"), int)
            )
        ):
            raise PermanentError("duplicate Graph campaign create body is not authorized")
        authority.duplicate_counters["campaign_create"] = campaign_index + 1
        _duplicate_queue_role(
            authority,
            operation=operation,
            role=f"create_campaign:{campaign_index}",
        )
        return

    if endpoint == f"/{plan.source_adset_id}/copies":
        if set(body) != {"campaign_id", "deep_copy", "status_option"}:
            raise PermanentError("duplicate Graph adset copy body is not authorized")
        campaign_id = _duplicate_string_id(
            body.get("campaign_id"),
            label="copy campaign_id",
        )
        if (
            campaign_id not in authority.duplicate_created_campaign_slots
            or body.get("deep_copy") is not False
            or body.get("status_option") != "PAUSED"
        ):
            raise PermanentError("duplicate Graph adset copy body is not authorized")
        campaign_index = authority.duplicate_created_campaign_slots[campaign_id]
        counter_key = f"copy_adset:{campaign_id}"
        adset_index = authority.duplicate_counters.get(counter_key, 0)
        if adset_index >= len(plan.adset_names[campaign_index]):
            raise PermanentError("duplicate Graph adset copy cardinality exceeded")
        authority.duplicate_counters[counter_key] = adset_index + 1
        _duplicate_queue_role(
            authority,
            operation=operation,
            role=f"copy_adset:{campaign_index}:{adset_index}",
        )
        return

    if direct_target_match is not None:
        adset_id = direct_target_match.group(1)
        slot = authority.duplicate_created_adset_slots.get(adset_id)
        role = f"configure_adset:{adset_id}"
        if slot is None:
            raise PermanentError("duplicate Graph adset configuration has no task-local provenance")
        if adset_id in authority.duplicate_configured_adset_ids or _duplicate_role_is_pending(
            authority, role=role
        ):
            raise PermanentError("duplicate Graph adset was already configured")
        campaign_index, adset_index = slot
        expected_body = {
            "name": plan.adset_names[campaign_index][adset_index],
            "status": "PAUSED",
            "start_time": plan.start_time,
        }
        if plan.budget_level == "ABO":
            expected_body["daily_budget"] = plan.daily_budget_minor_units
        if body != expected_body or (
            plan.budget_level == "ABO"
            and (
                isinstance(body.get("daily_budget"), bool)
                or not isinstance(body.get("daily_budget"), int)
            )
        ):
            raise PermanentError("duplicate Graph adset configuration body is not authorized")
        _duplicate_queue_role(authority, operation=operation, role=role)
        return

    if endpoint == f"{plan.account_root}/ads":
        if set(body) != {"name", "adset_id", "creative", "status"}:
            raise PermanentError("duplicate Graph ad create body is not authorized")
        adset_id = _duplicate_string_id(body.get("adset_id"), label="ad adset_id")
        if adset_id not in authority.duplicate_configured_adset_ids:
            raise PermanentError(
                "duplicate Graph ad target has no configured task-local adset provenance"
            )
        next_key = f"create_ad:{adset_id}"
        source_index = authority.duplicate_counters.get(next_key, 0)
        if source_index >= len(plan.selected_ad_ids):
            raise PermanentError("duplicate Graph ad create cardinality exceeded")
        source_ad_id = plan.selected_ad_ids[source_index]
        source_name, source_creative_id = authority.duplicate_source_ads[source_ad_id]
        expected_body = {
            "name": source_name,
            "adset_id": adset_id,
            "creative": {"creative_id": source_creative_id},
            "status": "PAUSED",
        }
        if body != expected_body:
            raise PermanentError("duplicate Graph ad create body is not authorized")
        authority.duplicate_counters[next_key] = source_index + 1
        _duplicate_queue_role(
            authority,
            operation=operation,
            role=f"create_ad:{adset_id}:{source_ad_id}",
        )
        return

    raise PermanentError("duplicate Graph write target is not authorized")


def _is_money_graph_call(
    *,
    method: str,
    endpoint: str,
    query_params: dict[str, str] | None,
) -> bool:
    """Classify every Graph write and status reconciliation as money-control."""
    normalized_method = method.strip().upper()
    if normalized_method != "GET":
        return True
    normalized_endpoint = endpoint.strip()
    if re.fullmatch(r"/?\d+/thumbnails", normalized_endpoint):
        return True
    if not re.fullmatch(r"/?\d+", normalized_endpoint):
        return False
    fields = {
        field.strip().lower()
        for field in str((query_params or {}).get("fields") or "").split(",")
        if field.strip()
    }
    return bool(fields & {"status", "effective_status"})


def _account_from_endpoint(endpoint: str) -> str | None:
    match = re.search(r"(?:^|/)act_(\d+)(?:/|$)", endpoint)
    return match.group(1) if match else None


class MetaApiClient:
    """Клиент Marketing API через gRPC к browser-agent.

    Usage:
        client = MetaApiClient(host="localhost", port=50051)
        await client.start()
        try:
            data = await client.execute_graph_call(
                method="GET", endpoint="/me", query_params={}
            )
        finally:
            await client.close()
    """

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 50051,
        channel: grpc.aio.Channel | None = None,
        session_id: str = "",
        circuit_breaker: AsyncCircuitBreaker | None = None,
        operation_engine: AsyncEngine | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._external_channel = channel is not None
        self._channel: grpc.aio.Channel | None = channel
        self._stub: meta_api_pb2_grpc.MetaApiServiceStub | None = None
        # session_id="" → browser-agent сам выбирает preferred session
        self.session_id = session_id
        self._operation_engine = operation_engine
        self._circuit_breaker = circuit_breaker or AsyncCircuitBreaker(
            name="meta-api",
            failure_threshold=3,
            recovery_timeout=60.0,
        )

    @contextmanager
    def operation_authority(
        self,
        *,
        caller: str,
        task_id: int,
        lease_owner: uuid.UUID,
        lease_token: int,
        vision_profile_id: str,
        browser_readiness_generation: int | None = None,
    ) -> Iterator[None]:
        """Bind one claimed task to every money RPC in the current async context."""
        normalized_caller = caller.strip()
        normalized_profile_id = vision_profile_id.strip()
        if normalized_caller not in _AUTHORIZED_OPERATION_CALLERS:
            raise ValueError("browser operation caller is not authorized")
        if task_id <= 0 or lease_token <= 0 or not isinstance(lease_owner, uuid.UUID):
            raise ValueError("browser operation requires a valid task lease")
        if not normalized_profile_id:
            raise ValueError("browser operation requires an exact Vision profile")
        if browser_readiness_generation is not None and int(browser_readiness_generation) <= 0:
            raise ValueError("browser readiness generation must be positive")
        token = _OPERATION_AUTHORITY.set(
            BrowserOperationAuthority(
                caller=normalized_caller,
                task_id=int(task_id),
                lease_owner=lease_owner,
                lease_token=int(lease_token),
                vision_profile_id=normalized_profile_id,
                browser_readiness_generation=(
                    int(browser_readiness_generation)
                    if browser_readiness_generation is not None
                    else None
                ),
            )
        )
        try:
            yield
        finally:
            _OPERATION_AUTHORITY.reset(token)

    def _remember_campaign_uploaded_video_id(
        self,
        video_id: str,
        *,
        ad_account_id: str,
    ) -> None:
        """Record one upload result inside the exact current create-task context."""
        authority = _OPERATION_AUTHORITY.get()
        normalized_video_id = str(video_id).strip()
        require_ad_account_id(ad_account_id)
        if authority is None or authority.caller != "campaign_creator":
            raise PermanentError(
                "campaign video provenance requires create-task operation authority"
            )
        if not normalized_video_id.isdigit():
            raise PermanentError("campaign upload returned an invalid video id")
        authority.uploaded_video_ids.add(normalized_video_id)

    def _remember_campaign_uploaded_image_hash(
        self,
        image_hash: str,
        *,
        ad_account_id: str,
    ) -> None:
        """Record one image upload result inside the exact create-task context."""
        authority = _OPERATION_AUTHORITY.get()
        normalized_image_hash = str(image_hash).strip()
        require_ad_account_id(ad_account_id)
        if authority is None or authority.caller != "campaign_creator":
            raise PermanentError(
                "campaign image provenance requires create-task operation authority"
            )
        if not normalized_image_hash or len(normalized_image_hash) > 512:
            raise PermanentError("campaign upload returned an invalid image hash")
        authority.uploaded_image_hashes.add(normalized_image_hash)

    def _remember_campaign_created_object_id(
        self,
        *,
        endpoint: str,
        object_id: str,
        ad_account_id: str,
    ) -> None:
        """Advance the task-local campaign creation graph after an acknowledged POST."""
        authority = _OPERATION_AUTHORITY.get()
        account_id = require_ad_account_id(ad_account_id)
        normalized_object_id = str(object_id).strip()
        account_root = f"/{graph_ad_account_id(account_id)}"
        edge = endpoint.removeprefix(f"{account_root}/")
        if (
            authority is None
            or authority.caller != "campaign_creator"
            or endpoint != f"{account_root}/{edge}"
            or edge not in _CAMPAIGN_CREATE_EDGES
        ):
            raise PermanentError("campaign object provenance requires the exact create-task edge")
        if not normalized_object_id.isdigit():
            raise PermanentError("campaign create returned an invalid object id")
        {
            "campaigns": authority.created_campaign_ids,
            "adsets": authority.created_adset_ids,
            "adcreatives": authority.created_creative_ids,
            "ads": authority.created_ad_ids,
        }[edge].add(normalized_object_id)

    async def _invalidate_claimed_browser_readiness(
        self,
        authority: BrowserOperationAuthority,
        *,
        reason_code: str,
    ) -> None:
        """CAS-expire only the scheduling evidence rejected by this live check."""
        generation = authority.browser_readiness_generation
        if self._operation_engine is None or generation is None:
            return
        try:
            async with asyncio.timeout(_OPERATION_AUTHORITY_DB_TIMEOUT_SECONDS):
                async with self._operation_engine.begin() as conn:
                    await conn.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
                    await conn.execute(
                        text(
                            """
                            SELECT pg_advisory_xact_lock(
                              hashtext('fb-agent'),
                              hashtext('browser-maintenance')
                            )
                            """
                        )
                    )
                    await conn.execute(
                        text(
                            """
                            UPDATE browser_channel_readiness
                            SET state = 'unavailable',
                                reason_code = :reason_code,
                                observed_at = clock_timestamp(),
                                readiness_expires_at = NULL,
                                generation = generation + 1,
                                updated_at = clock_timestamp()
                            WHERE channel = 'meta_api'
                              AND state = 'ready'
                              AND generation = :generation
                            """
                        ),
                        {
                            "generation": generation,
                            "reason_code": reason_code,
                        },
                    )
        except Exception:  # noqa: BLE001 - TTL remains the fail-closed backstop
            logger.warning(
                "failed to CAS-expire rejected browser readiness generation=%s",
                generation,
                exc_info=True,
            )

    async def _controlled_presend_readiness_error(
        self,
        exc: grpc.RpcError,
        *,
        endpoint: str,
    ) -> BrowserReadinessRejectedError | None:
        """Map explicit browser pre-dispatch statuses and expire their claim evidence."""
        authority = _OPERATION_AUTHORITY.get()
        if authority is None:
            return None
        code = exc.code() if hasattr(exc, "code") else None  # type: ignore[union-attr]
        reason_code = {
            grpc.StatusCode.FAILED_PRECONDITION: "presend_session_precondition",
            grpc.StatusCode.UNIMPLEMENTED: "presend_contract_unimplemented",
        }.get(code)
        if reason_code is None:
            return None
        details = (
            exc.details() if hasattr(exc, "details") else str(exc)  # type: ignore[union-attr]
        )
        await self._invalidate_claimed_browser_readiness(
            authority,
            reason_code=reason_code,
        )
        return BrowserReadinessRejectedError(
            f"browser-agent rejected the controlled request before Meta dispatch: {details}",
            endpoint=endpoint,
        )

    async def prepare_operation_authorization(
        self,
        *,
        rpc: str,
        operation: str,
        ad_account_id: str,
        graph_method: str | None = None,
        graph_endpoint: str | None = None,
        graph_query_params: Mapping[str, str] | None = None,
        graph_body_json: str | None = None,
    ) -> dict[str, Any]:
        """Resolve an exact live session and sign a single-use fenced capability."""
        if self._stub is None:
            raise RuntimeError("MetaApiClient не запущен: вызови await start()")
        authority = _OPERATION_AUTHORITY.get()
        if authority is None:
            raise PermanentError("money browser RPC requires claimed-task operation authority")
        graph_semantics: tuple[str, str, dict[str, str], str] | None = None
        if rpc == "execute_graph_call":
            if (
                graph_method is None
                or graph_endpoint is None
                or graph_query_params is None
                or graph_body_json is None
            ):
                raise PermanentError("Graph capability requires canonical request semantics")
            else:
                normalized_graph_method = graph_method.strip().upper()
                normalized_graph_query = {
                    str(key): str(value) for key, value in graph_query_params.items()
                }
                validate_graph_request_semantics(
                    method=normalized_graph_method,
                    endpoint=graph_endpoint,
                    query_params=normalized_graph_query,
                    body_json=graph_body_json,
                )
                if operation != graph_operation_binding(
                    method=normalized_graph_method,
                    endpoint=graph_endpoint,
                    query_params=normalized_graph_query,
                    body_json=graph_body_json,
                ):
                    raise PermanentError("browser Graph capability request binding is inconsistent")
                graph_semantics = (
                    normalized_graph_method,
                    graph_endpoint,
                    normalized_graph_query,
                    graph_body_json,
                )
        elif any(
            value is not None
            for value in (
                graph_method,
                graph_endpoint,
                graph_query_params,
                graph_body_json,
            )
        ):
            raise PermanentError("non-Graph capability has Graph request semantics")
        account_id = require_ad_account_id(ad_account_id)
        ttl_seconds = _OPERATION_RPC_TTL_SECONDS.get(rpc)
        if ttl_seconds is None:
            raise PermanentError("browser operation RPC is not authorized")
        secret = os.environ.get("BROWSER_OPERATION_CAPABILITY_SECRET", "")
        if len(secret) < 48:
            raise PermanentError("browser operation capability secret is unavailable")

        remaining = remaining_deadline_seconds()
        if remaining is not None and remaining <= 0.001:
            raise PreDispatchRejectedError(
                "absolute deadline exhausted before exact browser identity resolution"
            )
        # Money-предполёт делает РЕАЛЬНЫЙ GET /me той же сессией, что исполнит
        # операцию: иначе первым сетевым контактом с Meta во всём заливе
        # оказывается сам необратимый POST, и разлогин отказывает уже внутри
        # money-окна. Вердикт кешируется browser-agent'ом на страницу, поэтому
        # цепочка вызовов одного залива не платит за пробу на каждом шаге.
        timeout = _HEALTH_PROBE_TIMEOUT_SECONDS
        if remaining is not None:
            timeout = max(0.001, min(timeout, remaining))
        try:
            # Кабинет операции называется явно. Без него browser-agent выбирал
            # произвольную живую вкладку любого кабинета и лочил чужой
            # interactive: проба money-операции адресовала не тот кабинет,
            # в котором операция будет работать.
            #
            # Роль страницы называется тоже. Мутация уходит с control-страницы;
            # проба без роли уходила на interactive, и «токен жив» на одной
            # вкладке ничего не говорил о второй — доказательство относилось не
            # к той странице, которая отправит POST.
            identity = await self._stub.CheckMetaApiHealth(
                meta_api_pb2.CheckMetaApiHealthRequest(
                    session_id=self.session_id,
                    full_probe=True,
                    expected_vision_profile_id=authority.vision_profile_id,
                    ad_account_id=str(account_id or "").replace("act_", "").strip(),
                    operation_role="control",
                ),
                timeout=timeout,
            )
        except grpc.RpcError as exc:  # type: ignore[misc]
            await self._invalidate_claimed_browser_readiness(
                authority,
                reason_code="exact_live_transport_rejected",
            )
            raise BrowserReadinessRejectedError(
                f"exact browser identity unavailable: {exc.details() if hasattr(exc, 'details') else exc}"
            ) from exc

        observed_contract_version = int(getattr(identity, "browser_contract_version", 0) or 0)
        if observed_contract_version != BROWSER_CONTRACT_VERSION:
            await self._invalidate_claimed_browser_readiness(
                authority,
                reason_code="exact_live_contract_rejected",
            )
            raise BrowserReadinessRejectedError(
                "browser semantic contract is incompatible "
                f"(required={BROWSER_CONTRACT_VERSION}, "
                f"observed={observed_contract_version})"
            )

        if not bool(getattr(identity, "probe_performed", False)):
            # Здоровье без выполненной пробы — это признак в DOM, а не
            # доказательство: строка токена переживает и разлогин профиля, и
            # мёртвый сетевой канал. Подписывать под неё одноразовый грант
            # значит отдать первый контакт с Meta необратимому POST.
            await self._invalidate_claimed_browser_readiness(
                authority,
                reason_code="exact_live_probe_missing",
            )
            raise BrowserReadinessRejectedError(
                "money preflight has no live Meta probe from the operation session"
            )

        exact_session_id = str(identity.session_id or "").strip()
        exact_profile_id = str(identity.vision_profile_id or "").strip()
        if (
            not identity.healthy
            or not exact_session_id
            or exact_profile_id != authority.vision_profile_id
            or (self.session_id and exact_session_id != self.session_id)
        ):
            await self._invalidate_claimed_browser_readiness(
                authority,
                reason_code="exact_live_identity_rejected",
            )
            # Наружу уходит только машинный вердикт пробы из известного набора:
            # свободный текст browser-agent может нести Graph-ответ с токеном.
            verdict = str(getattr(identity, "probe_detail", "") or "").strip()
            raise BrowserReadinessRejectedError(
                "exact browser session/profile is not ready for a money operation "
                f"({verdict if verdict in _LIVE_PROBE_REJECT_VERDICTS else 'not_ready'})"
            )

        nonce = secrets.token_hex(16)
        expires_at, payload = await self._issue_live_operation_authority(
            authority=authority,
            rpc=rpc,
            operation=operation,
            ad_account_id=account_id,
            session_id=exact_session_id,
            vision_profile_id=exact_profile_id,
            ttl_seconds=ttl_seconds,
            remaining_seconds=remaining,
            nonce=nonce,
            graph_semantics=graph_semantics,
        )
        fields: dict[str, Any] = {
            "session_id": exact_session_id,
            "vision_profile_id": exact_profile_id,
            "authorized_caller": authority.caller,
            "task_id": authority.task_id,
            "lease_owner": str(authority.lease_owner),
            "lease_token": authority.lease_token,
            "capability_expires_at": expires_at,
            "capability_nonce": nonce,
        }
        fields["capability_signature"] = hmac.new(
            secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        return fields

    async def _issue_live_operation_authority(
        self,
        *,
        authority: BrowserOperationAuthority,
        rpc: str,
        operation: str,
        ad_account_id: str,
        session_id: str,
        vision_profile_id: str,
        ttl_seconds: int,
        remaining_seconds: float | None,
        nonce: str,
        graph_semantics: tuple[str, str, dict[str, str], str] | None,
    ) -> tuple[int, str]:
        """Atomically re-read the fence and persist a pending one-shot grant."""
        if self._operation_engine is None:
            raise PermanentError("money browser RPC requires a PostgreSQL operation authority")
        remaining = remaining_deadline_seconds()
        if remaining is not None and remaining <= 0.001:
            raise PreDispatchRejectedError(
                "task absolute deadline exhausted before lease authority database read"
            )
        db_timeout = _OPERATION_AUTHORITY_DB_TIMEOUT_SECONDS
        if remaining is not None:
            db_timeout = max(0.001, min(db_timeout, remaining))
        try:
            async with asyncio.timeout(db_timeout):
                async with self._operation_engine.begin() as conn:
                    await conn.execute(
                        _SET_LOCAL_STATEMENT_TIMEOUT_SQL,
                        {"timeout_ms": str(max(1, int(db_timeout * 1000)))},
                    )
                    result = await conn.execute(
                        _LIVE_OPERATION_AUTHORITY_SQL,
                        {
                            "task_id": authority.task_id,
                            "lease_owner": authority.lease_owner,
                            "lease_token": authority.lease_token,
                            "uuid_re": _UUID_TEXT_RE,
                        },
                    )
                    row = result.mappings().one_or_none()
                    if row is None:
                        raise PreDispatchRejectedError(
                            "task lease is stale, cancelled, expired, or past its deadline"
                        )

                    self._validate_task_operation_binding(
                        authority=authority,
                        row=row,
                        rpc=rpc,
                        operation=operation,
                        ad_account_id=ad_account_id,
                        graph_semantics=graph_semantics,
                    )
                    deadline_value = row.get("deadline_epoch")
                    if deadline_value is None:
                        raise PreDispatchRejectedError("task absolute deadline is unavailable")
                    now_seconds = max(int(time.time()), int(row["db_now_epoch"]))
                    expires_at = min(
                        now_seconds + ttl_seconds,
                        int(row["lease_expires_epoch"]),
                        int(deadline_value),
                    )
                    if remaining_seconds is not None:
                        expires_at = min(
                            expires_at,
                            int(time.time() + max(0.0, remaining_seconds)),
                        )
                    if expires_at <= now_seconds:
                        raise PreDispatchRejectedError(
                            "live task lease/deadline expired before browser capability signing"
                        )
                    payload = browser_operation_payload(
                        browser_contract_version=BROWSER_CONTRACT_VERSION,
                        rpc=rpc,
                        operation=operation,
                        session_id=session_id,
                        vision_profile_id=vision_profile_id,
                        ad_account_id=ad_account_id,
                        caller=authority.caller,
                        task_id=authority.task_id,
                        lease_owner=authority.lease_owner,
                        lease_token=authority.lease_token,
                        expires_at_epoch=expires_at,
                        nonce=nonce,
                    )
                    await conn.execute(
                        _INSERT_PENDING_CAPABILITY_SQL,
                        {
                            "nonce_sha256": hashlib.sha256(nonce.encode("ascii")).digest(),
                            "capability_digest": hashlib.sha256(payload.encode("utf-8")).digest(),
                            "operation_digest": hashlib.sha256(operation.encode("utf-8")).digest(),
                            "browser_contract_version": BROWSER_CONTRACT_VERSION,
                            "caller": authority.caller,
                            "rpc": rpc,
                            "task_id": authority.task_id,
                            "lease_owner": authority.lease_owner,
                            "lease_token": authority.lease_token,
                            "session_id": session_id,
                            "vision_profile_id": vision_profile_id,
                            "ad_account_id": ad_account_id,
                            "expires_at_epoch": expires_at,
                        },
                    )
        except Exception as exc:  # noqa: BLE001 — fail closed before browser send
            if isinstance(exc, (PermanentError, SessionUnavailableError)):
                raise
            raise PreDispatchRejectedError(
                "task lease authority issuance failed before browser send"
            ) from exc
        return expires_at, payload

    @staticmethod
    def _validate_task_operation_binding(
        *,
        authority: BrowserOperationAuthority,
        row: Mapping[str, Any],
        rpc: str,
        operation: str,
        ad_account_id: str,
        graph_semantics: tuple[str, str, dict[str, str], str] | None,
    ) -> None:
        expected_task_type, allowed_lanes = _CALLER_TASK_BINDINGS[authority.caller]
        task_type = str(row.get("task_type") or "")
        lane = str(row.get("lane") or "")
        if task_type != expected_task_type or lane not in allowed_lanes:
            raise PermanentError("browser operation caller/task/lane binding is not authorized")

        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            raise PermanentError("browser operation task payload is invalid")
        allowed_mutations = _CALLER_MUTATION_KINDS.get(authority.caller)
        mutation_kind = str(payload.get("mutation_kind") or "")
        if allowed_mutations is not None and mutation_kind not in allowed_mutations:
            raise PermanentError("browser operation caller/mutation binding is not authorized")
        requested_by = str(row.get("requested_by") or "")
        if authority.caller == "autopause" and requested_by != "bot_auto_stop":
            raise PermanentError("browser operation caller/requester binding is not authorized")
        if authority.caller == "meta_api" and requested_by == "bot_auto_stop":
            raise PermanentError("browser operation caller/requester binding is not authorized")

        try:
            bound_account = require_ad_account_id(str(row.get("bound_ad_account_id") or ""))
        except ValueError as exc:
            raise PermanentError("browser operation task has no valid cabinet binding") from exc
        if bound_account != ad_account_id:
            raise PermanentError("browser operation task/cabinet binding is not authorized")

        if authority.caller != "campaign_creator" and rpc != "execute_graph_call":
            raise PermanentError("browser operation caller/RPC binding is not authorized")
        if rpc in {"upload_image", "upload_video"}:
            if re.fullmatch(rf"{rpc}\|r=[0-9a-f]{{64}}", operation) is None:
                raise PermanentError("browser upload operation binding is invalid")
            return
        if rpc != "execute_graph_call":
            raise PermanentError("browser operation RPC binding is invalid")

        graph_binding = _GRAPH_OPERATION_RE.fullmatch(operation)
        if graph_binding is None:
            raise PermanentError("browser Graph operation binding is invalid")
        method, endpoint = graph_binding.group(1), graph_binding.group(2)
        MetaApiClient._validate_graph_operation_for_task(
            authority=authority,
            payload=payload,
            task_result=(row.get("result") if isinstance(row.get("result"), Mapping) else {}),
            mutation_kind=mutation_kind,
            method=method,
            endpoint=endpoint,
            operation=operation,
            ad_account_id=ad_account_id,
            graph_semantics=graph_semantics,
        )

    @staticmethod
    def _validate_graph_operation_for_task(
        *,
        authority: BrowserOperationAuthority,
        payload: Mapping[str, Any],
        task_result: Mapping[str, Any],
        mutation_kind: str,
        method: str,
        endpoint: str,
        operation: str,
        ad_account_id: str,
        graph_semantics: tuple[str, str, dict[str, str], str] | None,
    ) -> None:
        normalized_endpoint = endpoint.rstrip("/") or "/"
        if (
            endpoint != normalized_endpoint
            or _SAFE_MONEY_GRAPH_ENDPOINT_RE.fullmatch(endpoint) is None
        ):
            raise PermanentError("browser Graph operation target is invalid")
        if mutation_kind in {"pause_ad", "activate_ad", "bulk_status_change"}:
            if authority.caller not in {"autopause", "meta_api"}:
                raise PermanentError("browser status mutation caller is not authorized")
            if mutation_kind in {"pause_ad", "activate_ad"}:
                target = str(payload.get("target_id") or "")
                desired_status = "PAUSED" if mutation_kind == "pause_ad" else "ACTIVE"
                allowed_operations = {
                    graph_operation_binding(
                        method="POST",
                        endpoint=f"/{target}",
                        query_params={"status": desired_status},
                        body_json="",
                    ),
                    graph_operation_binding(
                        method="GET",
                        endpoint=f"/{target}",
                        query_params={"fields": "effective_status,status"},
                        body_json="",
                    ),
                }
                if (
                    not target.isdigit()
                    or normalized_endpoint != f"/{target}"
                    or operation not in allowed_operations
                ):
                    raise PermanentError("browser operation does not match the claimed ad mutation")
                return

            params = payload.get("params")
            if not isinstance(params, Mapping):
                raise PermanentError("browser bulk operation payload is invalid")
            raw_ids = params.get("ad_ids")
            if not isinstance(raw_ids, list):
                raise PermanentError("browser bulk operation targets are invalid")
            ordered_ids: list[str] = []
            for value in raw_ids:
                target = str(value).strip()
                if not target.isdigit():
                    raise PermanentError("browser bulk operation target is invalid")
                if target not in ordered_ids:
                    ordered_ids.append(target)
            if not ordered_ids or len(ordered_ids) > 50:
                raise PermanentError("browser bulk operation targets are invalid")
            desired_status = {
                "pause": "PAUSED",
                "paused": "PAUSED",
                "activate": "ACTIVE",
                "active": "ACTIVE",
            }.get(str(params.get("action") or "").strip().lower())
            if desired_status is None:
                raise PermanentError("browser bulk operation action is invalid")
            mutation_batch = json.dumps(
                [
                    {
                        "method": "POST",
                        "relative_url": f"{target}?status={desired_status}",
                    }
                    for target in ordered_ids
                ]
            )
            reconciliation_batch = json.dumps(
                [
                    {
                        "method": "GET",
                        "relative_url": (f"{target}?fields=status,effective_status"),
                    }
                    for target in sorted(set(ordered_ids))
                ]
            )
            allowed_operations = {
                graph_operation_binding(
                    method="POST",
                    endpoint="/",
                    query_params={"batch": mutation_batch},
                    body_json="",
                ),
                graph_operation_binding(
                    method="POST",
                    endpoint="/",
                    query_params={"batch": reconciliation_batch},
                    body_json="",
                ),
            }
            if (
                method != "POST"
                or normalized_endpoint != "/"
                or operation not in allowed_operations
            ):
                raise PermanentError("browser operation does not match the claimed bulk mutation")
            return

        if authority.caller == "meta_api":
            _validate_duplicate_graph_operation(
                authority=authority,
                payload=payload,
                task_result=task_result,
                method=method,
                endpoint=endpoint,
                operation=operation,
                ad_account_id=ad_account_id,
                graph_semantics=graph_semantics,
            )
            return
        if authority.caller != "campaign_creator":
            raise PermanentError("browser Graph caller is not authorized")

        if graph_semantics is None:
            raise PermanentError("campaign Graph capability requires canonical request semantics")
        (
            semantic_method,
            semantic_endpoint,
            semantic_query,
            semantic_body,
        ) = graph_semantics
        if (
            method != semantic_method
            or endpoint != semantic_endpoint
            or operation
            != graph_operation_binding(
                method=semantic_method,
                endpoint=semantic_endpoint,
                query_params=semantic_query,
                body_json=semantic_body,
            )
        ):
            raise PermanentError("campaign Graph capability binding is inconsistent")

        account_root = f"/{graph_ad_account_id(ad_account_id)}"
        if method == "POST":
            edge = normalized_endpoint.removeprefix(f"{account_root}/")
            if (
                normalized_endpoint != f"{account_root}/{edge}"
                or edge not in _CAMPAIGN_CREATE_EDGES
                or semantic_query
            ):
                raise PermanentError("campaign Graph create action is not authorized")
            body = _load_canonical_graph_body(semantic_body) if semantic_body else None
            if not isinstance(body, Mapping) or not body:
                raise PermanentError("campaign Graph create body is not authorized")
            _validate_campaign_create_body(
                authority=authority,
                edge=edge,
                value=body,
            )
            return

        target_match = re.fullmatch(r"/([0-9]+)(/thumbnails)?", normalized_endpoint)
        if method != "GET" or target_match is None or semantic_body:
            raise PermanentError("campaign Graph read action is not authorized")
        target_id, thumbnails_edge = target_match.groups()
        if target_id not in authority.uploaded_video_ids:
            raise PermanentError("campaign Graph video target has no task-local upload provenance")
        expected_query = {"fields": "uri,is_preferred"} if thumbnails_edge else {"fields": "status"}
        if semantic_query != expected_query:
            raise PermanentError("campaign Graph video read action is not authorized")

    async def start(self) -> None:
        """Открыть свой gRPC канал (если не передан извне)."""
        if self._channel is None:
            self._channel = grpc.aio.insecure_channel(
                f"{self._host}:{self._port}",
                options=[
                    ("grpc.max_receive_message_length", 50 * 1024 * 1024),
                    ("grpc.max_send_message_length", 50 * 1024 * 1024),
                ],
            )
            logger.info("MetaApiClient gRPC канал открыт: %s:%d", self._host, self._port)
        self._stub = meta_api_pb2_grpc.MetaApiServiceStub(self._channel)

    async def close(self) -> None:
        """Закрыть канал — только если он наш собственный."""
        if self._channel and not self._external_channel:
            await self._channel.close()
            logger.info("MetaApiClient gRPC канал закрыт")
        self._channel = None
        self._stub = None

    async def __aenter__(self) -> MetaApiClient:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # ====================== Health ======================

    async def check_health(
        self,
        *,
        full_probe: bool = False,
        expected_profile_id: str | None = None,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]:
        """CheckMetaApiHealth — статус канала Marketing API для health_watchdog.

        Token-only режим (full_probe=False, дефолт) — дёшево: только URL + наличие
        EAA-токена в DOM, без сетевых запросов. Для частых проверок.

        full_probe=True — browser-agent дополнительно делает РЕАЛЬНЫЙ GET /me?fields=id
        тем же page.evaluate(fetch), что и auto-stop pause_ad. Ловит инцидент 2026-06-19:
        token-only возвращал healthy=true при мёртвом сетевом канале (Failed to fetch).

        Возвращает dict: healthy, current_url, token_present, token_length, detail +
        probe_performed, probe_ok, probe_status_code, probe_duration_ms, probe_detail.
        Не бросает на unhealthy — это просто статус.
        """
        if self._stub is None:
            raise RuntimeError("MetaApiClient не запущен: вызови await start()")

        req = meta_api_pb2.CheckMetaApiHealthRequest(
            session_id=self.session_id,
            full_probe=full_probe,
            expected_vision_profile_id=(expected_profile_id or "").strip(),
            # Пусто = переиспользовать живую вкладку Ads Manager и не открывать новую.
            ad_account_id=(ad_account_id or "").strip(),
        )
        timeout = _HEALTH_PROBE_TIMEOUT_SECONDS if full_probe else _HEALTH_CHECK_TIMEOUT_SECONDS
        try:
            resp = await self._circuit_breaker.call(
                self._stub.CheckMetaApiHealth,
                req,
                timeout=timeout,
            )
        except CircuitOpenError as exc:
            return {
                "healthy": False,
                "current_url": "",
                "token_present": False,
                "token_length": 0,
                "detail": f"circuit_open: {exc}",
                "probe_performed": False,
                "probe_ok": False,
                "probe_status_code": 0,
                "probe_duration_ms": 0,
                "probe_detail": "not_performed",
                "browser_contract_version": 0,
                "session_id": "",
                "vision_profile_id": "",
            }
        return {
            "healthy": bool(resp.healthy),
            "current_url": str(resp.current_url),
            "token_present": bool(resp.token_present),
            "token_length": int(resp.token_length),
            "detail": str(resp.detail),
            "probe_performed": bool(resp.probe_performed),
            "probe_ok": bool(resp.probe_ok),
            "probe_status_code": int(resp.probe_status_code),
            "probe_duration_ms": int(resp.probe_duration_ms),
            "probe_detail": str(resp.probe_detail),
            "browser_contract_version": int(resp.browser_contract_version),
            "session_id": str(resp.session_id),
            "vision_profile_id": str(resp.vision_profile_id),
        }

    # ====================== Core: ExecuteGraphCallV5 ======================

    async def execute_graph_call(
        self,
        *,
        method: str,
        endpoint: str,
        query_params: dict[str, str] | None = None,
        body_json: str | dict[str, Any] | None = None,
        timeout_ms: int | None = None,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Универсальный Graph API call через активную Vision-сессию.

        Возвращает распарсенный JSON-ответ Meta API (dict).
        Бросает доменное исключение из core.meta_api.errors при ошибке Meta.
        Бросает SessionUnavailableError при недоступности Vision.

        Args:
            method: "GET"/"POST"/"DELETE" (case-insensitive)
            endpoint: путь БЕЗ /vXX.Y, например "/me" или "/act_123/insights"
            query_params: query string / form params
            body_json: тело POST (dict сериализуется в JSON-строку)
            timeout_ms: таймаут одного вызова на стороне browser-agent
            ad_account_id: явный кабинет исполнения. Для всех Graph writes и
                status-reconciliation обязателен; отсутствие отклоняется до gRPC.
        """
        # Отметка стоит ПЕРВОЙ строкой, до любой проверки: она отвечает не на
        # вопрос «всё ли в порядке», а на вопрос «кто вёл этот вызов». Только
        # пройдя отсюда до транспорта и не дойдя, можно утверждать, что запрос
        # не уходил; отказ любой из проверок ниже — как раз такой случай.
        mark_graph_call_observed()
        if self._stub is None:
            raise RuntimeError("MetaApiClient не запущен: вызови await start()")

        normalized_method = method.strip().upper()
        params_map = {str(k): str(v) for k, v in (query_params or {}).items()}
        body_str = ""
        if body_json is not None:
            body_str = body_json if isinstance(body_json, str) else json.dumps(body_json)
        validate_graph_request_semantics(
            method=normalized_method,
            endpoint=endpoint,
            query_params=params_map,
            body_json=body_str,
        )

        requested_account_id = (
            require_ad_account_id(ad_account_id) if ad_account_id is not None else None
        )
        money_call = _is_money_graph_call(
            method=normalized_method,
            endpoint=endpoint,
            query_params=params_map,
        )
        authority = _OPERATION_AUTHORITY.get()
        controlled_call = money_call or (
            authority is not None and authority.caller in {"campaign_creator", "meta_api"}
        )
        if controlled_call and requested_account_id is None:
            raise ValueError("money Graph call requires explicit ad_account_id")
        resolved_account_id = requested_account_id or _account_from_endpoint(endpoint)

        remaining = remaining_deadline_seconds()
        if remaining is not None and remaining <= 0.001:
            # Отправки не было: дедлайн проверен ДО единственного вызова, который
            # мог бы уйти к Meta. Это REJECTED, а не потерянный ответ.
            raise PreDispatchRejectedError(
                "absolute deadline exhausted before Graph call",
                endpoint=endpoint,
            )

        requested_timeout_ms = int(timeout_ms or 30_000)
        browser_timeout_ms = requested_timeout_ms
        grpc_timeout_seconds = _DEFAULT_TIMEOUT_SECONDS
        if remaining is not None:
            # Leave a small margin so AbortController reports its structured
            # timeout before the enclosing gRPC deadline cancels the transport.
            browser_timeout_ms = max(
                1,
                min(requested_timeout_ms, int(remaining * 1000) - 250),
            )
            grpc_timeout_seconds = max(0.001, remaining)
        elif timeout_ms is not None:
            grpc_timeout_seconds = max(0.001, requested_timeout_ms / 1000 + 5.0)

        req_kwargs: dict[str, Any] = {
            "session_id": self.session_id,
            "method": normalized_method,
            "endpoint": endpoint,
            "query_params": params_map,
            "body_json": body_str,
            "ad_account_id": resolved_account_id or "",
        }
        if controlled_call:
            req_kwargs.update(
                await self.prepare_operation_authorization(
                    rpc="execute_graph_call",
                    operation=graph_operation_binding(
                        method=normalized_method,
                        endpoint=endpoint,
                        query_params=params_map,
                        body_json=body_str,
                    ),
                    ad_account_id=resolved_account_id or "",
                    graph_method=normalized_method,
                    graph_endpoint=endpoint,
                    graph_query_params=params_map,
                    graph_body_json=body_str,
                )
            )
        req_kwargs["timeout_ms"] = browser_timeout_ms

        req = meta_api_pb2.ExecuteGraphCallRequest(**req_kwargs)

        stub_call = self._stub.ExecuteGraphCallV5

        # functools.wraps: обёртка остаётся узнаваемой как ExecuteGraphCallV5 —
        # и в трассировке, и для теста маршрутизации вызова.
        @functools.wraps(stub_call)
        async def _dispatch_graph_call(request: Any, **call_kwargs: Any) -> Any:
            # Единственная точка, где запрос действительно уходит наружу. Отметка
            # стоит ЗДЕСЬ, а не перед circuit_breaker.call: предохранитель
            # отказывает раньше транспорта, и его отказ — отказ до отправки.
            # Всё, что отвергло вызов выше (нет явного кабинета, отказ выдачи
            # одноразового гранта, живая проба канала), тоже отказало до отправки.
            # Дедлайн едет транспорту как есть (timeout=grpc_timeout_seconds):
            # обёртка ничего не подменяет и своего таймаута не заводит.
            mark_graph_dispatched()
            return await stub_call(request, **call_kwargs)

        try:
            resp = await self._circuit_breaker.call(
                _dispatch_graph_call,
                req,
                timeout=grpc_timeout_seconds,
            )
        except CircuitOpenError as exc:
            if controlled_call and authority is not None:
                await self._invalidate_claimed_browser_readiness(
                    authority,
                    reason_code="presend_circuit_open",
                )
                raise BrowserReadinessRejectedError(
                    f"browser-agent rejected the controlled request before dispatch: {exc}",
                    endpoint=endpoint,
                ) from exc
            raise SessionUnavailableError(
                f"browser-agent недоступен: {exc}",
                endpoint=endpoint,
            ) from exc
        except grpc.RpcError as exc:  # type: ignore[misc]
            readiness_error = (
                await self._controlled_presend_readiness_error(
                    exc,
                    endpoint=endpoint,
                )
                if controlled_call
                else None
            )
            if readiness_error is not None:
                raise readiness_error from exc
            # Capability/lease authorization failures retain their permanent or
            # ambiguous semantics and must not be relabelled as channel readiness.
            raise self._grpc_to_meta_error(exc, endpoint=endpoint) from exc

        # browser-agent заполняет error из ответа Meta, если он там есть.
        if resp.HasField("error"):
            err = resp.error
            raise classify_graph_error(
                code=err.code or None,
                subcode=err.subcode or None,
                message=err.message or "",
                endpoint=endpoint,
                fbtrace_id=err.fbtrace_id or None,
            )

        # An HTTP error without Meta's structured error block does not prove
        # whether a mutation was applied.  Keep it ambiguous so post-boundary
        # callers reconcile or stop UNKNOWN instead of treating a malformed
        # proxy/Graph response as a safe rejection.
        if resp.status_code >= 400:
            raise AmbiguousResultError(
                f"HTTP {resp.status_code} without a structured Graph error",
                code=resp.status_code,
                endpoint=endpoint,
            )

        try:
            result = json.loads(resp.response_json) if resp.response_json else {}
        except json.JSONDecodeError as exc:
            raise AmbiguousResultError(
                f"Невалидный JSON в ответе Meta: {exc}",
                endpoint=endpoint,
            ) from exc
        if authority is not None and authority.caller == "meta_api":
            completed_operation = graph_operation_binding(
                method=normalized_method,
                endpoint=endpoint,
                query_params=params_map,
                body_json=body_str,
            )
            if completed_operation in authority.duplicate_pending_roles and not isinstance(
                result, Mapping
            ):
                raise AmbiguousResultError(
                    "duplicate Graph operation returned a non-object response",
                    endpoint=endpoint,
                )
            if isinstance(result, Mapping):
                _record_duplicate_operation_result(
                    authority=authority,
                    operation=completed_operation,
                    result=result,
                    ad_account_id=resolved_account_id or "",
                )
        if (
            authority is not None
            and authority.caller == "campaign_creator"
            and normalized_method == "POST"
        ):
            object_id = result.get("id") if isinstance(result, Mapping) else None
            self._remember_campaign_created_object_id(
                endpoint=endpoint,
                object_id=str(object_id or ""),
                ad_account_id=resolved_account_id or "",
            )
        return result

    # ====================== Высокоуровневые шорткаты ======================

    async def get_ad_insights(
        self,
        *,
        ad_account_id: str,
        fields: list[str] | tuple[str, ...],
        date_preset: str | None = None,
        since: str | None = None,
        until: str | None = None,
        level: str = "ad",
        filtering: list[dict[str, Any]] | None = None,
        breakdowns: list[str] | None = None,
        limit: int = 25,
        action_attribution_windows: list[str] | tuple[str, ...] = (
            "1d_click",
            "7d_click",
            "1d_view",
        ),
    ) -> dict[str, Any]:
        """GET /act_{ad_account_id}/insights — обёртка над execute_graph_call.

        Возвращает распарсенный ответ Meta как dict (с ключом 'data' — массив строк).
        На входе допускается numeric ID или ``act_``-form; в gRPC всегда
        передаётся канонический numeric ID, а Graph endpoint строится как
        ``/act_<id>/insights``.
        """
        numeric_account_id = require_ad_account_id(ad_account_id)
        params: dict[str, str] = {
            "level": level,
            "fields": ",".join(fields),
            "limit": str(limit),
            "action_attribution_windows": json.dumps(list(action_attribution_windows)),
        }
        if date_preset:
            params["date_preset"] = date_preset
        if since and until:
            params["time_range"] = json.dumps({"since": since, "until": until})
        if filtering:
            params["filtering"] = json.dumps(filtering)
        if breakdowns:
            params["breakdowns"] = ",".join(breakdowns)

        return await self.execute_graph_call(
            ad_account_id=numeric_account_id,
            method="GET",
            endpoint=f"/{graph_ad_account_id(numeric_account_id)}/insights",
            query_params=params,
        )

    async def list_ad_accounts(
        self,
        *,
        fields: list[str] | tuple[str, ...] = ("id", "name", "account_status", "currency"),
        limit: int = 50,
    ) -> dict[str, Any]:
        """GET /me/adaccounts — список ad accounts текущего пользователя."""
        return await self.execute_graph_call(
            method="GET",
            endpoint="/me/adaccounts",
            query_params={
                "fields": ",".join(fields),
                "limit": str(limit),
            },
        )

    # ====================== внутреннее ======================

    @staticmethod
    def _grpc_to_meta_error(exc: grpc.RpcError, *, endpoint: str) -> MetaApiError:
        """Преобразовать gRPC error из browser-agent в доменное исключение.

        browser-agent возвращает:
        - FAILED_PRECONDITION → token_not_found / session not active
        - UNAVAILABLE → browser-agent упал
        - DEADLINE_EXCEEDED → таймаут

        `details()` — сырой текст от browser-agent (может содержать токен или URL
        с секретом из Graph-ответа); наружу уходит только машинный код gRPC status
        и код причины из трейлера.
        """
        rejection = browser_operation_rejection_error(exc, endpoint=endpoint)
        if rejection is not None:
            return rejection
        code = exc.code() if hasattr(exc, "code") else None  # type: ignore[union-attr]
        code_name = code.name if code is not None and hasattr(code, "name") else "UNKNOWN"

        if code == grpc.StatusCode.FAILED_PRECONDITION:
            return SessionUnavailableError(
                f"Vision-сессия не готова (gRPC {code_name})",
                endpoint=endpoint,
            )
        if code == grpc.StatusCode.UNIMPLEMENTED:
            return SessionUnavailableError(
                f"browser semantic contract is incompatible (gRPC {code_name})",
                endpoint=endpoint,
            )
        if code in (grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.PERMISSION_DENIED):
            return PermanentError(
                f"browser operation authorization rejected (gRPC {code_name})",
                endpoint=endpoint,
            )
        # Once ExecuteGraphCallV5 has been dispatched, transport termination is
        # not evidence that Meta did not commit the request.  Circuit-open is
        # handled before dispatch; FAILED_PRECONDITION and UNIMPLEMENTED are
        # explicit pre-send contracts. Every other gRPC failure remains ambiguous.
        return AmbiguousResultError(
            f"gRPC response lost after dispatch ({code_name})",
            endpoint=endpoint,
        )
