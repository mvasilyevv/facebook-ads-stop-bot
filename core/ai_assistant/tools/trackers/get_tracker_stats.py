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
from typing import Any, ClassVar

from core.adset_pro.credentials import create_adsetpro_client
from core.adset_pro.errors import AdsetProError
from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError

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


def _num(value: Any) -> float:
    """Безопасно в float. None/мусор → 0."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _money(value: Any) -> str:
    return f"${_num(value):.2f}"


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
        }
        if group_by:
            mcp_args["groups"] = [group_by]

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
        if not group_by:
            head = rows[0] if isinstance(rows[0], dict) else {}
            return (
                f"AdSet.pro {since}…{until} · итого\n{self._metrics_line(head)}\n"
                "Примечание: provider FTD ≠ локально подтверждённый депозит STOP-контура."
            )

        valid = [r for r in rows if isinstance(r, dict)]
        valid.sort(key=lambda r: (_num(r.get("ftds")), _num(r.get("revenue"))), reverse=True)

        lines = [f"AdSet.pro {since}…{until} · разрез: {group_by}"]
        for r in valid[:limit]:
            label = str(r.get(group_by) or "—").strip() or "—"
            lines.append(f"• {label[:48]}: {self._metrics_line(r, compact=True)}")
        if len(valid) > limit:
            lines.append(f"… ещё {len(valid) - limit} строк (увеличь limit)")

        tot_clicks = sum(_num(r.get("clicks")) for r in valid)
        tot_regs = sum(_num(r.get("registrations")) for r in valid)
        tot_ftds = sum(_num(r.get("ftds")) for r in valid)
        tot_rev = sum(_num(r.get("revenue")) for r in valid)
        tot_profit = sum(_num(r.get("profit")) for r in valid)
        lines.append(
            f"ИТОГО ({len(valid)}): клики {int(tot_clicks)}, реги {int(tot_regs)}, "
            f"provider FTD {int(tot_ftds)}, доход {_money(tot_rev)}, профит {_money(tot_profit)}"
        )
        lines.append("Примечание: provider FTD ≠ локально подтверждённый депозит STOP-контура.")
        return "\n".join(lines)

    @staticmethod
    def _metrics_line(r: dict[str, Any], *, compact: bool = False) -> str:
        clicks = int(_num(r.get("clicks")))
        regs = int(_num(r.get("registrations")))
        ftds = int(_num(r.get("ftds")))
        rev = r.get("revenue")
        if compact:
            return f"клики {clicks}, реги {regs}, provider FTD {ftds}, доход {_money(rev)}"

        parts = [
            f"Клики: {clicks}",
            f"Реги: {regs}",
            f"Provider FTD: {ftds}",
            f"Доход: {_money(rev)}",
            f"Расход: {_money(r.get('cost'))}",
            f"Профит: {_money(r.get('profit'))}",
        ]
        roi, epc = r.get("roi"), r.get("epc")
        if roi is not None:
            parts.append(f"ROI: {roi}")
        if epc is not None:
            parts.append(f"EPC: {epc}")
        return " · ".join(parts)
