# -*- coding: utf-8 -*-
"""Tool get_account_health — статус кабинета + spending today.

GET /me/adaccounts → list. Для конкретного ad_account — GET /act_X с fields:
name, account_status, currency, amount_spent, balance, spend_cap, disable_reason.
+ insights за today (если указан ad_account_id).
"""

from __future__ import annotations

from typing import Any, ClassVar

from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError
from core.ai_assistant.tools.meta._currency import (
    currency_evidence,
    format_major_money,
    format_minor_money,
)
from core.meta_api.errors import MetaApiError
from core.meta_api.insights.fetcher import InsightsFetcher

# Marketing API account_status enum
_ACCOUNT_STATUS = {
    1: "ACTIVE",
    2: "DISABLED",
    3: "UNSETTLED",
    7: "PENDING_RISK_REVIEW",
    8: "PENDING_SETTLEMENT",
    9: "IN_GRACE_PERIOD",
    100: "PENDING_CLOSURE",
    101: "CLOSED",
    201: "ANY_ACTIVE",
    202: "ANY_CLOSED",
}


class GetAccountHealthTool:
    """Здоровье кабинета: статус, баланс, spend today, disable_reason."""

    name: ClassVar[str] = "get_account_health"
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ_ONLY
    schema: ClassVar[dict[str, Any]] = {
        "name": "get_account_health",
        "description": (
            "Статус ad account: account_status, currency, amount_spent, "
            "spend_cap, disable_reason, spend за today. "
            "Без ad_account_id — список всех кабинетов из /me/adaccounts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ad_account_id": {"type": "string", "description": "act_X либо пусто"},
            },
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        client = ctx.require_meta_api()
        ad_account_id = (args.get("ad_account_id") or "").strip()

        if not ad_account_id:
            try:
                response = await client.list_ad_accounts()
            except MetaApiError as exc:
                raise ToolError(f"Marketing API: {exc}") from exc
            accounts = response.get("data") or []
            if not accounts:
                return "У текущей сессии нет доступных ad accounts."
            lines = [f"Доступные ad accounts ({len(accounts)}):"]
            for acc in accounts:
                status = _ACCOUNT_STATUS.get(int(acc.get("account_status") or 0), "UNKNOWN")
                listed_currency = currency_evidence(acc.get("currency"))
                lines.append(
                    f"- {acc.get('id')} «{acc.get('name', '?')}» "
                    f"status={status} "
                    f"currency={listed_currency.code if listed_currency else 'unknown'}"
                )
            return "\n".join(lines)

        if not ad_account_id.startswith("act_"):
            raise ToolError("ad_account_id должен начинаться с 'act_'")

        try:
            account = await client.execute_graph_call(
                method="GET",
                endpoint=f"/{ad_account_id}",
                query_params={
                    "fields": (
                        "name,account_status,currency,amount_spent,balance,"
                        "spend_cap,disable_reason,timezone_name"
                    ),
                },
                ad_account_id=ad_account_id,
            )
        except MetaApiError as exc:
            raise ToolError(f"Marketing API: {exc}") from exc

        fetcher = InsightsFetcher(client)
        try:
            insights_row = await fetcher.fetch_account_summary(
                ad_account_id=ad_account_id, date_preset="today"
            )
        except MetaApiError as exc:
            raise ToolError(f"Marketing API (insights): {exc}") from exc

        status = _ACCOUNT_STATUS.get(int(account.get("account_status") or 0), "UNKNOWN")
        disable_reason = account.get("disable_reason")
        currency = currency_evidence(account.get("currency"))
        amount_spent = account.get("amount_spent", "?")
        balance = account.get("balance", "?")
        spend_cap = account.get("spend_cap")

        lines = [
            f"Кабинет {ad_account_id} «{account.get('name', '?')}»",
            f"Статус: {status} (raw={account.get('account_status')})",
            f"Валюта: {currency.code if currency else 'не подтверждена'}",
            f"Spent (lifetime): {format_minor_money(amount_spent, currency)}",
            f"Balance: {format_minor_money(balance, currency)}",
            f"Spend cap: {format_minor_money(spend_cap, currency)}",
            f"Timezone: {account.get('timezone_name', '?')}",
        ]
        if disable_reason:
            lines.append(f"⚠️ disable_reason: {disable_reason}")
        if insights_row:
            cpc_str = format_major_money(insights_row.cpc, currency)
            lines.append(
                f"Today: spend={format_major_money(insights_row.spend, currency)} "
                f"impr={insights_row.impressions} "
                f"clicks={insights_row.clicks} cpc={cpc_str}"
            )
        else:
            lines.append("Today: insights не вернули данные (нет активности либо новый кабинет)")
        return "\n".join(lines)
