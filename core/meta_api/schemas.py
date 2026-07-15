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

# Допустимые типы mutations — должны совпадать с handlers в meta_api_worker.
MUTATION_KINDS: frozenset[str] = frozenset(
    {
        "pause_ad",
        "activate_ad",
        "pause_campaign",
        "activate_campaign",
        "set_adset_budget",
        "duplicate_campaign",
        "duplicate_adset_structure",
        "bulk_status_change",
        "create_campaign",
        "custom_audience",
        "set_ad_creative",
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
        "create_campaign",
        "duplicate_campaign",
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
    filtering: tuple[dict[str, str], ...] = ()
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


@dataclass(slots=True, frozen=True)
class MetaMutationPayload:
    """Payload для task_queue.payload (task_type='meta_api_mutation').

    Сериализуется в JSONB колонку payload. Возвращается as-is через from_dict
    после json.loads на стороне worker'а.
    """

    mutation_kind: str  # один из MUTATION_KINDS
    target_id: str  # ad_id | adset_id | campaign_id (что меняем)
    params: dict[str, Any] = field(default_factory=dict)
    ad_account_id: str | None = None  # для аудита и rate-limit монитора

    def __post_init__(self) -> None:
        if self.mutation_kind not in MUTATION_KINDS:
            raise ValueError(
                f"Неизвестный mutation_kind: {self.mutation_kind!r}. "
                f"Допустимо: {sorted(MUTATION_KINDS)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mutation_kind": self.mutation_kind,
            "target_id": self.target_id,
            "params": dict(self.params),
            "ad_account_id": self.ad_account_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetaMutationPayload:
        return cls(
            mutation_kind=str(data["mutation_kind"]),
            target_id=str(data["target_id"]),
            params=dict(data.get("params") or {}),
            ad_account_id=data.get("ad_account_id"),
        )
