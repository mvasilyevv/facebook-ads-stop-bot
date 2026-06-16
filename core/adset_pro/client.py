# -*- coding: utf-8 -*-
"""AdsetProClient — async MCP-клиент трекера AdSet.pro.

Архитектура (см. META_INTEGRATION_PLAN.md §4.4 / Этап 6):
- AdSet.pro — независимый от Vision канал post-click статистики.
- **Реальный API — это MCP-сервер `platform-stats-mcp`** (verify 2026-05-27):
  - base_url: https://adset.pro
  - endpoint:   POST /mcp
  - protocol:   JSON-RPC 2.0 (MCP 2025-06-18)
  - auth:       Authorization: Bearer mcp_xxx
  - tools:      query_stats, get_metadata, export_csv, list_campaigns,
                get_campaign, list_sources, list_offers, list_flows,
                list_cpas, resolve_ids
  - OAuth metadata: https://adset.pro/.well-known/oauth-authorization-server
- Hostname api.adset.pro **не существует** (NXDOMAIN) — старый предположительный
  REST путь /api/stats/query тоже отсутствует (404). См. отчёт в коммите.
- Retry на transient через tenacity (5xx и сеть).
- Постбэки (входящие webhook'и от AdSet.pro) — отдельный канал, см.
  apps/api/routers/postback.py + core.adset_pro.ingest.
"""

from __future__ import annotations

import logging
from datetime import date
from types import TracebackType
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.adset_pro.errors import (
    AdsetProError,
    AuthError,
    TemporaryError,
    classify_http_error,
)
from core.adset_pro.schemas import (
    ConversionRow,
    StatsQueryRequest,
    StatsQueryResponse,
)
from core.config import get_settings

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 15.0
# Все вызовы AdSet.pro идут через один MCP endpoint.
_MCP_PATH = "/mcp"
# MCP protocol version из live verify (initialize → "2025-06-18").
_MCP_PROTOCOL_VERSION = "2025-06-18"


def _build_headers(api_key: str) -> dict[str, str]:
    """Заголовки для MCP-запросов: Bearer + поддержка SSE как fallback."""
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }


class AdsetProClient:
    """Async MCP-клиент AdSet.pro (platform-stats-mcp).

    Все методы — обёртки над POST /mcp с JSON-RPC payload. Высокоуровневый
    публичный контракт (query_stats(StatsQueryRequest)) сохранён ради совместимости
    с предыдущей REST-абстракцией — внутри он маппится в MCP tool call.

    Usage:
        async with AdsetProClient() as client:
            ok = await client.health_check()
            stats = await client.query_stats(StatsQueryRequest(since=..., until=...))
            rows = await client.list_conversions(since=..., until=...)
            # Низкоуровневый доступ к любому MCP-tool:
            data = await client.call_mcp_tool("list_campaigns", {"limit": 10})
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = 3,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key if api_key is not None else settings.adsetpro_mcp_key
        self._base_url = (base_url or settings.adsetpro_base_url).rstrip("/")
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else getattr(settings, "adsetpro_timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
        )
        self._external_client = http_client is not None
        self._http: httpx.AsyncClient | None = http_client
        self._max_retries = max(1, max_retries)
        # JSON-RPC id монотонно растёт по жизни клиента.
        self._next_rpc_id = 1

    # ====================== lifecycle ======================

    async def start(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                headers=_build_headers(self._api_key),
            )
            logger.info("AdsetProClient запущен: %s", self._base_url)

    async def close(self) -> None:
        if self._http is not None and not self._external_client:
            await self._http.aclose()
            logger.info("AdsetProClient закрыт")
        self._http = None

    async def __aenter__(self) -> AdsetProClient:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # ====================== public API ======================

    async def health_check(self) -> bool:
        """Лёгкий пинг через JSON-RPC `initialize`. True/False без exception."""
        if self._http is None:
            raise RuntimeError("AdsetProClient не запущен: вызови await start()")
        rpc_body = self._make_rpc_envelope(
            method="initialize",
            params={
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "clientInfo": {"name": "fb-stop-bot", "version": "0.1"},
                "capabilities": {},
            },
        )
        try:
            resp = await self._http.post(_MCP_PATH, json=rpc_body)
        except httpx.HTTPError as exc:
            logger.debug("AdsetProClient health_check: сетевая ошибка %s", exc)
            return False
        if not (200 <= resp.status_code < 300):
            return False
        try:
            payload = resp.json()
        except ValueError:
            return False
        return isinstance(payload, dict) and "result" in payload

    async def query_stats(self, query: StatsQueryRequest) -> StatsQueryResponse:
        """MCP tool `query_stats` — выборка статистики/конверсий за интервал.

        Контракт StatsQueryRequest конвертируется в MCP args: since/until → from/to,
        ad_id (наш ext_sub6 ↔ fb_ad_id) → filter в массиве. group_by/extra_filters
        пробрасываем как есть для совместимости.
        """
        args = self._stats_args_from_request(query)
        structured = await self.call_mcp_tool("query_stats", args)
        return StatsQueryResponse.from_api_payload(structured)

    async def list_conversions(
        self,
        *,
        since: date,
        until: date,
        ad_id: str | None = None,
    ) -> list[ConversionRow]:
        """Сахар над query_stats для удобства потребителей."""
        request = StatsQueryRequest(since=since, until=until, ad_id=ad_id)
        response = await self.query_stats(request)
        return list(response.rows)

    async def call_mcp_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Универсальный MCP tool call. Возвращает structuredContent от tool (или
        парсит JSON из content[0].text как fallback).

        Используется и в query_stats, и как public API для будущих AI-tools
        (list_campaigns, get_metadata, resolve_ids, и т.п.).
        """
        rpc_body = self._make_rpc_envelope(
            method="tools/call",
            params={"name": tool_name, "arguments": arguments or {}},
        )
        result = await self._post_rpc(rpc_body, tool_name=tool_name)
        self._raise_if_tool_error(result, tool_name=tool_name)
        return self._extract_tool_result(result, tool_name=tool_name)

    # ====================== internals ======================

    def _make_rpc_envelope(
        self,
        *,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_rpc_id, "method": method}
        if params is not None:
            body["params"] = params
        self._next_rpc_id += 1
        return body

    async def _post_rpc(
        self,
        rpc_body: dict[str, Any],
        *,
        tool_name: str,
    ) -> dict[str, Any]:
        """POST /mcp с retry. Возвращает разобранный `result` JSON-RPC."""
        if self._http is None:
            raise RuntimeError("AdsetProClient не запущен: вызови await start()")

        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1.0, min=1.0, max=8.0),
            retry=retry_if_exception_type((TemporaryError, httpx.TransportError)),
            reraise=True,
        )

        async for attempt in retrying:
            with attempt:
                try:
                    resp = await self._http.post(_MCP_PATH, json=rpc_body)
                except httpx.TransportError:
                    logger.warning(
                        "AdsetProClient: сетевая ошибка на tool=%s — будет retry",
                        tool_name,
                    )
                    raise
                return self._parse_rpc_response(resp, tool_name=tool_name)

        raise AdsetProError(
            "AdsetProClient: retry loop finished without result",
            endpoint=_MCP_PATH,
        )

    def _parse_rpc_response(
        self,
        resp: httpx.Response,
        *,
        tool_name: str,
    ) -> dict[str, Any]:
        """Разобрать HTTP-ответ MCP. Не-2xx → AdsetProError; JSON-RPC error → тоже."""
        if not (200 <= resp.status_code < 300):
            body = resp.text[:500]
            message = f"AdSet.pro MCP {resp.status_code} на tool={tool_name}: {body}"
            raise classify_http_error(
                resp.status_code,
                message,
                endpoint=_MCP_PATH,
                response_body=body,
            )

        if not resp.content:
            raise TemporaryError(
                f"Пустой ответ MCP на tool={tool_name}",
                status_code=resp.status_code,
                endpoint=_MCP_PATH,
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise TemporaryError(
                f"Невалидный JSON в ответе MCP: {exc}",
                status_code=resp.status_code,
                endpoint=_MCP_PATH,
                response_body=resp.text[:500],
            ) from exc

        if not isinstance(data, dict):
            raise TemporaryError(
                f"Ожидали JSON-RPC объект, получили {type(data).__name__}",
                status_code=resp.status_code,
                endpoint=_MCP_PATH,
            )

        if "error" in data:
            err = data["error"]
            err_msg = err.get("message") if isinstance(err, dict) else str(err)
            raise AdsetProError(
                f"AdSet.pro MCP error на tool={tool_name}: {err_msg}",
                status_code=resp.status_code,
                endpoint=_MCP_PATH,
                response_body=resp.text[:500],
            )

        return data

    @staticmethod
    def _raise_if_tool_error(rpc_response: dict[str, Any], *, tool_name: str) -> None:
        """MCP tool-level ошибка (result.isError=true) → исключение.

        Без этого write-фейлы (напр. create_* с read-only ключом: «not authenticated
        or missing required scope») тихо терялись как {} и выглядели успехом.
        """
        result = rpc_response.get("result")
        if not isinstance(result, dict) or not result.get("isError"):
            return
        msg = ""
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                msg = str(item["text"])
                break
        msg = msg or f"MCP tool {tool_name} вернул isError"
        low = msg.lower()
        if any(
            k in low for k in ("scope", "authenticat", "permission", "not allowed", "forbidden")
        ):
            raise AuthError(msg, endpoint=_MCP_PATH)
        raise AdsetProError(msg, endpoint=_MCP_PATH)

    @staticmethod
    def _extract_tool_result(
        rpc_response: dict[str, Any],
        *,
        tool_name: str,
    ) -> dict[str, Any]:
        """Из JSON-RPC ответа вынуть полезную часть.

        MCP-схема: result.structuredContent — типизированный объект (предпочтительно).
        Fallback — result.content[0].text как JSON-строка (когда structuredContent
        пуст у конкретного сервера). Если оба пусты — возвращаем {}.
        """
        result = rpc_response.get("result")
        if not isinstance(result, dict):
            return {}

        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured

        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                text = item.get("text", "")
                if not isinstance(text, str) or not text.strip():
                    continue
                try:
                    import json as _json

                    parsed = _json.loads(text)
                except ValueError:
                    logger.debug(
                        "AdsetProClient: content[].text для tool=%s — не JSON, fallback пропущен",
                        tool_name,
                    )
                    return {}
                if isinstance(parsed, dict):
                    return parsed
                return {"data": parsed}

        return {}

    @staticmethod
    def _stats_args_from_request(query: StatsQueryRequest) -> dict[str, Any]:
        """Конвертация StatsQueryRequest → arguments для MCP tool `query_stats`.

        Маппинг:
        - since/until → from/to (ISO date)
        - ad_id → filter {field:'ext_sub6', op:'eq', value:<ad_id>}
        - group_by → groups
        - extra_filters → дополнительные фильтры (формат extra_filters совпадает
          по форме с MCP filters: {field,op,value} либо {key:value} как простой
          equality — на всякий случай поддерживаем оба варианта).
        """
        args: dict[str, Any] = {
            "from": query.since.isoformat(),
            "to": query.until.isoformat(),
        }
        filters: list[dict[str, Any]] = []
        if query.ad_id:
            filters.append({"field": "ext_sub6", "op": "eq", "value": query.ad_id})
        for key, value in (query.extra_filters or {}).items():
            if isinstance(value, dict) and {"field", "op", "value"} <= value.keys():
                filters.append({k: value[k] for k in ("field", "op", "value")})
            else:
                filters.append({"field": str(key), "op": "eq", "value": str(value)})
        if filters:
            args["filters"] = filters
        if query.group_by:
            args["groups"] = list(query.group_by)
        return args
