# -*- coding: utf-8 -*-
"""Скаффолдинг кампаний/PWA в AdSet.pro через документированные MCP create-тулзы.

Что покрывает API (снято 16.06, MCP `platform-stats-mcp` v1.0.0, 43 тула):
  create_offer / create_flow / create_pixel — полноценно;
  create_campaign / create_pwa — только СКЕЛЕТ (пустые stream-sets / без контента).
Ротация, сплит-тест, антибот-фильтры кампании и контент PWA (иконка/скрины/текст/
отзывы) в API НЕ выведены — добиваются в UI. Поэтому это «скаффолдинг», не сквозной
автозалив (см. обсуждение 16.06).

**Confirm-first (money-критично):** каждый create по умолчанию `confirm=False` →
возвращает `BuildPlan` (ничего НЕ создаёт), показываем юзеру → только `confirm=True`
реально дёргает MCP. Сам API это требует: «only call AFTER explicit confirmation».
Перекликается с draft-first в core/meta_api.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.adset_pro.client import AdsetProClient

# Mongo ObjectId — 24 hex. Если строка такая, считаем её готовым id (не резолвим по имени).
_ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")


@dataclass(slots=True, frozen=True)
class BuildPlan:
    """Что будет создано — показывается юзеру ДО реального вызова (confirm-first)."""

    tool: str
    args: dict[str, Any]
    summary: str


class AdsetProBuilder:
    """Обёртка над create-тулзами AdSet.pro. Только скелет; confirm-first.

    Usage:
        async with AdsetProClient() as cl:
            b = AdsetProBuilder(cl)
            plan = await b.create_offer(name="GH_CR", cpa="Offerleader")  # confirm=False → BuildPlan
            ...показать plan юзеру...
            res = await b.create_offer(name="GH_CR", cpa="Offerleader", confirm=True)  # реально создаём
    """

    def __init__(self, client: AdsetProClient) -> None:
        self._c = client

    # ====================== resolvers (read-only) ======================

    async def _resolve(self, list_tool: str, value: str) -> str:
        """value = 24-hex id (вернём как есть) ИЛИ имя (найдём id через list_*).

        Матч по имени: сначала точное (case-insensitive), потом подстрока. Берём
        первую страницу list_* — для аккаунтов с сотнями сущностей может понадобиться
        пагинация (TODO), пока хватает.
        """
        if not value:
            raise ValueError(f"{list_tool}: пустое значение для резолва")
        if _ID_RE.match(value):
            return value
        data = await self._c.call_mcp_tool(list_tool, {})
        items = data.get("items") or [] if isinstance(data, dict) else []
        low = value.lower()
        exact = [i for i in items if str(i.get("name", "")).lower() == low]
        partial = [i for i in items if low in str(i.get("name", "")).lower()]
        hit = exact or partial
        if not hit:
            raise ValueError(f"{list_tool}: не нашёл '{value}' (резолв id по имени)")
        return str(hit[0].get("id") or hit[0].get("_id") or "")

    async def find_cpa(self, name: str) -> str:
        return await self._resolve("list_cpas", name)

    async def find_offer(self, name: str) -> str:
        return await self._resolve("list_offers", name)

    async def find_source(self, name: str) -> str:
        return await self._resolve("list_sources", name)

    async def find_domain(self, name: str) -> str:
        return await self._resolve("list_domains", name)

    # ====================== creates (confirm-first) ======================

    async def _do(
        self, tool: str, args: dict[str, Any], summary: str, confirm: bool
    ) -> BuildPlan | dict[str, Any]:
        clean = {k: v for k, v in args.items() if v is not None}
        plan = BuildPlan(tool=tool, args=clean, summary=summary)
        if not confirm:
            return plan
        return await self._c.call_mcp_tool(tool, clean)

    async def create_campaign(
        self,
        *,
        name: str,
        source: str | None = None,
        domain: str | None = None,
        confirm: bool = False,
    ) -> BuildPlan | dict[str, Any]:
        """Скелет кампании (пустые stream-sets, без фильтров). source/domain — имя или id."""
        sid = await self.find_source(source) if source else None
        did = await self.find_domain(domain) if domain else None
        args = {"name": name, "sourceId": sid, "domainId": did}
        return await self._do("create_campaign", args, f"Кампания-скелет «{name}»", confirm)

    async def create_pwa(
        self,
        *,
        name: str,
        category: str,
        language: str | None = None,
        confirm: bool = False,
    ) -> BuildPlan | dict[str, Any]:
        """Скелет PWA (без контента — иконку/скрины/текст заливают в UI)."""
        args = {"name": name, "category": category, "language": language}
        return await self._do("create_pwa", args, f"PWA-скелет «{name}» [{category}]", confirm)

    async def create_offer(
        self,
        *,
        name: str,
        cpa: str,
        kind: str | None = None,
        revenue: float | None = None,
        country: list[str] | None = None,
        language: str | None = None,
        confirm: bool = False,
    ) -> BuildPlan | dict[str, Any]:
        """Оффер под CPA-сеть. cpa — имя или id (резолвим через list_cpas)."""
        cpa_id = await self.find_cpa(cpa)
        args = {
            "name": name,
            "cpaId": cpa_id,
            "type": kind,
            "revenue": revenue,
            "country": country,
            "language": language,
        }
        return await self._do("create_offer", args, f"Оффер «{name}» (cpa={cpa})", confirm)

    async def create_flow(
        self,
        *,
        name: str,
        cpa: str,
        offer: str,
        url: str,
        kind: str | None = None,
        confirm: bool = False,
    ) -> BuildPlan | dict[str, Any]:
        """Поток (offer→url). cpa/offer — имя или id."""
        cpa_id = await self.find_cpa(cpa)
        offer_id = await self.find_offer(offer)
        args = {"name": name, "cpaId": cpa_id, "offerId": offer_id, "url": url, "type": kind}
        return await self._do("create_flow", args, f"Поток «{name}» (offer={offer})", confirm)

    async def create_pixel(
        self,
        *,
        name: str,
        confirm: bool = False,
        **fields: Any,
    ) -> BuildPlan | dict[str, Any]:
        """Пиксель (постбэк/конверсии). Доп.поля (token/eventMap/...) — pass-through по MCP-схеме."""
        args = {"name": name, **fields}
        return await self._do("create_pixel", args, f"Пиксель «{name}»", confirm)
