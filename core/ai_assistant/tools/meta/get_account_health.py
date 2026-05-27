# -*- coding: utf-8 -*-
"""Tool get_account_health — статус кабинета + spending today.

GET /me/adaccounts → list. Для конкретного ad_account — GET /act_X с fields:
name, account_status, currency, amount_spent, balance, spend_cap, disable_reason.
+ insights за today (если указан ad_account_id).
"""

from __future__ import annotations

from typing import Any, ClassVar

from core.ai_assistant.tools.base import RiskLevel, ToolContext, ToolError
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
                lines.append(
                    f"- {acc.get('id')} «{acc.get('name', '?')}» "
                    f"status={status} currency={acc.get('currency', '?')}"
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
        currency = account.get("currency", "?")
        amount_spent = account.get("amount_spent", "?")
        balance = account.get("balance", "?")
        spend_cap = account.get("spend_cap")

        lines = [
            f"Кабинет {ad_account_id} «{account.get('name', '?')}»",
            f"Статус: {status} (raw={account.get('account_status')})",
            f"Валюта: {currency}",
            f"Spent (lifetime): {amount_spent}",
            f"Balance: {balance}",
            f"Spend cap: {spend_cap or '—'}",
            f"Timezone: {account.get('timezone_name', '?')}",
        ]
        if disable_reason:
            lines.append(f"⚠️ disable_reason: {disable_reason}")
        if insights_row:
            cpc_str = f"${insights_row.cpc:.2f}" if insights_row.cpc is not None else "—"
            lines.append(
                f"Today: spend=${insights_row.spend:.2f} impr={insights_row.impressions} "
                f"clicks={insights_row.clicks} cpc={cpc_str}"
            )
        else:
            lines.append("Today: insights не вернули данные (нет активности либо новый кабинет)")
        return "\n".join(lines)
