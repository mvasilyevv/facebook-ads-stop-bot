# -*- coding: utf-8 -*-
"""Tool get_tracker_stats — post-click статистика из AdSet.pro (MCP query_stats).

Независимый от Vision/кабинета канал: клики, регистрации, сырой provider FTD,
доход, профит, ROI за период. Provider FTD не равен локально подтверждённому
депозиту STOP-контура (там обязательна связка registration + FTD одного click_id).

Схема AdSet.pro (live verify 2026-05-30):
- метрики: clicks, registrations, ftds (= депозиты/FTD), revenue, cost, profit, roi, epc;
- валидные группы: event_type (воронка SOURCE_CLICK/CPA_HOLD/CPA_ACCEPT/...),
  ext_sub1..ext_sub8 (макросы трекера; ext_sub8 — стабильный Meta ad id в новых кампаниях);
- country отдельным разрезом трекер НЕ отдаёт (гео закодировано в offer-коде/кампании).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

from core.adset_pro.credentials import create_adsetpro_client
from core.adset_pro.errors import AdsetProError
from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError
from core.money import (
    UnsupportedCurrencyExponentError,
    currency_exponent,
    require_exact_currency_amount,
    validated_currency_code,
)

# Метрики, которые запрашиваем всегда (дефолт query_stats отдаёт только clicks).
_METRICS: tuple[str, ...] = (
    "clicks",
    "registrations",
    "ftds",
    "revenue",
    "cost",
    "profit",
    "roi",
    "epc",
)
# Разрешённые дименшены группировки (остальные AdSet.pro молча игнорит → пустой агрегат).
_ALLOWED_GROUPS: frozenset[str] = frozenset(
    {
        "event_type",
        "ext_sub1",
        "ext_sub2",
        "ext_sub3",
        "ext_sub4",
        "ext_sub5",
        "ext_sub6",
        "ext_sub7",
        "ext_sub8",
    }
)
_MAX_WINDOW_DAYS = 365


def _decimal(value: Any, *, non_negative: bool = False) -> Decimal | None:
    """Parse a finite decimal without inventing a confirmed zero."""

    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or (non_negative and parsed < 0):
        return None
    return parsed


def _count(value: Any) -> int | None:
    parsed = _decimal(value, non_negative=True)
    if parsed is None or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def _format_count(label: str, value: Any) -> str:
    parsed = _count(value)
    return f"{label}: {parsed}" if parsed is not None else f"{label}: —"


def _row_currency(row: dict[str, Any]) -> tuple[str, int] | None:
    code = validated_currency_code(row.get("currency") or row.get("event_currency"))
    if code is None:
        return None
    try:
        return code, currency_exponent(code)
    except UnsupportedCurrencyExponentError:
        return None


def _money(value: Any, *, currency: tuple[str, int] | None) -> str:
    """Render only exact money carrying a reviewed currency/exponent."""

    if currency is None:
        return "— (валюта не подтверждена)"
    code, exponent = currency
    try:
        amount = require_exact_currency_amount(
            value,
            currency=code,
            exponent=exponent,
            field="tracker money",
        )
    except (TypeError, ValueError):
        return f"— ({code}: некорректная сумма)"
    return f"{amount:.{exponent}f} {code}"


def _sum_counts(rows: list[dict[str, Any]], field: str) -> int | None:
    values = [_count(row.get(field)) for row in rows]
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _common_currency(rows: list[dict[str, Any]]) -> tuple[str, int] | None:
    currencies = [_row_currency(row) for row in rows]
    if not currencies or any(currency is None for currency in currencies):
        return None
    unique = set(currencies)
    return next(iter(unique)) if len(unique) == 1 else None


def _sum_money(
    rows: list[dict[str, Any]],
    field: str,
    *,
    currency: tuple[str, int] | None,
) -> str:
    if currency is None:
        return "— (mixed/unknown currency)"
    code, exponent = currency
    amounts: list[Decimal] = []
    for row in rows:
        try:
            amounts.append(
                require_exact_currency_amount(
                    row.get(field),
                    currency=code,
                    exponent=exponent,
                    field=f"tracker {field}",
                )
            )
        except (TypeError, ValueError):
            return f"— ({code}: неполные данные)"
    return f"{sum(amounts, start=Decimal(0)):.{exponent}f} {code}"


class GetTrackerStatsTool:
    """AdSet.pro post-click статистика: клики/реги/provider FTD/доход/профит/ROI."""

    name: ClassVar[str] = "get_tracker_stats"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "get_tracker_stats",
        "description": (
            "Post-click статистика из трекера AdSet.pro (НЕ зависит от Vision/кабинета): "
            "клики, регистрации, сырой provider FTD, доход, профит, ROI за период. "
            "Не называй FTD подтверждённым депозитом STOP-контура: там требуется "
            "локальная связка registration + FTD одного click_id. "
            "Опциональный разрез group_by: event_type (воронка) либо ext_sub1..ext_sub8 "
            "(макросы трекера; в новых кампаниях ext_sub8=Meta ad id "
            "креатива). Отдельного разреза по стране трекер не отдаёт — гео закодировано в "
            "offer-коде/кампании."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_WINDOW_DAYS,
                    "default": 7,
                    "description": "Окно в днях назад от сегодня (UTC). Игнорируется если заданы since+until.",
                },
                "since": {
                    "type": "string",
                    "description": "ISO-дата начала YYYY-MM-DD. Вместе с until переопределяет days.",
                },
                "until": {"type": "string", "description": "ISO-дата конца YYYY-MM-DD."},
                "group_by": {
                    "type": "string",
                    "enum": sorted(_ALLOWED_GROUPS),
                    "description": "Разрез. Пусто — суммарные тоталы за окно.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                    "description": "Сколько строк показать при group_by (сортировка по FTD).",
                },
            },
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        engine = ctx.require_engine()

        since, until = self._resolve_window(args)
        group_by = (str(args.get("group_by") or "")).strip() or None
        if group_by and group_by not in _ALLOWED_GROUPS:
            raise ToolError(f"group_by должен быть одним из {sorted(_ALLOWED_GROUPS)}")
        limit = max(1, min(int(args.get("limit") or 20), 100))

        mcp_args: dict[str, Any] = {
            "from": since.isoformat(),
            "to": until.isoformat(),
            "metrics": list(_METRICS),
            # Monetary aggregates are meaningless without their provider-owned
            # currency identity.  Force it into every returned bucket.
            "groups": ["event_currency"],
        }
        if group_by:
            mcp_args["groups"].insert(0, group_by)

        client = await create_adsetpro_client(engine)
        try:
            async with client:
                payload = await client.call_mcp_tool("query_stats", mcp_args)
        except AdsetProError as exc:
            raise ToolError(f"AdSet.pro недоступен: {exc}") from exc

        rows = payload.get("data")
        if not isinstance(rows, list) or not rows:
            return f"AdSet.pro: нет данных за {since}…{until}."

        return self._format(rows, since, until, group_by, limit)

    @staticmethod
    def _resolve_window(args: dict[str, Any]) -> tuple[date, date]:
        """since/until (ISO) при наличии обоих, иначе последние `days` дней (UTC)."""
        s = str(args.get("since") or "").strip()
        u = str(args.get("until") or "").strip()
        if s and u:
            try:
                since, until = date.fromisoformat(s), date.fromisoformat(u)
            except ValueError as exc:
                raise ToolError(f"since/until должны быть в формате YYYY-MM-DD: {exc}") from exc
            if since > until:
                raise ToolError(f"since ({since}) позже until ({until})")
            return since, until

        days = max(1, min(int(args.get("days") or 7), _MAX_WINDOW_DAYS))
        until = datetime.now(timezone.utc).date()
        return until - timedelta(days=days - 1), until

    def _format(
        self,
        rows: list[Any],
        since: date,
        until: date,
        group_by: str | None,
        limit: int,
    ) -> str:
        valid = [row for row in rows if isinstance(row, dict)]
        if not valid:
            return f"AdSet.pro: нет валидных данных за {since}…{until}."

        if not group_by:
            currency = _common_currency(valid)
            clicks = _sum_counts(valid, "clicks")
            regs = _sum_counts(valid, "registrations")
            ftds = _sum_counts(valid, "ftds")
            parts = [
                f"Клики: {clicks}" if clicks is not None else "Клики: —",
                f"Реги: {regs}" if regs is not None else "Реги: —",
                f"Provider FTD: {ftds}" if ftds is not None else "Provider FTD: —",
                f"Доход: {_sum_money(valid, 'revenue', currency=currency)}",
                f"Расход: {_sum_money(valid, 'cost', currency=currency)}",
                f"Профит: {_sum_money(valid, 'profit', currency=currency)}",
            ]
            return (
                f"AdSet.pro {since}…{until} · итого\n{' · '.join(parts)}\n"
                "Примечание: provider FTD ≠ локально подтверждённый депозит STOP-контура."
            )

        valid.sort(
            key=lambda row: (
                _count(row.get("ftds")) is not None,
                _count(row.get("ftds")) or 0,
                _decimal(row.get("revenue"), non_negative=True) is not None,
                _decimal(row.get("revenue"), non_negative=True) or Decimal(0),
            ),
            reverse=True,
        )

        lines = [f"AdSet.pro {since}…{until} · разрез: {group_by}"]
        for r in valid[:limit]:
            label = str(r.get(group_by) or "—").strip() or "—"
            currency = _row_currency(r)
            currency_label = f" [{currency[0]}]" if currency is not None else ""
            lines.append(f"• {label[:48]}{currency_label}: {self._metrics_line(r, compact=True)}")
        if len(valid) > limit:
            lines.append(f"… ещё {len(valid) - limit} строк (увеличь limit)")

        total_currency = _common_currency(valid)
        tot_clicks = _sum_counts(valid, "clicks")
        tot_regs = _sum_counts(valid, "registrations")
        tot_ftds = _sum_counts(valid, "ftds")
        lines.append(
            f"ИТОГО ({len(valid)}): "
            f"клики {tot_clicks if tot_clicks is not None else '—'}, "
            f"реги {tot_regs if tot_regs is not None else '—'}, "
            f"provider FTD {tot_ftds if tot_ftds is not None else '—'}, "
            f"доход {_sum_money(valid, 'revenue', currency=total_currency)}, "
            f"профит {_sum_money(valid, 'profit', currency=total_currency)}"
        )
        lines.append("Примечание: provider FTD ≠ локально подтверждённый депозит STOP-контура.")
        return "\n".join(lines)

    @staticmethod
    def _metrics_line(r: dict[str, Any], *, compact: bool = False) -> str:
        currency = _row_currency(r)
        clicks = _count(r.get("clicks"))
        regs = _count(r.get("registrations"))
        ftds = _count(r.get("ftds"))
        rev = r.get("revenue")
        if compact:
            return (
                f"клики {clicks if clicks is not None else '—'}, "
                f"реги {regs if regs is not None else '—'}, "
                f"provider FTD {ftds if ftds is not None else '—'}, "
                f"доход {_money(rev, currency=currency)}"
            )

        parts = [
            _format_count("Клики", r.get("clicks")),
            _format_count("Реги", r.get("registrations")),
            _format_count("Provider FTD", r.get("ftds")),
            f"Доход: {_money(rev, currency=currency)}",
            f"Расход: {_money(r.get('cost'), currency=currency)}",
            f"Профит: {_money(r.get('profit'), currency=currency)}",
        ]
        roi, epc = r.get("roi"), r.get("epc")
        parsed_roi = _decimal(roi)
        if parsed_roi is not None:
            parts.append(f"ROI: {parsed_roi}")
        if epc is not None:
            parts.append(f"EPC: {_money(epc, currency=currency)}")
        return " · ".join(parts)
