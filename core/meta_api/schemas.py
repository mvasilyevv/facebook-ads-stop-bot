# -*- coding: utf-8 -*-
"""Frozen dataclasses для Marketing API: запросы insights, наблюдения, payload mutations.

Контракты:
- MetaInsightsRequest — параметры запроса GET /act_X/insights.
- MetaInsightsRow — одна строка ответа /insights (нормализованная).
- MetaApiAdRow — снимок объявления из Marketing API (параллель ScannedAdRow).
- MetaMutationPayload — payload для task_queue.payload (task_type='meta_api_mutation').
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from core.meta_api.identity import require_ad_account_id

# Допустимые типы mutations — должны совпадать с handlers в meta_api_worker.
MUTATION_KINDS: frozenset[str] = frozenset(
    {
        "pause_ad",
        "activate_ad",
        "duplicate_adset_structure",
        "bulk_status_change",
    }
)

# Необратимые mutations: создают НОВЫЕ объекты в Meta (кампания/копия/структура). Если ответ
# потерян ПОСЛЕ коммита на стороне Meta, повторный вызов = ДУБЛЬ кампании + двойной
# открут бюджета. idempotency_key (на enqueue) от retry той же строки НЕ защищает.
# Эти kinds нельзя ретраить: и в meta_api_worker (transient/неожиданная ошибка →
# mark_failed), и в reconciler-крэш-пути (зависшая 'running' → failed, НЕ retrying).
# Единый источник правды — здесь; импортируется воркером и reconciler'ом.
IRREVERSIBLE_MUTATION_KINDS: frozenset[str] = frozenset(
    {
        "duplicate_adset_structure",
    }
)


@dataclass(slots=True, frozen=True)
class MetaInsightsRequest:
    """Параметры одного запроса /act_X/insights.

    Attribution windows — обязательно явно (см. § 3.11 плана).
    Deprecated 7d_view / 28d_view не подставляем.
    """

    ad_account_id: str  # "act_123..."
    level: str = "ad"  # ad | adset | campaign | account
    date_preset: str | None = None  # today | yesterday | last_7d | ...
    since: date | None = None
    until: date | None = None
    fields: tuple[str, ...] = (
        "ad_id",
        "campaign_id",
        "adset_id",
        "spend",
        "impressions",
        "clicks",
        "ctr",
        "cpc",
        "cpm",
        "reach",
        "frequency",
        "actions",
    )
    filtering: tuple[dict[str, Any], ...] = ()
    breakdowns: tuple[str, ...] = ()
    limit: int = 25
    action_attribution_windows: tuple[str, ...] = (
        "1d_click",
        "7d_click",
        "1d_view",
    )


@dataclass(slots=True, frozen=True)
class MetaInsightsRow:
    """Одна строка insights — каноническая структура из Marketing API."""

    ad_id: str
    campaign_id: str | None
    adset_id: str | None
    ad_account_id: str
    spend: Decimal
    impressions: int
    clicks: int
    reach: int
    cpc: Decimal | None
    ctr: Decimal | None
    cpm: Decimal | None
    frequency: Decimal | None
    actions: dict[str, int] = field(default_factory=dict)
    date_start: date | None = None
    date_stop: date | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class MetaApiAdRow:
    """Снимок объявления из Marketing API.

    Параллельный контракт ScannedAdRow, но из API. Преобразуется в ScannedAdRow
    через adapters.py для использования в pipeline и rule_evaluator.
    """

    fb_ad_id: str
    fb_campaign_id: str | None
    fb_adset_id: str | None
    ad_account_id: str
    name: str
    campaign_name: str
    adset_name: str
    effective_status: str  # ACTIVE | PAUSED | DISAPPROVED | ...
    configured_status: str
    spend: Decimal
    impressions: int
    clicks: int
    cpc: Decimal | None
    ctr: Decimal | None
    cpm: Decimal | None
    reach: int
    frequency: Decimal | None
    actions: dict[str, int] = field(default_factory=dict)
    observed_at: datetime | None = None
    moderation_reason: str | None = None


@dataclass(slots=True, frozen=True)
class MetaMutationPayload:
    """Payload для task_queue.payload (task_type='meta_api_mutation').

    Сериализуется в JSONB колонку payload. Возвращается as-is через from_dict
    после json.loads на стороне worker'а.
    """

    mutation_kind: str  # один из MUTATION_KINDS
    target_id: str  # ad_id | adset_id | campaign_id (что меняем)
    ad_account_id: str  # explicit numeric account id; never inferred from a browser tab
    params: dict[str, Any] = field(default_factory=dict)
    currency: str | None = None
    cabinet_timezone: str | None = None
    account_context_observed_at: str | None = None
    account_context_issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mutation_kind not in MUTATION_KINDS:
            raise ValueError(
                f"Неизвестный mutation_kind: {self.mutation_kind!r}. "
                f"Допустимо: {sorted(MUTATION_KINDS)}"
            )
        object.__setattr__(
            self,
            "ad_account_id",
            require_ad_account_id(self.ad_account_id),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mutation_kind": self.mutation_kind,
            "target_id": self.target_id,
            "params": dict(self.params),
            "ad_account_id": self.ad_account_id,
            "account_id": self.ad_account_id,
            "currency": self.currency,
            "cabinet_timezone": self.cabinet_timezone,
            "account_context_observed_at": self.account_context_observed_at,
            "account_context_issues": list(self.account_context_issues),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetaMutationPayload:
        account_id = data.get("ad_account_id") or data.get("account_id")
        if account_id is None:
            raise KeyError("ad_account_id")
        return cls(
            mutation_kind=str(data["mutation_kind"]),
            target_id=str(data["target_id"]),
            ad_account_id=account_id,
            params=dict(data.get("params") or {}),
            currency=str(data["currency"]) if data.get("currency") else None,
            cabinet_timezone=(
                str(data["cabinet_timezone"]) if data.get("cabinet_timezone") else None
            ),
            account_context_observed_at=(
                str(data["account_context_observed_at"])
                if data.get("account_context_observed_at")
                else None
            ),
            account_context_issues=tuple(
                str(issue) for issue in (data.get("account_context_issues") or [])
            ),
        )
