# -*- coding: utf-8 -*-
"""Frozen dataclasses для AdSet.pro MCP-клиента + inbound postback.

Контракты:
- PostbackEvent     — один inbound postback от AdSet.pro (приходит на наш
  FastAPI endpoint, см. apps/api/routers/postback.py).
- StatsQueryRequest — высокоуровневая обёртка над MCP tool `query_stats`.
  Внутри client.py since/until → from/to, ad_id → filter ext_sub8=eq.
- StatsQueryResponse — рассыпает MCP `structuredContent.data[]` в ConversionRow.
- ConversionRow     — нормализованная строка конверсии (ext_sub8 = fb_ad_id).

Все dataclass frozen — DTO, не мутируются.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from core.money import validated_currency_code

# В AdSet.pro поле ext_sub8 хранит fb_ad_id — это контракт новой наливки в трекер.
# Меняется только при общей перенастройке трекера.
EXT_SUB_FIELD_FOR_AD_ID: str = "ext_sub8"


@dataclass(slots=True, frozen=True)
class StatsQueryRequest:
    """Параметры запроса статистики (внутри клиента → MCP tool `query_stats`).

    Поля since/until — обязательные границы выборки. ad_id — опциональный фильтр
    по конкретному объявлению (мэтчится по ext_sub8 в AdSet.pro). Конвертация
    в MCP arguments делается в AdsetProClient._stats_args_from_request.
    """

    since: date
    until: date
    ad_id: str | None = None
    group_by: tuple[str, ...] = ()
    extra_filters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.since > self.until:
            raise ValueError(f"StatsQueryRequest.since ({self.since}) > until ({self.until})")


@dataclass(slots=True, frozen=True)
class ConversionRow:
    """Одна строка конверсии из AdSet.pro.

    Поле fb_ad_id извлекается из ext_sub8 (контракт новой наливки).
    revenue хранится как Decimal, а отсутствие/невалидность — как None, чтобы
    неизвестное значение не превращалось в подтверждённый ноль.
    """

    click_id: str
    fb_ad_id: str | None
    event_type: str
    revenue: Decimal | None
    currency: str | None
    occurred_at: datetime | None
    raw: dict[str, Any] = field(default_factory=dict)
    issues: tuple[str, ...] = ()

    @classmethod
    def from_api_row(cls, row: dict[str, Any]) -> ConversionRow:
        """Распарсить «сырую» строку ответа AdSet.pro в ConversionRow.

        Маппинг полей — best-effort с учётом того, что схема ответа может меняться:
        - click_id берётся из click_id/clickid/id (что нашли первым).
        - fb_ad_id берётся из ext_sub8 (контракт).
        - revenue парсится из строки в Decimal; пусто/None остаётся unknown.
        - невалидный/non-finite revenue остаётся unknown с quality issue.
        - occurred_at пытаемся распарсить из ISO-формата; неудача → None.
        """
        click_id = str(
            row.get("click_id")
            or row.get("event_click_id")
            or row.get("clickid")
            or row.get("id")
            or ""
        )
        ad_id_raw = row.get(EXT_SUB_FIELD_FOR_AD_ID)
        fb_ad_id = str(ad_id_raw) if ad_id_raw not in (None, "") else None

        revenue_raw = row["revenue"] if "revenue" in row else row.get("event_revenue")
        issues: tuple[str, ...] = ()
        if revenue_raw is None or (isinstance(revenue_raw, str) and not revenue_raw.strip()):
            revenue = None
            issues = ("revenue_missing",)
        else:
            try:
                revenue = Decimal(str(revenue_raw))
            except (InvalidOperation, ValueError):
                revenue = None
                issues = ("revenue_invalid",)
            else:
                if not revenue.is_finite():
                    revenue = None
                    issues = ("revenue_invalid",)

        occurred_at_raw = (
            row.get("occurred_at")
            or row.get("event_time")
            or row.get("created_at")
            or row.get("time")
        )
        occurred_at: datetime | None = None
        if isinstance(occurred_at_raw, str) and occurred_at_raw:
            try:
                occurred_at = datetime.fromisoformat(occurred_at_raw.replace("Z", "+00:00"))
            except ValueError:
                occurred_at = None
        elif isinstance(occurred_at_raw, datetime):
            occurred_at = occurred_at_raw

        return cls(
            click_id=click_id,
            fb_ad_id=fb_ad_id,
            event_type=str(row.get("event_type") or row.get("status") or ""),
            revenue=revenue,
            currency=validated_currency_code(row.get("currency") or row.get("event_currency")),
            occurred_at=occurred_at,
            raw=dict(row),
            issues=issues,
        )


@dataclass(slots=True, frozen=True)
class StatsQueryResponse:
    """Ответ AdSet.pro на /api/stats/query — список ConversionRow + raw."""

    rows: tuple[ConversionRow, ...]
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api_payload(cls, payload: dict[str, Any]) -> StatsQueryResponse:
        """Распарсить ответ. Список строк ожидаем в data/rows/result (что нашли первым)."""
        rows_raw: list[Any] = (
            payload.get("data") or payload.get("rows") or payload.get("result") or []
        )
        if not isinstance(rows_raw, list):
            rows_raw = []
        rows = tuple(ConversionRow.from_api_row(r) for r in rows_raw if isinstance(r, dict))
        return cls(rows=rows, raw=dict(payload))


@dataclass(slots=True, frozen=True)
class PostbackEvent:
    """Validated positive event accepted by the AdSet.pro postback endpoint."""

    click_id: str
    fb_ad_id: str | None
    event_type: str
    revenue: Decimal | None
    currency: str | None
    received_at: datetime
    raw: dict[str, Any] = field(default_factory=dict)
    source: str = "adsetpro"
    provider_event_id: str | None = None
    occurred_at: datetime | None = None
